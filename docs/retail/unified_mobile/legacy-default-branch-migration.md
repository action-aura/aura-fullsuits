# Aura Retail Unified Mobile — Legacy Default-Branch Migration (M5.4)

Companion to branch-domain-contract.md, scoped specifically to how the Milestone 18 full importer is expected to consume `EnsureDefaultBranchUseCase` — not a new design, just the concrete migration usage this milestone's use case exists to serve.

## The real problem

Legacy `branch_id` columns are nullable everywhere they appear (`sales`, `returns`, `inventory_balances` — confirmed in the real Python `schema.py`, carried forward unchanged into the unified schema per table-count-reconciliation.md's own migration-rule column). Any installation that never enabled multi-branch mode has real historical rows with `branch_id IS NULL`. A straight column-for-column import (`CatalogImporter`'s M4 pattern, `importBranch`/`importCategory`/`importProduct`) would carry the `NULL` straight through, unchanged — technically faithful, but it leaves rows the unified app's own branch-scoped UI (Milestone 6+) cannot meaningfully display without a fallback at read time, everywhere, forever.

## The migration-time rule (for Milestone 18 to apply, not yet wired into any importer today)

Before importing any table with a nullable `branch_id`, call `EnsureDefaultBranchUseCase.execute(companyId, importStartedAtEpochMillis)` exactly once per company being imported, and substitute its returned branch's `id` for every legacy row whose `branch_id` was `NULL`. Rows whose legacy `branch_id` was already non-null are imported unchanged (via the same explicit-ID `import*` query pattern `CatalogImporter` already established) — the default-branch backfill only ever fills a genuine gap, never overrides a real legacy value.

This keeps the backfill decision in exactly one place (`EnsureDefaultBranchUseCase`, already real and tested in M5.4) rather than re-implemented per importer-table, and keeps it deterministic across every table in the same import run — a sale and its own related return row, both legacy-branchless, always backfill to the identical branch.

## What is real today vs. deferred to Milestone 18

Real today: `EnsureDefaultBranchUseCase` itself, fully tested (branch-domain-contract.md). Deferred: the actual call site inside a sales/returns/inventory importer — `CatalogImporter` (M4) only covers branches/categories/products, none of which have a nullable `branch_id` to backfill, so no real call site exists yet. This document exists so Milestone 18's implementer does not have to rediscover the rule — not to claim the migration itself is done.
