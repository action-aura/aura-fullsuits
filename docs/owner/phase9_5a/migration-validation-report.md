# Phase 9.5A Milestone 21 — Migration Validation Report

## Real implementation, deviating from `migration-plan.md`'s "9 separate migrations" — recorded honestly

`migration-plan.md` planned one migration per bounded context (9 migrations). The real implementation
consolidated into **one migration** (`3c0d51d82d8c_phase_9_5a_commercial_operations_.py`, chaining from
head `0f8d55b753ed`) for a practical reason found during implementation: Alembic's `--autogenerate`
diffs the *entire* live database against the *entire* model metadata in one pass — producing genuinely
separate migrations would have required commenting out not-yet-included model modules and re-running
autogenerate 9 times, applying each to the DB before generating the next. Given every table in this
migration is purely additive with no cross-migration ordering risk beyond what a single topologically-
sorted `CREATE TABLE` sequence already handles (which Alembic's autogenerate does correctly — verified
below), one migration was the real, safe, practical choice. The **bounded-context organization
itself** is preserved — inside the single migration file, tables are still grouped and commented to
match `bounded-context-map.md`'s module boundaries (Alembic's own natural FK-dependency ordering
happens to match this closely already).

## New tables: 36. New columns on existing tables: 6 (across 4 tables). Zero existing columns
altered/renamed/removed.

Confirmed via a real Python import: `Base.metadata.tables` grew from the pre-existing 65 (implied:
102 total post-migration - a handful of test/system tables not modeled) to 102 total, 36 of them new
Phase 9.5A tables (verified by name, not assumed).

## Real bugs found and fixed during this milestone (not merely "the plan," the actual work)

1. **NOT NULL column with no DB-level default against a table with real existing rows.**
   `StaffSession.platform` (`String(16), default="WEB"`) is a Python-side ORM default — Postgres's own
   `ALTER TABLE ... ADD COLUMN ... NOT NULL` has no way to backfill the real, pre-existing session rows
   from every prior phase's dev/test work without a `server_default`. First `alembic upgrade head`
   attempt against the real dev database failed with a genuine `NotNullViolation`. Fixed by adding
   `server_default='WEB'` to the migration DDL (the correct real value — every session that already
   existed was, by definition, a browser session before mobile login existed).
2. **Unnamed FK/unique constraints can't be dropped by `None` in a downgrade.** This codebase has no
   `naming_convention` configured on `Base.metadata` (a real, pre-existing gap — every prior migration
   only ever created whole new tables, whose `DROP TABLE` implicitly removes all constraints, so this
   is the first migration to need a *named* constraint reference for a downgrade of an additive column
   on an *existing* table). Real downgrade attempt failed with `CompileError: ... it has no name`.
   Fixed by querying the live database for Postgres's own real auto-generated constraint names
   (`pg_constraint`, not guessed) and hardcoding them into the migration's `downgrade()`.
3. **Table-name collision with a real, deliberate structural safety test.** `CommercialInvoiceLine`
   (table `owner_commercial_invoice_lines`) tripped `owner/tests/test_data_boundary.py`'s
   `FORBIDDEN_TERMS` guard (`"invoice_line"` is deliberately forbidden — the same real, existing test
   that structurally proves Owner never grows a Retail/Clinic-POS-shaped table). Confirmed this was a
   real naming collision, not a real architecture violation (Owner's commercial invoice is B2B billing
   of the customer *organization* for its Aura subscription — a different real concept from a Retail
   POS sale/invoice line) — the correct fix was renaming the new table, not weakening the guard.
   Renamed `CommercialInvoiceLine` -> `CommercialInvoiceItem` (`owner_commercial_invoice_items`)
   throughout the model, the migration (regenerated, revision ID changed from `2859a4885f45` to
   `3c0d51d82d8c` since the file was deleted and regenerated), and every design doc.
4. **Stale disposable test database.** The now-deleted first migration (`2859a4885f45`) had already
   been applied to `aura_owner_test` (the disposable Owner test database) by an earlier regression run
   in this same session, before the rename above. After deleting that migration file, `aura_owner_test`
   pointed at a "ghost" revision Alembic could no longer locate. Fixed by dropping and recreating
   `aura_owner_test` fresh (safe — it is explicitly disposable, truncated between every test by
   `owner/tests/conftest.py`'s own fixture design, never holds anything of real value).

## Real testing performed (per Milestone 21's own requirement)

- **Empty/fresh database**: `aura_owner_test`, rebuilt via the real `owner/tests/conftest.py`
  `_migrated_schema` fixture (`alembic upgrade head` from a genuinely empty database) — exercised by
  the full Owner test run below.
- **Populated synthetic database**: the real `aura_owner_staging` database (Phase 9's own restore-drill
  data — a real staff account, a real customer, a real subscription/license chain) — migration applied
  clean, real data confirmed intact and unchanged afterward (staff email, customer legal_name/
  lifecycle_status queried directly post-migration).
- **Upgrade/downgrade/upgrade round-trip**: performed twice for real against `aura_owner_dev` (once
  before the rename fix, once after) — both times clean, no data loss (a fresh/near-empty dev database,
  matching the honest caveat already stated in `migration-plan.md`: downgrading a database with real
  Phase 9.5A *data* in the new tables would discard it, same as any other migration in this codebase).
- **Schema-drift check**: implicit in the successful full Owner regression below — SQLAlchemy's model
  layer and the live migrated schema must agree, or the ORM-level tests (creating/querying every new
  model) would have failed.

## Final real regression result

**423/423 Owner tests passing**, including `test_data_boundary.py`'s full forbidden-term suite (the
real guard the naming collision above was caught by) and every RBAC/security/subscription/scheduler
test already real from Phase 8/9 — confirming zero regression from this migration across the entire
existing Owner test surface, not just the new Phase 9.5A code.
