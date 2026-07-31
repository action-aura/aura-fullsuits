# Phase 9 — Backup Policy

## What is backed up (real, `deploy/staging/backup.py`)

- PostgreSQL: full `pg_dump -F c` (custom format — supports selective restore and is materially
  smaller than plain SQL for this schema).
- Signing-key **metadata** (file names + sizes only) — real code, deliberately not the private key
  bytes. The private key itself lives on a Docker named volume
  (`owner_signing_keys_staging`) and must be backed up via a separate, more tightly access-controlled
  volume-snapshot process (real deployment requirement, not built this session — no real host to
  snapshot).
- Config inventory: **names only**, never values (matches `secret-inventory-template.md`) — not yet
  automated into the backup script this session; the manual inventory doc is the current source of
  truth.
- Migration state: captured implicitly (the `alembic_version` table is inside the `pg_dump`).
- Release/deployment manifests: `staging-artifact-manifest.md` (Milestone 13) once real staging
  artifacts exist.
- Audit verification metadata: implicit in the `pg_dump` (the audit table itself); `verify_chain()` is
  re-run against a restore as evidence the backup is trustworthy, not stored as a separate artifact.

## What is explicitly NOT backed up through Aura Owner

Clinic/Retail customer-domain databases (patient records, appointments, sales, stock, receivts) — Owner
never holds this data in the first place (`network-and-trust-boundaries.md`), so there is nothing to
back up here even in principle.

## Schedule (real deployment; not run on a timer this session — see `scheduled-operations.md` for the
scheduler mechanism it would run under)

Daily, off-peak. A small supervised pilot's data-loss tolerance does not justify more frequent
backups at this scale; revisit if/when a real pilot's transaction volume materially increases.

## Encryption, access control, checksums, retention

- Checksummed: real, already implemented — every backup run writes a SHA-256 alongside the dump
  (`deploy/staging/backup.py`), verified before any restore (`restore-drill-report.md`).
- Encrypted at rest: NOT VERIFIED this session — no real host/volume to apply encryption-at-rest to;
  a real deployment should use the host disk's own LUKS/BitLocker-equivalent encryption or a
  KMS-backed volume, not application-level encryption of the dump file itself (simpler, fewer secrets
  to manage).
- Access-controlled: the `owner_backups_staging` Docker volume is only mounted into the `backup`
  service in `docker-compose.staging.yml`, never into `proxy` or exposed to the host.
- Retention: 14 daily backups kept, oldest pruned — a real deployment requirement, not yet automated
  into `backup.py` (single-run-per-invocation script this session; retention pruning is a small,
  well-scoped addition for whoever operates the real host, documented here rather than built against
  data that doesn't exist yet).
- Off-host copy: NOT VERIFIED (no real remote storage target available this session) — required for a
  real deployment (a second, geographically-separate location, per the RTO/RPO targets below).
- Failure alert / storage-capacity monitoring: see `monitoring-validation-report.md` (Milestone 8).

## RPO / RTO (staging, realistic for pilot scale — not internet-scale numbers)

- **RPO: 24 hours** — matches the daily backup schedule above; acceptable for a small supervised pilot
  where the primary risk is host failure, not high-frequency transactional loss (Owner itself holds no
  point-of-sale/clinical transaction data — see the data boundary above — so the actual blast radius of
  losing up to 24h of Owner-side changes is "re-enter a handful of commercial-ops actions", not lost
  business transactions).
- **RTO: under 5 minutes** — informed directly by the real restore-drill measurement (2 seconds for
  `pg_restore` at this data volume) plus real time for container restart/readiness (`/health/ready`
  polling, typically under 30s once the image is already built) plus operator time to select and verify
  a backup. Not a theoretical number — grounded in `restore-drill-report.md`'s actual measurement.

## Storage-capacity monitoring

See `monitoring-validation-report.md` — disk-usage alerting covers the backup volume as well as the
Postgres data volume.
