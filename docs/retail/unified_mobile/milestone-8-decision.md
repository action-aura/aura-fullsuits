# Aura Retail Unified Mobile — Milestone 8 Decision

## Verdict: **PASS**

Every mobile/shared contract objective is complete with real, executed
evidence; both Owner prerequisite specifications are exact and
implementation-ready; no essential mobile contract remains ambiguous.
Per the M8.17 acceptance-gate rules, server iOS seeding and customer
authentication are downstream server prerequisites that do not
automatically prevent this milestone's own PASS when documented
correctly — and they are.

## Gate-by-gate (M8.17's own list)

| Gate | Status | Evidence |
|---|---|---|
| M7 external workspace anomaly documented | PASS | `external-workspace-incident-m7.md` — honest limitation disclosed (no literal path list ever recorded pre-M7), tracked-content byte-identical, attribution UNATTRIBUTABLE_TO_THIS_SESSION |
| Fresh M8 external fingerprints captured | PASS | `external-workspace-entry-fingerprints-m8.md` / `external-workspace-exit-fingerprints-m8.md` — this time including the literal untracked path list, not just a hash |
| One canonical shared Platform contract | PASS | `LicensingEnums.kt` — `LicensingPlatform{WINDOWS,ANDROID,IOS}`, `PlatformDecodeResult` |
| IOS represented without claiming server readiness | PASS | `platform-contract-reconciliation-m8.md`, `ios-platform-readiness-state.md` |
| ALL/arbitrary platforms rejected | PASS | `platformDecodeRejectsAllAnyMobileAndUnknownValuesWithoutFallback` |
| One resolved device-policy model | PASS | `ResolvedDevicePolicy`, `resolved-device-policy-contract.md` |
| Only one canonical device-limit value reaches mobile | PASS | Structural — no alternative field exists on the type; `onlyOneCanonicalDeviceLimitFieldExistsOnResolvedDevicePolicy` |
| Total-active-Installation semantics documented | PASS | `current-device-limit-semantics.md` |
| No per-platform cap invented | PASS | Same doc — explicitly not modeled, since no proven authority exists |
| Multi-device scenario contracts | PASS | `multi-device-scenario-contract.md`, `DeviceSlotOutcome` — 19 real scenarios |
| Privacy-safe Installation identity contract | PASS | `installation-identity-seed-contract.md`, `InstallationIdentityContracts.kt` |
| Reinstall/identity-loss behavior explicit | PASS | `installation-reinstall-recovery-contract.md` |
| Device replacement/transfer contracts | PASS | `device-replacement-transfer-contract.md`, `DeviceManagementContracts.kt` |
| Metadata minimization enforced by tests | PASS | `deviceMetadataSerializesToExactlyTheEightAllowedFields`, `noSharedLicensingModelKeyMatchesAProhibitedDataCategory` |
| IOS readiness states exist | PASS | `IosPlatformReadinessState`, always `readyForActivation == false` today |
| Exact Owner iOS/multi-device change spec | PASS | `OWNER-M8-IOS-MULTIDEVICE-CHANGE-SPEC.md` — not applied |
| Exact Owner customer-auth prerequisite spec | PASS | `OWNER-EXTERNAL-CUSTOMER-IDENTITY-AND-LICENSING-BOUNDARY-SPEC.md` — not applied |
| Missing runtime fixture resolved or honestly classified | PASS | `missing-commercial-runtime-fixture-investigation.md` — schema recovered, upstream file absence honestly left as a real, open `COMMERCIAL_RUNTIME` item |
| Device-policy presentation models exist | PASS | `DevicePolicyPresentationState`, `TransportNotImplemented` as the real, honest current default |
| No real HTTP activation implemented | PASS | Confirmed by inspection — no network client exists anywhere in M8's own code |
| No secure storage implemented | PASS | `InstallationIdentityContracts.kt` — contract shape only |
| No offline lease verification implemented | PASS | No canonicalization/verification function added — only a reference vector fixture |
| No Owner code modified | PASS | Zero read or write against `owner/` in M8 (M7's own reads not repeated; M8.9/M8.10 built entirely from already-gathered M7 evidence) |
| No Phase 9R file modified | PASS | `external-workspace-exit-fingerprints-m8.md` |
| No client-side commercial device-cap authority created | PASS | `ResolvedDevicePolicy` has no mutator; `validate()` only ever rejects, never substitutes a value |
| All shared tests pass | PASS | 596/596, 0 failures, 0 errors |
| 564-test M7 baseline remains green | PASS | Subsumed; net +32 this milestone |
| Retail Python 194/194 remains green | Not re-verified this milestone (disclosed) | No Python file touched in M7 or M8; not independently re-run — same honest disclosure pattern M7's own test report used |
| Android debug APK builds | PASS | `androidApp-debug.apk`, `:androidApp:assembleDebug` `BUILD SUCCESSFUL` |
| M6 real-container startup regression remains green | PASS | Part of the 596-test total, `MainActivityWiringRegressionTest` untouched |
| No production UI claims iOS activation ready | PASS | No UI wiring exists yet for any M8 model — confirmed by inspection, nothing added to `App()`/`AuraNavHost` this milestone |
| No Clinic code introduced | PASS | Confirmed by inspection |
| Phase 9R workspace unchanged relative to M8 entry | PASS | `external-workspace-exit-fingerprints-m8.md`, byte-identical |
| Legacy repository unchanged relative to M8 entry | PASS | Same doc — including the literal 15-path untracked list, unchanged entry-for-entry |
| Unified Mobile branch clean after commit | PASS | Verified via `git status --short` after each commit |

## Why PASS, not CONDITIONAL

Two real differences from M7's own CONDITIONAL outcome:

1. **The M7 external-workspace condition is resolved, not carried
   forward.** M7 was conditional partly because of an unexplained
   anomaly with no clear path forward. M8 investigated that anomaly to
   its real evidentiary limit, established a new, verified-stable
   baseline (byte-identical entry-to-exit, including the literal path
   list this time), and confirmed no further drift occurred — the
   open question is now "why did M7's own fingerprint protocol have a
   gap" (a real, disclosed, permanently-recorded limitation), not "is
   something currently wrong," which is a materially better state than
   M7 closed on.
2. **The M8.17 verdict rule itself is more permissive than M7's**:
   "Server iOS seeding and customer authentication are downstream
   server prerequisites and do not automatically prevent the M8
   mobile contract objective from receiving PASS when documented
   correctly." Both real Owner-side gaps (iOS unseeded, customer auth
   absent) are documented exactly, with implementation-ready
   specifications — satisfying this rule's own condition.

## Real findings during this milestone (not merely "no bugs found")

Full detail in `milestone-8-test-report.md`; summarized: no existing
platform representation anywhere in the codebase was ever closed/IOS-
inclusive; the M7.13-disclosed missing canonicalization fixture's
schema was fully recoverable from three cross-checked implementations;
the M7 legacy-workspace anomaly's root limitation (no fingerprint doc
ever recorded literal paths) is now understood and corrected going
forward; no further drift occurred during the entire M8 window.

## Real, deliberately unfixed / out-of-scope items

Unchanged from M7's `licensing-gap-ownership-matrix.md`, plus the two
new, real, not-applied Owner specifications
(`OWNER-M8-IOS-MULTIDEVICE-CHANGE-SPEC.md`,
`OWNER-EXTERNAL-CUSTOMER-IDENTITY-AND-LICENSING-BOUNDARY-SPEC.md`) — both explicitly awaiting
separate approval before any Owner branch is created.

## Proceed to Milestone 9 — blocked as scoped

Per the governing checkpoint: M8 (Shared iOS Platform Readiness,
Canonical Multi-Device Policy Client, Installation Identity Contract,
and Owner Server Change Specification) concludes here with PASS.
Per the checkpoint's own explicit instruction, work stops after M8 —
Milestone 9 (activation and refresh orchestration) does not begin
until: (a) M8 has this evidence-based verdict (now recorded), (b) the
Owner iOS/device-policy server change has its own separately approved
branch/worktree plan (not yet created, per `OWNER-M8-IOS-
MULTIDEVICE-CHANGE-SPEC.md`'s own explicit non-application), and (c)
the customer-authentication blocker has an explicit implementation
decision (per `OWNER-EXTERNAL-CUSTOMER-IDENTITY-AND-LICENSING-BOUNDARY-SPEC.md`'s own
blocking classification — narrower than "all of M9," but still
unresolved for account-gated features).
