# Aura Retail Unified Mobile — Shared Database Schema Decision (M4)

## SQLite authority: SQLDelight (`app.cash.sqldelight`), real, verified evidence

Real, current evidence gathered this milestone: `app.cash.sqldelight:gradle-plugin` **2.3.2** on Maven Central ([libraries.io/maven/app.cash.sqldelight:gradle-plugin](https://libraries.io/maven/app.cash.sqldelight:gradle-plugin)), real KMP support spanning Android, iOS/Native, JVM, JS ([sqldelight.github.io/sqldelight/2.3.2](https://sqldelight.github.io/sqldelight/2.3.2/)). Generates typed Kotlin APIs from `.sq` files, verifies schema/migrations at compile time, ships real per-platform drivers (`AndroidSqliteDriver`, `NativeSqliteDriver` for iOS). This satisfies the governing spec's own preference ("Prefer a schema-controlled solution such as SQLDelight only after verifying it fits the actual migration and packaging requirements") — verified fit: same logical schema compiles identically for Android and iOS from one `.sq` source set, matching this whole initiative's "one shared database contract" mandate.

## Real finding driving the most important schema decision: `REAL` columns must become `TEXT`

`android-database-audit.md` documents every money/quantity column in the real Python schema (`cost_price`, `sell_price`, `tax_rate`, all `quantity*` columns, every sale/return/payment/purchase-order total) as SQLite `REAL` — an 8-byte IEEE binary float. This is exactly the representation Milestone 3's `Money`/`Quantity` types exist to eliminate (`money-decimal-decision.md`). The SQLDelight schema stores every such column as `TEXT`, holding the exact canonical decimal string `Money.toString()`/`Quantity.toString()` produce — never round-tripping through a binary float at the persistence layer. This is not a Milestone-3-scope decision revisited; it is the same decision, extended to where it was always going to have to apply once real storage was built.

## Timestamp representation: epoch milliseconds (`INTEGER`), not SQLite's `TIMESTAMP DEFAULT CURRENT_TIMESTAMP`

The real Python schema uses SQLite's dynamically-typed `TIMESTAMP DEFAULT CURRENT_TIMESTAMP` (stored as a `TEXT` string in practice, in the server's local time — see `create_sale()`'s own explicit `now_local` handling and comment about UTC-vs-local dashboard-date bugs). `FinalizedSaleSnapshot.createdAtEpochMillis` (Milestone 3) already committed to a platform-neutral epoch-millisecond `Long` contract, formatted to a locale/timezone-correct display string only at render time. The SQLDelight schema stores `INTEGER` epoch millis for every `created_at`/`ordered_at`/`received_at` column, matching that already-established contract exactly rather than introducing a second time representation at the persistence layer.

## `company_id` retained, not removed

Every real Python table carries `company_id` (multi-tenant scoping, always `1` on a real standalone Android install per `AURA_STANDALONE=1`). Retained in the SQLDelight schema unchanged — removing it would be an architecture change nobody asked for and would complicate the Android data-preservation import (Milestone 4's other half) for no real benefit; every query already needs to filter by it identically on the new schema.

## Real gap closed: indexes

`android-database-audit.md`'s own real finding: zero `CREATE INDEX` statements in the entire Python schema despite `company_id`/`product_id`/`branch_id`/`sale_id` foreign-key filtering in nearly every real query. The SQLDelight schema adds a real index on every foreign-key column and every `company_id` column from the start — the same class of fix this whole engagement's prior Owner phases repeatedly found necessary via real `EXPLAIN ANALYZE` evidence (Phase 9.5C M24, Phase 9.5D M22, Phase 9.5E M23). This is a pure additive index-coverage improvement, not a schema *shape* change — same tables, same columns, same relationships, so it does not complicate the Android-data-preservation import.

## Foreign keys and connection pragmas — real settings to reproduce exactly

`android-database-audit.md`: `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=30000`, `PRAGMA foreign_keys=ON` (set per-connection, every time — SQLite does not persist this in the file). The shared `DatabaseDriverFactory` (Milestone 2's platform-interface skeleton) sets all three on every driver it creates, on both Android and iOS.

## Migration-safety design: mirrors `commercial_runtime/security/migration_safety.py`, not invented from scratch

The real Python authority already has a proven schema-version-safety pattern (`PRAGMA user_version`, integrity-check-before-migrating, live `.backup()` API — never a raw file copy — before any schema change, only advance the version marker after a post-migration integrity re-check). The shared Kotlin `DatabaseMigrationSafety` (implemented alongside the real SQLite driver) reproduces the same design: SQLDelight's own `Schema.migrate()` callback wrapped with the identical safety sequence.

## What SQLDelight does NOT decide for us (real, separate scope)

- **Secret storage** stays entirely outside this database (Milestone 10's `SecureCredentialStore` / Android Keystore / iOS Keychain) — license private credentials are never written to any table here, per the governing spec's own explicit instruction, matching this schema's own real absence of any credential-shaped column in the audited Python schema.
- **Encryption at rest** — the real Python-era database is plain, unencrypted SQLite (relying on OS sandbox permissions). Whether the unified product adds SQLCipher or an equivalent is a Milestone 20 (security threat model) decision, not decided here; the schema design above is encryption-representation-neutral (works identically whether or not the underlying driver is later swapped for an encrypted one).
