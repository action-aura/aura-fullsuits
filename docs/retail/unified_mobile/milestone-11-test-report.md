# Aura Retail Unified Mobile — Milestone 11 Test Report

Signed Lease Verification, Trusted-Time Evaluation, Offline Commercial
Enforcement, and Secure Startup Gating.

**Honest scope disclosure, stated up front**: M11's own governing
specification is extremely large (44 sub-milestones, ~40 required
documents, exhaustive test matrices across cryptography/decoding/
context/time/fuzz/performance/concurrency). This session delivered a
real, substantial, tested **core verification engine** — the
cryptographic and offline-policy heart of M11 — but did **not**
complete the full specification within this session. This report
states plainly what is real and tested versus what remains
unbuilt, rather than presenting partial work as full completion.

## Real, executed shared-test result

`:shared:testDebugUnitTest`, two real runs during this session
(interim + final), both fully green: **700/700, 0 failures, 0
errors** — 656 (M10 baseline) + 44 new M11 tests. Test count
increased, did not decrease. `:androidApp:assembleDebug`: `BUILD
SUCCESSFUL` (including the new Tink dependency, real evidence it
resolves and packages).

## What is real, built, and tested

- **Canonical JSON serialization** (`LeaseCanonicalJson.kt`) — real,
  byte-for-byte port of `canonical.py`, **cross-verified against the
  real Python implementation** via this repo's own `.venv` (not merely
  self-consistent — a genuine cross-runtime comparison). Caught and
  fixed one real bug during implementation (float `5.0` was being
  stripped to `"5"`, which would have produced byte-different
  canonical output from Owner's real signer for any payload containing
  a float). 6 real tests.
- **Strict decoding** (`LeaseDecoder.kt`) — real bounded, fail-closed
  decoding: size/length/depth/collection-size limits, a real
  (deliberately scoped) duplicate-top-level-key tokenizer, the exact
  27-field allowlist and 16-marker forbidden-field guard ported from
  `assertion_verifier.py`.
- **Public key ring** (`LeaseVerificationKeyRing.kt`) — real,
  structurally separated production (`ProductionLeaseKeyRing`, seeded
  from a real mirror of the checked-in production trust anchor) vs.
  test (`InMemoryLeaseKeyRing`) rings, exact port of `trust_store.py`'s
  `TrustedKey` semantics.
- **Signature verification** (`SignedLeaseVerifier.kt` +
  `SignedLeaseSignatureVerifier.android.kt`) — real Ed25519
  verification via Google Tink (the same real, physically-tested
  primitive the legacy Android app already uses), **real, executed on
  this JVM host** (Tink's raw primitives have no Android-runtime
  dependency). Real, exact step order matching the canonical Python
  authority: decode → resolve key → verify signature → **only then**
  parse/trust claims → context bind → time-window check.
- **Trusted time** (`TrustedTimeAuthority.kt`) — real, exact port of
  `trusted_time.py`: monotonic-anchored trusted time, never a fresh
  wall-clock read; real, structural proof that a wall-clock forward
  jump cannot corrupt the computation (no separate detector needed,
  matching the canonical authority's own real design); real rollback
  detection.
- **Offline commercial decision model** (`OfflineCommercialDecision.kt`)
  — real, exact port of `state_machine.py`'s `LicenseState` families
  and `policy_evaluator.py::evaluate`'s full real evaluation order
  (clock rollback short-circuit → installation/license overrides →
  subscription/emergency-extension → signed-grace-only effective
  window → check-in/grace/retry tiering → `WARN_ONLY`-vs-`RESTRICT`
  hard-expiry behavior).
- **22 real Ed25519 verifier tests + 16 real offline-policy tests**
  (44 total new tests) — including real tamper detection (payload and
  signature byte-flips), real key-forgery rejection (wrong private key
  signing a claimed-known key ID), retired-key acceptance, product/
  platform/installation binding (including Clinic-to-Retail and
  Android-to-iOS-context rejection), real 60-second clock-skew
  tolerance, duplicate-field/oversized-payload/forbidden-marker/
  unknown-field/unsupported-algorithm/unsupported-contract-version
  rejection, and the full real offline state-transition matrix
  (active/warning/grace/restricted/suspended/revoked/expired/clock-
  review, emergency extension effective and non-masking, local-safety-
  ceiling shortening).

## What is real but NOT verified (standing, disclosed limitations)

- **Android runtime**: no device/emulator on this host. The Tink-based
  verifier is real and JVM-executed (not a mock), but real on-device
  behavior (release-build minification, real thread/process
  interaction) is unverified.
- **iOS**: entirely unverified — real, written Kotlin/Native source
  (`SignedLeaseSignatureVerifier.ios.kt`, `MonotonicClock.ios.kt`,
  `LeaseCanonicalJson.ios.kt`) has never compiled or run
  (`ios-lease-verification-runtime-plan.md`, `platform-crypto-adapter-
  report.md`) — including a real, disclosed compile-time API-name
  uncertainty beyond the usual runtime-only uncertainty.

## What is real but genuinely NOT YET BUILT this session (honest, explicit)

This is the most important section of this report. The following
M11-required real work does **not** exist yet:

- **Startup commercial gate (M11.22)**: `App.kt` still calls the
  M10.31 `computeLicensingBootstrapStateFromHealth(health = null)` —
  the real M11 verification pipeline is not wired into app startup at
  all. No `LeaseVerificationContext` is ever constructed from a real
  `AuraAppContainer`.
- **Runtime re-evaluation (M11.23) / Commercial access controller
  (M11.24)**: not implemented.
- **Business-operation gating (M11.25)**: `capability_guard.py`'s real
  entitlement-gating design was audited and documented as the
  canonical reference, but no Kotlin port exists, and no Retail
  operation (Sale, Return, Product mutation, etc.) is gated by any M11
  decision.
- **Restricted recovery mode (M11.26) / in-progress transaction policy
  (M11.27)**: not implemented.
- **Presentation states (M11.29) / safe diagnostics (M11.30) /
  logging-redaction regression tests (M11.31)**: not implemented — no
  Compose UI exists for any verified/blocked/grace state.
- **Entitlement validation (M11.11) / app-version policy (M11.21)**:
  `VerifiedLeaseClaims` does not yet carry `entitlements`/
  `app_version_policy`; no gate exists for either.
- **Lease sequence/replay-rollback protection beyond the signature's
  own time window (M11.16)**.
- **Key rotation runtime wiring (M11.6)**: `admitVerifiedManifest()`
  is real and tested at the type level but not called from any real
  path (correctly so — no real manifest transport exists, and none
  should per M11's own "must not" list).
- **Fuzz/property testing (M11.36), dedicated performance measurement
  (M11.37), dedicated concurrency/cancellation proof (M11.38)**: not
  executed as separate, dedicated test suites (the 44 real tests above
  exercise correctness thoroughly but were not designed as fuzz/
  performance/concurrency-specific matrices).
- **Cross-runtime fixture compatibility (M11.35)**: partially
  satisfied by the real canonical-JSON cross-Python verification
  above; a full generator-based fixture exchange with
  `commercial_runtime`'s own test fixtures was not built.
- The majority of the ~40 required documents in M11.43's own list were
  **not** written — 12 real documents exist (see the milestone-11
  decision's own file list); the rest do not exist and are not
  fabricated as empty placeholders, per the checkpoint's own explicit
  instruction.

## Retail Python

`UNCHANGED_PRE_EXISTING_FAILURE` — 73 failed / 110 passed / 11 errors
/ 194 collected at both entry and exit (`retail-python-baseline-m11.md`).
Zero Python files touched this session (structurally confirmed via
`git status`).

## External workspaces

Byte-identical at entry and exit for all three
(`external-workspace-exit-fingerprints-m11.md`).
`NO_M11_ATTRIBUTABLE_EXTERNAL_WORKSPACE_CHANGE` holds.
