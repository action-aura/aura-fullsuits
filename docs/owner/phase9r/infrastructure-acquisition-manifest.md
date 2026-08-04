# Phase 9R — Infrastructure Acquisition Manifest

Exact minimum assets required to resume M18. Provider-neutral requirements
first; provider examples remain in `production-cost-model.md` (M1) only —
nothing below is selected or paid for in code or config.

## Remote host

| Requirement | Minimum |
|---|---|
| OS | A currently-supported Ubuntu LTS release (Ubuntu 26.04 LTS as of `deployment-architecture.md`'s check date — re-verify current LTS at acquisition time, don't assume this stays current) |
| CPU | 2 vCPU |
| RAM | 4 GB |
| Storage | 40 GB SSD minimum (OS + Docker images + Postgres data + local backup staging before off-host upload) |
| Network | Public IPv4 required; IPv6 recommended, not required |
| Access | SSH, key-based only, no password auth |
| Firewall | Host-level or provider-level capability to allow only 22 (restricted source), 80, 443 inbound |
| Backup/snapshot | Provider-level disk snapshot capability, independent of the application-level backup this project already builds (defense in depth, not a replacement for M12's off-host backup) |
| Region | Nearest to the actual pilot customer base — not yet decided, owner's call |

## Domain and DNS

| Requirement | Detail |
|---|---|
| Domain | A domain the owner actually owns/controls — none currently owned |
| DNS access | Ability to create A/AAAA/CNAME/TXT records for that domain |
| Staging hostname | e.g. `owner-staging.<domain>` |
| Production hostname | e.g. `owner.<domain>` |
| Download hostname (optional) | e.g. `downloads.<domain>`, only if release distribution is later split onto its own subdomain rather than sharing the main app's routes |
| Records needed | One A (or AAAA) record per hostname pointing at the remote host's IP; Caddy handles TLS automatically via ACME (Let's Encrypt) once DNS resolves correctly — no separate TXT record needed unless DNS-01 challenge is used instead of HTTP-01 |
| TTL guidance | Low TTL (300s or less) during initial setup/testing so DNS changes propagate quickly; raise to a standard value (3600s+) once stable |

## PostgreSQL

| Requirement | Detail |
|---|---|
| Topology | Docker Compose service (`db`, per the already-selected, already-real `docker-compose.staging.yml` — see `phase9-reconciliation.md` for why this was adopted over a fresh decision) |
| Storage | Sized to the real backup evidence in `disaster-recovery-report.md` (504KB at current dev-scale data) plus real growth headroom — 10GB is generous for a controlled pilot's first months |
| Backup | Handled by the existing `app/system/backup.py` + `deploy/staging/backup.py` wrapper — needs only a real off-host upload destination (object storage, below), not new backup code |
| Connection security | Least-privilege application role (not the Compose default `aura_owner` superuser-adjacent role — see `production-postgresql.md`'s real finding on this), created explicitly at first deploy, never reused from local dev |

## Object storage

| Requirement | Detail |
|---|---|
| Interface | S3-compatible API (ADR-7, `architecture-decision-record.md`) — any provider offering this works without a code change |
| Buckets/namespaces | At minimum two logical namespaces: release artifacts (M11) and backups (M12) — can be the same bucket with a prefix, or separate buckets |
| Access credentials | A scoped credential (read/write to only its own namespace(s)), never the app's only credential also having account-wide access |
| Region | Same region as the remote host where possible, to minimize latency and (for some providers) egress cost |
| Versioning/lifecycle | Versioning or immutability enabled on the backup namespace specifically — protects against a compromised deploy credential silently deleting backup history |
| Signed URL / adapter support | The provider's SDK must support presigned/signed URL generation (standard for any real S3-compatible provider) — `app/releases/storage.py`'s local/test adapter will need a real-storage counterpart implementing the same interface (`read_artifact_bytes`/`write_artifact_bytes`-equivalent) when this is acquired; not built yet, deliberately (M11's own scoping note) |

## Monitoring

| Requirement | Detail |
|---|---|
| Uptime destination | Any external uptime-check service hitting `/health/live` and `/health/ready` |
| Metrics destination | A Prometheus-compatible endpoint/remote-write target, or equivalent — `alert-catalog.md` already maps every alert to a real signal this app can emit |
| Logs destination | A log-aggregation destination that can ingest the existing structured JSON log format (`app/observability/logging_config.py`) — no code change needed, just a shipper (e.g. the Docker `json-file` driver plus a forwarding agent) |
| Alert destination | At minimum one channel (email or a webhook/chat integration) capable of receiving a P0/P1 page |
| Email/webhook | Only needed if the chosen alert destination requires it — no feature in the current codebase sends email itself |

## CI/CD

| Requirement | Detail |
|---|---|
| GitHub repository access | Already exists — `origin` → `github.com/b-3tabi/aura-fullsuits` (confirmed this session; not previously true during Phase 9's own work) |
| Actions enablement | Confirm GitHub Actions is enabled for this repository (account/org setting, not a code change) |
| Environment secrets | Real `OWNER_SECRET_KEY`/`OWNER_LICENSE_PEPPER`/`OWNER_DATABASE_URL`/backup+storage credentials, entered into GitHub's own encrypted environment-secret store — never committed, never in this manifest |
| Staging environment approval | GitHub Environments' own protection rule (optional gate before staging deploy) |
| Production environment approval | Required gate before any production deploy, once a production environment is defined (none exists yet — Phase 9 and Phase 9R both scope only staging) |
| Deployment credential | A scoped SSH key or the provider's deployment API token, held only by the CI runner/environment secret store, never a human's interactive credential |

## What this manifest deliberately does not do

Select a specific provider for any row above — `production-cost-model.md`
names real 2026 examples (Hetzner, Backblaze B2) for cost-estimation
purposes only; the actual choice is the owner's, made against real current
pricing/terms at acquisition time, not fixed in this document or in code.
