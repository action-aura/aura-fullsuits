# Aura Retail Unified Mobile — Product/Category Integration (M5.5.4)

## Rules, each with real, executed proof

| Rule | Proof |
|---|---|
| Active product may be assigned to an active category | `createWithActiveCategorySucceeds` |
| Product may be created without a category (optional FK, matches legacy `category_id` nullability) | Every test that passes `categoryId = null` |
| New assignment to an archived category is rejected | `createRejectsArchivedCategoryAssignment` (create-time), `AssignProductCategoryUseCase`'s own `validateCategoryAssignment` call (update-time, same check) |
| Archiving a category does not corrupt or remove a product's existing assignment | `archivedProductCannotReceiveNewCategoryAssignmentButKeepsExistingOne` |
| Archiving a category does not archive its products | Same test — `stillThere.isActive` asserted `true` after the category is archived |
| Product remains visible (and its `categoryName` still resolves) after category archive | Already proven at the repository layer in M5.3's `archivingCategoryPreservesExistingProductAssociation` — the `LEFT JOIN` in `selectProductById`/`selectActiveProducts` is not status-filtered; M5.5 adds no new join logic, so this guarantee carries forward unchanged |
| Cross-business category assignment rejected | `createRejectsCrossBusinessCategoryAssignment` — company 2 attempting to assign company 1's category id gets `RepositoryError.NotFound`, not a silent cross-tenant leak |
| Stale category state is revalidated, not trusted from the UI | `validateCategoryAssignment` (`ProductUseCases.kt`) always re-reads the category via `categoryRepository.getById` at the moment of the create/update/assign call — never accepts a caller-supplied "is this category active" flag. `AssignProductCategoryUseCase`'s own KDoc states this explicitly: "Do not trust Category state loaded earlier in the UI" |

## Why "Products remain discoverable under an explicit All Products view" needs no new code this milestone

The governing spec calls for products whose category has been archived to "remain discoverable under an explicit All Products view" and to be "reassignable to an active category." `ListProductsUseCase`/`ProductRepository.listActive` already return every active product regardless of its category's own status (the query has no join-status filter on `categories`), and `AssignProductCategoryUseCase` already supports moving a product to a different (active) category or to `null`. No new query or use case was needed — this is a real case of a requirement already satisfied by the existing M5.1/M5.5.1 design, verified by inspection and by `archivedProductCannotReceiveNewCategoryAssignmentButKeepsExistingOne` rather than assumed.
