# Deploying Aura Owner Control Center to Fly.io

Owner Control Center is the licensing authority for the whole suite. While it
is down, **no Aura Retail or Aura Dental install can activate, check in, or
sync.** Treat its availability as a product requirement, not infrastructure
housekeeping.

## Why Fly and not the other free options

| | |
|---|---|
| **Fly.io** ✅ | Real Postgres, persistent volumes, no forced sleep, Docker-native. The existing `owner/Dockerfile` works essentially as-is. |
| Render | Free Postgres **expires after 90 days** and free web services sleep. Disqualifying for the service that gates every activation. |
| Railway | Good DX, but the free allowance is trial-shaped rather than genuinely free. |
| Oracle Cloud Free Tier | Genuinely free forever and generous, but a raw VM — you own the OS, patching, TLS and backups. Viable fallback if zero cost matters more than zero sysadmin. |

## Before you start

You need: a Fly account, `flyctl` installed, and about ten minutes. Signup
needs a card for identity verification even on the free allowance — Fly does
not bill it for a machine this size, but it must be on file.

## First run

```bash
# from the REPOSITORY ROOT, not owner/ -- the Dockerfile copies requirements/
# from the repo root, so that must be the build context
fly auth login
fly apps create aura-owner

# Postgres. Note the connection string it prints -- you need it below.
fly postgres create --name aura-owner-db --region fra
fly postgres attach aura-owner-db --app aura-owner

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
  OWNER_BOOTSTRAP_PASSWORD="$(python -c 'import secrets;print(secrets.token_urlsafe(24))')"

fly deploy --config owner/deploy/fly/fly.toml --dockerfile owner/Dockerfile
```

`fly postgres attach` sets `DATABASE_URL`. Owner reads **`OWNER_DATABASE_URL`**
and rejects any URI that is not `postgresql://` or `postgresql+psycopg://`, so
set it explicitly from what attach printed:

```bash
fly secrets set --app aura-owner \
  OWNER_DATABASE_URL="postgresql+psycopg://<user>:<pass>@<host>:5432/<db>"
```

## Then verify — do not assume

```bash
fly ssh console --app aura-owner -C "flask --app app:create_app commercial preflight"
```

`preflight` runs twelve checks and exists precisely to catch a bad bring-up
before anyone trusts it. Run it. It is also what catches a signing key that
has drifted from `trust_anchor.json`.

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

## Aura Dental

Aura Dental already has its own cloud stack (`clinic/cloud/`: Docker Compose +
Caddy with automatic TLS + a read-only backup sidecar), so it is close to a
lift-and-shift onto the same platform. Aura Retail is desktop and Android and
needs no server of its own beyond this one.
