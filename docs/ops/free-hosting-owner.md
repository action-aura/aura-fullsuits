# Getting Owner Control Center back online, on free hosting

Written 2026-09-22. The previous hosting is gone and is not coming back — the
account is unreachable, the servers were destroyed, and the owner's
instruction was to remove every trace of that provider from this repo. There
is currently NO hosted environment at all. Until Owner is reachable
somewhere, **every Aura Retail and Aura
Clinic install is read-only and cannot ring a sale** — an install with no
licensing configured resolves to `NOT_CONFIGURED`, which is not in
`ACTIVE_FAMILY`, so `require_license_capability` denies anything outside a
read-only allowlist (see the root `CLAUDE.md`'s licensing section, verified
against `licensing_contracts/state_machine.py`). This is the runbook to fix
that, on hosting that costs nothing.

## TL;DR

- **The signing key is the whole decision.** It lives only as a PEM file on
  local disk — never in Postgres, never in an env var, by deliberate design.
  A host with an ephemeral filesystem mints a new, untrusted identity on
  every restart and **permanently invalidates every licence already issued**,
  recoverable only by shipping a new installer to every till. See "The crux"
  below.
- Every free-tier PaaS web host we checked (Render, Koyeb) explicitly does
  **not** offer persistent disk on its free plan. Fly.io does offer real
  persistent volumes but **no longer has a free tier at all** for a new
  account as of 2026 (only a 7-day/2-VM-hour trial).
- **Recommended, genuinely $0/month: Oracle Cloud Always Free VM + Neon
  Postgres.** A real VM with real persistent block storage, free forever
  (not a trial), plus a managed free Postgres that auto-wakes on the next
  query. More setup than a PaaS click-deploy; you own OS patching, Docker,
  and TLS (solved here with Caddy). Runbook: **Path A** below.
- **If you'd rather pay to skip the sysadmin: Fly.io, ~$2.34/month.** Real
  persistent volumes, no VM to patch, automatic TLS, a deploy config for it
  already exists in this repo (`owner/deploy/fly/`). Runbook: **Path B**
  below.
- Both paths use the same free Postgres (Neon) and the same verification
  steps. Pick a path, do not mix compute providers.
- **The mechanical Oracle setup is one script now:**
  `owner/deploy/oracle-free/bootstrap.sh` — `git clone` the deploy branch
  onto a fresh VM, run the script from that checkout, fill in `.env.oracle`
  when it tells you to, re-run. Section 4.3.
- **Restoring an existing signing key onto a new deploy needs a script, not
  just a copy.** `flask licensing activate-signing-key` alone fails on a
  fresh database (`No such signing key`) — `scripts/deploy/
  restore_signing_key.py` closes that gap and refuses if the PEM doesn't
  match what your installers already trust. Section 4.4a — read it before
  touching the signing key on any redeploy. **Updated 2026-09-24: it now
  REFUSES (not just warns) when `trust_anchor.json` is missing or doesn't
  list the key_id at all, not only on an outright mismatch** — see the
  audit note in section 4.4a.
- **A spare laptop is a legitimate alternative to both cloud paths**, given
  the task's own finding that Owner only needs to be reachable for about a
  minute per activation. See **Path C**, section 5b, for the honest
  trade-off and a concrete recommendation.
- **Scheduled Owner operations (expiry scan, device-limit scan, report
  snapshots, sync-event pruning) now run automatically on the Oracle Free
  path** — `bootstrap.sh` installs and enables a systemd timer for this as
  its last step. They previously ran on NEITHER free path. Section 4.7.
- **Four gaps found in a 2026-09-24 audit of this runbook are addressed
  below, one honestly still only partly** — G1 (signing-key restore failing
  open) fixed and tested, G2 (no scheduler) fixed and tested, G3 (container
  file permissions after `docker cp`) fixed in the code paths that are
  checkable without Docker, but **the actual container behavior is
  UNVERIFIED — Docker is not available in the environment this fix was
  written in**, G4 (stale CDN copies of `bootstrap.sh`) fixed by running the
  script from a `git clone` of the deploy branch, printing the commit it
  runs from AND the commit it deploys, and refusing when they differ. See
  sections 4.4a, 4.7, 4.4a's ownership note, and 4.3 respectively.
  **None of this has run on a real Oracle VM yet**: `bootstrap.sh` (including
  its new systemd-timer step) was exercised end to end only in a local
  sandbox with `docker`, `sudo` and `systemctl` stubbed out.

---

## 1. What Owner actually needs (verified against the code, not assumed)

- **Entrypoint:** `create_app()` in `owner/app/__init__.py:12`. Flask app
  selected by `OWNER_ENV` (`owner/app/config.py`: `DevelopmentConfig` /
  `TestingConfig` / `ProductionConfig` / `StagingConfig`).
- **Database:** `owner/app/config.py:106` —
  `SQLALCHEMY_DATABASE_URI = os.environ.get("OWNER_DATABASE_URL", "")`.
  `validate()` (config.py:293-297) refuses to start outside dev/test unless
  the URL starts with `postgresql://` or `postgresql+psycopg://`, and
  refuses one that looks like a dev/test database name (config.py:298-303).
  Owner is Flask + Postgres — no ORM-agnostic story, no SQLite fallback in
  production (`requirements/owner-server.txt` pins `psycopg[binary]==3.2.1`).
- **Migrations:** Alembic. `python -m alembic upgrade head` before the app
  serves traffic — confirmed as a required step in every working deploy
  path already in this repo (`owner/docker-compose.yml`,
  `docker-compose.staging.yml`, `owner/deploy/fly/fly.toml`'s
  `release_command`).
- **Seeding — confirmed REQUIRED, not optional:**
  `flask seed-rbac` (`owner/app/cli.py:24`), `flask seed-catalog`
  (`cli.py:55`), `flask seed-offline-policy` (`cli.py:143`). A bare
  `gunicorn` start with no seeding comes up with **zero roles and zero
  catalog** — the plain Dockerfile `CMD` does exactly this, which is why
  every real deploy path wraps it in a shell command that seeds first.
  **Also needed, and NOT run automatically anywhere in this repo:**
  `flask seed-plans` (`cli.py:62-76`) — without it there is no sellable
  plan and `issue_license_direct` "has nothing to issue against", i.e. you
  cannot cut a real licence key even once everything else works. It seeds a
  **placeholder $99 flat price** you're expected to override from the
  Owner UI before selling anything real. This is deliberately left as a
  manual one-time step in Path A/B below, matching this repo's own
  convention (infra seeding runs automatically on boot; business/pricing
  config is a deliberate admin action).
  Then `flask create-superadmin` (`cli.py:78`) for the first admin login.
- **Verification command that already exists and is real:**
  `flask commercial preflight` (`cli.py:259`) — "deterministic trust-anchor +
  permission-seed environment check... exits nonzero on any blocking
  mismatch." Confirmed real, not aspirational.
- **Required/secret env vars enforced by `BaseConfig.validate()`
  (config.py:258-390) outside dev/test:** `OWNER_SECRET_KEY`,
  `OWNER_LICENSE_PEPPER` (both rejected if short or containing "insecure"),
  a valid `OWNER_DATABASE_URL`, `SESSION_COOKIE_SECURE=true`,
  `OWNER_TRUSTED_PROXY_COUNT>=1`, non-empty `OWNER_ALLOWED_HOSTS`, a remote
  (not local-path) `OWNER_BACKUP_TARGET_URL`, and an explicit
  `OWNER_SCHEDULER_ROLE`. When `OWNER_EXTERNAL_API_ENABLED=true` (see
  below), `validate_external_api_production()` (config.py:368-390) adds:
  the signing-key directory must already exist, replay protection and
  distributed rate limiting must both be true, `DEBUG` must be false.
- **The landmine that produces a healthy-looking system with no licensing
  at all:** `OWNER_EXTERNAL_API_ENABLED` defaults to **false**. Unset, the
  app boots, `/healthz` returns 200, the whole admin UI works — and the
  licensing/external-API/sync blueprints are simply never registered
  (`owner/app/__init__.py`'s conditional import). No till can activate,
  check in, or sync, and **nothing logs an error.** Must be set explicitly.
- **Disk requirement: yes, and it is not optional.** See below.

## 2. The crux: the signing key is filesystem-only, by design, forever

Read directly from `owner/app/licensing_service/signing.py:1-8`:

> "Private key material NEVER touches a database column or the Owner UI.
> Each key's private half lives as a PEM file at
> `OWNER_SIGNING_KEY_DIRECTORY/<key_id>.pem`, outside the repository
> (gitignored), referenced only by key_id..."

This is a deliberate security decision (the file cites ADR-6.1/6.6), not an
oversight, and nothing in this runbook proposes routing around it — that
would be a different, worse security posture, not a hosting workaround.
Concretely:

- `generate_signing_key()` (signing.py:50-79) writes a fresh Ed25519 PEM to
  that directory with `os.open(..., O_CREAT | O_EXCL, 0o600)`. It is never
  called automatically at boot — a key only exists if an admin explicitly
  ran `flask licensing generate-signing-key` + `activate-signing-key`.
- `config.py:377`'s `validate_external_api_production()` **refuses to
  start** if `OWNER_SIGNING_KEY_DIRECTORY` does not already exist as a
  directory — correct fail-closed behavior, not a bug to work around.
- Key **rotation requires retired keys' PEM files to stay on disk
  indefinitely** (signing.py:265-269): the signed keyset manifest
  cross-signs a new key with every still-retained old key so already-fielded
  tills can extend trust one hop at a time. Delete a retired PEM and that
  bridge breaks for any till that hasn't caught up yet.

**The consequence of an ephemeral filesystem, stated plainly:** redeploying
(or restarting, on a host that doesn't persist local disk across restarts)
wipes the key directory. On next boot, one of two things happens: the
directory is simply gone and the app refuses to start (the good failure —
loud, obvious, nothing silently wrong); or something recreates an empty
directory and the app boots with **no active signing key at all** —
`/api/licensing/v1/service-info` will report `active_signing_key_id: null`
and every activation/check-in will fail closed with
`SIGNING_KEY_UNAVAILABLE` (`licensing_service/health.py`'s
`is_service_ready`). Either way, an admin has to notice and run
`generate-signing-key` again — which mints a **brand-new key with a new
key_id that no till has ever seen**, because a till only extends trust to a
new key when an *already-trusted* key cross-signs it, and that cross-signing
needs the private key that was just lost. There is no way to re-sign your
way out of it. The only recovery is shipping a new installer with a fresh
bundled `trust_anchor.json` to every device already in a customer's shop.

**A licensing authority that cold-starts for 30 seconds is a minor
inconvenience. One that silently loses its signing key is an outage you
discover from a confused shop owner days later.** This is why the platform
comparison below is weighted almost entirely on "does the free tier include
a real persistent disk", not on price or cold-start behavior.

## 3. Platform comparison (checked 2026-09-22, cite sources where it matters)

| Platform | Free web compute? | Persistent disk on free tier? | Free Postgres? | Verdict |
|---|---|---|---|---|
| **Fly.io** | No — free allowance removed; new accounts get only a 7-day/2-VM-hour trial | Yes, but **not free** (~$0.15/GB/mo) | Legacy Postgres deprecated; replacement "Managed Postgres" is a separate paid product | Real persistent volumes, no forced sleep. Cheapest **paid** option: ~$2.19/mo compute + ~$0.15/mo volume ≈ **$2.34/mo total**. Named as Path B. |
| **Render** | Yes, but sleeps after 15 min idle (~1 min cold start — tolerable) | **No.** Persistent disks require the paid Starter plan ($7/mo+) | Free Postgres exists but **expires 30 days** after creation (was 90 days before a 2024-05-20 policy change) | **Disqualified** — cannot hold the signing key on the free plan at all. |
| **Koyeb** | Yes (512MB/0.1vCPU) but **forced** scale-to-zero after 1h idle, cannot be disabled on free | **No.** Volumes require a paid plan | N/A | **Disqualified** — same reason as Render. |
| **Railway** | No — free tier removed in 2023 (abuse). Now a one-time $5/30-day trial, then $1/mo minimum plus usage | N/A (paid regardless) | N/A | Not meaningfully cheaper than Fly, without Fly's proven volume story. Not pursued further. |
| **Vercel** | N/A | N/A — serverless functions, no persistent process or filesystem across invocations | N/A | **Architecturally disqualified**, not just a free-tier limitation — Owner is a stateful long-running gunicorn/WSGI app with a local key directory; a serverless function model cannot host that regardless of price. |
| **Oracle Cloud Always Free** | Yes — real Ampere A1 (ARM) VM, up to 4 OCPU/24GB total today, **dropping to 2 OCPU/12GB from 15 June 2026** (still generous for this workload), genuinely free forever (not a trial) | **Yes — real block storage** (up to 200GB free), a real disk you own | N/A (use Neon alongside it) | **Recommended.** Real persistent disk, no forced sleep, $0 forever. Cost: you own OS patching, Docker, TLS, and Oracle's idle-reclamation policy (below). Path A. |
| **Neon (Postgres)** | — | — | Free forever, 0.5GB/project, 100 CU-hours/mo, **autosuspends after 5 min idle and auto-resumes on the very next query** (sub-second to a few seconds) | Recommended Postgres for both paths. |
| **Supabase (Postgres)** | — | — | Free, 500MB, but **pauses after 7 days of zero activity and requires a manual dashboard/API action to resume** — does not auto-wake on connection like Neon does | Viable, and a Supabase connector happens to be available in this working session — but a licensing authority that goes quiet between till activations for over a week would need a human to notice and click "Restore project" before the next activation could succeed. Neon's auto-resume is a better fit for something meant to be reachable unattended. Use Supabase only if you're willing to babysit it or add your own keepalive ping. |

**Oracle's idle-reclamation policy, stated honestly:** Oracle may reclaim an
Always Free compute instance if, over a 7-day window, its 95th-percentile
CPU, network, and memory utilization are all under 20%. A real gunicorn
process answering occasional activation/check-in traffic plus Caddy's health
probes should sit above that on its own; if a shop goes quiet for a long
stretch, add a one-line cron `curl` against `/healthz` every few minutes as
cheap insurance (a systemd timer pattern already exists in this repo at
`deploy/staging/systemd/` to copy from, or `owner/deploy/oracle-free/
systemd/` for this path's own scheduled-ops timer -- section 4.7 -- which is
a different job, but the same install mechanism).

## 4. Path A — Oracle Cloud Always Free + Neon (recommended, $0/month)

Uses `owner/deploy/oracle-free/docker-compose.yml` + `Caddyfile` +
`.env.example` (this repo), modeled directly on the repo's own proven
hardened stack at `docker-compose.staging.yml` + `deploy/staging/Caddyfile`
— same non-root image (`owner/Dockerfile.staging`), same Caddy security
headers, same health-checked reverse proxy. The two differences: `OWNER_ENV`
is `production` here (this is meant to serve real customers, and this
repo's own Non-Negotiable Principle 2 says staging must be labeled as
staging — that cuts against running real traffic through something every
log calls "staging" too), and Postgres is Neon instead of a self-hosted `db`
container (one less thing to patch and back up on a free VM; see the
compose file's own comment if you'd rather self-host Postgres there instead).

### 4.1 Create the Neon Postgres database

1. Sign up at <https://neon.tech> (no card required for the free tier).
2. Create a project (any region close to your shop is fine — for Jordan,
   Neon's AWS `eu-central-1` (Frankfurt) region is the closest).
3. From the project dashboard, copy the connection string. It looks like
   `postgresql://<user>:<password>@<host>/<db>?sslmode=require`.
4. Keep this tab open — you need it in step 4.3.

### 4.2 Create the Oracle Cloud Always Free VM

1. Sign up at <https://signup.oraclecloud.com> (a card is required for
   identity verification; Oracle does not charge it for Always Free
   resources).
2. Create a Compute instance: **Create a VM Instance** → choose the
   **Ampere (ARM) A1** shape → 1 OCPU / 6GB memory is plenty for this
   workload and leaves headroom under the free limit for anything else you
   might run later. Image: **Ubuntu 24.04** (or whatever current LTS Oracle
   offers). Keep the default boot volume (persistent — this is where the
   Docker named volumes in step 4.4 physically live).
3. Under **Networking**, note the VM's public IP.
4. **Open ports 80 and 443 — at BOTH levels, or this silently doesn't work.**
   Oracle's stock Ubuntu image blocks inbound traffic in two independent
   places, and missing either one leaves the port closed with no error
   message anywhere obvious:
   - **Cloud level:** OCI console → your VM's **Subnet** → **Security
     Lists** (or **Network Security Groups**) → **Add Ingress Rules** →
     allow TCP 80 and 443 from `0.0.0.0/0`.
   - **OS level:** Oracle's Ubuntu image ships with `iptables` rules active
     by default — a plain `ufw allow` is not enough on its own if `ufw`
     isn't what's actually enforcing the block. Confirm which is active
     (`sudo iptables -L INPUT -n` vs `sudo ufw status`) and open both 80 and
     443 on whichever is live, e.g.:
     `sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT`
     `sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT`
     `sudo netfilter-persistent save` (or `sudo apt install iptables-persistent`
     first if that command doesn't exist) so the rule survives a reboot.
     This exact trap is a well-known first-deploy blocker on Oracle Always
     Free specifically — do not skip verifying it.
5. Point a DNS A record for your chosen domain (e.g. `owner.yourdomain.com`)
   at the VM's public IP. Caddy cannot get a TLS certificate until this
   resolves publicly.

### 4.3 Install Docker and bring the stack up

**The fast path: one script.** `owner/deploy/oracle-free/bootstrap.sh`
collapses the mechanical part of this section (install Docker, clone/pull
the repo, sanity-check `.env.oracle` is actually filled in, build, start,
wait for healthy, run `commercial preflight`) into one paste. It refuses
loudly and tells you exactly what to fix rather than guessing or silently
continuing on a placeholder value — it does not touch secrets, the signing
key, seeding a plan, or creating the admin; those stay deliberate manual
steps below (4.4-4.5), on purpose. It is idempotent — safe to re-run after
fixing whatever it flagged.

```bash
ssh ubuntu@<vm-ip>
git clone --branch worktree-retail-hardware-viewports \
  https://github.com/action-aura/aura-fullsuits.git ~/aura-fullsuits
bash ~/aura-fullsuits/owner/deploy/oracle-free/bootstrap.sh
```

Clone straight into `~/aura-fullsuits` — the path the script deploys from
(its `REPO_DIR` default) — so the script you run and the code it deploys
are the same checkout. Every re-run below is that same second line.

> **AUDIT 2026-09-24 gap G4 — fetch by `git clone`, not a bare `curl` of the
> single file, and this is why.** A previous version of this section had you
> `curl -fsSL https://raw.githubusercontent.com/.../bootstrap.sh -o
> bootstrap.sh` directly. `raw.githubusercontent.com` is a CDN, and it was
> observed — while writing this runbook — serving a STALE copy of that exact
> file for several minutes after a push: the commit was already confirmed
> present on the branch via the GitHub API while the raw URL still handed
> back the previous version. The failure is silent and very convincing: you
> fix something, `curl` it, run it, and watch the old bug happen again.
> `git clone` does not go through that CDN cache the same way and does not
> have this problem.
>
> `bootstrap.sh` prints two commits: `Running bootstrap.sh from commit …`
> (where the script itself was read from) as its very first line, and
> `Deploying commit …` (what `~/aura-fullsuits` is at after its fetch/pull)
> in step 2. **If they differ it refuses and tells you to re-run** — that
> happens once, harmlessly, whenever the pull brought in newer commits than
> the copy of the script already running. (An earlier draft of this fix had
> you clone a separate `bootstrap-checkout` directory and run the script
> from there; re-running that older copy after a push deployed the newer
> code under a banner naming the older commit, exit 0 — reproduced in a
> sandbox, which is why the refusal exists and why the clone now goes
> straight into `~/aura-fullsuits`.)
>
> If you prefer a single-file fetch anyway (e.g. scripting this from a tool
> that cannot `git clone`), pin an exact commit SHA into the raw URL instead
> of a branch name — `https://raw.githubusercontent.com/action-aura/
> aura-fullsuits/<commit-sha>/owner/deploy/oracle-free/bootstrap.sh` — never
> a branch name, which is a moving pointer the CDN can legitimately cache
> stale against. Get the SHA with `git rev-parse HEAD` from a local checkout
> of the branch, or `gh api repos/action-aura/aura-fullsuits/commits/
> worktree-retail-hardware-viewports --jq .sha`. Know what that does and
> does not pin: it pins the SCRIPT only. The script still clones/pulls the
> branch head into `~/aura-fullsuits`, and a single file cannot know its own
> commit, so it prints a WARNING instead of the first banner and cannot
> refuse a mismatch — check the `Deploying commit …` line against the SHA
> you pinned yourself.
>
> **That branch name is a branch name, not `master`, and that is
> deliberate.**
> None of the free-hosting deploy content is on `master` — verified by
> HTTP, not assumed: `master`'s copies of `bootstrap.sh`,
> `restore_signing_key.py`, `fly.toml` and this runbook all return **404**,
> while the branch's return **200**. `master`'s last substantive commit
> predates this work by over a month and the branch is more than a thousand
> commits ahead of it, so merging is the whole project's divergent history
> rather than a tidy-up, and is not something to do in order to fix a
> download URL.
>
> `bootstrap.sh` carries a matching `REPO_BRANCH` default, because the
> script *also* fetches/checks out that branch in `~/aura-fullsuits` (and
> clones it there if the directory is missing), and a bare `git clone` takes
> the remote's default branch — `master` — which would put a checkout on the
> server with no `owner/deploy/oracle-free` directory in it at all. The
> `--branch` in the clone command above and `REPO_BRANCH` must always name
> the same branch; when this content does reach `master`, change both in one
> commit.
>
> To deploy a different branch without editing anything:
> `REPO_BRANCH=some-branch bash ~/aura-fullsuits/owner/deploy/oracle-free/bootstrap.sh`
> — it switches the checkout, then (because the script now running came
> from the old branch) refuses once and asks for the same command again.
> That is why the clone above is a full clone, not `--depth 1`: a shallow
> clone is single-branch, and switching branches in it fails at checkout.

First run will stop and tell you to fill in `~/aura-fullsuits/owner/deploy/oracle-free/.env.oracle`
(it creates it from `.env.example` if it doesn't exist yet) — open it, fill
in every value per the comments in the file itself, then re-run
`bash ~/aura-fullsuits/owner/deploy/oracle-free/bootstrap.sh`. It will stop
again if Docker was *just* installed (your shell's group membership needs a
fresh login to pick up `docker` group access) — log out, back in, re-run
once more. Three runs, at most, gets you to a healthy container (a fourth
only if a push landed on the branch in between — see the commit-mismatch
refusal above).

**What it's actually doing, if you'd rather run it by hand (or the script
fails and you need to see each step):**

```bash
# Docker + Compose plugin
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker

# Get the code onto the VM. If the GitHub repo is private, use a fine-
# grained personal access token in place of a password when prompted, or
# set up a deploy key first. --branch is NOT optional: a bare clone takes
# master, which has none of owner/deploy/oracle-free (see the note above).
git clone --branch worktree-retail-hardware-viewports https://github.com/action-aura/aura-fullsuits.git
cd aura-fullsuits
git rev-parse HEAD   # note the commit you are deploying

cp owner/deploy/oracle-free/.env.example owner/deploy/oracle-free/.env.oracle
nano owner/deploy/oracle-free/.env.oracle   # fill in every value -- see the comments in the file itself
```

**Keep `.env.oracle` in this exact directory, next to `docker-compose.yml`
(`owner/deploy/oracle-free/`) — NOT at the repo root.** Docker Compose
resolves every relative path written inside a Compose file (this one's
`env_file: - .env.oracle` and `./Caddyfile` mount) against the directory the
Compose file itself lives in, not against wherever you happen to run the
`docker compose` command from — so this is not a matter of taste. An earlier
version of `.env.example`'s own header comment said "repo root", which was
wrong; it has been corrected in the file itself. (`.gitignore` already
excludes `.env.*` everywhere in the repo, so this is never at risk of being
committed regardless of where it sits.)

Fill in `.env.oracle`:
- `SITE_DOMAIN` — the domain you pointed at the VM in step 4.2.5. **Not**
  `OWNER_DOMAIN`: everything prefixed `OWNER_` in this file is also injected
  into the `owner` container (both services share one `env_file`), and
  `OWNER_STRICT_CONFIG=true` below makes `config.py` refuse to boot on any
  `OWNER_*` variable it doesn't recognize. An earlier version of this file
  used `OWNER_DOMAIN` here, which tripped exactly that check —
  reproduced locally 2026-09-23 (`ConfigError: ... unrecognized OWNER_*
  environment variable(s) (possible typo): OWNER_DOMAIN`) before the rename.
- `OWNER_SECRET_KEY` / `OWNER_LICENSE_PEPPER` — generate each with
  `python3 -c "import secrets;print(secrets.token_urlsafe(48))"`
  (two *different* random values).
- `OWNER_DATABASE_URL` — the Neon connection string from step 4.1, in
  `postgresql+psycopg://` form (Owner reads `+psycopg`, not bare
  `postgresql://`, and refuses anything else — config.py:352-354). Keep
  `?sslmode=require`.
- `OWNER_ALLOWED_HOSTS` — same value as `SITE_DOMAIN` above, kept as a
  separate line because env files can't expand one variable inside another.
  This is the one `config.py` itself reads.
- `OWNER_BACKUP_TARGET_URL` — a **remote** URI (config.py rejects a local
  path outright). Cloudflare R2 and Backblaze B2 both have free tiers large
  enough for this; anything is fine as long as it's off this VM. This isn't
  wired to an automated backup job in this compose file — see step 4.6.
- Leave `OWNER_EXTERNAL_API_ENABLED=true` — this is the landmine from
  section 1. If you ever find licensing "vanished" after an env change,
  this is the first thing to check.
- `OWNER_BOOTSTRAP_PASSWORD` — set it for the first boot only (used once in
  step 4.5), then remove it from the file and never reuse that password.

Then bring it up:

```bash
docker compose -f owner/deploy/oracle-free/docker-compose.yml up -d --build
docker compose -f owner/deploy/oracle-free/docker-compose.yml logs -f owner   # watch migrations/seeding run, then Ctrl-C
```

The `owner` service's command runs `alembic upgrade head`, then
`seed-rbac`/`seed-catalog`/`seed-plans`/`seed-offline-policy`, then starts
gunicorn — all idempotent, safe to re-run on every redeploy. Note
`seed-plans` now runs automatically here (added to this compose file
2026-09-22); it does not need a separate manual step, unlike an earlier
version of this runbook which showed it as one in section 4.5 below.

### 4.4 The signing key — read this before running anything in this section

**Answer this first: does a signing key already exist that any real or
rehearsal till trusts — i.e. does
`commercial_runtime/licensing_contracts/trust_anchor.json` in this repo list
a key you need to keep serving?** As of this writing that file lists exactly
one key, `owner-ed25519-20260903T160641Z-cdcaa65b`, and it is baked into
every installer already built. If that is the key your shops need to keep
trusting, you are in path **4.4a**. Only a genuinely first-ever deploy, with
zero installers built and zero existing licensed installs anywhere, is path
**4.4b**. When in doubt, treat it as 4.4a — generating a new key you didn't
need to is the unrecoverable mistake; restoring a key you didn't strictly
need to was always safe.

#### 4.4a — Restore the EXISTING key (the path almost everyone needs)

`flask licensing activate-signing-key` alone is **not enough** here, and
this is a real gap this runbook did not previously cover: that command
requires a matching `owner_signing_keys` database row to already exist
(`owner/app/licensing_service/signing.py::activate_signing_key`), and a
brand-new Neon database has no such row for a key a previous, now-gone
deployment created — only the PEM file itself gets copied over, and copying
the PEM alone leaves the database with no record of it. Following only "copy
the PEM in, then activate it" fails at the activate step with
`SigningKeyError: No such signing key`, and the only *other* command
available, `generate-signing-key`, mints a brand-new, different key —
exactly the outcome this whole section exists to prevent.

`scripts/deploy/restore_signing_key.py` closes that gap: it re-derives the
key's public half from the PEM file itself (never trusts anything typed on
the command line), cross-checks it against `trust_anchor.json` and REFUSES
if they don't match, registers the row, then calls the real
`activate_signing_key()` to do the retire/activate step — proven locally
2026-09-23 against a real Postgres database in both directions: it correctly
restored a key from a PEM-only state (database row deleted, PEM left in
place), and it correctly REFUSED when given a PEM whose derived public key
didn't match the key_id's entry in `trust_anchor.json`, exiting nonzero
without writing anything.

> **AUDIT 2026-09-24 gap G1, fixed and mutation-tested — it now REFUSES on
> two more cases, not just a mismatch.** Before this fix, a MISSING
> `trust_anchor.json` or one that simply didn't LIST this key_id both
> printed a WARNING/NOTE and **proceeded anyway** — the only net against
> registering the wrong key under a trusted key_id was a human reading a
> warning line in scrollback. Both now REFUSE (nonzero exit, nothing
> written) exactly like the mismatch case always did. The one override is
> `--skip-trust-anchor-check`, already loud and already documented as
> correct for exactly one case — a deliberate first-ever key with nothing
> yet to conflict with — and wrong for every other one, including the
> ordinary restore below. See
> `owner/tests/test_restore_signing_key_trust_anchor.py` for the mutation
> proof (both the deny-half and the allow-half are tested independently).
> The 2026-09-26 verification pass found those tests only exercised the
> cross-check FUNCTION: rewriting `main()` to print the refusal and carry
> on — the same fail-open one layer up — left them all green. The file now
> also runs the real `main()` against the test database and asserts exit
> code 1, nothing registered and `activate_signing_key` never called on a
> refusal, and a registered ACTIVE key on a match (mutation-proved).

```bash
# 1. Get the existing private key PEM onto the VM, named exactly
#    <key_id>.pem -- e.g. from wherever you backed it up per section 4.6 of
#    a previous deployment, or from a local dev/rehearsal instance's own
#    OWNER_SIGNING_KEY_DIRECTORY. This step is on you -- deliberately not
#    scripted here, and nothing in this repo should ever copy this file for
#    you. If it's encrypted (section 4.6's backup format), decrypt it
#    locally first: `gpg --decrypt owner-signing-keys.tgz.gpg | tar xzf -`.
#    Also bring commercial_runtime/licensing_contracts/trust_anchor.json --
#    it is gitignored (permanently, not just "not yet committed"), so it
#    will NOT be on the VM from `git clone` alone; the restore script uses
#    it to cross-check the PEM and REFUSE a mismatch, so bring the real one
#    from wherever your repo checkout has it, not a fresh/empty one.
scp owner-ed25519-20260903T160641Z-cdcaa65b.pem trust_anchor.json ubuntu@<vm-ip>:~/
ssh ubuntu@<vm-ip>
OWNER_CID="$(docker compose -f aura-fullsuits/owner/deploy/oracle-free/docker-compose.yml ps -q owner)"
docker cp ~/owner-ed25519-20260903T160641Z-cdcaa65b.pem "$OWNER_CID":/app/owner/var/signing-keys/
docker cp ~/trust_anchor.json "$OWNER_CID":/app/trust_anchor.reference.json
rm ~/owner-ed25519-20260903T160641Z-cdcaa65b.pem   # don't leave the private key sitting outside the volume

# 1b. AUDIT 2026-09-24 gap G3 -- set ownership explicitly. Without -a,
#    `docker cp` creates files inside the container owned by ROOT (uid 0),
#    keeping the source file's permission bits (Docker's own `docker cp`
#    reference: "Files copied to a container are created with UID:GID of
#    the root user"). The app runs as the non-root "aura" user
#    (owner/Dockerfile.staging: `useradd --system --gid aura ... aura`,
#    `USER aura`), so a 0600 PEM lands present-but-unreadable to the one
#    process that needs it. `--user root` runs the chown/chmod as root
#    inside the container; `aura:aura` is resolved BY NAME inside the image,
#    so it stays right even if a rebuilt image assigns `aura` a different
#    system uid. The trust anchor gets the same treatment: the restore
#    script refuses if it cannot read it. This uses "$OWNER_CID" from step 1
#    (plain `docker exec`), so it works from whatever directory you are in.
docker exec --user root "$OWNER_CID" \
  chown aura:aura /app/owner/var/signing-keys/owner-ed25519-20260903T160641Z-cdcaa65b.pem /app/trust_anchor.reference.json
docker exec --user root "$OWNER_CID" \
  chmod 600 /app/owner/var/signing-keys/owner-ed25519-20260903T160641Z-cdcaa65b.pem
docker exec "$OWNER_CID" test -r /app/owner/var/signing-keys/owner-ed25519-20260903T160641Z-cdcaa65b.pem \
  && echo "PEM readable by the app user" || echo "PEM NOT readable by the app user -- stop and fix before step 2"

# 2. Register it and activate it. This is the script from above --
#    docker-compose.yml already bind-mounts scripts/deploy/ for exactly
#    this (trust_anchor.json is copied by hand above, not mounted, for the
#    gitignore reason noted there).
cd aura-fullsuits
docker compose -f owner/deploy/oracle-free/docker-compose.yml exec owner \
  python /app/scripts/deploy/restore_signing_key.py owner-ed25519-20260903T160641Z-cdcaa65b

# 3. Confirm.
docker compose -f owner/deploy/oracle-free/docker-compose.yml exec owner \
  flask --app app:create_app commercial preflight
```

> **Gap G3 is UNVERIFIED on the actual container — say so plainly.** Step
> 1b above was written and reasoned through against
> `owner/Dockerfile.staging` and `docker-compose.yml`, but **Docker is not
> available in the environment this fix was written in, so the chown step
> has never actually been run against a real container.** What IS verified,
> with a real Python test
> (`owner/tests/test_signing_key_unreadable_pem.py`, mutation-proved
> RED→GREEN): if step 1b is skipped, wrong, or simply doesn't fix the
> permissions on your platform, `owner/app/licensing_service/signing.py::
> load_private_key` no longer lets a raw `PermissionError` escape as an
> unhandled traceback — every caller (this script, `flask commercial
> preflight`, `flask licensing verify-signing-key-health`) now gets a clear
> `SigningKeyError` naming the file and the likely cause instead. So a wrong
> step 1b will be LOUD and readable, not a silent crash — but the step
> itself still needs a real run on real Oracle infrastructure to confirm it
> is sufficient (e.g. that `--user root` is accepted the way described, and
> that `aura`'s in-container gid actually owns the target directory).
>
> Two more things, added by the 2026-09-26 verification pass:
>
> - **An earlier draft of step 1b could not have worked as pasted.** It used
>   `docker compose -f owner/deploy/oracle-free/docker-compose.yml ...`, a
>   path relative to the repo root, while the shell was still in `~` (the
>   `cd aura-fullsuits` only happens in step 2), so both commands would have
>   failed with "no such file" and the chown would never have run. It now
>   uses `docker exec "$OWNER_CID"`, the same handle step 1 already set. It
>   also used to claim `docker cp` keeps the source file's owner; per
>   Docker's own reference it does not (root, unless `-a`) — same
>   conclusion, correct reason.
> - **The app does NOT refuse to boot on an unreadable key, and that is
>   deliberate.** gunicorn still starts and `/health/live` stays healthy. The
>   clear message surfaces instead wherever the key is actually read: `flask
>   commercial preflight` (which `bootstrap.sh` runs right after startup, and
>   step 3 above runs again), `restore_signing_key.py`, `flask licensing
>   verify-signing-key-health`, `/health/ready` (503 on the signing check),
>   and — as a `SigningKeyError` naming the file, not a bare
>   `PermissionError` — on the first activation that needs a signature. A
>   hard refusal inside `create_app()` was considered and rejected: every
>   tool you would use to FIX the file (this restore script, `flask`
>   commands via `docker compose exec`) goes through the same
>   `create_app()`, so a boot-time refusal would lock you out of the cure.
>   A pre-gunicorn check in the compose `command:` that ignores the CLI
>   would avoid that, but cannot be tested without Docker and is left as an
>   open decision, not built.

If step 2 refuses with a public-key mismatch, **stop** — you have the wrong
PEM file for this key_id. Do not pass `--skip-trust-anchor-check` to get
past it; get the correct file instead. If step 2 instead refuses because no
trust-anchor file was found (or it doesn't list this key_id), step 1's
`docker cp` of `trust_anchor.json` didn't happen or landed at the wrong path
— fix that and re-run; do not pass `--skip-trust-anchor-check` to get past
this either unless you are certain this is a genuinely first-ever deploy
with nothing yet to conflict with. If it refuses because the trust-anchor
file "exists but could not be read or parsed", step 1b's `chown` of
`/app/trust_anchor.reference.json` did not happen (it lands root-owned) or
you copied the wrong file — fix that and re-run (added 2026-09-26; before
that this case was a raw Python traceback rather than a `REFUSING:` line).

#### 4.4b — Generate a brand-new key (ONLY for a genuinely first-ever deploy)

Only do this if section 4.4's opening question was genuinely "no existing
installer or install anywhere trusts anything yet." This does not happen
automatically — it's a deliberate admin action, and it only needs to happen
once (rotation later is `flask licensing rotate-signing-key`, not this):

```bash
docker compose -f owner/deploy/oracle-free/docker-compose.yml exec owner \
  flask licensing generate-signing-key
# -> "Generated signing key owner-ed25519-XXXXXXXXTXXXXXXZ-xxxxxxxx (status=DRAFT)..."

docker compose -f owner/deploy/oracle-free/docker-compose.yml exec owner \
  flask licensing activate-signing-key owner-ed25519-XXXXXXXXTXXXXXXZ-xxxxxxxx
```

Immediately update `commercial_runtime/licensing_contracts/trust_anchor.json`
with this new key_id/public_key before building or shipping another
installer — every future installer needs to trust it.

### 4.5 Seed a sellable plan and create the first admin

`seed-plans` now runs automatically as part of the `owner` container's
startup command (section 4.3) — you do not need to run it by hand.
`create-superadmin` deliberately stays its own explicit, password-bearing,
one-time step:

```bash
docker compose -f owner/deploy/oracle-free/docker-compose.yml exec owner \
  flask create-superadmin --non-interactive --email you@example.com --display-name "Your Name"
```

Log in at `https://owner.yourdomain.com`, complete MFA enrollment (required
on first login for a Super Admin), then remove `OWNER_BOOTSTRAP_PASSWORD`
from `.env.oracle` and re-run
`docker compose -f owner/deploy/oracle-free/docker-compose.yml up -d`
(a plain `restart` reuses the container's existing environment and would
NOT pick up the edit -- `up -d` recreates it from the changed env file) so
the password is no longer sitting in a readable file. Real pricing is still
a manual step from here — `seed-plans` only seeds a placeholder $99 flat
price per product so there is something to issue a licence against;
override it from the Owner UI before selling anything for real.

### 4.6 Back up the signing key — before your first real customer

Nothing in this compose file backs this up automatically (matching this
repo's own admission in `owner/deploy/fly/DEPLOY.md` that signing-key backup
is not automated anywhere in this codebase yet). Do it manually now, and
repeat it after every rotation:

```bash
docker compose -f owner/deploy/oracle-free/docker-compose.yml exec owner \
  tar czf - /app/owner/var/signing-keys > owner-signing-keys.tgz
gpg --symmetric --cipher-algo AES256 owner-signing-keys.tgz
rm owner-signing-keys.tgz
# copy owner-signing-keys.tgz.gpg somewhere that is NOT this VM -- your
# laptop, a second cloud account, anywhere off-host.
```

This is safe to defer only while there is no real customer data. The moment
one shop activates against this key, losing it stops being recoverable.

### 4.7 Scheduled operations — expiry scan, device-limit scan, report snapshots, sync-event pruning

**AUDIT 2026-09-24 gap G2, fixed.** These four real, already-idempotent
`flask` CLI commands (`owner/app/cli.py`) previously ran on **neither** free
hosting path — the only existing systemd timer for this bundle
(`deploy/staging/systemd/`) is hardwired to the old staging deploy's paths
(`/opt/aura-fullsuits`, `docker-compose.staging.yml`) and does not cover
`sync prune-events` or `reports generate-scheduled` at all (both are newer
than that bundle). Without this, expired subscriptions never transitioned,
over-limit device slots were never flagged, no operational report snapshot
was ever generated, and `owner_sync_events` grew forever.

**`bootstrap.sh` now installs and enables this automatically, as its last
step** — nothing manual to do here on a fresh Oracle Free deploy. What it
installs:

- `owner/deploy/oracle-free/run_scheduled_ops.sh` — a bash wrapper (not a
  Python one, unlike the staging bundle — see the script's own header for
  why) that runs, inside the already-running `owner` container via `docker
  compose exec`: `commercial expiry-scan --apply`, `commercial reconcile
  --apply`, `commercial device-limit-scan --apply`, `sync prune-events
  --apply`, and `reports generate-scheduled --period-type
  daily|weekly|monthly --currency $REPORT_CURRENCY` (all three period
  types, every night: `generate_snapshot()` returns the existing snapshot
  for an already-generated period, so weekly/monthly are a no-op except the
  first night after their period completes — the first version of this
  script ran only `daily`, so weekly and monthly summaries would never have
  been generated). Logs one clear OK/FAILED line per job with its exit
  code, always runs every job regardless of an earlier one's failure, and
  exits nonzero overall if any job failed (for `OnFailure=`/alerting to key
  off of, the same convention as the staging bundle). Output goes to the
  journal: `journalctl -u aura-owner-scheduled-ops.service`.
- **Not covered — a known regression from the staging bundle:** staging
  also runs audit-chain verification (`app.audit.services.verify_chain`).
  There is no `flask` command for it, so it is not in this script; adding
  it needs a CLI command first. Open gap, not silently dropped.
- `owner/deploy/oracle-free/systemd/aura-owner-scheduled-ops.{service,timer}`
  — same daily 03:00 cadence as staging's own bundle. The `.service` file is
  a template (`__COMPOSE_DIR__` placeholder); `bootstrap.sh` substitutes
  your install's real, resolved path into it before installing to
  `/etc/systemd/system/` and running `daemon-reload` + `enable --now` on the
  `.timer` (never the `.service` directly — enabling the service instead
  would run it once immediately and never again).
- `REPORT_CURRENCY` in `.env.oracle` (default `JOD` in `.env.example`) feeds
  the `--currency` flag `reports generate-scheduled` requires. Deliberately
  **not** `OWNER_REPORT_CURRENCY` — same reasoning as `SITE_DOMAIN` above:
  `OWNER_STRICT_CONFIG=true` rejects any unrecognized `OWNER_*`-prefixed
  variable, and this one is read by the shell wrapper, never by
  `app/config.py`.

Concurrency: `sync prune-events` and `reports generate-scheduled` both
refuse outright unless `OWNER_SCHEDULER_ROLE=owner` in the container's
environment (`app/scheduling.py::require_scheduler_owner`) — already set in
`.env.example`. The other three scans are idempotent by construction (dedup
keys / unique constraints, per their own docstrings in `owner/app/cli.py`).
No separate Postgres-advisory-lock layer was added on top the way the
staging bundle has one: there is exactly one host on this path, not a
fleet, and systemd's own `Type=oneshot` semantics already refuse to start a
second instance of an already-active unit.

**Tested**: `owner/tests/test_oracle_free_scheduled_ops_units.py` parses the
real `run_scheduled_ops.sh` and the real `.service`/`.timer` files, derives
the list of currently-registered `flask` CLI commands from the live Flask
app (`app.cli.commands`, walked recursively — never a hand-typed list here),
and asserts every command the script invokes is one of them. Mutation-proved
by temporarily renaming a command in the script (`device-limit-scan` →
`device-limt-scan`): the test caught it immediately, then passed again once
reverted. The 2026-09-26 verification pass found that checking the command
PATH alone let `--apply` → `--aply` and `--period-type daily` → `daly` pass
every test, although either typo fails that job every night; the suite now
also hands each invocation's full argument list to the real Click command's
own parser, and checks every `--period-type` choice the CLI offers is
scheduled (both mutation-proved). **What is not proven here:** the
`systemctl` install itself. `bootstrap.sh` step 7 was run end to end in a
local sandbox with `sudo`/`systemctl`/`docker` stubbed — the rendered unit
names the real install path with no placeholder left, the timer is copied,
and `daemon-reload` + `enable --now aura-owner-scheduled-ops.timer` are
issued — but no real systemd has loaded these units yet. On the VM, confirm
with `systemctl list-timers aura-owner-scheduled-ops.timer` and, once, a
manual `sudo systemctl start aura-owner-scheduled-ops.service` followed by
`journalctl -u aura-owner-scheduled-ops.service`.

**Fly path**: nothing here is Fly-specific, but nothing wires it there
either. `owner/deploy/fly/` has no equivalent script or timer today. The
Fly path would need either (a) Fly's own [Machines scheduled
runs](https://fly.io/docs/machines/flyctl/fly-machine-run/) or a small
always-on "cron" machine running `run_scheduled_ops.sh`'s logic against
`flyctl ssh console -C '...'` in place of `docker compose exec`, or (b) a
GitHub Actions scheduled workflow that SSHes in and runs the same
commands. Not built here — genuinely not trivial (Fly's own volume-backed
signing key still needs the same reachability this script assumes) and out
of scope for this pass; flagged here rather than silently left for the next
session to rediscover.

### 4.8 Phone notifications for managers (Step 1, 2026-10-25) — optional, OFF by default

When a seller records a sale with **Sell a licence**, every manager who may
confirm it (Sales Manager, Finance, Super Admin — never the seller, never
whoever typed it in) gets a notification on each phone where they turned
notifications on. It shows only "New sale waiting for confirmation" and the
seller's first name — never the customer or the money. Tapping it opens
**Sales to confirm**; if another manager was first, the page says so.
Without push, managers still see the count badge in the top bar and the menu.

Turning it on, on the VM (once):

```bash
cd ~/aura-fullsuits/owner/deploy/oracle-free
# 1. Make the server key. The private key is APPENDED to .env.oracle and never
#    shown -- the command refuses to print it to a terminal. The public key
#    (not secret) is printed on the screen.
docker compose -f docker-compose.yml --env-file .env.oracle exec -T owner     flask --app app:create_app notifications generate-vapid-keys >> .env.oracle
# If .env.oracle already had an empty OWNER_PUSH_VAPID_PRIVATE_KEY= line, the
# appended one (the last) is the one that counts; deleting the empty one is tidier.
# 2. Restart so the app reads it, then install the every-minute sender timer:
docker compose -f docker-compose.yml --env-file .env.oracle up -d owner
./bootstrap.sh            # step 7c installs aura-owner-push.timer when the key is set
# 3. Check:
docker compose -f docker-compose.yml --env-file .env.oracle exec -T owner     flask --app app:create_app notifications status      # "push_enabled": true
systemctl list-timers aura-owner-push.timer
```

Then each manager, on their phone: open Owner (Android: Chrome; iPhone:
iOS 16.4 or newer, and ONLY from the app added to the Home Screen — Safari
itself cannot receive web notifications), go to **Sales to confirm**, press
**Turn on notifications on this device** and allow it.

Who sends: the web process itself (a background thread, every 10 s) and, as
a backstop, `run_push_sender.sh` every minute (`aura-owner-push.timer`). Both
use the same claims, so nothing is sent twice. A burst of sales becomes at
most one notification per phone per minute, saying how many are waiting.

**Keep the key secret.** It lives only in `.env.oracle` (never in git, a chat
or a ticket). If it leaks, make a new one the same way and restart: every
phone then simply turns notifications on again by itself on its next visit
to Sales to confirm. To turn push OFF, delete the line and restart — nothing
else changes.

## 5. Path B — Fly.io, ~$2.34/month (if you'd rather pay to skip sysadmin)

Full runbook already lives at `owner/deploy/fly/DEPLOY.md` (updated
2026-09-22 alongside this document — it previously claimed Fly was free,
which stopped being true for new accounts in 2026, and it previously used a
now-deprecated `fly postgres create` command; both are corrected there).
Short version:

1. `fly auth login`, `fly apps create aura-owner` (needs a card on file —
   billing starts immediately, there is no ongoing free allowance).
2. Create a free Neon Postgres project exactly as in Path A step 4.1.
3. `fly volumes create aura_owner_var --region fra --size 1 --app aura-owner`
   — the signing key's persistent disk. Without this the app refuses to
   start, by design (config.py:377).
4. `fly secrets set` the same values as `.env.oracle` above, plus the Neon
   connection string as `OWNER_DATABASE_URL`.
5. `fly deploy --config owner/deploy/fly/fly.toml --dockerfile owner/Dockerfile`.
6. Same signing-key generation/activation, plan seeding, and superadmin
   creation as Path A steps 4.4-4.5, via `fly ssh console` instead of
   `docker compose exec`.

Real cost at today's published Fly rates: `shared-cpu-1x`/256MB ≈ $2.19/mo
+ a 1GB volume ≈ $0.15/mo ≈ **$2.34/mo total**. In exchange: no VM to patch,
automatic TLS, no Caddy to configure, a platform that pages you (via its own
monitoring) rather than you finding out from Oracle's idle-reclamation
policy.

## 5b. Path C — a laptop/spare PC, considered honestly

Owner only needs to be reachable for roughly a minute per activation (proven
twice: a till activated once runs offline indefinitely — 14-day grace,
`WARN_ONLY` hard-expiry — and a real phone stayed licensed 9 days with the
authority unreachable). That fact genuinely weakens the case for an
always-on cloud VM, so this deserves a real answer, not a dismissal.

**The cost comparison is a wash, not a win.** Oracle Always Free + Neon is
already $0/month (Path A) — a laptop doesn't undercut it on price. The real
trade-offs are reachability and per-activation friction:

- **A laptop exposed to the public internet as a lightweight home server**
  (port-forwarded, dynamic-DNS'd, Caddy doing the same TLS dance as Path A)
  carries *all* of a VM's operational burden — DDNS instead of a static IP,
  router port-forwarding, firewall rules, the same signing-key backup
  discipline — while adding new risks a cloud VM doesn't have: residential
  ISPs sometimes sit behind CGNAT with no public IP obtainable at all; the
  machine competes with whatever else it's used for (OS updates that reboot
  it, sleep/hibernate, someone reinstalling Windows); and every activation
  now depends on remembering to boot it, confirm the network path still
  works, and bring the stack up — more manual steps at the exact moment a
  customer is waiting, not fewer. **Not recommended** — it is strictly more
  fragile than Path A for no cost saving.
- **A laptop carried to the shop and run on its own LAN for that one
  activation** is a genuinely different, better shape: no public exposure,
  no TLS/DDNS/port-forwarding at all, the till reaches Owner over the same
  Wi-Fi it's already on. This sidesteps nearly every downside above, and
  this codebase's own LAN site-relay work (`commercial_runtime/sync/
  site_relay/`) is direct proof that local-network flows here are solid.
  **This is a reasonable choice if — and only if — someone is physically
  present at every new shop during setup anyway**, which is plausible for "a
  handful of pilot shops." It stops being reasonable the moment activation
  needs to happen without anyone travelling there.

**Recommendation:** default to Path A (Oracle + Neon) — it is equally free,
reachable without anyone travelling anywhere, and the signing key sits on a
machine whose only job is being this service. Use the on-site-laptop variant
as a legitimate *stopgap* for the very first activation or two while an
Oracle account is still being verified (card/ID check can take a day), then
migrate to Path A once it's ready — both share the same Neon database and
the same signing-key restore procedure (section 4.4a), so nothing about an
early activation is lost by moving later. Do not build the
publicly-exposed-laptop variant; it is worse than Path A on every axis that
matters here.

## 6. Point a till at it

Retail/Clinic read the licensing base URL from **`AURA_OWNER_LICENSING_URL`**
(`products/retail/backend/config.py:49` —
`OWNER_LICENSING_BASE_URL = os.environ.get('AURA_OWNER_LICENSING_URL', '')`;
note the env var name and the Python attribute name differ). Empty by
default — a till with nothing set simply has its licensing routes report
`NOT_CONFIGURED` rather than erroring, so this is always an explicit,
deliberate step per install, never a hidden default:

```
AURA_OWNER_LICENSING_URL=https://owner.yourdomain.com
```

Set this wherever the till's own environment is configured (the desktop
launcher's config, or the packaged installer's defaults — see
`products/retail/desktop/launcher_*.py`). A till's bundled `trust_anchor.json`
must already trust whatever signing key is ACTIVE on your Owner instance —
if you rotate keys later, that propagates automatically via the signed
keyset manifest and cross-signing (section 2), **except** for a brand-new
key generated because the old one was lost, which cannot propagate and
needs a new installer.

## 7. Verify — do not assume

```bash
# 1. Liveness only -- proves the process is up, nothing about licensing.
curl -s https://owner.yourdomain.com/healthz
# -> {"status": "ok", "product_code": "AURA_OWNER"}

# 2. The real check -- proves the DB is reachable, replay protection is up,
#    AND there is an active signing key. This is the one that actually
#    matters.
curl -s https://owner.yourdomain.com/api/licensing/v1/service-info
# -> {"service": "aura-owner-licensing", "status": "OK",
#     "supported_contract_versions": ["v1"],
#     "active_signing_key_id": "owner-ed25519-20260922T..."}
#
# If active_signing_key_id is null, or "status" is "DEGRADED", section 4.4
# (or Fly's equivalent) was not completed. Restore the existing key (4.4a)
# unless this is genuinely a first-ever deploy with nothing to restore
# (4.4b) -- do not reach for `generate-signing-key` by default.
# If this 404s instead of returning JSON, OWNER_EXTERNAL_API_ENABLED is not
# actually true -- the landmine from section 1.

# 3. The public key manifest a till actually fetches:
curl -s https://owner.yourdomain.com/api/licensing/v1/signing-keys
# -> {"schema_version": 1, "keys": [{"key_id": "owner-ed25519-...",
#     "algorithm": "ed25519", "public_key": "...", "status": "ACTIVE", ...}],
#     "signed_by_key_id": "owner-ed25519-...", "signature": "..."}
# An empty "keys" list here means the same thing as #2's null key id.
```

And the full internal check, which is what actually gates a real deploy:

```bash
docker compose -f owner/deploy/oracle-free/docker-compose.yml exec owner flask commercial preflight
# (or, on Fly: fly ssh console --app aura-owner -C "flask --app app:create_app commercial preflight")
# -> JSON with one entry per check; exits nonzero on any blocking mismatch.
```

## 8. What breaks and how you'll know

| Symptom | Cause | Fix |
|---|---|---|
| `/healthz` returns 200, admin UI works, but no till can activate and nothing logs an error | `OWNER_EXTERNAL_API_ENABLED` is unset/false — the landmine (section 1) | Set it `true` explicitly and redeploy |
| `/api/licensing/v1/service-info` 404s | Same as above, or the external API blueprint genuinely isn't registered | Check `OWNER_EXTERNAL_API_ENABLED` first |
| `active_signing_key_id` is `null`, activation returns `SIGNING_KEY_UNAVAILABLE` | No signing key was ever generated/activated, OR the key directory was wiped by an ephemeral filesystem/redeploy | Section 4.4. If the directory was wiped and this is a NEW key, every already-fielded till needs a new installer — see section 2 |
| App container refuses to start at all, log says `OWNER_SIGNING_KEY_DIRECTORY does not exist` | Fail-closed by design (config.py:377) — the volume didn't mount, or the path in `.env` doesn't match the compose file's volume target | Confirm `docker volume ls` shows `oracle-free_owner_signing_keys` (or the Fly volume is attached); confirm the path matches exactly |
| Everything was fine, then suddenly the VM is unreachable | Oracle reclaimed an idle instance (7-day/20% rule, section 3) | Add a cron/systemd-timer keepalive ping; if already reclaimed, redeploy on a fresh instance — this is why the signing-key backup in 4.6 is not optional |
| A till gets `SERVICE_TEMPORARILY_UNAVAILABLE` on first activation after a quiet week | Neon's compute was autosuspended and is waking up (sub-second to a few seconds normally, but a redeploy's cold TLS handshake can stack with it) | Retry once after a few seconds; this is expected/acceptable per the task's own cold-start tolerance |
| A till gets the same error and it does NOT clear on retry | You chose Supabase instead of Neon and its 7-day pause requires a manual dashboard "Restore project" click | Restore it manually, or switch to Neon |
| `flask create-superadmin` refuses with "A Super Admin already exists" | Expected — it's a one-time bootstrap, by design | Use the Owner UI to create additional admins from here on |
| A licence can't be issued even though everything else checks out | `flask seed-plans` never ran | It runs automatically on container start as of 2026-09-22 (section 4.3) — if you still hit this, check the container's startup logs for a seeding failure rather than assuming the step was skipped |
| `flask licensing activate-signing-key <id>` fails with `SigningKeyError: No such signing key: <id>` | You copied an existing PEM into the volume but the database (fresh on this deploy) has no row for it — `activate-signing-key` alone can't create one | Use `scripts/deploy/restore_signing_key.py` instead — section 4.4a. This is the gap that made 4.4a exist. |
| The restore script refuses with a public-key mismatch against `trust_anchor.json` | The PEM file you copied in is not the same key that `trust_anchor.json` — and therefore every already-built installer — actually trusts under that key_id | Stop. Find the correct PEM; do not pass `--skip-trust-anchor-check` to force past this |
| Container refuses to start, log says `unrecognized OWNER_* environment variable(s) (possible typo)` | `OWNER_STRICT_CONFIG=true` caught a var name it doesn't recognize — either a real typo, or a new var that needs adding to `KNOWN_CONFIG_ENV_VARS` in `owner/app/config.py` | Fix the typo, or (if it's a deliberate new var) get it added to that allowlist — do not just set `OWNER_STRICT_CONFIG=false` to silence it |

## 9. Honest limitations, summarized

- **Neither path is a managed, zero-maintenance service.** Path A means you
  own a real Linux VM: OS security updates, Docker updates, and Oracle's
  idle-reclamation policy are now your problem. Path B removes the VM but
  costs ~$2.34/month and still needs you to run the CLI steps by hand.
- **Signing-key backup is manual in both paths**, and is not automated
  anywhere in this codebase today (this was already true and documented in
  `owner/deploy/fly/DEPLOY.md` before this runbook existed — it remains
  true here). Do it before your first real activation, and after every key
  rotation.
- **Neon's free tier caps at 0.5GB storage and 100 compute-hours/month per
  project.** For a licensing authority (not a full transactional retail
  database) this should be generous for a long time, but it is not
  unlimited — watch it as customer count grows.
- **Oracle's Always Free ARM allocation is shrinking** from 4 OCPU/24GB to
  2 OCPU/12GB total on 15 June 2026. Still comfortably enough for this
  single small Flask app, but worth knowing before assuming the current
  larger number is permanent.
- **If this needs to scale past "a handful of pilot shops"** — more admin
  concurrency, more activation throughput, a real SLA — the honest next
  step is Fly.io's paid tier (Path B) or a proper managed VPS, not squeezing
  further out of either free tier. Free hosting got the licensing authority
  back online at $0-2.34/month; it was never meant to be the permanent
  answer for a business that succeeds.
