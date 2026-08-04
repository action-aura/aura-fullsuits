# Aura Retail Unified Mobile — Shared Use-Case Catalog (M5.2, growing through M5)

Tracks every use case in `shared/src/commonMain/kotlin/com/actionaura/retail/usecases/`, the layer the governing spec requires between the repository interfaces (M5.1) and any future ViewModel/UI code (Milestone 6). No use case is called from any ViewModel or UI code yet.

## M3 (pre-existing)

`FinalizeSaleCommand`/`FinalizeReturnCommand` (`usecases/FinalizeSaleCommand.kt`) — command objects consumed directly by `SaleRepository`/`ReturnRepository`'s single atomic method (M3's transaction-boundary-audit.md: no separate use-case orchestration layer for sale finalization, by design, to preserve atomicity).

## M5.3 — Category domain (`usecases/category/CategoryUseCases.kt`)

| Use case | Responsibility | Repository-layer invariant it does NOT re-implement |
|---|---|---|
| `CreateCategoryUseCase` | Trims/validates the name, runs NFKC-normalized duplicate detection against active categories, inserts | — |
| `ArchiveCategoryUseCase` | Idempotent status change to archived | Historical product association (structural — `CategoryRepository.setActive` never touches `products.category_id`) |
| `ReactivateCategoryUseCase` | Idempotent status change to active, re-running the same dedup check (a same-named active category may have been created while this one was archived) | — |
| `ListActiveCategoriesUseCase` | Thin passthrough | — |

See `category-domain-contract.md` for the full behavioral contract and real test evidence.

## Convention established here, binding on M5.4/M5.5's use cases

- One class per operation (not one god-class per entity) — matches the command-object style already established by `FinalizeSaleCommand`.
- Constructor-injected repository + platform-contract dependencies (no service locator, no global singletons) — same pattern `PlatformContracts.kt` implementations already use.
- Returns `DomainResult<T>` (M5.1) for every operation that can fail; a bare return type only for operations that structurally cannot fail (`ListActiveCategoriesUseCase`).
- Business rules that need transactional atomicity stay in the repository (M5.1's own documented split); use cases own everything else (validation, cross-cutting checks like dedup that read-then-decide without needing lock-fresh atomicity because a concurrent duplicate is caught by the database's own partial unique index as a backstop — see category-domain-contract.md's real-bug-found section).
