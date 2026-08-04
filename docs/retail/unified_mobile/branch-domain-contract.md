# Aura Retail Unified Mobile — Branch Domain Contract (M5.4)

## Scope

`usecases/branch/BranchUseCases.kt`: `ActivateBranchUseCase`, `DeactivateBranchUseCase`, current-branch selection (`GetCurrentBranchUseCase`/`SetCurrentBranchUseCase`), `EnsureDefaultBranchUseCase` (legacy default-branch backfill support), `ListActiveBranchesUseCase`.

## Final-active-branch protection

Already real, atomic, and tested at the repository layer (`BranchRepository.setActive`, M5.1) — a company can never reach zero active branches, proven under a same-shape concurrency-sensitive transaction as `SaleRepository`'s oversell protection. `DeactivateBranchUseCase` adds an existence check on top (matching how `ArchiveCategoryUseCase` adds one above `CategoryRepository.setActive`, which has no existence check of its own) and otherwise delegates straight through — proven by `deactivateUseCasePropagatesLastActiveProtectedFromRepository`.

## Current-branch selection

"Current branch" is per-company session state (which branch this device's POS session currently operates against) — deliberately not a new table, since it is conceptually identical to every other single-value setting `retail_settings` already stores. Persisted via `SettingsRepository` under the key `current_branch_id`.

`GetCurrentBranchUseCase` falls back to a deterministic default (lowest-id active branch) whenever nothing is selected yet, or the previously-selected branch has since been archived — proven by `currentBranchDefaultsToDeterministicFallbackWhenNoneSelected` and `currentBranchFallsBackWhenPreviouslySelectedBranchWasArchived`. `SetCurrentBranchUseCase` refuses to select an archived branch (`RepositoryError.ValidationFailed`) — proven by `setCurrentBranchRejectsArchivedBranch`.

## Legacy default-branch backfill (`EnsureDefaultBranchUseCase`)

Both the legacy Python schema and the unified schema leave `sales.branch_id`/`returns.branch_id`/`inventory_balances.branch_id` nullable (table-count-reconciliation.md) — a real legacy row can have no branch at all. `EnsureDefaultBranchUseCase` gives the Milestone 18 full importer (and first-run initialization, before any UI exists) a deterministic fallback:

- If at least one active branch already exists, the default is the **lowest-id active branch** — deterministic, never ambiguous between two calls, and deliberately not "most recently created," so the same company always backfills to the same branch regardless of call order (proven by `ensureDefaultBranchReturnsLowestIdActiveBranchWhenSeveralExist`, which inserts two branches and confirms the lower-id one — not the one with the later `created_at` — wins).
- If none exists yet, exactly one canonical branch named `"Main Branch"` is created and becomes the default. A second call on the same company returns the *same* branch, not a duplicate (proven by `ensureDefaultBranchCreatesExactlyOneMainBranchOnFirstCall`).

**Real, acknowledged limitation, not silently ignored**: two concurrent first-ever calls for the same company (no branch exists yet) could each observe an empty `listActive` and each create a distinct `"Main Branch"` row — `EnsureDefaultBranchUseCase` does not wrap its read-then-maybe-insert in a transaction the way `BranchRepository.setActive`/`InventoryRepository.adjustStock` do for their own invariants. This was a deliberate scoping decision, not an oversight: the realistic triggers for this use case (single-device first-run initialization, and the single-threaded Milestone 18 import) never exercise concurrent same-company branch creation. True multi-device simultaneous first activation is a narrow edge case, tracked for a future hardening pass rather than fixed here.

## M5.5.11 addendum — active-cart/current-Branch integration

Real gap found and closed: M3's original `financial.Cart` had no Branch concept at all. `Cart.branchId` (new field) is captured once at cart creation and never changed by any of `Cart`'s own pure transformations (`addLine`/`updateLineQuantity`/`applyLineDiscount`/`removeLine` all `copy()` without touching it) — "cart retains its originating Branch," proven by `CartTest.kt`'s three real tests.

The remaining M5.5.11 rules are enforcement responsibilities that already belong to existing, tested code, not new `Cart` logic:

- **"Current branch required for branch-scoped inventory actions"** — every M5.5 inventory use case (`AdjustInventoryUseCase`, `ReconcileInventoryUseCase`) already takes an explicit `branchId` parameter, never reads a mutable global — "capture the canonical Branch identifier at use-case invocation" is already how these are shaped.
- **"Branch switch blocked during an active cart"** — a UI/ViewModel-layer concern (Milestone 6, no ViewModel layer exists yet) built on `SetCurrentBranchUseCase` (M5.4), which already exists and is already tested; no new shared-core logic was needed to express the rule itself.
- **"Sale finalization revalidates that Branch"** — exactly what `sale-return-inventory-integration.md` documents: the future `SaleRepository` will call `BranchRepository.getById(...).isActive` at finalization time, never trust a Branch snapshot the UI loaded earlier. The real primitive it will call is already built and tested (`AdjustInventoryUseCase`/`InventoryRepository.adjustStock` both re-validate branch state fresh).
- **"UI query parameters cannot override the authorized/current business context"** — every repository and use case in M5.1-M5.5 is `companyId`-parameterized from the *caller's authenticated context*, never inferred from request data; this is a structural property of the whole layer, not a rule specific to Branch/cart.

## Real, executed evidence

`./gradlew :shared:testDebugUnitTest`: 112/112 (102 M5.3 baseline + 10 new `BranchUseCasesTest` cases) as of M5.4; extended in M5.5.11 by `CartTest.kt`'s 3 cases (see stock-concurrency-report.md / product-lifecycle-report.md for the full current M5.5 count). `./gradlew :androidApp:assembleDebug`: BUILD SUCCESSFUL.
