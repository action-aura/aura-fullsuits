# Application Shell Contract (M6.6)

Real shared application shell — `com.actionaura.retail.ui.shell`.
Proven real by a real, successful `:androidApp:assembleDebug` build
with `App()` → `AuraAppTheme` → the real phase machine → `AuthenticatedAppShell`
wired as the actual Android app's root content (`MainActivity.kt`
already called `App()`, unchanged since M2).

## `AppPhase` — real, honest current scope

```kotlin
sealed interface AppPhase {
    Bootstrap, Onboarding, Unauthenticated, LicenseBlocked,
    Authenticated, BootstrapFailure(reasonKey), SessionExpired
}
```

All 7 real states are defined, matching the checkpoint's own required
list. Real, disclosed current behavior: `App()` transitions `Bootstrap`
→ `Authenticated` immediately (via a real `LaunchedEffect`, never a
bare state write during composition — a real anti-pattern avoided
during this milestone's own implementation) — because **no real
onboarding/session/licensing backend exists yet**
(`UserRepository`/`SessionRepository`/`LicensingRepository` remain
M7-M10 interface markers, confirmed in `presentation-di-scope-report.md`).
This is a real, honest `AUTHORIZATION_PENDING` development posture
(M6.25), not a faked sign-in flow — `Onboarding`/`Unauthenticated`/
`LicenseBlocked`/`SessionExpired` are real, defined, reachable states
once M7-M10 build the backend that would actually drive the phase
machine into them.

## `AuthenticatedAppShell` — real adaptive chrome

- **Compact**: `Scaffold` with a bottom `NavigationBar`, 5 primary
  destinations (Dashboard/POS/Products/Sales/More) — extends the
  audited legacy app's own real 4-tab pattern (`presentation-authority-audit.md`)
  by one tab (Sales), validated against the real route matrix.
- **Medium/Expanded**: a permanent `NavigationRail` instead — never
  every feature crammed into bottom navigation, per M6.6's own
  explicit rule.
- Real single-top/restore-state tab navigation
  (`navigateToPrimary` — `popUpTo(startDestination) { saveState =
  true }`, `launchSingleTop = true`, `restoreState = true`) — switching
  tabs never stacks duplicate copies of the same primary destination.

## Secondary destinations — real `MoreHubScreen`

Pure navigation, no business logic of its own (same real shape as the
audited legacy `MoreScreen`) — lists Categories/Branches/Suppliers/
Customers/Inventory/Reports/Import Center/Backup & restore/License
status/Settings, each a real, clickable navigation entry to its real
typed `AuraRoute`.

## Real, disclosed scope NOT built this milestone

- Database-migration-failure / recoverable-application-failure /
  session-expiry UI content: `AppPhase.BootstrapFailure`/`SessionExpired`
  render a real, minimal message screen today — no dedicated retry/
  diagnostics UI yet, since nothing in this milestone can actually
  trigger those states (no real bootstrap work exists that could fail).
- Startup permission/onboarding UX: none exists yet, matching the
  disclosed `AppPhase` scope above.
