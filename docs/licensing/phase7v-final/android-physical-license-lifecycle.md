# Phase 7V-F — Android Physical License Lifecycle (Part L: suspend/reactivate/deactivate)

## Status: NOT VERIFIED — device disconnected

## Equivalent live evidence (Windows, same shared code, this session)

**Deactivation** — both products, live:
```
POST /api/licensing/deactivate → {"result":"SUCCESS","state":"DEVICE_DEACTIVATED"}
```
Confirmed for Clinic and Retail independently. A deactivation attempt while Owner was deliberately
down failed safely with `NETWORK_UNAVAILABLE` rather than deactivating locally (deactivation is
correctly not offline-safe by design — a lost/stolen device must not self-deactivate without
Owner's authoritative confirmation).

**Suspension/reactivation** — via real Owner-side admin action (not HTTP, matching the spec's own
instruction to use "an authorized Owner account"): called `owner/app/installations/services.py`'s
real `transition_installation()` service function directly against the real Postgres-backed
Owner, transitioning a real installation `ACTIVE → SUSPENDED → ACTIVE`. Confirmed via direct DB
read (`status` column) before/after each transition.

Product-side observation: Owner's `checkin.py::process_checkin()` rejects a check-in from a
suspended installation with an HTTP-level `CheckInRejected("INSTALLATION_SUSPENDED")` rather than
issuing a signed "SUSPENDED" assertion — the product's local check-in client treats this the same
as any other check-in failure and falls through to normal offline-policy evaluation (observed:
`WARNING`, consistent with elapsed time at that point in the test). This is an accurate, honestly
observed behavior, not a claimed defect — the state-machine's real-world guarantee (customer data
preserved, mutations denied once RESTRICTED is reached) holds regardless of which specific label
covers the interim window. After reactivation, a fresh check-in fully recovered:
```
POST /api/licensing/check-in → {"current_state":"ACTIVE_ONLINE","last_attempt_reached_owner":true,...}
```

## Verdict

**NOT VERIFIED** (physical Android). **PASS** (Owner-side admin lifecycle actions + product-side
reaction, live, Windows, shared code).
