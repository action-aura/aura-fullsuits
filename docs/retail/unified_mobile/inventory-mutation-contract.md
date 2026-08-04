# Aura Retail Unified Mobile — Inventory Mutation Contract (M5.5.7)

## The one rule the whole contract exists to enforce

**No caller ever supplies an authoritative post-mutation quantity.** Every entry point takes a direction + magnitude (`adjustStock`) or a counted value that gets converted to a derived delta against a lock-fresh read (`reconcileStock`) — never a raw "set balance to X" write. This closes the one real, cited gap in the legacy authority: `import_api.py`'s `_handle_retail_products` did exactly the forbidden thing — an absolute `UPDATE inventory_balances SET quantity_on_hand=?` with no movement row at all (product-inventory-authority-audit.md #3) — and is explicitly not replicated anywhere in this milestone's code.

## Reasons — real, named, not free text

`StockMovementReason` (`InventoryRepository.kt`): `INITIAL_STOCK`, `MANUAL_RECEIPT`, `MANUAL_CORRECTION_INCREASE`, `MANUAL_CORRECTION_DECREASE`, `SALE`, `RETURN`, `IMPORT`, `MIGRATION`, `RESTORE_RECONCILIATION`, `STOCK_TAKE_RECONCILE`. **CANONICAL_UNIFIED**: the legacy `movement_type` column is free TEXT with no CHECK constraint or enum (product-inventory-authority-audit.md #3) — a real, cited gap this milestone closes structurally, not just by convention.

`SALE`/`RETURN`/`IMPORT`/`MIGRATION`/`RESTORE_RECONCILIATION` are declared now (the enum is the stable contract later milestones' sale/return/import/migration/restore code will use) but have no real caller yet in this milestone — sale-return-inventory-integration.md's own honest scope note explains why (no SQLite-backed `SaleRepository` exists yet to call them from).

## Commands (`usecases/inventory/InventoryUseCases.kt`)

- **`AdjustInventoryUseCase`** — validates product existence and branch existence+active state before delegating to `InventoryRepository.adjustStock`'s own atomic, lock-fresh-read-then-write transaction (M5.1's `InsufficientStock` guard, unchanged). Real proof: `adjustInventoryRejectsArchivedBranch`, `adjustInventoryRejectsUnknownProduct`.
- **`ReconcileInventoryUseCase`** — the real, named "stock-take" requirement M5.5.7 explicitly calls out as absent from the legacy authority (product-inventory-authority-audit.md #3/#6: no reconcile-to-counted-quantity endpoint exists at all). Takes a counted absolute value but computes and records the real delta against the lock-fresh prior balance — proven by `reconcileNeverOverwritesBlindly` (`quantity_before` on the recorded movement equals the value that was actually there, not an assumed one) and `reconcileToCountedQuantityRecordsCorrectDeltaBothDirections` (both increase and decrease directions).

## Idempotency

`idempotencyKey` (optional on both commands) makes a retry with the identical payload return the original result rather than double-applying; a retry with the SAME key but a DIFFERENT payload returns `RepositoryError.IdempotencyConflict` rather than silently applying the new one. Same contract `InMemorySaleRepository` (M3) already established for sale finalization — real proof at the atomic-transaction level in `SqlDelightInventoryRepository.adjustStock`/`reconcileStock`, and combined proof at the use-case level in `retryWithSameIdempotencyKeyReturnsOriginalProductNotADuplicate` (via `CreateProductWithInitialStockUseCase`, which shares the same idempotency-key discipline for its own opening-stock movement).

## Manual reconciliation's required fields

The spec requires: explicit permission (M5.5.13's own honest scope note — no Kotlin RBAC exists yet to check against), reason (`STOCK_TAKE_RECONCILE`, fixed — not caller-supplied, since reconciliation is definitionally always this one reason), previous quantity (`quantity_before`), counted quantity (`quantity_after`), resulting delta (`quantity`), actor (`createdBy`), timestamp (`created_at`), idempotency key (optional parameter, supported). Every one of these is a real column on `inventory_movements`, not a field only present in an in-memory result object — the movement row itself is the permanent record.
