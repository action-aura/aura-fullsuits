# Aura Retail Unified Mobile — Low-Stock Definition (M5.5.12)

## Chosen policy: business-default, company-wide, real rows not just a count

Audited before choosing (product-inventory-authority-audit.md #6): the legacy authority already has exactly one low-stock definition — `reorder_level` on `products` (company-wide, no per-branch override), compared against on-hand **summed across all branches**, active products only — and exactly one consumer, a dashboard **count**. No per-branch threshold, no business-default-with-per-product-override model, exists anywhere in the legacy system to port. Rather than inventing a richer threshold-ownership model unprompted, this milestone keeps the audited policy and only changes the one real, cited gap: **there is no route that lists the actual low-stock products, only the count** (#6) — `GetLowStockProductsUseCase`/`ProductRepository.listLowStock` close exactly that gap, nothing more.

## The five specific questions the spec asks, answered

| Question | Answer | Why |
|---|---|---|
| Threshold ownership | Product-level (`products.reorder_level`), company-wide across branches | Matches the audited legacy policy exactly — no evidence a richer model is needed yet |
| Is zero low stock | Yes — `<=` reorder_level, and `reorder_level` defaults to 5, so a product with 0 on hand and any positive reorder level is always included | Matches the legacy SQL's own `<=` comparison verbatim |
| Non-inventory-tracked products excluded | N/A — this milestone has no inventory-tracking-policy flag at all (product-inventory-authority-audit.md's own field classification: no such concept exists in the legacy authority or was proven needed; every product implicitly participates in inventory) | Avoids inventing an unused field |
| Archived product behavior | Excluded — `WHERE p.status = 'active'` (`selectLowStockProducts`, `Catalog.sq`) | Matches the legacy query exactly |
| Archived branch behavior | Included in the sum — the legacy query has no branch-status join at all, it sums `inventory_balances` unconditionally; this milestone's `selectLowStockProducts` does the same (no `branches` join). A stale balance sitting in an archived branch still contributes to "how much of this product exists," which is the real question a low-stock alert answers | Deliberately unchanged from the audited legacy behavior — no requirement was found to exclude archived-branch stock from the total |
| Sorting/pagination | Stable (`ORDER BY p.name`), no pagination added — the legacy feature was a single dashboard count with no list at all, so there is no prior UX to preserve; a bound will be added if/when a real caller needs one (Milestone 6 UI) | Avoids speculative pagination design ahead of the UI that will actually consume it |

## Real, executed evidence

`getLowStockProductsReflectsSumAcrossBranches` (`InventoryUseCasesTest.kt`): two branches with 2 units each (sum 4), `reorder_level = 5` → the product is correctly returned with `totalOnHandAcrossBranches = 4`. Query-plan verification (index usage on `products.company_id`/`status`/`reorder_level` and the `inventory_balances` join) is covered in `product-inventory-query-plan-report.md` at the 10K-product synthetic scale.
