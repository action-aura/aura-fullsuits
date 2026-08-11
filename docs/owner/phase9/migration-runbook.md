# Phase 9 — Migration Runbook

## Procedure (real, exercised this session against `aura_owner_staging`)

1. **Pre-deploy backup** — `pg_dump` (see `backup-policy.md`) before any migration touches a database
   with real data in it. Skippable only for a genuinely fresh/empty database (as this session's first
   staging migration was).
2. **Current revision** — `psql -c "SELECT version_num FROM alembic_version;"` (or `/health/ready`'s
   `migration_at_head` check, which does exactly this comparison).
3. **Target revision** — `alembic heads` against the checked-out source tree.
4. **Dry run where possible** — `alembic upgrade head --sql` prints the DDL without executing it;
   reviewed for any `DROP`/destructive statement before a real apply against a database with real data.
5. **Apply** — `python -m alembic upgrade head`. This session: applied clean, 6 revisions in sequence,
   `62e4adb0a7b9` (initial schema) through `0f8d55b753ed` (Phase 8 milestone 5), no error.
6. **Post-migration checks** — `/health/ready`'s `migration_at_head: OK` (confirmed this session);
   `flask commercial preflight` for the broader commercial-readiness picture; a smoke check against one
   real table per migrated area.
7. **Rollback decision** — see below.
8. **Evidence record** — this document + the deployment record format in `staging-deployment-report.md`
   (source commit, target revision, operator, timestamp).

## Rollback honesty

Not every migration in this history is safely reversible in the general "downgrade with data intact"
sense — several Phase 8 migrations add non-nullable columns backfilled from existing data, and Alembic
`downgrade()` for those would need to either drop the column (data loss) or leave it nullable
permanently (schema drift from what a fresh install would produce). Per the Non-Negotiable Principle
("do not promise reversible migrations when the migration is not safely reversible"), the actual
rollback procedure for a bad staging migration is: **restore the pre-deploy backup**, not
`alembic downgrade`. `alembic downgrade -1` remains available for migrations confirmed safe
case-by-case (the two Phase 8 M1/M3 additive-only migrations are pure `CREATE TABLE`, genuinely
reversible) — never assumed safe by default.

## Migration locking

Alembic's own `alembic_version` table update is a single-row transaction — two concurrent
`alembic upgrade head` invocations would race, not corrupt data, but could both attempt the same DDL.
The staging Compose command runs migrations as part of container startup (`sh -c "alembic upgrade head
&& ... && gunicorn ..."`), and Compose does not start two `owner` containers against the same DB
concurrently in this topology (single replica) — no additional lock was needed or added this session.
A future multi-replica staging topology would need `alembic upgrade head` moved to a dedicated
one-shot init container/job, not run inside every replica's own startup command.
