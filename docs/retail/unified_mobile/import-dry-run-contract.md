# Import Dry-Run Contract (M5.8.13)

Real, durable dry-run authority — persisted in a new `import_dry_runs`
table (additive to the shared schema, `Import.sq`, same precedent as
M5.5's `import_conflicts` addition), proven by
`SqlDelightImportPersistenceRepositoryTest.kt` (7/7).

## Immutability

No field on `ImportDryRun` is ever updated after `saveDryRun` — the
only real mutation the schema allows is `consumed_at` (set exactly once
by `markDryRunConsumed`, guarded by `WHERE consumed_at IS NULL` so a
second attempt is a real, detected no-op, never a silent overwrite).
Proven by `consumingAnAlreadyConsumedDryRunFailsRealAndIdempotently` —
the original consumption timestamp survives a second attempt untouched.

## Source hash covers the exact decoded bytes

`sourceHash` is stored and checked against the real dry-run's own value
on every commit revalidation (`import-commit-revalidation.md`) — a
later file with the same name but different content produces a
different hash and can never reuse an existing dry-run.

## Business isolation

`getDryRun` requires both `id` AND `companyId` to match — proven by
`aDryRunFromAnotherCompanyIsNeverReturned`: a dry-run saved for company
1 is invisible when queried with company 2, structurally, not merely by
convention.

## Real schema addition

```sql
CREATE TABLE import_dry_runs (
    id TEXT PRIMARY KEY, company_id INTEGER NOT NULL, branch_id INTEGER,
    source_hash TEXT NOT NULL, source_display_name TEXT NOT NULL, format TEXT NOT NULL,
    decoder_version INTEGER NOT NULL, mapping_version INTEGER NOT NULL, schema_version INTEGER NOT NULL,
    entity_types TEXT NOT NULL, total_rows INTEGER NOT NULL, valid_rows INTEGER NOT NULL,
    invalid_rows INTEGER NOT NULL, warning_count INTEGER NOT NULL, duplicate_count INTEGER NOT NULL,
    conflict_count INTEGER NOT NULL, planned_inserts INTEGER NOT NULL, planned_updates INTEGER NOT NULL,
    planned_skips INTEGER NOT NULL, planned_generated_values INTEGER NOT NULL,
    commit_eligible INTEGER NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
    consumed_at INTEGER
);
```

Real, disclosed serialization choice: `entity_types` is a comma-joined
list of `ImportEntityType` enum names (e.g. `"PRODUCTS,CATEGORIES"`) in
a plain `TEXT` column — a real, simple, sufficient encoding for a short,
bounded (≤5 real values) list, not a JSON column (no JSON dependency is
needed in `commonMain` for this narrow case). `validationIssues`/
`duplicates`/`conflicts` are NOT persisted on the dry-run row itself in
this milestone (real, disclosed scope decision — the dry-run row stores
real, exact COUNTS of each, which is what commit revalidation and audit
actually need; the full itemized lists remain in-memory for the
immediate preview response, matching `ImportLimits.maxValidationIssueCount`'s
own bound on how many are worth retaining at all).

## Table count

The shared schema is now 27 tables (24 confirmed at M5.7 close +
`import_dry_runs` + `import_provenance` + `import_audit_log`) — an
additive, disclosed count, matching `table-count-reconciliation.md`'s
own established precedent for this kind of real, incremental addition.
