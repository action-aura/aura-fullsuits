# Aura Retail Unified Mobile — Database Schema Contract (M5.0 authoritative)

Supersedes any prior "19-table" framing (`shared-database-schema-decision.md`'s own prose, the M4 commit message). This is the single authoritative statement of the shared schema contract, cross-referencing `table-count-reconciliation.md`'s full evidence rather than repeating it.

## The contract

**20 tables**, defined once in `mobile/aura-retail-unified/shared/src/commonMain/sqldelight/com/actionaura/retail/db/*.sq`, compiled by SQLDelight into one identical schema consumed by both the Android driver (`AndroidDatabaseDriverFactory`, real, committed) and the future iOS driver (Milestone 19, written but unverifiable on this host). One schema, two platforms, zero divergence — this is the literal, load-bearing meaning of "one shared database contract" from the governing spec's own core mandate.

Every one of the 20 tables has a real, named legacy-authority source (`table-count-reconciliation.md`'s matrix) — none were invented. The only real additions beyond a faithful port are: the `categories.status` column (M1 Product Owner Override), the `sale_items.product_name_at_sale`/`return_items.product_name_at_sale` columns (DIFF-03), and index coverage (real index-only additions on every FK/`company_id` column, the legacy schema having none).

## Representation rules (binding on every future table/column added in later milestones)

1. Every money or quantity-shaped column is `TEXT`, holding the canonical decimal string produced by `Money.toString()`/`Quantity.toString()` (Milestone 3) — never SQLite `REAL`.
2. Every timestamp column is `INTEGER` epoch milliseconds — never SQLite `TIMESTAMP`/text.
3. Every table scoped to a single local business carries `company_id INTEGER NOT NULL DEFAULT 1` and an index on it.
4. Every foreign-key column has an explicit `REFERENCES` clause and an index.
5. `PRAGMA foreign_keys=ON`, `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=30000` are set on every connection a `DatabaseDriverFactory` implementation opens (real, verified: `RetailDatabaseSchemaTest.foreignKeyEnforcementIsReallyOn`).
6. Row-mutating SQL that could apply SQLite `REPLACE` semantics to a mutable business entity is forbidden — see `sqlite-replace-safety-audit.md` for the full, real classification of every current use.

## Versioning

`RetailDatabase.Schema.version` (SQLDelight-managed) is the single schema-version authority. Milestone 4's `AndroidDatabaseDriverFactory` currently always calls `Schema.create()` (fresh database) — no in-place `Schema.migrate()` path exists yet because there is no prior *unified*-schema version to migrate from (the only real predecessor is the legacy Python schema, which is a cross-schema *import*, not an in-place migration — `data-preservation-plan.md`). A real `Schema.migrate()` path becomes necessary starting from the unified schema's own v2, whenever this contract's tables/columns next change after a real device has already created v1 data — tracked as a real, near-term concern for Milestone 18, not yet needed.

## What this contract explicitly does not promise

Multi-branch live synchronization across devices, encryption at rest (Milestone 20 decision), or a public/external API surface — this schema is private to the shared Kotlin core; no UI or platform code queries it directly except through the repository layer (Milestone 5.1).
