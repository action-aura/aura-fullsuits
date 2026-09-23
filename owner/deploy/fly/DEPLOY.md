# Deploying Aura Owner Control Center to Fly.io

Owner Control Center is the licensing authority for the whole suite. While it
is down, **no Aura Retail or Aura Dental install can activate, check in, or
sync.** Treat its availability as a product requirement, not infrastructure
housekeeping.

## CORRECTED 2026-09-22 — Fly is not free. It is the cheap option, not the free one.

This file previously claimed "Fly does not bill it for a machine this size"
under a free allowance. That was checked again while building
`docs/ops/free-hosting-owner.md` and is **wrong for any account created
today**: Fly retired its Hobby/free allowance; a new organization gets only a
one-time trial (2 VM-hours or 7 days, whichever ends first), and after that
this deployment bills continuously. For a `shared-cpu-1x` / 256MB machine
plus a 1GB volume for the signing key, that is **about $2.19/mo (compute) +
$0.15/mo (volume) ≈ $2.34/mo** at Fly's published rates (unchanged by the
October 1, 2026 pricing update). It also needs a card on file from account
creation, not just "for verification."

**The genuinely free, primary recommendation is now Oracle Cloud Always
Free + Neon — see `docs/ops/free-hosting-owner.md`.** This file (and this
whole `owner/deploy/fly/` directory) is kept as the documented **cheap,
low-sysadmin alternative** named in that runbook: about $2.34/mo gets you a
managed platform (no VM patching, automatic TLS, no Caddy to configure)
instead of owning a raw VM. Use it if $2-3/month is an acceptable trade for
less operational work; use the free-hosting runbook if it genuinely must be
$0.

The **`fly postgres create` step below is also stale** — Fly deprecated that
command ("Legacy Postgres") in favor of a paid `fly mpg create` product. This
file now uses free Neon Postgres instead (see the Postgres section below),
which also keeps this path's real cost at ~$2.34/mo rather than adding a
second paid Fly product.

## Why Fly over the other paid-tier PaaS options

| | |
|---|---|
| **Fly.io** ✅ | Real persistent volumes (the signing key requirement below is non-negotiable — see `docs/ops/free-hosting-owner.md`), no forced sleep, Docker-native. Not free, but cheap and predictable (~$2.34/mo). |
| Render | Free web services sleep (tolerable) but the **free tier cannot mount a persistent disk at all** — persistent disks require the paid Starter plan ($7/mo+). Disqualifying on the free tier for the service that must never lose its signing key. |
| Koyeb | Free tier has no persistent volumes either, plus forced scale-to-zero after 1h idle that cannot be disabled on the free instance. Same disqualification as Render. |
| Railway | Free tier was removed in 2023. Now a one-time $5/30-day trial, then $1/mo minimum plus usage — not meaningfully cheaper than Fly, without Fly's persistent-volume story. |
| Oracle Cloud Free Tier | Genuinely free forever (not a trial) and generous, but a raw VM — you own the OS, patching, TLS and backups. This is the **primary recommendation** in `docs/ops/free-hosting-owner.md` for exactly the owner who does not want to pay anything. |

## Before you start

You need: a Fly account with a card on file (billing starts immediately —
there is no ongoing free allowance for a new account), `flyctl` installed,
and about ten minutes.

## First run

```bash
# from the REPOSITORY ROOT, not owner/ -- the Dockerfile copies requirements/
# from the repo root, so that must be the build context
fly auth login
fly apps create aura-owner

# Postgres: use Neon (free, never expires, auto-resumes on the next query
# after its idle suspend -- see docs/ops/free-hosting-owner.md for why this
# beats both Fly's own Postgres offerings here). `fly postgres create`
# (Legacy Postgres) is deprecated, and its replacement `fly mpg create`
# (Managed Postgres) is a second paid product on top of the compute machine
# below -- neither is worth it when Neon's free tier already satisfies
# Owner's config.py validator (postgresql:// / postgresql+psycopg://).
# Create a Neon project at https://console.neon.tech, copy its connection
# string, and set it as a Fly secret in the next block.

# The volume that holds the signing key. WITHOUT THIS THE APP REFUSES TO
# START, by design -- see "Landmine 2" below.
fly volumes create aura_owner_var --region fra --size 1 --app aura-owner

# Secrets. Generate real ones; do not reuse anything from a dev .env.
# The config validator rejects any pepper containing "insecure", and any
# secret shorter than its minimum length.
fly secrets set --app aura-owner \
  OWNER_SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" \
  OWNER_LICENSE_PEPPER="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" \
  OWNER_ALLOWED_HOSTS="aura-owner.fly.dev" \
  OWNER_BOOTSTRAP_PASSWORD="$(python -c 'import secrets;print(secrets.token_urlsafe(24))')" \
  OWNER_DATABASE_URL="postgresql+psycopg://<user>:<pass>@<neon-host>/<db>?sslmode=require"

# Deliberately still owner/Dockerfile (root user), not Dockerfile.staging,
# even though Dockerfile.staging is leaner and non-root. Dockerfile.staging's
# USER aura is set at BUILD time; this fly.toml's [processes] entry runs
# `mkdir -p /app/var/...` at CONTAINER START time against a volume that is
# attached fresh at that point, not baked into the image -- a freshly
# created Fly volume's ownership was not verified against the non-root user
# in this session (no Docker available to test), so switching images here
# risks a permission denial on first boot that would only surface on a real
# deploy. Revisit once that is verified; do not swap this silently.
fly deploy --config owner/deploy/fly/fly.toml --dockerfile owner/Dockerfile
```

Owner reads **`OWNER_DATABASE_URL`** and rejects any URI that is not
`postgresql://` or `postgresql+psycopg://`. Neon connection strings need
`?sslmode=require` appended (Neon requires TLS; the app itself does not add
this for you):

```bash
fly secrets set --app aura-owner \
  OWNER_DATABASE_URL="postgresql+psycopg://<user>:<pass>@<neon-host>/<db>?sslmode=require"
```

## The signing key -- CORRECTED 2026-09-23, this step was missing entirely

**This file previously jumped straight from `fly deploy` to `commercial
preflight` with no signing-key step in between at all.** Followed literally,
that leaves no ACTIVE signing key, and `preflight` (correctly) hard-fails on
it -- there was no instruction here for what to do about that. Fixed below.

**First, answer this: does an existing signing key already exist that some
till needs to keep trusting** -- i.e. does
`commercial_runtime/licensing_contracts/trust_anchor.json` in this repo list
a key_id you must keep serving? As of this writing it lists exactly one,
`owner-ed25519-20260903T160641Z-cdcaa65b`, baked into every installer
already built. If that's your key, restore it -- do NOT generate a new one:

```bash
# Copy the existing PEM onto the machine, named exactly <key_id>.pem, then:
fly ssh sftp shell --app aura-owner
# (sftp> put owner-ed25519-20260903T160641Z-cdcaa65b.pem /app/var/signing-keys/)

# flask licensing activate-signing-key ALONE is not enough here -- it needs
# a matching database row that a fresh deploy's database doesn't have yet.
# scripts/deploy/restore_signing_key.py (same script Path A uses) creates
# it, cross-checks the PEM against trust_anchor.json, and refuses on a
# mismatch instead of silently registering the wrong key under a trusted
# id. It isn't baked into this image (owner/Dockerfile only COPYs owner/),
# so copy it over the same way:
fly ssh sftp shell --app aura-owner
# (sftp> put ../../../scripts/deploy/restore_signing_key.py /app/restore_signing_key.py)
# (sftp> put ../../../commercial_runtime/licensing_contracts/trust_anchor.json /app/trust_anchor.reference.json)

fly ssh console --app aura-owner -C \
  "python /app/restore_signing_key.py owner-ed25519-20260903T160641Z-cdcaa65b --trust-anchor /app/trust_anchor.reference.json"
```

(`fly ssh sftp shell` is real flyctl functionality for moving files onto a
Machine over SSH, but the exact interactive-session syntax above was not
verified against a live `flyctl` install or Fly account in this session --
confirm with `fly ssh sftp shell --help` before relying on it verbatim. Path
A's Docker-based restore flow, which this was ported from, WAS verified
against a real Postgres database -- see docs/ops/free-hosting-owner.md
section 4.4a.)

Only if this is a genuinely first-ever deploy, with zero existing installers
or installs anywhere, generate a brand-new key instead -- and then update
`trust_anchor.json` before building another installer:

```bash
fly ssh console --app aura-owner -C "flask --app app:create_app licensing generate-signing-key"
# -> "Generated signing key owner-ed25519-XXXXXXXXTXXXXXXZ-xxxxxxxx (status=DRAFT)..."
fly ssh console --app aura-owner -C \
  "flask --app app:create_app licensing activate-signing-key owner-ed25519-XXXXXXXXTXXXXXXZ-xxxxxxxx"
```

## Then verify — do not assume

```bash
fly ssh console --app aura-owner -C "flask --app app:create_app commercial preflight"
```

`preflight` runs many checks (see `app/commercial_ops/preflight.py` for the
current, authoritative list rather than a count here that will drift) and
exists precisely to catch a bad bring-up before anyone trusts it. Run it. It
is also what catches a signing key that has drifted from `trust_anchor.json`,
plans never seeded (`seed-plans`), or the external API left disabled
(`OWNER_EXTERNAL_API_ENABLED`) -- all three now hard-FAIL it, added
2026-09-22 after a real end-to-end run found them silent.

Create the first admin:

```bash
fly ssh console --app aura-owner -C \
  "flask --app app:create_app create-superadmin --email you@example.com --display-name 'Your Name'"
```

## The two landmines

**1. `OWNER_EXTERNAL_API_ENABLED` defaults to `false`.**
Unset, the app boots perfectly, `/healthz` returns 200, the admin UI works
completely — and the licensing, external-API and sync blueprints are never
registered. No product can activate, check in or sync, and **nothing logs an
error.** It is set explicitly in `fly.toml` for this reason. If licensing ever
appears to have "vanished" after a config change, check this first.

**2. The app refuses to start if `OWNER_SIGNING_KEY_DIRECTORY` does not exist.**
That is correct behaviour, not a bug —
`config.py::validate_external_api_production()` fails closed rather than
letting a licensing authority come up with nowhere to put its signing key. It
also requires the pepper to be set and non-default, replay protection and
distributed rate limiting both on, `DEBUG` off, and secure cookies on. A
misconfigured deploy therefore stops loudly instead of quietly serving a
licensing API with no replay protection.

Because a freshly created Fly volume is empty, `fly.toml`'s `[processes]`
entry creates the subdirectories before gunicorn starts.

## Back up the signing key — before your first real customer

The private Ed25519 key in `/app/var/signing-keys` signs **every licence
assertion the suite ever issues.** Losing it cannot be fixed by re-signing: a
device only extends trust to a new key when an *already-trusted* key
cross-signs it, and that operation needs the key that is gone. The only
recovery is shipping a new installer with a new bundled trust anchor to every
device already in a customer's shop.

The project's own risk register records that key backup is **not automated**,
and `app/system/backup.py` covers Postgres only — the key directory is in no
automated process anywhere in this codebase. A checklist already exists for
the Android signing key; nothing equivalent exists for this one.

```bash
# pull it off-host, encrypt it, keep two copies somewhere that is not Fly
fly ssh console --app aura-owner -C "tar czf - /app/var/signing-keys" > owner-signing-keys.tgz
gpg --symmetric --cipher-algo AES256 owner-signing-keys.tgz
rm owner-signing-keys.tgz
```

**This is currently safe to defer only because there are no real activations
yet.** With test data only, a lost key costs nothing — generate a fresh one.
The moment one real customer activates, that stops being true permanently.

## Aura Dental / Aura Retail

**CORRECTED 2026-09-22:** this section previously claimed Aura Dental has its
own `clinic/cloud/` Docker Compose + Caddy stack. No such directory exists
anywhere in this repository -- checked while writing
`docs/ops/free-hosting-owner.md`. `products/clinic` has no application UI at
all (see the root `CLAUDE.md`); there is nothing there to lift and shift.

The real Docker Compose + Caddy + automatic-TLS pattern in this repo is
`docker-compose.staging.yml` (repository root) plus `deploy/staging/` -- built
for Owner itself, not Clinic. `docs/ops/free-hosting-owner.md`'s Oracle Cloud
Always Free path (`owner/deploy/oracle-free/`) is modeled directly on that
proven shape rather than this Fly config -- same non-root hardened image
(`owner/Dockerfile.staging`), same Caddy security headers -- swapping
self-hosted Postgres for free Neon and `OWNER_ENV=staging` for `production`.
Aura Retail is desktop and Android and needs no server of its own beyond
this one licensing authority.
