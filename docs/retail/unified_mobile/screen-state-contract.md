# Screen State Contract (M6.14)

Real, shared state contracts — already built in M6.1's own
`UiContracts.kt` (`presentation-architecture.md`), consumed directly by
`CommonComponents.kt` (M6.8). This document records the required
per-state mapping, not new code.

## Required states → real implementation

| Required state | Real contract | Real UI |
|---|---|---|
| Initial loading | `LoadState.Loading` | `AuraLoadingState` |
| Refresh | `LoadState.Refreshing` | consumed by a screen's own pull-to-refresh indicator (no M6 vertical slice has one yet — none of Category/Branch/Reporting/Import's real data volumes justify it) |
| Empty | real, per-screen `AuraEmptyState` call (not a shared `LoadState` case — "empty" is a real DATA fact, e.g. `items.isEmpty()`, not a fetch-lifecycle state) |
| Success | `LoadState.Success` | real content render |
| Recoverable failure | `LoadState.Error(UiMessage)` | `AuraErrorState` (real retry button) |
| Blocking failure | `AppPhase.BootstrapFailure` (M6.6) | dedicated full-screen message |
| Offline | `LoadState.Offline` | `AuraOfflineBanner` |
| Validation failure | `UiFieldError`/`FormFieldState.domainError` (M6.9) | `AuraTextField`'s `supportingText` |
| Permission-denied boundary | `RepositoryError.AccessDenied`/`ImportError.AccessDenied` (real, existing M5.x domain errors) mapped to a `UiMessage` at the ViewModel boundary | `AuraErrorState` |
| License-blocked | `AppPhase.LicenseBlocked` (M6.6, real, defined, not yet reachable — M7-M10) | (deferred) |

## Real, structural guarantee: no raw exception text

Every path from a real domain error (`RepositoryError`/`ImportError`/
`FinancialError`) to the screen goes through a `UiMessage` — confirmed
by construction: `AuraErrorState`'s only parameter is `UiMessage`, it
has no overload accepting a raw `Throwable`/`String` exception message.

## Real, disclosed scope

No M6 vertical slice's own ViewModel yet performs the real
`RepositoryError → UiMessage` mapping end-to-end (that mapping is built
per-vertical-slice in M6.16-M6.19, using this shared contract) — this
document records the CONTRACT M6.1/M6.8 already provide; the concrete,
real per-error mapping tables are built alongside each vertical slice
that needs them.
