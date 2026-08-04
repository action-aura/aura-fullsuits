# Aura Retail Unified Mobile — Branch Inventory Contract (M5.5.5)

## Canonical record

One row per (business, branch, product) — `inventory_balances`, `UNIQUE(company_id, product_id, branch_id)` (M4, unchanged this milestone). Every quantity is exact `Quantity`/TEXT, never `Double`/`Float` (database-schema-contract.md rule 1). `quantity_reserved` remains in the physical schema but is confirmed DEPRECATED — the legacy authority never reads or writes it (product-inventory-authority-audit.md #1); no code added in M5.5 references it either.

## Missing-inventory-row policy — chosen and tested

**Policy: implicit zero, not an explicit not-configured state.** `InventoryRepository.getStockOnHand` returns `Quantity.ZERO` when no `inventory_balances` row exists for a (product, branch) pair, rather than a nullable/sentinel "not configured" value. Reasoning: `upsertOpeningStock`/`adjustStock` both lazily create the row on first real use ("INSERT OR IGNORE" — sqlite-replace-safety-audit.md), so "no row" and "zero balance" are the same real-world state in this schema by construction — there is no way to distinguish "explicitly configured at zero" from "never touched" once a row is INSERT-OR-IGNORE'd into existence anyway, so introducing a separate not-configured type would add a distinction the schema itself cannot preserve. Proven: `adjustStockWorksWithNoPriorOpeningStockRow` (M5.1), `adjustInventoryRejectsUnknownProduct`/`getLowStockProductsReflectsSumAcrossBranches` (M5.5).

## Real behaviors, each proven

| Rule | Proof |
|---|---|
| Active product may have inventory in multiple active branches | `getLowStockProductsReflectsSumAcrossBranches` (two branches, one product) |
| Current-branch scoping is a use-case/UI concern, not an inventory-table concern | `GetCurrentBranchUseCase` (M5.4) is the only thing that decides "which branch" — `InventoryRepository` itself is branch-id-parameterized, never branch-current-aware |
| Archived branch retains its historical inventory row and transaction associations | Structural: no code path in M5.4/M5.5 deletes an `inventory_balances` or `inventory_movements` row when a branch is archived (`BranchRepository.setActive` only updates `branches.status`) |
| New sale cannot target an archived branch | Deferred to the actual `SaleRepository` SQLite implementation (not yet built — sale-return-inventory-integration.md's own honest scope note); the underlying primitive (`BranchRepository.getById(...).isActive`) it will need already exists and is tested |
| Ordinary stock mutation cannot target an archived branch | `adjustInventoryRejectsArchivedBranch`, and `reconcileStock`'s own use-case wrapper performs the identical check |
| Cross-business Product/Branch combination rejected | Structural: every repository method is `companyId`-parameterized and every query filters by it; `adjustInventoryRejectsUnknownProduct`-style tests confirm a wrong-company id resolves to `NotFound`, never a cross-tenant row |
| No duplicate inventory row for the same (business, branch, product) | The real `UNIQUE(company_id, product_id, branch_id)` constraint (M4, unchanged) — `upsertOpeningStock`'s `INSERT OR IGNORE` is the only insert path and is itself immune to duplication by construction |

## What M5.5 does NOT add here

No new "not configured" sentinel type, no per-branch reorder-level override (low-stock-definition.md keeps the legacy's own company-wide `reorder_level` on `products`, not a branch-specific one — audited, not invented), no inventory-row soft-delete concept (a branch/product being archived is sufficient signal; the balance row itself is never a thing to archive independently).
