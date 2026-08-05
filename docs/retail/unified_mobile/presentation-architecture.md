# Shared Presentation Architecture (M6.1)

Real, unidirectional state architecture — `commonMain`, package
`com.actionaura.retail.presentation`. Proven by `AuraViewModelTest.kt` (4/4).

## Flow

```
User action → AuraViewModel (concrete subclass) → shared use case/repository
    → setState { reducer } → immutable UiState (StateFlow) → Compose UI
                            → sendEffect(...) → one-time UiEffect (Channel/Flow)
```

## `UiContracts.kt` — the shared vocabulary

- **`UiMessage`** — `key` + `args` + `UiMessageSeverity`, never raw
  English text. Screens map `key` through the M6.11 localization
  authority; `RepositoryError`/`ImportError`/`FinancialError` domain
  results are converted to a `UiMessage` at the ViewModel boundary,
  never displayed directly.
- **`UiFieldError`** — one form field's real validation state.
- **`LoadState`** (`Idle`/`Loading`/`Refreshing`/`Success`/`Error`/`Offline`)
  and **`SubmissionState`** (`Idle`/`Submitting`/`Success`/`Error`) —
  deliberately separate: `LoadState` is content-fetch lifecycle,
  `SubmissionState` is a user-initiated write, matching M6.14's own
  required state list exactly.
- **`PaginationState<T>`** — `hasMore` is the real repository-reported
  signal, never inferred from `items.size`.
- **`SearchState`** — `rawQuery` (what the field shows) vs.
  `appliedQuery` (what the last real repository call used) are
  deliberately distinct, so a fast typist never sees a result flash
  from a stale, already-superseded query.
- **`SelectionState<K>`**, **`ConfirmationState<T>`** — generic
  multi-select and pending-destructive-confirmation state.
- **`UiEffect`** — a marker interface for one-time events. Never stored
  inside `UiState` itself (a `StateFlow` re-delivers its last value to
  every new collector, which would replay a stale navigation/snackbar
  event on recomposition or process restoration) — delivered
  exclusively through `AuraViewModel.effects`.

## What is explicitly NOT used (per the checkpoint's own prohibitions)

- No mutable global screen state — every `UiState` is an immutable
  `data class`, replaced wholesale via `setState`.
- No raw SQLDelight rows in `UiState` — every screen's state holds
  only domain types (`Product`, `Category`, ...) or presentation-only
  derived types, never a generated `Select*` row class.
- No direct repository calls from Composables — only from inside an
  `AuraViewModel` subclass (`AuraDependencyGraph`/`AuraAppContainer`,
  M6.3, is the only place a repository reference is even constructed
  outside a ViewModel).
- No platform `ViewModel` class used as the "shared" authority directly
  — `AuraViewModel` (M6.2) IS the real, official
  `androidx.lifecycle.ViewModel`, but every screen depends on the
  shared `AuraViewModel` base, never a platform-specific subclass.
- No hard-coded English domain errors, no Android `Context` in
  `commonMain` — confirmed by construction (no such import exists in
  any file under `presentation/`).
