# Phase 9R — M16/M17: Deployment Pipeline and Migration/Rollback Safety

## M16: extends Phase 9's real pipeline design, one fact has genuinely changed

Phase 9's `staging-deployment-pipeline.md` (manual, protected, never
automatic — Non-Negotiable Principle 9) and `ci-validation-contract.md`
(every command in `.github/workflows/ci.yml` manually verified real, but
`git remote -v` was empty so the workflow itself had never actually run)
remain accurate design documents — not rewritten here.

**One fact has genuinely changed since Phase 9's own session:** this
repository now has a real connected remote
(`origin` → `github.com/b-3tabi/aura-fullsuits`, set up earlier in this
project's history, shared across every worktree including this one).
Phase 9's own "`git remote -v` is empty" observation is no longer true.
This means `.github/workflows/ci.yml` **could** now actually execute as a
real GitHub Actions run if this branch were pushed — a genuine change in
what's verifiable, not just a documentation update.

**Deliberately not exercised this session:** pushing this branch and
watching CI actually run is a real, visible, consequential action (uses
CI compute, becomes visible on GitHub) that the repository owner should
decide when to trigger, not something to do unilaterally mid-phase. The
workflow file's commands remain the same ones already manually verified
(now with substantially higher real test counts than Phase 9's own
423/235 — this phase's own M0 baseline recorded 1,536/1,536). Actually
triggering and observing a real CI run is deferred to when the owner
pushes, or to M28's final regression if that's when the push happens.

## M17: migration and rollback safety — this phase's own migrations classified

Per Phase 9's own established classification scheme
(`docs/owner/phase9/migration-runbook.md`) and its "rollback honesty"
principle (restore-from-backup is the real rollback for anything not
*provably* safe to `alembic downgrade`, not an assumed-safe default):

| Migration | Classification | Downgrade safety |
|---|---|---|
| `13944658bddf` (M4 — `owner_installations` composite index) | Additive, backward-compatible | Genuinely safe — `drop_index` only, no data loss, no application behavior depends on the index existing (only performance does) |
| `96429a63cb29` (M10 — release-authority columns) | Additive, backward-compatible | Safe in the same sense as Phase 8's own additive column migrations Phase 9 already classified this way — `drop_column` on nullable/backfilled columns, no data loss beyond the columns' own (newly-added) content |
| `86e9229f85c1` (M11 — `ReleaseDownloadAuthorization` table) | Additive, backward-compatible | Genuinely safe — `drop_table` on a brand-new table with no other table depending on it existing |

All three are pure additive migrations (new index, new columns, new
table) — none destructive, none requiring the "restore from backup, not
`alembic downgrade`" fallback Phase 9's own honest rollback policy
reserves for non-reversible changes. Each was applied to the real local
`aura_owner_dev` database as part of its own milestone's work
(M4/M10/M11 above), verified drift-clean via `alembic check` immediately
after, and re-verified once more in M13's real isolated restore drill
(`disaster-recovery-report.md`) — the restored database's
`alembic_version` matched the backup-time head (`86e9229f85c1`) exactly.

## Migration locking — unchanged, still applies

Phase 9's own reasoning (`migration-runbook.md`'s "Migration locking"
section) still holds without modification: the staging Compose topology
runs migrations once at container startup in a single-replica setup, so
no additional lock mechanism was needed there, and nothing in this phase
changes that topology. Noted as still-correct, not re-derived.

## Disposition

**PASS for the repository-controlled portion.** Real migrations, real
classification, real drift-clean verification, real restore-drill
re-confirmation. What remains **NOT VERIFIED**: an actual executed CI
run (blocked on the owner's decision to push, now technically possible
for the first time since a remote exists) and a real Docker-containerized
staging deployment (blocked on infrastructure,
`infrastructure-availability-audit.md`).
