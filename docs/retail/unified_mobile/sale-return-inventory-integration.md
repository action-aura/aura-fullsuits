# Aura Retail Unified Mobile — Sale/Return Inventory Integration Boundary (M5.5.9/M5.5.10)

## Explicit scope boundary (per the governing spec)

"Do not implement complete SaleRepository UI flows in this milestone. Implement and test the Product/Inventory integration boundary needed by later sale use cases." No new `SaleRepository`/`ReturnRepository` SQLite-backed implementation is built here — that remains real, separate, near-term work (still noted as deferred since M5.1's `shared-repository-architecture.md`). What this milestone delivers is the set of real, tested primitives a future SQLite-backed `SaleRepository`/`ReturnRepository` will compose, plus proof that they compose correctly for exactly this purpose.

## The primitives, all already real and tested before this document

| Requirement | Primitive | Where proven |
|---|---|---|
| Load authoritative product state | `ProductRepository.getById` | M5.1/M5.5 |
| Reject archived product | `Product.isActive` | `ProductInventorySaleReturnBoundaryTest.saleRejectsArchivedProduct` |
| Load authoritative branch state | `BranchRepository.getById` | M5.4 |
| Reject archived branch | `Branch.isActive` | `ProductInventorySaleReturnBoundaryTest.saleRejectsArchivedBranch` |
| Load authoritative inventory state | `InventoryRepository.getStockOnHand` | M5.1 |
| Reject insufficient stock | `InventoryRepository.adjustStock`'s own atomic `InsufficientStock` guard | M5.1, re-proven in the boundary test |
| Decrement stock exactly once, atomically with the movement record | `InventoryRepository.adjustStock(DECREASE, reason=SALE, relatedSaleId=..., idempotencyKey=...)` | `ProductInventorySaleReturnBoundaryTest.saleDecrementsStockExactlyOnceAndLinksTheMovementToTheSale` |
| Preserve idempotency | `idempotencyKey` on `adjustStock` (M5.5.7's own contract, inventory-mutation-contract.md) | `ProductInventorySaleReturnBoundaryTest.retryingSaleStockDecrementWithSameKeyDoesNotDoubleDecrement` |
| Restore stock exactly once on return, linked to the return | `InventoryRepository.adjustStock(INCREASE, reason=RETURN, relatedReturnId=..., idempotencyKey=...)` | `ProductInventorySaleReturnBoundaryTest.returnRestoresStockExactlyOnceAndLinksTheMovementToTheReturn` |

## What is deliberately NOT re-proven here

Oversell-race protection under real concurrent load, exact financial calculation (`calculateLine`/`calculateInvoice`), cumulative-return-limit enforcement, and idempotent sale/return replay at the SALE level (not the stock-mutation level) are already real, tested, and owned by M3's `InMemorySaleRepository`/`FinancialSecurityTest` — this milestone does not duplicate that coverage. What M5.5.9/M5.5.10 adds is specifically the Product/Inventory-side half of the boundary: proving the SQLite-backed primitives a *future* SQLite-backed `SaleRepository` will call behave correctly in isolation, since M3's own proof used the in-memory test double, not these real repositories.

## Real transaction-atomicity note for the eventual SaleRepository implementation

`InventoryRepository.adjustStock` is already itself one atomic transaction (M5.1). A future SQLite-backed `SaleRepository.finalizeSale` will need to wrap ITS OWN insert-sale/insert-sale_items/adjustStock sequence in one further-outer transaction (mirroring `ProductRepository.insertWithInitialStock`'s own pattern of composing multiple table writes atomically, M5.5.6) — `adjustStock` being independently atomic does not by itself make the whole sale atomic; it is one necessary component, not a substitute for the outer transaction the future implementation must still add. Documented here so that future work does not mistake "the stock half is already atomic" for "the whole sale is already atomic."
