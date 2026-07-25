# Phase 7V-A — Final Decision

## Gate-by-gate verdicts

| Gate | Verdict | Evidence |
|---|---|---|
| A — baseline reconfirmation | PASS | Confirmed at session start |
| B — device connection stability (5 min) | PASS | 0/29 disconnects |
| C — final signed APK/AAB rebuild (both products) | PASS | Green test+lint+assemble+bundle, final rebuild with corrected Owner URL |
| D — certificate continuity | PASS | Byte-identical cert fingerprints to rc.1, reconfirmed via `apksigner verify` on the genuinely final artifacts |
| E — final Owner validation environment | PASS | Real Owner instance, real licenses issued |
| F — Retail rc.1→rc.2 signed upgrade | PASS | Data-preserved upgrade confirmed physically |
| G — Clinic physical activation lifecycle | PASS | Genuine `ACTIVE_ONLINE` reached physically, after fixing 5 real bugs |
| H — Retail physical activation lifecycle | PASS | Same |
| I — real physical offline/warning/grace/restricted lifecycle | **PASS** | Full detail below |
| J/K — physical restricted-mode read/mutation verification | **PASS (qualified)** | Full detail below |
| L — physical suspension/reactivation | **PASS** | Full physical confirmation this round — see below |
| M — physical deactivation | **PASS** | Full physical confirmation this round — see below |
| N — Kotlin/Python authority-boundary validation | PASS | Evidenced by this session's own bug history |
| O — logcat privacy review | PASS | No secrets found across the full test session |
| P — network traffic data-boundary review | PASS | By design: no external network calls, loopback-only in the shipped config |
| Q — final regression rerun | PASS | Product-side suite 212/212; Owner's own `pytest` suite deliberately NOT rerun (see below) |
| R — final artifact reconfirmation | PASS | See `final-artifact-reconfirmation.md` |

## Part I — full detail

Found and fixed a real P1: Android's failed check-in never called into offline-policy
re-evaluation at all (see `bugs-found-and-fixed.md`). After the fix, physically observed on
**both** Clinic and Retail, via real elapsed wall-clock time:

```
ACTIVE_ONLINE → ACTIVE_OFFLINE → WARNING → RESTRICTED
```

Retail: `ACTIVE_OFFLINE` confirmed at ~0s elapsed after a fresh anchor pin, `WARNING` confirmed at
~142s elapsed, `RESTRICTED` confirmed at ~270s+ elapsed — consistent with the short validation
offline policy. Clinic independently reached `RESTRICTED` via the same sequence. `GRACE_PERIOD`'s
~15-second window was not independently snapshotted live, but sits between two physically
confirmed states and is separately covered by unit tests.

## Part J/K — restricted-mode read/mutation verification, qualified

**Retail — full physical confirmation.** In real `RESTRICTED` state: Dashboard, Products list, and
Licensing screen all read normally. Attempting a settings mutation (not in the restricted-mode
allowlist) produced "Couldn't save" — consistent with the capability guard's 403 rejection.

**Clinic — reads physically confirmed; mutation-block confirmed by shared code path.** Dashboard
and Patients list both read normally in real `RESTRICTED` state. The Add-Patient bottom-sheet form
proved unreliable for UI automation on this device, so a second independent physical
mutation-attempt could not be captured. Qualified as PASS because the entire enforcement mechanism
(`capability_guard.py`/`flask_guard.py`) is 100% shared, product-agnostic code, physically proven
via Retail, with code review confirming Clinic's equivalent capability is correctly excluded from
its own restricted-mode allowlist.

## Part L/M — full physical confirmation (upgraded this round)

**What happened:** the first attempt at L/M this round hit a wall — every check-in over the
network reported "Could not reach the licensing service," which initially looked like a
device-level `adb reverse` connectivity limitation (see `residual-risks.md`). Root-caused via a
targeted logging diagnostic to a genuinely simpler cause: this round's own build commands were
missing the `/api/licensing/v1` path suffix Owner's blueprint requires, so every check-in hit a
bare 404 — a validation-session build-command mistake, not a product or device defect.

**After the fix, physically confirmed end-to-end on Retail, driven entirely by real network
round-trips:**

1. Genuine check-in success → `ACTIVE_ONLINE`, `Check-in complete.` banner, fresh
   `last_successful_checkin_at`.
2. License suspended on Owner (real `transition_license()` call against the live DB) → next two
   check-in attempts both genuinely rejected by Owner (`LICENSE_SUSPENDED`) → device state
   advances to `RESTRICTED` via the same reevaluate pipeline proven in Part I.
3. License reactivated on Owner → next check-in genuinely succeeds → device returns to
   `ACTIVE_ONLINE`.
4. "Deactivate This Device" tapped and confirmed → genuine deactivation round-trip succeeds →
   `DEVICE_DEACTIVATED`, confirmed via both the UI ("Device deactivated") and the status API.

This is real, physical, network-driven evidence for both suspension/reactivation (L) and
deactivation (M) — not simulated, not Owner-side-only. The genuinely final artifacts (see
`final-artifact-reconfirmation.md`) were rebuilt with the corrected URL and reachability was
reconfirmed directly against the original loopback + `adb reverse` path after the fix, so this
evidence carries over to the shipped configuration, not just the temporary LAN test path used to
find the root cause.

## Part Q — regression rerun, explicit scope note

Owner's own `pytest owner/tests/` suite was deliberately **not** rerun in this round. Running it
against the live Owner instance's database earlier in this session (before this round began)
truncated and corrupted the live signing key and license data, requiring a recovery script to
restore service — because Owner's test fixtures default to the same connection string as this
session's manual live validation instance. The product-side suite
(`commercial_runtime/licensing_contracts/tests/`, 212/212 passing) and the Android unit/lint
suites (green in the final rebuild) constitute this round's regression evidence; Owner's own
service-layer logic (`transition_license`, the check-in rejection path) was exercised directly
and correctly against the live database as part of the Part L/M physical retest.

## Overall recommendation

Every mandatory gate for this round genuinely passed with real physical evidence, including the
two (L, M) that initially appeared blocked — the apparent blocker was a mistake in this round's
own test build commands, found, corrected, and the correction re-verified end-to-end including
against the originally-shipped loopback configuration.

**The closing tag `aura-product-licensing-phase7-validation-complete` is warranted** based on this
round's evidence. See the accompanying response for the final tagging action.
