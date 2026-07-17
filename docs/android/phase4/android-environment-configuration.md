# Phase 4M — Android Environment Configuration (re-verification)

Status: **PROVEN**, re-verified this phase, unchanged from
`docs/android/environment-configuration.md` (the canonical, still-accurate
source for the three-build-type table). Not duplicated verbatim here.

## What this phase re-confirmed

- `debug`/`staging`/`release` build types exist for both products, each
  with distinct `applicationIdSuffix`, `debuggable`, and
  `BuildConfig.BUILD_ENV` (verified by reading `app/build.gradle` directly
  this phase, and by `staging`/`release` both building successfully — see
  the build reports).
- `PRODUCT_CODE` (`"AURA_RETAIL"` / `"AURA_CLINIC"`) is already set as a
  `buildConfigField`, matching Phase 4J's allowed product-code list exactly.
- `identity/CommercialIdentity.kt` (both products) already exists as the
  non-secret placeholder for `installation_id`/`tenant_id`/environment —
  present from the prior migration phase, not modified this phase (no
  reason to touch it: it contains no real customer_id, no real tenant_id,
  no license, no owner credentials, confirmed by reading it).
- Every build type still talks only to the on-device loopback server —
  no remote "Owner Server" endpoint exists yet in any build type
  (unchanged, correctly out of scope per this phase's explicit
  exclusions).
- Logging level: still N/A — zero `Log`/`println` calls in either tree,
  now additionally regression-guarded by `PrivacyLoggingGuardTest.kt`
  (Clinic) rather than relying only on a one-time manual grep.

## What changed this phase

Nothing in the environment-configuration surface itself. The readiness
-check fix (`main.py`) and the financial-contract fixes (Kotlin `Models.kt`)
are orthogonal to build-type/environment configuration and apply
identically across `debug`/`staging`/`release`.
