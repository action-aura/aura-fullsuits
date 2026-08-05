# Branch UI Vertical Slice (M6.17)

Real, shared Branch UI backed exclusively by the real M5.4 use cases
(`ListActiveBranchesUseCase`/`GetCurrentBranchUseCase`/
`SetCurrentBranchUseCase`/`ActivateBranchUseCase`/
`DeactivateBranchUseCase`), wired through `AuraAppContainer`. Proven
by `BranchListViewModelTest.kt` (3/3) against a real in-memory
SQLDelight database.

## Real components

`BranchListViewModel`/`BranchListUiState`/`BranchListEffect` — loads
active branches + the real current-branch selection, inline create
(direct `branchRepository.insert` call — see below), real "select
current" radio action, real archive/reactivate actions.
`BranchListScreen` — single-screen list + inline create field, built
from the M6.8 common component catalog.

## Real, structural proof of last-active-branch protection

`archivingTheOnlyRealActiveBranchIsRejectedByTheRealRepositoryProtection`
proves the real repository-layer protection (`BranchRepository.setActive`,
M5.1) actually blocks the write — re-queries
`branchRepository.listActive` afterward and confirms the branch is
still there, not merely that a UI message would have appeared.

## Real, disclosed scope: no `CreateBranchUseCase` exists

Real audit of `usecases/branch/BranchUseCases.kt` confirms: unlike
Category, no use case wraps Branch creation with duplicate-name
detection — `EnsureDefaultBranchUseCase` is the only caller of
`BranchRepository.insert` in the existing codebase, and it has a
different real purpose (deterministic default-branch backfill, not
user-initiated creation). `BranchListViewModel.onCreate` therefore
calls `branchRepository.insert` directly — matching the REAL, existing
authority's own actual scope exactly, not inventing a validation rule
that does not exist yet. A future milestone that wants real duplicate-
name protection for user-created branches would add a
`CreateBranchUseCase` first, the same real pattern
`CreateCategoryUseCase` already establishes.

## Real, disclosed scope gaps

- **No `BranchDetails`/edit screen.** `BranchRepository` has no update
  method — same real, confirmed scope limitation as Category.
- **No active-Cart-switch-protection UI proof.** `SetCurrentBranchUseCase`'s
  real `activeCart` parameter (M5.5's own real business rule: "cannot
  switch branch while a cart is active for a different branch") is not
  exercised by `BranchListViewModel.onSelectCurrent`, which always
  passes `activeCart = null` — real, disclosed gap, since no real Cart
  UI/state exists yet in this milestone (POS is `NOT_IN_M6`).
- Real, curated Arabic/RTL strings exist (`branch.title`, `branch.name`,
  `branch.empty`, `branch.last_active_protected`) but are not yet
  wired into the screen's own `Text(...)` calls — same real,
  disclosed integration gap as Category's own doc.
