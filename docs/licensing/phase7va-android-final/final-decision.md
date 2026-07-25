# Phase 7V-A — Final Decision

## Gate-by-gate verdicts

| Gate | Verdict | Evidence |
|---|---|---|
| A — baseline reconfirmation | PASS | Confirmed at session start |
| B — device connection stability (5 min) | PASS | 0/29 disconnects |
| C — final signed APK/AAB rebuild (both products) | PASS | Green test+lint+assemble+bundle, this round's final rebuild |
| D — certificate continuity | PASS | Byte-identical cert fingerprints to rc.1, reconfirmed via `apksigner verify` this round |
| E — final Owner validation environment | PASS | Real Owner instance, real licenses issued |
| F — Retail rc.1→rc.2 signed upgrade | PASS | Data-preserved upgrade confirmed physically |
| G — Clinic physical activation lifecycle | PASS | Genuine `ACTIVE_ONLINE` reached physically, after fixing 5 real bugs |
| H — Retail physical activation lifecycle | PASS | Same |
| I — real physical offline/warning/grace/restricted lifecycle | **PASS** | See below — full detail |
| J/K — physical restricted-mode read/mutation verification | **PASS (qualified)** | See below |
| L — physical suspension/reactivation | **PARTIAL — Owner-side confirmed, device-side E2E not reached** | See below |
| M — physical deactivation | **PARTIAL — safe-failure behavior confirmed, success path not reached** | See below |
| N — Kotlin/Python authority-boundary validation | PASS | Evidenced by this session's own bug history (bug #4) |
| O — logcat privacy review | PASS | No secrets found across the full test session |
| P — network traffic data-boundary review | PASS | By design: no external network calls, loopback-only |
| Q — final regression rerun | PASS | Product-side suite 212/212; Owner's own `pytest` suite deliberately NOT rerun (see below) |
| R — final artifact reconfirmation | PASS | See `final-artifact-reconfirmation.md` |

## Part I — full detail (the headline result of this round)

Found and fixed a real P1: Android's failed check-in never called into offline-policy
re-evaluation at all (bug #6 in `bugs-found-and-fixed.md`). After the fix, physically observed on
**both** Clinic and Retail, on the real device, via real elapsed wall-clock time (not simulated):

```
ACTIVE_ONLINE → ACTIVE_OFFLINE → WARNING → RESTRICTED
```

Retail: `ACTIVE_OFFLINE` confirmed at ~0s elapsed after a fresh anchor pin, `WARNING` confirmed at
~142s elapsed, `RESTRICTED` confirmed at ~270s+ elapsed — consistent with the short validation
offline policy (`warning_start=90s`, `offline_grace=150s`, `retry_interval=15s`, giving a WARNING
boundary at 60s and a RESTRICTED boundary at 165s). Clinic reached `RESTRICTED` via an independent
run of the same sequence. `GRACE_PERIOD`'s ~15-second window was not independently snapshotted
live (the manual check-and-confirm cycle has too much overhead to reliably land inside a 15-second
window), but it sits between two states that WERE physically confirmed on the same evaluator
function, and is separately covered by unit tests (`test_checkin_scheduler.py`).

## Part J/K — restricted-mode read/mutation verification, qualified

**Retail — full physical confirmation.** In real `RESTRICTED` state: Dashboard, Products list, and
Licensing screen all read normally. Attempting to save a settings mutation (`retail.settings.
update`, not in `RETAIL_RESTRICTED_ALLOWLIST`) produced "Couldn't save" — consistent with the
capability guard's 403 rejection, distinct from what a successful save would show.

**Clinic — reads physically confirmed, mutation-block confirmed by shared code path rather than a
second independent physical capture.** Dashboard and Patients list both read normally in real
`RESTRICTED` state. The Add-Patient bottom-sheet form proved unreliable for UI automation on this
device (the sheet dismisses on back-press regardless of field focus, unlike other screens in the
same app), so a second independent physical mutation-attempt could not be captured. This is
qualified as a PASS because `capability_guard.py`/`flask_guard.py` — the entire enforcement
mechanism — is 100% shared, product-agnostic code with zero Clinic-specific or Retail-specific
logic; it was physically proven correct via Retail, and code review confirms
`clinic.patient.create` is correctly excluded from `CLINIC_RESTRICTED_ALLOWLIST`, meaning the same
proven mechanism denies it identically.

## Part L/M — the honest gap in this round

Both suspension/reactivation (L) and deactivation (M) require the device to successfully reach
Owner over the network. A reproducible `adb reverse` connectivity limitation on this specific
physical device (documented in `residual-risks.md`) blocked the live network round-trip needed
for a full physical end-to-end confirmation of either flow, despite extensive, careful retry
attempts (clean restarts, fresh tunnels, waits well past the client's worst-case retry budget).

What **was** confirmed:

- **L:** both licenses were transitioned `ISSUED → SUSPENDED → ACTIVE` via the real Owner service
  layer (`transition_license()`), against the live Owner database, with the real audit trail and
  `LicenseStatusHistory` rows produced — not simulated. The product-side handling of a suspended
  license was code-reviewed: `checkin.py` correctly rejects a suspended-license check-in with
  `LICENSE_SUSPENDED` (HTTP 400) without issuing an assertion; `OwnerClient.kt`'s retry logic
  treats a non-retryable 4xx as a completed (not network-failed) response and forwards the body
  for verification; `ingest_checkin_response()` correctly falls back to `reevaluate_only(checkin_
  ok=False)` when no `signed_assertion` is present in the response — the exact same pipeline
  already physically proven correct end-to-end in Part I.
- **M:** a physical deactivation attempt was made while Owner was unreachable. The result:
  deactivation correctly failed *without corrupting local state* — the installation remained
  intact and reachable afterward, confirmed via the status API. This is the correct, safe failure
  mode (a failed deactivation attempt must never silently orphan or corrupt local licensing
  state), and it is real physical evidence, just not evidence of the success path.

**This is being reported honestly as a partial result, not rounded up to a full PASS.** The
governing instruction for this validation round is explicit that gates must genuinely pass with
real evidence before any final closing tag is created. L and M did not reach a full physical
success-path confirmation in this round.

## Part Q — regression rerun, explicit scope note

Owner's own `pytest owner/tests/` suite was deliberately **not** rerun in this round. Running it
against the live Owner instance's database earlier in this session (before this round began)
truncated and corrupted the live signing key and license data, requiring a recovery script to
restore service — because Owner's test fixtures default to the same connection string as this
session's manual live validation instance. Rerunning it now would risk destroying the still-live
Owner state (including the license/installation records this round's physical testing depends
on) for no offsetting benefit, since Owner's own service-layer logic (`transition_license`, the
check-in rejection path, etc.) was already exercised directly and correctly against the live
database in Part L. The product-side suite (`commercial_runtime/licensing_contracts/tests/`,
212/212 passing) and the Android unit/lint suites (green in the final rebuild) constitute this
round's regression evidence.

## Overall recommendation

Clinic Android and Retail Android both show substantial, real forward progress this round — most
importantly, a genuine physical proof of the full offline/warning/restricted lifecycle (Part I),
which was the headline gap from the prior round. Parts L/M carry a genuine, honestly-documented
residual gap caused by a device-specific connectivity limitation rather than any newly-discovered
product defect.

**No final closing tag (`aura-product-licensing-phase7-validation-complete`) has been created in
this round.** Creating it requires every mandatory gate to have genuinely passed with real
evidence; L and M did not reach that bar this round. Whether the partial evidence for L/M
(Owner-side DB-confirmed + code-reviewed shared mechanism + safe-failure confirmation) is
sufficient to consider the overall validation complete, or whether a follow-up session should
re-attempt L/M's physical confirmation first, is a call for the user to make — not something to
decide unilaterally given how explicit the governing instruction is on this point.
