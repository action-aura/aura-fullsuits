# Phase 7 -- Local License State Machine

One state machine, implemented identically (same state names, same transition table) in `commercial_runtime/licensing_contracts/state_machine.py`, consumed by both Windows and the embedded Android backend since both run the same Python. Kotlin never implements a second copy (see `product-integration-architecture.md`'s authority-boundary rationale) -- it only ever *displays* whatever state Python's `LicensingStatusPresenter` route reports.

## States (Part M's recommended list, used as-is -- no renaming needed, semantics already fit)

`NOT_CONFIGURED`, `ACTIVATION_REQUIRED`, `ACTIVATING`, `ACTIVE_ONLINE`, `ACTIVE_OFFLINE`, `WARNING`, `GRACE_PERIOD`, `RESTRICTED`, `SUSPENDED`, `REVOKED`, `EXPIRED`, `DEVICE_DEACTIVATED`, `DEVICE_REPLACED`, `CLOCK_REVIEW_REQUIRED`, `SERVICE_UNAVAILABLE`, `LOCAL_STATE_CORRUPT`.

`NOT_CONFIGURED` is distinct from `ACTIVATION_REQUIRED`: the former is the true first-run state before any device key exists; the latter covers both first run after key generation and the rc.1-upgrade legacy case (`local-license-state-machine.md` ties into `phase7-implementation-plan.md`'s Part Y migration state).

## Transition table (deterministic, table-driven -- no ad hoc `if` chains scattered across call sites)

| From | Event | To |
|---|---|---|
| `NOT_CONFIGURED` | device key generated | `ACTIVATION_REQUIRED` |
| `ACTIVATION_REQUIRED` | user submits license key | `ACTIVATING` |
| `ACTIVATING` | Owner returns `SUCCESS` + valid assertion | `ACTIVE_ONLINE` |
| `ACTIVATING` | Owner returns any rejection reason code | `ACTIVATION_REQUIRED` (with the reason surfaced to UI, not silently dropped) |
| `ACTIVATING` | network/timeout failure | `ACTIVATION_REQUIRED` (distinct UI message: "Network Unavailable" / "Owner Service Temporarily Unavailable", per Part G's screen list -- same target state, different presented reason) |
| `ACTIVE_ONLINE` | check-in succeeds, assertion refreshed | `ACTIVE_ONLINE` (self-loop, resets grace clock) |
| `ACTIVE_ONLINE` | check-in fails (network) | `ACTIVE_OFFLINE` |
| `ACTIVE_OFFLINE` | check-in succeeds | `ACTIVE_ONLINE` |
| `ACTIVE_OFFLINE` | elapsed time crosses `warning_start_seconds` before grace expiry | `WARNING` |
| `WARNING` | check-in succeeds | `ACTIVE_ONLINE` |
| `WARNING` | elapsed time crosses `offline_grace_seconds` | `GRACE_PERIOD` |
| `GRACE_PERIOD` | check-in succeeds | `ACTIVE_ONLINE` |
| `GRACE_PERIOD` | grace fully exhausted | `RESTRICTED` |
| `RESTRICTED` | check-in succeeds with valid assertion | `ACTIVE_ONLINE` |
| any active-family state | Owner assertion reports `installation_status=SUSPENDED` | `SUSPENDED` |
| `SUSPENDED` | Owner assertion reports active status again (reactivation) | `ACTIVE_ONLINE` |
| any state | Owner assertion reports `installation_status=REVOKED` | `REVOKED` (terminal until explicit new activation) |
| any active-family state | Owner assertion reports `license_status` past `valid_until` per governing policy | `EXPIRED` |
| any state | user/staff confirms device deactivation, Owner confirms | `DEVICE_DEACTIVATED` |
| `DEVICE_DEACTIVATED` | device-replacement flow completes | `DEVICE_REPLACED` -> immediately re-enters `ACTIVATION_REQUIRED` for the new device key |
| any state | suspicious local-clock rollback detected (Part N) | `CLOCK_REVIEW_REQUIRED` |
| `CLOCK_REVIEW_REQUIRED` | online verification succeeds | whatever state the verified assertion indicates |
| any state | Owner unreachable at a point where a call was attempted (distinct from grace-timer expiry) | `SERVICE_UNAVAILABLE` is a *transient overlay*, not a stored state -- see note below |
| any state | local state file/DB corruption detected | `LOCAL_STATE_CORRUPT` |
| `LOCAL_STATE_CORRUPT` | controlled reset flow completes | `ACTIVATION_REQUIRED` |

## `SERVICE_UNAVAILABLE` is not a persisted state

Part M's own distinction ("Owner cannot currently be reached -- does not automatically mean the license is invalid") means this is the *result of the last network attempt*, layered on top of whatever the last-known persisted state was -- persisting it as a stored state would let a transient outage overwrite a perfectly valid `ACTIVE_OFFLINE`/`GRACE_PERIOD` classification. The presenter shows "Owner Service Temporarily Unavailable" as a banner over the last known real state, and the policy evaluator (`local-license-state-machine.md` + `offline-enforcement-policy.md`) continues to compute `ACTIVE_OFFLINE`/`WARNING`/`GRACE_PERIOD`/`RESTRICTED` purely from elapsed time against the last valid assertion, regardless of whether the most recent attempt to reach Owner succeeded.

## No arbitrary UI-driven mutation

Every transition above is triggered only by: (a) a verified Owner response, (b) a locally-computed elapsed-time boundary crossing, (c) an explicit, confirmation-gated user action (activate, deactivate, reset). No route or UI component may call `set_state(...)` directly -- the state machine module exposes only `evaluate(current_state, verified_evidence, trusted_now) -> new_state`, called from exactly one place: the check-in/activation completion handler and a periodic elapsed-time re-evaluation tick (also needed while fully offline, since grace/warning boundaries must still be crossed without any network event to trigger them).
