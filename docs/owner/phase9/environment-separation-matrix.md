# Phase 9 — Environment Separation Matrix

| Dimension | Development | Automated test | Local integration | Staging | Future production (template) |
|---|---|---|---|---|---|
| Purpose | day-to-day coding | `pytest` runs | manual local end-to-end (Owner + real Windows/Android) | small supervised pilot | not implemented this phase |
| Data classification | synthetic | synthetic, ephemeral (per-test DB/tmp) | synthetic | synthetic until written pilot acceptance, then limited real operational (non-customer-domain) | real |
| Allowed users | developer | CI/test runner | developer | named pilot owner + approved pilot staff | not implemented |
| Database | `aura_owner_dev` (local Postgres) | ephemeral test DB per run | `aura_owner_dev` (same as dev, reused for real-device runs) | dedicated `aura_owner_staging` DB, own container/volume | dedicated, not implemented |
| Domain/hostname | `127.0.0.1:5551` | n/a (in-process) | `127.0.0.1:5551` + `adb reverse` | `staging.local` (this session) / real subdomain (real deployment) | not implemented |
| Secrets | `.env` (gitignored, dev-grade values) | pytest fixtures, synthetic | same as dev | separate `.env.staging` (outside repo, see `secrets-and-key-management.md`) | not implemented |
| Signing authority | dev Ed25519 keypair | ephemeral per-test keys | dev keypair | **separate staging Ed25519 keypair**, never reused from dev/prod | not implemented, must be separate again |
| License pepper | dev placeholder pepper (flagged by the Phase 8V-P9 preflight check as a WARNING) | ephemeral/synthetic | dev placeholder | **separate real staging pepper**, not the dev placeholder | not implemented, must be separate again |
| TLS trust | none (plain HTTP) | n/a | none / `adb reverse` tunnel | local CA (this session) / real CA (real deployment) | real CA |
| Deployment method | `flask run` / manual | `pytest` invocation | manual process launch | Docker Compose, `docker compose up -d` | not implemented |
| Backup policy | none (disposable) | none (disposable) | none (disposable) | scheduled, encrypted, retained (Milestone 7) | not implemented |
| Log retention | console only | pytest capture only | console + captured wire log (scratch, this session) | rotated, retained per `logging-and-redaction-policy.md` | not implemented |
| Access restrictions | local machine only | CI runner only | local machine only | firewalled, reverse-proxy only, SSH key-only | not implemented |
| Artifact channel | none | none | locally built APK/exe | private distribution (Milestone 14, NOT VERIFIED this session) | not implemented |
| Rollback process | `git checkout` | n/a | n/a | documented image/tag rollback (Milestone 3/7) | not implemented |

## Hard rule enforced by this matrix

No staging secret, signing key, pepper, database, or backup location is shared with development,
test, or any future production environment. This is a design constraint on every artifact produced by
this phase, not just a table entry.
