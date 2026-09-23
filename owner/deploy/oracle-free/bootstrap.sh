#!/usr/bin/env bash
# Aura Owner -- Oracle Cloud Always Free bootstrap.
#
# Paste this whole block into a fresh Oracle Ubuntu VM (SSH'd in as the
# default user, e.g. `ssh ubuntu@<vm-ip>`), or download and run it:
#
#   curl -fsSL https://raw.githubusercontent.com/action-aura/aura-fullsuits/worktree-retail-hardware-viewports/owner/deploy/oracle-free/bootstrap.sh -o bootstrap.sh
#   bash bootstrap.sh
#
# NOTE THE BRANCH IN THAT URL -- it is deliberately not `master`. None of the
# free-hosting deploy content exists on master yet (verified by HTTP: master's
# copies of this script, restore_signing_key.py, fly.toml and the runbook all
# return 404, while the branch's return 200). master's last substantive commit
# predates all of it by more than a month, and this branch is over a thousand
# commits ahead of it, so "just merge to master" is not a tidy-up -- it is the
# whole project's divergent history, and not something to do in order to fix a
# download URL.
#
# When master does catch up, change REPO_BRANCH's default below and this URL
# together, in one commit. They must agree: the URL decides which copy of this
# script you download, REPO_BRANCH decides which copy of the repo it then
# clones onto the VM, and a mismatch means running one version's script
# against another version's compose file.
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
REPO_BRANCH="${REPO_BRANCH:-worktree-retail-hardware-viewports}"
REPO_DIR="${REPO_DIR:-$HOME/aura-fullsuits}"
COMPOSE_DIR="$REPO_DIR/owner/deploy/oracle-free"
ENV_FILE="$COMPOSE_DIR/.env.oracle"
COMPOSE="docker compose -f $COMPOSE_DIR/docker-compose.yml --env-file $ENV_FILE"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
fail() { printf '\033[31mFAIL: %s\033[0m\n' "$1" >&2; exit 1; }

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
for key in OWNER_SECRET_KEY OWNER_LICENSE_PEPPER OWNER_DATABASE_URL OWNER_BACKUP_TARGET_URL; do
  [ -n "$(env_value "$key")" ] || problems+=("$key is empty")
done
declare -A placeholders=(
  [SITE_DOMAIN]="owner.example.com"
  [OWNER_ALLOWED_HOSTS]="owner.example.com"
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
# 6. Preflight -- the real readiness picture, not just "the process is up".
# ---------------------------------------------------------------------------
bold "Running 'flask commercial preflight'..."
$COMPOSE exec -T owner flask --app app:create_app commercial preflight || true

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
