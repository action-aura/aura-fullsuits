# Aura Retail Unified Mobile — Android Local Database Audit (M4)

Real audit of the actual current Android database, read from the real source, not assumed.

## Real, exact file location

`<app-private-filesDir>/data/database/subsystems/retail.db` (`android_platform.py:resolve()` -> `<filesDir>/data`, `config.py:22-23` -> `<AURA_APP_DATA>/database`, `schema.py:_get_path()` -> `<BASE_DIR>/subsystems/<name>.db`). SQLite, opened via Python's stdlib `sqlite3`, no ORM.

## Real connection settings (`schema.py:_conn()`, every connection, not persisted in the file)

```
PRAGMA journal_mode=WAL
PRAGMA busy_timeout=30000
PRAGMA foreign_keys=ON
```

**Real finding**: the code's own comment confirms foreign-key enforcement was previously silently OFF everywhere in the codebase until a real fix (AUDIT-016, Wave 0) — SQLite does not enforce `FOREIGN KEY` by default, and it must be set per-connection every time, never persisted. This is real evidence the shared Kotlin driver must replicate exactly: enable `foreign_keys=ON` and `journal_mode=WAL` on every connection it opens, not assume a one-time setup suffices.

## Real schema version mechanism (already exists, real, reusable design reference)

`schema.py:288-301` calls `commercial_runtime/security/migration_safety.py::ensure_schema_version()` — a real, already-built, already-proven safety wrapper using SQLite's own `PRAGMA user_version` integer field:

- No-op fast path if already at target version.
- Otherwise: `PRAGMA integrity_check` on the *current* file BEFORE touching anything; refuses to migrate a database that's already damaged.
- Live, transaction-consistent backup via `sqlite3.Connection.backup()` (never a raw file copy — the module's own docstring cites a real prior incident where a raw copy of a live WAL-mode database produced a malformed backup).
- Runs the migration function, re-checks integrity, only then advances `PRAGMA user_version`.
- On any failure, the on-disk file is left exactly as the backup captured it, `user_version` is not advanced, and the backup file remains as an explicit recovery point.

This is the **real design reference** for Milestone 4's own "create a pre-migration backup... validate source database integrity... fail safely... never delete source data automatically" requirements — not invented from scratch, mirrored from an already-proven pattern this same codebase already uses for its own Python-side schema upgrades.

## Real schema: 17 tables, zero triggers, zero explicit secondary indexes (real gap, opportunity for the new implementation)

Full table list (all `CREATE TABLE IF NOT EXISTS`, `schema.py:82-287`): `branches`, `categories`, `products`, `inventory_movements`, `inventory_balances`, `customers`, `suppliers`, `purchase_orders`, `purchase_order_items`, `sales`, `sale_items`, `returns`, `return_items`, `payments`, `tax_rates`, `journal_entries`, `audit_log`. Plus two lazily-created tables (`retail_settings`, `doc_sequences`) added on first use by `api/retail_api.py`'s `_ensure_credit_schema()` rather than in `init_retail()` itself — real, must be included in the SQLDelight schema as first-class tables from the start (no reason to replicate the lazy-creation quirk).

**Real finding**: zero `CREATE INDEX` statements anywhere in `schema.py`, despite every real query in `retail_api.py` filtering by `company_id` (multi-tenant scoping, present on nearly every table) and frequently by `product_id`/`branch_id`/`sale_id` foreign keys. This is the same class of real, previously-found gap this whole engagement's own prior phases repeatedly caught (Phase 9.5A/9.5C/9.5D/9.5E's own EXPLAIN-ANALYZE-found missing-index bugs, `docs/owner/phase9_5c/`, `docs/owner/phase9_5d/`, `docs/owner/phase9_5e/performance-explain-analyze.md`). The new SQLDelight schema adds real indexes on every foreign-key/company_id column from day one — a `CANONICAL_UNIFIED` improvement, not silently carried-forward technical debt, and not a schema *shape* change (same tables, same columns, same relationships — purely additive index coverage).

## Data-type mapping (SQLite's own dynamic typing, real column definitions read from `schema.py`)

| SQLite declared type | Real usage | SQLDelight/Kotlin mapping |
|---|---|---|
| `INTEGER PRIMARY KEY AUTOINCREMENT` | every table's `id` | `INTEGER AS Long`, `PRIMARY KEY AUTOINCREMENT` |
| `INTEGER` (company_id, branch_id, product_id, etc.) | foreign keys | `INTEGER AS Long`, `REFERENCES` |
| `TEXT` | names, statuses, references | `TEXT` |
| `REAL` | `cost_price`, `sell_price`, `tax_rate`, quantities, all money/quantity columns | **real finding, must NOT be ported as-is**: SQLite `REAL` is an 8-byte IEEE float — exactly the binary-float representation Milestone 3's own `Money`/`Quantity` types exist to avoid. The new schema stores these columns as `TEXT` (canonical decimal string, matching `Money.toString()`/`Quantity.toString()`'s own deterministic format) instead, never `REAL`. This is the single most important schema decision this audit surfaces — see `shared-database-schema-decision.md`. |
| `TIMESTAMP DEFAULT CURRENT_TIMESTAMP` | `created_at` columns | `TEXT` (ISO-8601 string) or `INTEGER` (epoch millis, matching `FinalizedSaleSnapshot.createdAtEpochMillis`'s own contract from Milestone 3) — decided in `shared-database-schema-decision.md` |

## What is NOT present (real absence, not overlooked)

- No triggers.
- No views.
- No CHECK constraints beyond column defaults.
- No encryption at the SQLite level (SQLCipher or similar) — the real file is plain SQLite, relying entirely on Android's app-sandbox filesystem permissions for at-rest protection. Real finding to carry into Milestone 20's threat model (local database theft is listed there as a threat to model, not solved by the current Python-era design either).
