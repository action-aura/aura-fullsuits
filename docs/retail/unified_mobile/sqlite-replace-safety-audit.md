# Aura Retail Unified Mobile — SQLite REPLACE Safety Audit (M5.0.B)

## Real, exhaustive search (not a sample)

```
$ grep -n "INSERT OR REPLACE\|REPLACE INTO\|INSERT OR IGNORE" shared/src/commonMain/sqldelight/com/actionaura/retail/db/*.sq
Inventory.sq:36: INSERT OR IGNORE INTO inventory_balances(...)
Settings.sq:33:  INSERT OR REPLACE INTO retail_settings(company_id, skey, svalue) VALUES (?, ?, ?);
Settings.sq:39:  INSERT OR REPLACE INTO doc_sequences(company_id, doc_type, last_no) VALUES (?, ?, ?);
```

**Exactly two `INSERT OR REPLACE` statements exist in the entire schema, and both are audited below. Zero uses of `REPLACE INTO` (the bare form) exist anywhere.** The one `INSERT OR IGNORE` use is a structurally different, inherently safer statement (never deletes an existing row; no-ops on conflict) and is addressed separately at the end of this document.

## Classification

### `upsertSetting` (`Settings.sq:33`) — `retail_settings(company_id, skey, svalue)`, PK `(company_id, skey)`

**Classification: SAFE.**

Real evidence:
1. **No child foreign keys reference `retail_settings`** — confirmed by an exhaustive `grep -rn "REFERENCES retail_settings" *.sq` across every `.sq` file, zero matches. SQLite's `REPLACE` semantics (delete-then-insert on a PK conflict) can only cascade or orphan child rows when child rows exist; here, none do, so there is no cascading-deletion or orphaned-child-row risk to audit further.
2. **The statement supplies every non-PK column** (`svalue` is the only one) — a delete-then-reinsert and a partial `UPDATE` produce byte-identical resulting row content. There is no "column not present in the new statement silently reset to its default" risk (the specific risk the governing spec names), because there are no other columns to reset.
3. **No audit-sensitive timestamp exists on this table** (no `created_at`/`updated_at` column at all) — so there is no "REPLACE alters an audit timestamp" risk either.
4. **Real, executed proof of no accidental duplication**: `RetailDatabaseSchemaTest.settingsUpsertReplacesNotDuplicates` (already committed, passing) calls `upsertSetting` twice with the same key and different values, and asserts exactly one row exists afterward with the second value — real proof the statement behaves as an idempotent upsert, not as an accidental duplicate-producer.

**Disposition**: no change required. Retained as `INSERT OR REPLACE`.

### `upsertDocSequence` (`Settings.sq:39`) — `doc_sequences(company_id, doc_type, last_no)`, PK `(company_id, doc_type)`

**Classification: SAFE.** Identical reasoning to `upsertSetting`: zero child FK references (confirmed, `grep -rn "REFERENCES doc_sequences"`, zero matches), the statement supplies the only non-PK column (`last_no`), no audit timestamp exists on this table.

**Disposition**: no change required. Retained as `INSERT OR REPLACE`.

## Why these two are exempt from the general prohibition

The governing spec's own prohibition list (sales, sale lines, returns, payments, products-with-dependents, categories-with-linked-products, branches-with-linked-transactions, licensing identity, audit-sensitive records) names entities that are either (a) referenced by real foreign keys from other tables, (b) carry audit-sensitive timestamps, or (c) represent a business event whose *identity* (not just its current field values) matters. `retail_settings` and `doc_sequences` are neither — they are plain key-value configuration/counter tables with a composite key that exactly matches every field the statement supplies, no dependents, and no identity concept beyond their own key. This is precisely why the spec's own required classification scheme includes a `SAFE_*` category rather than banning `REPLACE` outright everywhere.

## `upsertOpeningStock` (`Inventory.sq:36`) — `INSERT OR IGNORE`, not `REPLACE`

**Not `REPLACE` at all** — `INSERT OR IGNORE` never deletes an existing row; on a primary-key/unique conflict it silently does nothing and the pre-existing row (and its current `quantity_on_hand`) is left completely untouched. This is the real, correct semantic for its actual use (creating an opening-stock balance row only if one does not already exist, mirroring the legacy Python's own `INSERT OR IGNORE INTO inventory_balances ... quantity_on_hand=?` for exactly the same reason — see `python-financial-authority-map.md`'s `create_product()` discussion). No further audit action required; included here only for completeness since it matched the search pattern.

## No further audit-target rows exist to migrate

`sales`, `sale_items`, `returns`, `return_items`, `payments`, `products`, `categories`, `branches` (the spec's own named-forbidden list) currently have **zero** `INSERT OR REPLACE`/`REPLACE INTO` statements anywhere in the schema — every insert into those tables uses plain `INSERT` (normal application path, autoincrement ID) or the explicit-ID `import*` variants added for the Milestone-4 data-preservation importer (`importBranch`/`importCategory`/`importProduct`, also plain `INSERT`, never `REPLACE`). This audit found nothing to fix in those tables because nothing unsafe was ever written there — confirmed by exhaustive search, not assumed from design intent.
