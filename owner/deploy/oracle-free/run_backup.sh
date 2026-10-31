#!/usr/bin/env bash
# Nightly off-VM backup for the Oracle Free Owner deploy (2026-10-10).
#
# Why this exists: OWNER_BACKUP_TARGET_URL in .env.oracle is only FORMAT-checked
# by config.py -- nothing in the app ever writes a backup there. Before this
# script the "backup target" was a promise with no job behind it, so losing
# the Neon project or the VM's signing-key volume would have been permanent:
# every licence ever issued would need re-issuing and every till re-trusting.
#
# What it does, every night (systemd/aura-owner-backup.timer):
#   1. pg_dump the Owner database (custom format) with a postgres client of
#      the SAME major version as Neon -- an older pg_dump refuses a newer
#      server, so the client comes from the postgres:<major> image, not apt.
#   2. tar the signing-key directory out of the running owner container.
#   3. Encrypt both (AES-256, PBKDF2) with BACKUP_PASSPHRASE. The bucket is
#      private and the token is scoped to it, but the dump holds customer and
#      licence records, so a leaked token alone must not leak them.
#   3b. Also tar the uploaded-files directories (expense attachments and
#      release artifacts, their own named volumes) as a THIRD encrypted object.
#      Empty/missing directories are logged and skipped, never a failure.
#   4. PUT each to Cloudflare R2 with curl --aws-sigv4 and re-read each
#      object's size; a size mismatch is a FAILED backup, not a warning.
#
# Secrets live in .env.r2 next to this script (root-readable, gitignored by
# the repo's `.env.*` rule): R2_ENDPOINT, R2_BUCKET, R2_ACCESS_KEY_ID,
# R2_SECRET_ACCESS_KEY, BACKUP_PASSPHRASE. Keep a copy of BACKUP_PASSPHRASE
# OFF the VM -- without it the backups cannot be opened.
#
# Retention is NOT done here: an R2 lifecycle rule on the bucket deletes
# objects after 35 days, so a broken script can never delete good backups.
#
# Restore (proven 2026-10-10 against a real backup from this script):
#   curl --aws-sigv4 ... -o x.dump.enc <object>
#   openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE -in x.dump.enc -out x.dump
#   pg_restore --list x.dump   (then pg_restore -d <url> x.dump)
set -euo pipefail

COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$COMPOSE_DIR/.env.oracle"
R2_ENV_FILE="$COMPOSE_DIR/.env.r2"
COMPOSE=(docker compose -f "$COMPOSE_DIR/docker-compose.yml" --env-file "$ENV_FILE")
PG_MAJOR="${PG_MAJOR:-18}"
SIGNING_KEY_DIR=/app/owner/var/signing-keys
UPLOADS_PARENT=/app/owner/var
# sync-bootstrap is DELIBERATELY absent (SYNC-BOOT Phase 3): it holds transient,
# encrypted shop copies in transit between devices (deleted on ack / after 72 h).
# Backing it up would keep a shop's data longer than its owner expects.
UPLOAD_DIRS=(expense-attachments release-artifacts kyc-documents)

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"; }
fail() { log "FAILED: $1"; exit 1; }
env_value() { grep -E "^${2}=" "$1" 2>/dev/null | tail -1 | cut -d= -f2-; }

[ -r "$R2_ENV_FILE" ] || fail "$R2_ENV_FILE missing or unreadable -- no backup taken."
for k in R2_ENDPOINT R2_BUCKET R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY BACKUP_PASSPHRASE; do
  [ -n "$(env_value "$R2_ENV_FILE" "$k")" ] || fail "$k is empty in $R2_ENV_FILE -- no backup taken."
done
R2_ENDPOINT="$(env_value "$R2_ENV_FILE" R2_ENDPOINT)"
R2_BUCKET="$(env_value "$R2_ENV_FILE" R2_BUCKET)"
R2_USER="$(env_value "$R2_ENV_FILE" R2_ACCESS_KEY_ID):$(env_value "$R2_ENV_FILE" R2_SECRET_ACCESS_KEY)"
BACKUP_PASSPHRASE="$(env_value "$R2_ENV_FILE" BACKUP_PASSPHRASE)"
export BACKUP_PASSPHRASE

# Owner reads postgresql+psycopg://; libpq (pg_dump) only knows postgresql://.
DB_URL="$(env_value "$ENV_FILE" OWNER_DATABASE_URL)"
[ -n "$DB_URL" ] || fail "OWNER_DATABASE_URL is empty in $ENV_FILE."
DB_URL="${DB_URL/postgresql+psycopg:\/\//postgresql://}"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
prefix="owner/$(date -u +%Y/%m/%d)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
chmod 700 "$work"

log "Dumping the Owner database (pg_dump from postgres:$PG_MAJOR)..."
# The URL goes in through the environment, never argv, so it is not visible
# in `ps` on the VM.
PGURL="$DB_URL" docker run --rm -e PGURL postgres:"$PG_MAJOR"-alpine \
  sh -c 'pg_dump --format=custom --no-owner --no-privileges "$PGURL"' > "$work/owner.dump" \
  || fail "pg_dump exited non-zero."
[ -s "$work/owner.dump" ] || fail "pg_dump produced an empty file."
log "Database dump: $(stat -c %s "$work/owner.dump") bytes."

log "Copying the signing-key directory out of the owner container..."
"${COMPOSE[@]}" exec -T owner tar -C "$(dirname "$SIGNING_KEY_DIR")" -cf - "$(basename "$SIGNING_KEY_DIR")" \
  > "$work/signing-keys.tar" || fail "could not tar $SIGNING_KEY_DIR from the owner container."
tar -tf "$work/signing-keys.tar" | grep -q '\.pem$' \
  || fail "the signing-key tar holds no .pem -- refusing to upload a backup that cannot restore licensing."

log "Copying the uploaded-files directories (expense attachments, release artifacts)..."
# Not fatal: a fresh deploy has no attachments, and a missing uploads tar must
# never cost the database and signing-key backups above.
uploads_ok=0
if "${COMPOSE[@]}" exec -T owner tar -C "$UPLOADS_PARENT" -cf - "${UPLOAD_DIRS[@]}" > "$work/uploads.tar" 2>"$work/uploads.err"; then
  if [ "$(tar -tf "$work/uploads.tar" | grep -vc '/$')" -gt 0 ]; then
    uploads_ok=1
  else
    log "No uploaded files yet (both directories empty) -- skipping the uploads backup."
  fi
else
  log "WARNING: could not tar the uploaded-files directories ($(head -c 200 "$work/uploads.err")) -- skipping the uploads backup."
fi

upload() {  # $1 = local file, $2 = object key
  local enc="$1.enc" size remote
  openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BACKUP_PASSPHRASE -in "$1" -out "$enc" \
    || fail "encryption of $(basename "$1") failed."
  size="$(stat -c %s "$enc")"
  curl -fsS --aws-sigv4 "aws:amz:auto:s3" --user "$R2_USER" \
       -H "x-amz-content-sha256: UNSIGNED-PAYLOAD" \
       --upload-file "$enc" "$R2_ENDPOINT/$R2_BUCKET/$2" -o /dev/null \
    || fail "upload of $2 failed."
  remote="$(curl -fsSI --aws-sigv4 "aws:amz:auto:s3" --user "$R2_USER" "$R2_ENDPOINT/$R2_BUCKET/$2" \
            | tr -d '\r' | awk -F': ' 'tolower($1)=="content-length"{print $2}')"
  [ "$remote" = "$size" ] || fail "$2 is $remote bytes in R2 but $size bytes locally."
  log "Uploaded $2 ($size bytes, size re-read from R2)."
}

upload "$work/owner.dump" "$prefix/owner-db-$stamp.dump.enc"
upload "$work/signing-keys.tar" "$prefix/owner-signing-keys-$stamp.tar.enc"
if [ "$uploads_ok" -eq 1 ]; then
  upload "$work/uploads.tar" "$prefix/owner-uploads-$stamp.tar.enc"
fi
log "Backup OK."
