# iOS Platform Readiness State (M8.8)

Shared iOS platform readiness states — makes explicit, at the type
level, that `LicensingPlatform.IOS` existing as a client-side contract
case (M8.0) does not mean iOS activation is available.

## Real states

`IosPlatformReadinessState` (`shared/.../licensing/
DevicePolicyContracts.kt`), a `Set<IosReadinessFlag>`-backed model —
each flag independently true/false, since readiness is additive
evidence, not a single linear progression:

```
CONTRACT_SUPPORTED
OWNER_PLATFORM_UNSEEDED
PRODUCT_PLATFORM_MAPPING_MISSING
RELEASE_MAPPING_MISSING
SERVER_ACCEPTANCE_NOT_VERIFIED
CLIENT_BUILD_NOT_VERIFIED
CLIENT_RUNTIME_NOT_VERIFIED
READY_FOR_ACTIVATION
```

## Current, real, evidence-based state

```
CONTRACT_SUPPORTED               = true   (M8.0's own LicensingPlatform.IOS addition)
OWNER_PLATFORM_UNSEEDED          = true   (platform-authority-audit.md: no IOS owner_platforms row)
PRODUCT_PLATFORM_MAPPING_MISSING = true   (no ProductPlatform row for IOS exists — same audit)
RELEASE_MAPPING_MISSING          = true   (no ProductVersion row targets an IOS platform_id — same audit)
SERVER_ACCEPTANCE_NOT_VERIFIED   = true   (JSON schemas hard-enum to WINDOWS/ANDROID)
CLIENT_BUILD_NOT_VERIFIED        = true   (no macOS/Xcode on this host — ios-presentation-readiness.md, M6, unchanged)
CLIENT_RUNTIME_NOT_VERIFIED      = true   (same reason)
READY_FOR_ACTIVATION             = false  (derived: false whenever any UNSEEDED/MISSING/NOT_VERIFIED flag is true)
```

This is real, unchanged from M7.8's own `SCHEMA_READY_BUT_UNSEEDED`
finding — M8 does not re-investigate Owner (no Owner code was read or
modified in M8), it only encodes the same real conclusion as a typed,
queryable model instead of prose.

## Real invariant

`READY_FOR_ACTIVATION` can only be `true` when every other flag above
except itself is `false` (or, symmetrically, is the only flag
present). The shared model enforces this — `IosPlatformReadinessState.
current()` factory always returns the real, hard-coded current
snapshot above; there is no code path that lets presentation logic set
`READY_FOR_ACTIVATION = true` while any blocking flag remains set.

## Production vs. development UI

- **Production UI must not present iOS activation as available.**
  `device-policy-presentation-contract.md` (M8.12)'s own iOS-readiness
  presentation state consumes this model and only ever renders a
  "coming soon" / not-yet-available affordance while
  `READY_FOR_ACTIVATION` is `false`.
- **Development UI may show a protected diagnostic state** — i.e. a
  debug-build-only screen that lists the real flags above verbatim,
  useful for internal QA tracking readiness progress — but this is
  explicitly gated behind a debug-build flag, never reachable in a
  production build, and never itself claims activation works.

## Update discipline

This document's "current, real, evidence-based state" table must only
change when new *executable* evidence supersedes it (a real Owner
migration seeding an `IOS` `owner_platforms` row, a real
`ProductPlatform`/`ProductVersion` row, a real schema-enum change, or
real macOS/Xcode build+run evidence) — never by narrative confidence
or a plan to do so later.
