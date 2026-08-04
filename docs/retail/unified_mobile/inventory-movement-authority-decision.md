# Aura Retail Unified Mobile — Inventory Movement Authority Decision (M5.5.8)

## Does an immutable movement authority already exist?

**Yes, mostly** — `inventory_movements` (M4, `Inventory.sq`) was already a real, faithful port of the legacy table, and the legacy authority already pairs almost every write path with a movement row inside the same transaction (product-inventory-authority-audit.md #3: `create_product` opening stock, `adjust_stock`, `receive_purchase_order`, `create_sale`, `create_return` all do this). The one real gap — `import_api.py`'s absolute-overwrite path with no movement row — is explicitly not ported (inventory-mutation-contract.md).

## Decision: extend, do not replace

`inventory_movements` gained three additive columns and one new unique index this milestone (`Inventory.sq`, M5.5 commit):

- `quantity_before` / `quantity_after` — **NEW_COMPLETE_PRODUCT_REQUIREMENT**. The legacy movement row records only the delta, never either side of the balance change — a real gap for a "complete audit trail," not present in any legacy table.
- `related_sale_id` / `related_return_id` — plain `INTEGER`, deliberately with **no `REFERENCES` clause**. This is a real, considered choice: `product_id`/`branch_id` already establish full identity for FK-enforcement purposes, and adding a cross-`.sq`-file FK (`Inventory.sq` is compiled before `Sales.sq`/`Returns.sq` alphabetically) would introduce file-ordering coupling with no functional benefit — SQLite defers FK existence checking to DML time regardless, so there was never a real risk, but there was also never a real need to take on the coupling.
- `idempotency_key` + `inventory_movements_idempotency` (partial unique index, `NULL`-exempt, same pattern as `returns_idempotency`) — the real mechanism `inventory-mutation-contract.md`'s idempotent-retry guarantee depends on.

## Migration impact

Additive only — every new column has a value supplied by every insert path this milestone touches (`SqlDelightInventoryRepository.adjustStock`/`reconcileStock`, `SqlDelightProductRepository.insertWithInitialStock`); there is no existing production data to migrate (`RetailDatabase.Schema.create()` is still the only real construction path, per database-schema-contract.md's own versioning note — no device has ever created a unified-schema database at a version before this change).

## Table-count reconciliation status

**No table count changed.** This extends an existing table (`inventory_movements`), not a new one — the schema remains the 20-table count `table-count-reconciliation.md` established in M5.0. No update to that document is needed; this is noted here explicitly so a later reader does not have to re-derive that fact.

## Rules (append-only, no editing, no deleting)

No code added anywhere in M5.1-M5.5 performs an `UPDATE` or `DELETE` against `inventory_movements` — every write path is `INSERT` only (`insertMovement`, the only mutating query in `Inventory.sq` targeting this table). A correction is always a new row (`reconcileStock`'s own delta-recording behavior is the concrete example: correcting a wrong balance produces a new `STOCK_TAKE_RECONCILE` row, never edits the old one). No opening/migration movement exists yet for the M4 `CatalogImporter`'s three tables (branches/categories/products carry no stock), and the full-schema Milestone 18 importer's own opening-movement strategy is out of this milestone's scope (already noted as deferred in `android-data-migration-report.md`).
