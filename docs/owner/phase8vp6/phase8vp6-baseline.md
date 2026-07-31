# Phase 8V-P6 — Baseline

## Entry gate

Real device: Infinix X6528, `adb devices -l` → `1122070476060894 device`. `getprop`: manufacturer
INFINIX, model Infinix X6528, release 13, sdk 33. `adb shell date`: `Fri Jul 31 07:47:59 +03 2026`.
Stable, gate PASS.

## Git baseline

HEAD `cf13d6e`, tree clean, branch `master`. Conditional tag
`aura-owner-commercial-ops-phase8-conditional-complete` → tag object `7150564af95b...`, dereferences
to commit `f593bce7...` (unchanged, correct). Final tag `aura-commercial-licensing-operations-phase8-complete`
does not exist. Original `AuraEnterprise` repo not opened this session.

## Prior docs read

`docs/owner/phase8vp5/{phase8-final-decision,scenario3-past-due-grace-restricted-final,
scenario5-emergency-extension-and-expiry-final,raw-wire-evidence,local-deactivation-reactivation-decision}.md`
(authored last session, content already in working memory, re-verified against source rather than
re-read verbatim). `docs/owner/phase8vp2/scenario7-resolution-report.md` (the existing
`Subscription.device_allowance -> License.device_limit` sync fix, for continuity with Scenario 7).

No file named `PHASE8VP5-REMAINING-PHYSICAL-CLOSURE-HANDOVER.md` exists (the actual phase8vp5 set uses
different filenames, listed in `docs/owner/phase8vp5/`) -- noted as a real discrepancy from the request
text rather than silently substituted without disclosure.

## Source re-inspected this session (see `commercial-enforcement-root-cause.md` for the finding)

`owner/app/licensing_service/checkin.py`, `owner/app/commercial_ops/state_resolution.py`,
`owner/app/commercial_ops/reconciliation.py`, `owner/app/licensing_service/assertions.py`,
`owner/app/commercial_ops/assertion_fields.py` (re-confirmed from last session),
`commercial_runtime/licensing_contracts/policy_evaluator.py`,
`commercial_runtime/licensing_contracts/assertion_verifier.py`.

## Current automated baseline (carried forward from Phase 8V-P5, to be re-run at the end)

Owner 395/395, commercial_runtime+licensing_contracts 219/219, Retail 194/194, Clinic 135/135 =
943/943, 0 failures.

## Severity reassessment (per this session's explicit instruction not to assume zero P1)

Two real, confirmed P1s open at the start of this session:

1. `EmergencyExtension` business records are not functionally wired into `OfflinePolicy`'s technical
   grace fields (found and disclosed Phase 8V-P5).
2. `Subscription.status` and `License.status == "SUSPENDED"` are already present in every real signed
   assertion payload (`subscription_status`, `license_status` fields, confirmed in the allowlist and
   parsed into `AssertionEvidence`) but are **not consumed** by
   `commercial_runtime/licensing_contracts/policy_evaluator.py::evaluate()` -- only
   `license_status in (EXPIRED, REVOKED)` and `installation_status in (SUSPENDED, REVOKED)` are
   checked. `subscription_status` is a dead field client-side; `license_status == "SUSPENDED"` is
   missing from the hard-state list entirely. This is the precise mechanism behind Phase 8V-P5's
   empirical finding (a real $100 sale completed while the license was genuinely SUSPENDED in Owner's
   database).

Both are addressed in this session -- see `commercial-enforcement-root-cause.md` and
`emergency-extension-wiring-design.md`.
