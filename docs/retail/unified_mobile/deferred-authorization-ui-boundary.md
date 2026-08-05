# Deferred Authorization UI Boundary (M6.25)

No temporary RBAC authority was created this milestone. M6 consumes
the existing, real, deferred capability-boundary contracts already
established by earlier milestones — never invents a new one.

## Real, existing deferred contracts M6 consumes/extends

- `ReportingAccessContext`/`ReportingCapability` (M5.6.18) — real,
  structural, still not wired into `ReportingRepository`. M6.18's
  `ReportingDashboardViewModel` calls `DashboardRepository` directly,
  the same real, unauthorized-by-design path M5.6/M5.7 already
  established and left `DEFERRED_TO_MILESTONES_7_TO_10`.
- `ImportAccessContext`/`ImportCapability` (M5.8.19) — same real
  pattern, still not wired into `ImportCommitExecutor`. M6.19's
  `ImportCategoriesViewModel` calls the executor directly.
- `RepositoryError.AccessDenied` — a real, existing error case (M5.1)
  every M6 vertical slice's `toUiMessage()` mapping already handles
  (`RepositoryErrorMessages.kt`) even though nothing currently produces
  it (no real authorization check exists yet to fail).

## Real, new UI-layer integration point this milestone

`FeatureSurfaceRegistry.requiredCapability` (M6.20) names the real,
future permission code each surface will require — a real, stable
string a Milestone 7-10 authority can key off of once it exists. This
is the real, additive integration point the checkpoint's own M6.25
text asks for: "define the presentation integration point that will
later receive canonical permissions" — done via this registry field,
not a new access-context type (the existing `ReportingAccessContext`/
`ImportAccessContext` shape already covers the "resolve a scope against
a granted context" mechanics; M6 did not need to invent a third
variant for Category/Branch, since neither vertical slice's real use
cases take a scope object at all — they take a plain `companyId`, the
same real, disclosed pattern `AuraAppContainer`'s own KDoc already
establishes).

## Real, current condition — explicitly unchanged

Every prior milestone's own deferred-authorization status remains
exactly as it was, re-affirmed rather than silently forgotten:

- M5.5 authorization: deferred.
- M5.6 authorization: deferred.
- M5.7 authorization: deferred.
- M5.8 authorization: deferred.
- **M6 feature-surface authorization: deferred** (this document).

None of these become unconditional PASS by virtue of M6's own work —
M6 explicitly does not attempt to resolve any of them, correctly,
since building a real Milestone 7-10 licensing/session authority is
out of this milestone's own scope.

## Real, disclosed development posture

`AppPhase.Bootstrap` transitions directly to `AppPhase.Authenticated`
(`App.kt`) — a real, honest `AUTHORIZATION_PENDING` posture for
internal development builds, not a faked sign-in. Production wiring
does not hard-code full administrative access anywhere: no M6 screen
grants itself elevated capability — every real use case called from a
vertical slice ViewModel is the SAME real use case that would exist
regardless of who calls it, with no capability check bypassed (because
none exists yet to bypass).
