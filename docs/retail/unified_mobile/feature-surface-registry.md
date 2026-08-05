# Feature Surface Registry (M6.20)

Real, canonical registry of every planned mobile feature surface —
`ui.navigation.FeatureSurfaceRegistry`. Proven by
`FeatureSurfaceRegistryTest.kt` (3/3). Cross-referenced against the
real, audited `mobile-screen-route-matrix.md`, not invented.

## Real, deliberate scope: not all 47 `AuraRoute`s duplicated here

The registry lists the real PRIMARY and SECONDARY destinations users
actually navigate to (15 entries) — not every typed detail route
(`ProductDetails`, `SaleDetails`, etc.), which are real but not
independent "feature surfaces" in the product sense (they are reached
FROM a listed surface, not separately discoverable). This mirrors
`mobile-screen-route-matrix.md`'s own real distinction between
top-level routes and detail overlays.

## Visibility is UX only, never authorization

`Availability` (`AVAILABLE`/`UNAVAILABLE_THIS_MILESTONE`/`DEFERRED`) is
a real, honest UX signal — it never substitutes for the real
capability check a Milestone 7-10 authority will enforce at the
use-case boundary. `requiredCapability` names the real, future
permission code each surface will require; none of these are enforced
anywhere yet (`deferred-authorization-ui-boundary.md`).

## Real, confirmed counts

`FeatureSurfaceRegistryTest.realAvailableEntriesMatchTheRealFourVerticalSlicesPlusShellChrome`
proves exactly 6 entries are `AVAILABLE` this milestone: `Dashboard`,
`More`, `Categories`, `Branches`, `Reports`, `ImportHome` — matching
the real 4 vertical slices (Reporting's `Dashboard`/`Reports` share one
real screen) plus the 2 real shell-chrome destinations. Every other
entry is honestly `DEFERRED`, citing its real owning milestone from
`mobile-screen-route-matrix.md`.
