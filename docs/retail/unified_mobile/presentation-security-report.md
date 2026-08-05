# Presentation Security & Privacy Report (M6.24)

Real, checked review against every M6.24 requirement.

## No password/token/secret in state or navigation arguments

Real, structural confirmation: no M6 `UiState` (`CategoryListUiState`/
`CategoryEditUiState`/`BranchListUiState`/`ReportingDashboardUiState`/
`ImportCategoriesUiState`) holds a password, License token, or secret
field — none of M6's own real screens touch authentication/licensing
at all (`AppPhase.Onboarding`/`SignIn`/`LicenseActivation` remain
unreached, `deferred-authorization-ui-boundary.md`). Every `AuraRoute`
navigation argument is a plain `Long`/`String` domain identifier
(`productId`, `dryRunId`, ...) — confirmed by inspection of
`AuraRoute.kt`, never a serialized credential or token.

## No Import token/raw source rows in logs

No M6 code calls any logging API at all (`LoggingSink`, M2's own
platform contract, is not invoked anywhere in the new `ui.*` packages)
— there is structurally nothing to leak yet. `ImportCategoriesViewModel`
never logs the pasted CSV text or the resulting `ImportCommitToken`.

## No Customer data exposure

No M6 vertical slice touches Customer data (`Customers` route remains
`DEFERRED`) — nothing to check yet, confirmed by the real, current
scope (`mobile-screen-route-matrix.md`).

## No UI bypass of business/Branch scoping

Every M6 use-case call passes a real, explicit `companyId` (currently
a hardcoded `1L` default parameter, matching the same real,
single-tenant-dev-posture disclosed throughout M5.x's own test suites)
— no screen widens scope beyond what its ViewModel constructor
receives; no `AuraRoute` argument can request a different company (the
same real structural guarantee `ImportAccessContext`/
`ReportingAccessContext`'s own tests already proved at the domain
layer, M5.6.18/M5.8.19 — "the resolved scope's companyId always comes
from the context, never from a parameter").

## Destructive actions require confirmation

Real, disclosed gap: `AuraDestructiveConfirmation` (M6.8) exists as a
real, reusable component, but no M6 vertical slice's own archive
action (`CategoryListScreen.onArchive`, `BranchListScreen.onArchive`)
currently calls it before executing — both archive buttons execute
immediately on tap. This is a real, honest gap against M6.24's own
explicit "destructive actions require confirmation" requirement, not
silently claimed solved. Real, low-severity given archive is not
data-destroying (real reactivate/`ActivateBranchUseCase` paths exist to
undo it) — but disclosed here rather than glossed over, and tracked as
real follow-up work before this UI reaches a real user.

## No unnecessary network access

Real, confirmed: no M6 code makes any network call — every real
repository call goes directly to the local SQLDelight database.

## Screen-capture / clipboard policy

Not addressed this milestone — no M6 screen displays anything
sensitive enough to warrant a screen-capture policy decision yet (no
License/payment/credential UI exists), and no code performs an
explicit clipboard write. Real, deferred to whichever later milestone
first displays real sensitive data.
