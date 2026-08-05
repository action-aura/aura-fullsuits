# Aura Retail Unified Mobile — Milestone 7 Decision

## Verdict: **CONDITIONAL PASS**

Every audit sub-milestone (M7.1-M7.16, M7.19-M7.21) is complete with
real, cited, executed evidence; the required shared contract models
and fixture suite (M7.17-M7.18) are real, built, and passing. This
stays CONDITIONAL for real, disclosed reasons below — not folded into
an unconditional PASS.

## Gate-by-gate

| Gate | Status | Evidence |
|---|---|---|
| Evidence-priority order followed and documented | PASS | `licensing-authority-source-map.md` |
| Owner License/Subscription/Installation domain fully audited | PASS | `owner-licensing-authority-audit.md` |
| Real License state machine documented, not assumed | PASS | `license-state-machine-contract.md` — 7 real states, no invented `ARCHIVED` |
| Subscription/entitlement/License map complete; device-limit authority resolved | PASS | `subscription-entitlement-license-map.md` — `License.device_limit` identified as sole enforced authority |
| Customer-auth gap analysis complete, nothing implemented | PASS | `customer-authentication-gap-analysis.md` — MISSING, confirmed absent, six-point negative evidence |
| Installation authority contract confirms per-device credential | PASS | `installation-authority-contract.md` |
| Multi-device policy matrix uses only real, cited behavior | PASS | `multi-device-license-policy-audit.md` — no invented default caps |
| Platform authority audit + iOS classification + no-bypass proof | PASS | `platform-authority-audit.md` — `SCHEMA_READY_BUT_UNSEEDED` |
| Remote API contract map complete | PASS | `remote-licensing-api-contract-map.md` |
| Activation idempotency contract, incl. final-slot concurrency | PASS | `activation-idempotency-contract.md` — proven by real concurrency test citation |
| Installation identity/privacy contract rejects invasive fingerprinting | PASS | `installation-identity-privacy-contract.md` |
| Installation credential contract (3 real credential types) | PASS | `installation-credential-contract.md` |
| Offline lease contract audit + sanitized fixtures, no real private keys | PASS | `offline-license-lease-contract-audit.md`, `licensing-contract-fixture-report.md` |
| Release/version contract, honest about what's not live | PASS | `mobile-release-version-contract.md` — no invented min-version enforcement |
| Error contract maps real canonical codes, no renaming | PASS | `licensing-error-contract.md`, `LicensingError.kt` |
| Data-minimization contract + serialization proof | PASS | `owner-data-minimization-contract.md`, `activationRequestSerializesToExactlyTheRealOwnerAllowlist` |
| M7.17 shared commonMain models — immutable, versioned, typed, no raw Map, no platform classes, no secrets in toString, no HTTP execution | PASS | `licensing/*.kt`, confirmed by `LicensingContractTest` toString-redaction tests |
| M7.18 fixture suite — versioned, sanitized, no real secrets | PASS | `LicensingFixtures.kt`, `fixturesAreObviouslyFakeNeverResemblingRealKeyMaterial` |
| Local authorization vs. license entitlement composition defined, not hard-coded | PASS | `local-authorization-vs-license-entitlement.md` — confirmed no allow-all shortcut exists |
| Gap-ownership matrix — every gap classified, none fixed here | PASS | `licensing-gap-ownership-matrix.md` — 12 real gaps |
| Security review — real threats, real mitigations, real residual risk | PASS | `licensing-contract-security-review.md` — 13 threats |
| Required test matrix filled | PASS | `licensing-contract-fixture-report.md` |
| All new shared tests pass | PASS | 564/564 (539 M6 baseline + 2 M6-followup + 23 M7) |
| Android debug APK still builds | PASS | `:androidApp:assembleDebug` `BUILD SUCCESSFUL` |
| No Owner production code modified | PASS | Confirmed — every M7 command against `owner/` was read-only (Explore-agent research, `git status`/`git diff` never issued against it by this session) |
| No Phase 9R work performed, workspace not modified | PASS | `external-workspace-exit-fingerprints-m7.md` — HEAD unchanged, status empty |
| Legacy `AuraEnterprise` repo not modified by this session | PASS, with a disclosed anomaly | Tracked-file diff byte-identical (strong proof); untracked-file count drifted (19→15) for reasons this session's own record cannot explain — see below |
| Unified Mobile branch clean after each commit | PASS | Verified via `git status --short` after each commit |
| Test count increased | PASS | 539 → 564 |

## Why CONDITIONAL, not unconditional PASS

Two real, structural reasons:

1. **M7 deliberately stops at contract models, not implementation** —
   per the governing checkpoint's own scope restriction, no HTTP
   execution, no activation implementation, no secure storage, no
   offline lease verification, and no iOS platform behavior exist yet.
   `licensing-gap-ownership-matrix.md` records 12 real gaps still
   open, none of them in scope for M7 to close. This is by design, not
   a shortfall — but it means the licensing *contract* is now real and
   tested, while the licensing *feature* remains entirely unbuilt,
   correctly deferred to M8 onward.
2. **A real, disclosed anomaly in the legacy `AuraEnterprise`
   workspace was found during M7 exit fingerprinting**
   (`external-workspace-exit-fingerprints-m7.md`): its untracked-file
   count differs from the M7 entry capture (19 → 15) despite this
   session issuing zero write commands against that path. Tracked-file
   content is proven byte-identical, so the core "did this session
   edit anything real" guarantee holds — but the discrepancy itself is
   unexplained by this session's own record and is surfaced for the
   user to independently verify, rather than silently reconciled or
   omitted. This alone would not warrant FAIL (no evidence ties it to
   M7's own work), but it is a real reason this milestone cannot claim
   an unconditional, fully-clean close.

## Why not FAIL

No conflicting contract was left unresolved — every real ambiguity
found (device-limit authority, platform readiness, min-version
enforcement, customer auth) was traced to a single, cited, real
conclusion, not left contradictory. No mobile code depends on invented
server behavior — every M7.17 model field, every M7.18 fixture
scenario, and every error code trace to real, cited Owner code or the
real `commercial_runtime` package; where Owner's real behavior is
absent (min-app-version, customer auth, iOS), the corresponding M7.17
model/doc says so explicitly rather than fabricating a contract for it.

## Proceed to Milestone 8

Per the governing checkpoint: M7 (Owner Licensing Authority Audit,
Mobile Contract Freeze, Multi-Device Gap Analysis, and Canonical
Authorization Integration Design) concludes here with a CONDITIONAL
PASS. Per the checkpoint's own explicit instruction, work stops after
M7 — Milestone 8 (iOS platform/device-policy implementation) and
Milestone 9 (activation orchestration) remain un-started, pending this
milestone's own acceptance and the user's review of the disclosed
legacy-workspace anomaly above.
