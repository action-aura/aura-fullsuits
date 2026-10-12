#!/usr/bin/env bash
# Aura Owner -- Oracle Cloud Always Free bootstrap.
#
# On a fresh Oracle Ubuntu VM (SSH'd in as the default user, e.g.
# `ssh ubuntu@<vm-ip>`), clone the deploy branch to the path this script
# deploys from, and run it FROM THAT CHECKOUT:
#
#   git clone --branch feat/money-fixes \
#     https://github.com/action-aura/aura-fullsuits.git ~/aura-fullsuits
#   bash ~/aura-fullsuits/owner/deploy/oracle-free/bootstrap.sh
#
# Re-runs are the same second line. Do NOT `curl` this file from a
# raw.githubusercontent.com BRANCH URL: that CDN was observed serving a stale
# copy for minutes after a push (AUDIT 2026-09-24 gap G4, runbook section
# 4.3). If a single-file fetch is unavoidable, put a COMMIT SHA in the URL,
# never a branch name.
#
# NOTE THE BRANCH -- it is deliberately not `master`. None of the
# free-hosting deploy content exists on master yet (verified by HTTP: master's
# copies of this script, restore_signing_key.py, fly.toml and the runbook all
# return 404, while the branch's return 200). master's last substantive commit
# predates all of it by more than a month, and this branch is over a thousand
# commits ahead of it, so "just merge to master" is not a tidy-up -- it is the
# whole project's divergent history, and not something to do in order to fix a
# download URL.
#
# When master does catch up, change REPO_BRANCH's default below and the
# clone command above together, in one commit. They must agree, and this
# script now ENFORCES the part that matters: step 2 refuses to deploy a
# checkout whose commit differs from the commit this script itself was
# read from (a mismatch means running one version's script against another
# version's compose file and systemd units).
#
# What it does, in order: installs Docker if missing, clones/updates the
# repo, checks .env.oracle exists and every required value is filled in
# (refuses to guess or silently proceed on a placeholder), builds and starts
# the stack, waits for the owner service to report healthy, and runs
# 'flask commercial preflight' so you see the real readiness picture
# immediately instead of finding out later.
#
# Idempotent -- safe to re-run after fixing something it flagged. Loud on
# failure: every failure path below prints exactly what to fix and exits
# nonzero, never continues past a problem it can't resolve itself.
#
# Deliberately does NOT touch the signing key, seed a sellable plan, or
# create the first admin -- those are deliberate, described, one-time
# manual steps (see docs/ops/free-hosting-owner.md sections 4.4-4.6). This
# script prints exactly what's left when it finishes.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/action-aura/aura-fullsuits.git}"
# Which branch to deploy from. NOT master -- see the note at the top of this
# file: master does not carry the free-hosting deploy content at all, so a
# bare `git clone` (which takes the remote's default branch) would put a repo
# on the VM that has no owner/deploy/oracle-free directory, and this script
# would then fail several steps later on a missing compose file rather than
# here, where the cause is obvious.
# Default is a branch that exists and carries this deploy content, NOT what the live
# server runs: the live Oracle server currently runs feat/real-email. Until a
# release branch exists, always pass the deploy branch explicitly
# (REPO_BRANCH=feat/... bash bootstrap.sh) rather than trusting this default.
REPO_BRANCH="${REPO_BRANCH:-feat/money-fixes}"
REPO_DIR="${REPO_DIR:-$HOME/aura-fullsuits}"
COMPOSE_DIR="$REPO_DIR/owner/deploy/oracle-free"
ENV_FILE="$COMPOSE_DIR/.env.oracle"
COMPOSE="docker compose -f $COMPOSE_DIR/docker-compose.yml --env-file $ENV_FILE"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
fail() { printf '\033[31mFAIL: %s\033[0m\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 0. AUDIT 2026-09-24 gap G4 -- announce which commit this script is
#    actually running from. raw.githubusercontent.com is a CDN and was
#    observed serving a STALE copy of this exact file for several minutes
#    after a push while writing this runbook -- confirmed via the GitHub API
#    (the commit was already present on the branch) while the raw URL still
#    handed back the previous version. This banner only works when the
#    script is running from inside a real git checkout, which is exactly
#    why section 4.3 now recommends `git clone` + running the script from
#    that checkout instead of a bare single-file `curl` -- see the
#    WARNING branch below for what to do if you see it.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_running_commit=""
# `ls-files --error-unmatch` rather than just "is SCRIPT_DIR inside some work
# tree": a curl'd copy dropped into an unrelated git repo (a dotfiles repo in
# $HOME, say) would otherwise report THAT repo's HEAD as this script's commit.
if git -C "$SCRIPT_DIR" ls-files --error-unmatch -- "$(basename "${BASH_SOURCE[0]}")" >/dev/null 2>&1; then
  _running_commit="$(git -C "$SCRIPT_DIR" rev-parse HEAD)"
  _running_branch="$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "(detached HEAD)")"
  bold "Running bootstrap.sh from commit $_running_commit (branch: $_running_branch)."
  if ! git -C "$SCRIPT_DIR" diff --quiet HEAD -- "$(basename "${BASH_SOURCE[0]}")" 2>/dev/null; then
    bold "WARNING: this copy of bootstrap.sh has local edits on top of that commit -- the commit above is not exactly what is running."
  fi
else
  bold "WARNING: bootstrap.sh is NOT running from inside a git checkout -- its commit cannot be verified. This usually means it was fetched with a bare 'curl' of the single file, which is exactly the shape that hit the stale-CDN-copy problem above. Prefer: git clone --branch $REPO_BRANCH \"$REPO_URL\" \"$REPO_DIR\" && bash \"$COMPOSE_DIR/bootstrap.sh\" -- see docs/ops/free-hosting-owner.md section 4.3."
fi

# ---------------------------------------------------------------------------
# 1. Docker + the compose plugin.
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  bold "Docker not found -- installing via get.docker.com..."
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  fail "Docker was just installed. Your current shell's group membership hasn't picked up 'docker' yet -- log out and back in (or run 'newgrp docker'), then re-run this script. This is not an error with the install."
fi
docker compose version >/dev/null 2>&1 \
  || fail "'docker compose' (the plugin, space not hyphen) isn't working. A fresh get.docker.com install includes it -- check 'docker compose version' by hand before re-running."

# ---------------------------------------------------------------------------
# 2. Get the code.
# ---------------------------------------------------------------------------
if [ -d "$REPO_DIR/.git" ]; then
  # Re-running after a branch change must actually move the checkout, not just
  # pull whatever branch happens to be out. A bare `git pull` on a re-run left
  # the VM on the old branch silently, which is the worst shape of this bug:
  # the script succeeds and deploys the wrong code.
  bold "Repo already present at $REPO_DIR -- fetching and checking out $REPO_BRANCH."
  git -C "$REPO_DIR" fetch origin "$REPO_BRANCH" \
    || fail "Could not fetch branch '$REPO_BRANCH' from $REPO_URL. Check the branch name and that the VM has outbound network."
  git -C "$REPO_DIR" checkout "$REPO_BRANCH" \
    || fail "Could not check out '$REPO_BRANCH'. If you have local edits on the VM, commit or discard them first -- this script will not throw away your work for you."
  git -C "$REPO_DIR" pull --ff-only origin "$REPO_BRANCH" \
    || fail "Fast-forward pull of '$REPO_BRANCH' failed, which means the VM's copy has diverged from the remote. Resolve that by hand -- refusing to merge or reset on your behalf."
else
  bold "Cloning $REPO_URL (branch $REPO_BRANCH) into $REPO_DIR..."
  git clone --branch "$REPO_BRANCH" "$REPO_URL" "$REPO_DIR" \
    || fail "Clone failed. If the error mentions the branch, confirm '$REPO_BRANCH' still exists on the remote -- branches get renamed and merged, and this default will need updating when the deploy content reaches master."
fi

# Prove the clone actually contains what the rest of this script depends on,
# HERE, where the cause is still obvious -- rather than four steps later with
# a confusing "no such file" about a compose file.
[ -f "$COMPOSE_DIR/docker-compose.yml" ] \
  || fail "Cloned '$REPO_BRANCH' but $COMPOSE_DIR/docker-compose.yml is missing. That branch does not carry the free-hosting deploy content. Set REPO_BRANCH to one that does."

# AUDIT 2026-09-24 gap G4, second half (adversarial verification 2026-09-26):
# printing the commit THIS SCRIPT came from is not enough on its own -- the
# code actually deployed is $REPO_DIR's checkout, which the fetch/pull above
# may just have moved. Reproduced in a sandbox before this check existed: a
# re-run of a stale copy (e.g. a second, older checkout) banner-announced
# commit A, pulled $REPO_DIR forward to commit B, and deployed B with exit
# 0 -- the stale-script-against-newer-compose-file mismatch the header warns
# about, with a banner actively naming the wrong commit. So: name the
# deployed commit, and refuse when the two differ. Running from inside
# $REPO_DIR itself (the documented way) self-heals: the refusal fires once
# after a pull brought in new commits, and the re-run it asks for matches.
_deploy_commit="$(git -C "$REPO_DIR" rev-parse HEAD)"
bold "Deploying commit $_deploy_commit from $REPO_DIR."
if [ -n "$_running_commit" ] && [ "$_running_commit" != "$_deploy_commit" ]; then
  fail "This bootstrap.sh was read from commit $_running_commit, but the checkout it is about to deploy ($REPO_DIR) is now at $_deploy_commit. Refusing to run one version's script against another version's compose file and systemd units. Re-run the copy inside that checkout: bash $COMPOSE_DIR/bootstrap.sh"
fi

# ---------------------------------------------------------------------------
# 3. .env.oracle must exist -- never auto-generate secrets silently. A
#    deploy nobody can reproduce because the script invented its own
#    passwords is worse than one that stops and asks.
# ---------------------------------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
  cp "$COMPOSE_DIR/.env.example" "$ENV_FILE"
  fail "$ENV_FILE didn't exist -- created from .env.example. Fill in every value (docs/ops/free-hosting-owner.md section 4.3 explains each one and how to generate the secrets), then re-run this script."
fi

# ---------------------------------------------------------------------------
# 4. Sanity-check the required values aren't empty or still the example
#    placeholder. Catches the single most common failure mode -- forgetting
#    one value -- before spending a build + cold start finding out the hard
#    way. Deliberately grep-based, not a full parser: fast, dependency-free,
#    and this file's shape (KEY=value, one per line) never needed more.
# ---------------------------------------------------------------------------
env_value() { grep -E "^${1}=" "$ENV_FILE" | tail -1 | cut -d= -f2-; }

problems=()
for key in OWNER_SECRET_KEY OWNER_LICENSE_PEPPER OWNER_DATABASE_URL OWNER_BACKUP_TARGET_URL OWNER_SYNC_RELAY_PUBLIC_URL; do
  [ -n "$(env_value "$key")" ] || problems+=("$key is empty")
done
declare -A placeholders=(
  [SITE_DOMAIN]="owner.example.com"
  [OWNER_ALLOWED_HOSTS]="owner.example.com"
  [OWNER_SYNC_RELAY_PUBLIC_URL]="https://owner.example.com"
  [OWNER_DATABASE_URL]="postgresql+psycopg://<user>:<pass>@<neon-host>/<db>?sslmode=require"
)
for key in "${!placeholders[@]}"; do
  value="$(env_value "$key")"
  if [ "$value" = "${placeholders[$key]}" ]; then
    problems+=("$key is still the .env.example placeholder value")
  fi
done
if [ "$(env_value OWNER_EXTERNAL_API_ENABLED)" != "true" ]; then
  problems+=("OWNER_EXTERNAL_API_ENABLED is not \"true\" -- this is THE LANDMINE: the app will boot fine and look healthy with no licensing API registered at all. See docs/ops/free-hosting-owner.md section 1.")
fi
if [ "${#problems[@]}" -gt 0 ]; then
  bold "$ENV_FILE is not ready yet:"
  printf '  - %s\n' "${problems[@]}"
  fail "fix the values above in $ENV_FILE, then re-run this script."
fi
bold "$ENV_FILE looks filled in."

# ---------------------------------------------------------------------------
# 5. Build and start the stack.
# ---------------------------------------------------------------------------
bold "Building and starting the stack (this runs alembic + seeding on first boot -- watch for errors)..."
$COMPOSE up -d --build

bold "Waiting for the owner container to report healthy (up to 3 minutes)..."
container_id=""
elapsed=0
while [ "$elapsed" -lt 180 ]; do
  container_id="$($COMPOSE ps -q owner || true)"
  if [ -n "$container_id" ]; then
    status="$(docker inspect -f '{{.State.Health.Status}}' "$container_id" 2>/dev/null || echo "")"
    [ "$status" = "healthy" ] && break
  fi
  sleep 5
  elapsed=$((elapsed + 5))
done
if [ "$container_id" = "" ] || [ "$(docker inspect -f '{{.State.Health.Status}}' "$container_id" 2>/dev/null || echo "")" != "healthy" ]; then
  fail "owner container did not become healthy within 3 minutes. Check: $COMPOSE logs owner"
fi
bold "owner service is healthy."

# ---------------------------------------------------------------------------
# 5b. Prove the PUBLIC path works, not just the container.
#     The healthcheck above runs curl INSIDE the owner container, so it only
#     proves Flask is up. On 2026-10-10 it reported healthy while the public
#     site (Caddy) answered 503 for every request: Caddy's own upstream probe
#     was being rejected by the app's Host allowlist, so Caddy marked its only
#     upstream down. Nothing in this script noticed, because nothing in this
#     script ever talked to Caddy.
#
#     So go through Caddy: ports 443 on this VM, SNI/Host = SITE_DOMAIN, with
#     --resolve pinning that name to 127.0.0.1 so the check does not depend on
#     public DNS or on the Oracle security list (those are real, separate
#     failure modes, but "Caddy is serving this app" is what is proven here).
#     TLS is verified (no -k): a certificate Caddy has not obtained yet is a
#     real not-ready-yet state. Retries cover that, and Caddy's 30s upstream
#     health interval after a fresh start. A permanent failure stops the
#     script loudly instead of printing the "setup is done" banner.
#     PUBLIC_CHECK_ATTEMPTS / PUBLIC_CHECK_INTERVAL exist so the retry logic
#     can be exercised in seconds; the defaults wait up to 2 minutes.
# ---------------------------------------------------------------------------
public_check() {
  local domain attempts interval n code
  domain="$(env_value SITE_DOMAIN)"
  attempts="${PUBLIC_CHECK_ATTEMPTS:-24}"
  interval="${PUBLIC_CHECK_INTERVAL:-5}"
  [ -n "$domain" ] || fail "SITE_DOMAIN is empty in $ENV_FILE -- cannot check the public path through Caddy."
  bold "Checking the public path through Caddy (https://$domain/health/live via 127.0.0.1:443, up to $((attempts * interval))s)..."
  n=0
  code="000"
  while [ "$n" -lt "$attempts" ]; do
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 \
      --resolve "$domain:443:127.0.0.1" "https://$domain/health/live" 2>/dev/null || true)"
    [ "$code" = "200" ] && break
    n=$((n + 1))
    [ "$n" -lt "$attempts" ] && sleep "$interval"
  done
  if [ "$code" != "200" ]; then
    fail "the owner container is healthy but https://$domain/health/live through Caddy returned HTTP ${code:-000} after $attempts tries. The public site is NOT serving. 503 usually means Caddy marked the app upstream unhealthy (check OWNER_ALLOWED_HOSTS includes $domain); 000 usually means no certificate yet or ports 80/443 not reachable. Check: $COMPOSE logs caddy"
  fi
  bold "Public path OK: Caddy answered 200 for https://$domain/health/live."
}
public_check

# ---------------------------------------------------------------------------
# 6. Preflight -- the real readiness picture, not just "the process is up".
# ---------------------------------------------------------------------------
bold "Running 'flask commercial preflight'..."
$COMPOSE exec -T owner flask --app app:create_app commercial preflight || true

# ---------------------------------------------------------------------------
# 7. AUDIT 2026-09-24 gap G2 -- install + enable the scheduled-ops timer.
#    Expiry scan, reconcile, device-limit scan, sync-event pruning, and
#    report snapshots ran on NEITHER free hosting path before this. The unit
#    files are a template (__COMPOSE_DIR__ placeholder) because they must
#    name this install's actual absolute path -- $COMPOSE_DIR here, which
#    depends on $REPO_DIR, which an operator can override -- so they can
#    never be installed byte-for-byte as committed to the repo the way a
#    fixed-path unit (like deploy/staging's) can. Idempotent: re-running
#    bootstrap.sh re-substitutes and re-installs the same two files, then
#    `daemon-reload` + `enable --now` again, which systemd treats as a no-op
#    if nothing changed.
# ---------------------------------------------------------------------------
bold "Installing the scheduled-ops systemd timer (docs/ops/free-hosting-owner.md section 4.7)..."
SYSTEMD_SRC_DIR="$COMPOSE_DIR/systemd"
if [ -d "$SYSTEMD_SRC_DIR" ] && command -v systemctl >/dev/null 2>&1; then
  sed "s|__COMPOSE_DIR__|$COMPOSE_DIR|g" "$SYSTEMD_SRC_DIR/aura-owner-scheduled-ops.service" \
    | sudo tee /etc/systemd/system/aura-owner-scheduled-ops.service >/dev/null
  sudo cp "$SYSTEMD_SRC_DIR/aura-owner-scheduled-ops.timer" /etc/systemd/system/aura-owner-scheduled-ops.timer
  sudo systemctl daemon-reload
  sudo systemctl enable --now aura-owner-scheduled-ops.timer
  bold "Scheduled-ops timer installed and enabled. Next run: $(systemctl list-timers aura-owner-scheduled-ops.timer --no-legend 2>/dev/null | awk '{print $1, $2, $3}')"
else
  bold "WARNING: could not install the scheduled-ops timer ($SYSTEMD_SRC_DIR missing, or 'systemctl' not found on this host -- expected on a non-systemd VM). Expiry scan, device-limit scan, report snapshots, and sync-event pruning will NOT run automatically until this is set up by hand; see docs/ops/free-hosting-owner.md section 4.7."
fi

# 7b. Nightly encrypted off-VM backup (2026-10-10). Installed only when
#     .env.r2 exists: without the R2 credentials and passphrase the job could
#     only fail every night, and a timer that always fails is noise that
#     teaches people to ignore it. Same __COMPOSE_DIR__ template as step 7.
if [ -r "$COMPOSE_DIR/.env.r2" ] && [ -d "$SYSTEMD_SRC_DIR" ] && command -v systemctl >/dev/null 2>&1; then
  sed "s|__COMPOSE_DIR__|$COMPOSE_DIR|g" "$SYSTEMD_SRC_DIR/aura-owner-backup.service" \
    | sudo tee /etc/systemd/system/aura-owner-backup.service >/dev/null
  sudo cp "$SYSTEMD_SRC_DIR/aura-owner-backup.timer" /etc/systemd/system/aura-owner-backup.timer
  sudo systemctl daemon-reload
  sudo systemctl enable --now aura-owner-backup.timer
  bold "Nightly backup timer installed. Next run: $(systemctl list-timers aura-owner-backup.timer --no-legend 2>/dev/null | awk '{print $1, $2, $3}')"
else
  bold "WARNING: NO nightly backup is scheduled -- $COMPOSE_DIR/.env.r2 is missing (R2_ENDPOINT, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, BACKUP_PASSPHRASE). See run_backup.sh."
fi

cat <<'BANNER'

=====================================================================
Mechanical setup is done: Docker, the repo, the stack, and migrations
are all in place and the owner container is healthy. What's left is
DELIBERATELY NOT automated by this script -- see
docs/ops/free-hosting-owner.md sections 4.4-4.6:

  1. SIGNING KEY -- the single most important step, and the one that
     cannot be undone if you get it wrong. Do you already have a
     signing-key PEM from a previous Owner deployment (the one every
     already-built installer trusts)?

       YES -> restore it. Do NOT run
              'flask licensing generate-signing-key' -- that mints a
              brand-new, different key that no existing till will
              ever trust. Follow section 4.4a.

       NO  -> this is a genuinely first-ever deploy with zero
              existing licensed installs. Follow section 4.4b
              ('flask licensing generate-signing-key' +
              'activate-signing-key' is correct here, and only here).

  2. Seed a sellable plan and create the first admin (section 4.5):
       docker compose -f owner/deploy/oracle-free/docker-compose.yml exec owner flask seed-plans
       docker compose -f owner/deploy/oracle-free/docker-compose.yml exec owner flask create-superadmin --non-interactive --email you@example.com --display-name "Your Name"

  3. Back up the signing key off this VM (section 4.6) -- before your
     first real customer activates against it, not after.

Then verify (section 7): curl https://<your-domain>/api/licensing/v1/service-info
BANNER
