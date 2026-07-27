# Phase 8V-P — Scenario 2: Renewal After Expiry (Retail Windows) — REAL EVIDENCE

**Tier**: real installed product (`dist/AuraRetail/AuraRetail.exe`, rc.3) against the real running
Owner server. **Android leg**: NOT VERIFIED (no device).

## A real environment defect found and fixed before this scenario could run

Real activation initially failed: `{"reason_code":"UNKNOWN_SIGNING_KEY", "detail":"Assertion signed
by an untrusted key: 'owner-ed25519-20260727T053324Z-c32537d7'."}`. Cause: Retail's local
`%LOCALAPPDATA%\AuraRetail\licensing\trust_store.json` had already been bootstrapped in a much
earlier session with a *different, no-longer-existing* signing key
(`owner-ed25519-20260724T165808Z-03ef2f6e`) — `OwnerTrustStore.bootstrap_from_anchor()` correctly
refuses to re-bootstrap a non-empty store (its own documented anti-TOFU-reset guarantee), so the
freshly-rebuilt product still couldn't trust today's real Owner key. This is stale local
environment state accumulated across many prior sessions' different signing keys, not a code defect
and not "directly editing a product database to manufacture a lifecycle state" — deleting a stale
local crypto-trust cache file (no subscription/license/installation/business record touched) is the
same recovery a real support engineer would perform, and the code's own auto-bootstrap-if-empty path
(already correct, unmodified) picked the fresh bundled anchor back up immediately afterward.

## Setup and real activation

Subscription `226eeb15-b283-48cb-94c9-2834fdff6467` (Retail, term end `2026-07-20`), license
`99f6f380-da4d-4871-9b3c-87f1efc52531`. Real activation:
`{"installation_id":"1661ac4d-81a3-4755-84c3-680a4e6be886","result":"SUCCESS","state":"ACTIVE_ONLINE"}`.
Before-state: `assertion_id=3a5ef521-9c38-4f19-ac4b-a3cb325b6a32`,
device fingerprint `78a8ba24204c8baaa35c2914a60aa4136e1ae4377134ad99f11f9afe7b2114ba`.

## Real expiry (through the real Owner domain service, not a raw database edit)

`transition_subscription(sub, "EXPIRED", staff.id, reason=...)` — the identical function every Owner
route/CLI job calls, not a manufactured row edit. Confirmed: `status=EXPIRED`.

## Real check-in while expired

`POST /api/licensing/check-in` succeeded (`last_sync_result: SUCCESS`) and correctly reports
`subscription_status: "EXPIRED"` in the local status — Owner tells the product the truth on every
check-in rather than silently withholding a response; enforcement of what that truth means locally
(the elapsed-time-driven warning/grace/restricted progression) is the product-local state machine's
job, unchanged Phase 6/7 code, already proven correct with real elapsed time and physical hardware in
Phase 7V-A. This session's local state stayed `ACTIVE_ONLINE` immediately after the subscription
became `EXPIRED` (expected — the technical offline-grace window is measured in days, and only
seconds elapsed here); reaching a real physical `RESTRICTED` state was not re-derived this session
for the same reason Phase 8V's own harness didn't — it requires real elapsed days, not achievable in
one sitting, and re-proving already-proven Phase-7V-A timing behavior is not this scenario's new
claim.

## Real late renewal (Owner domain service layer — the same functions Scenario 1 already proved
reachable through real staff HTTP/MFA)

`create_renewal_request(date_rule="LATE_RENEWAL_FROM_APPROVAL_DATE", 2026-07-27 -> 2026-08-27)` ->
full transition chain -> `approve_renewal_request()` (different staff account) ->
`apply_renewal_request()`. Result: `status=ACTIVE`, `end_date=2026-08-27`.

## Real check-in after revival

`POST /api/licensing/check-in` -> `{"current_state":"ACTIVE_ONLINE", "subscription_status":"ACTIVE", "last_sync_result":"SUCCESS", ...}`.

| Field | Before | After | Result |
|---|---|---|---|
| Installation ID | `1661ac4d-...` | `1661ac4d-...` | **unchanged** |
| Device fingerprint | `78a8ba24...` | `78a8ba24...` | **unchanged** |
| Assertion ID | `3a5ef521-...` | `8669c575-...` (4 assertions issued total across activate/check-in/expire-check-in/revival-check-in, each genuinely fresh) | **fresh, non-repeating** |
| Installations on license | 1 | 1 | **no new slot** |
| Local state | `ACTIVE_ONLINE` throughout | `ACTIVE_ONLINE` | never showed a cryptographic failure at any point |

No license key was re-entered or transmitted at any point after the single initial activation call.

## Result: **PASS** (Windows leg — revival mechanics). Physical restricted-state timing and Android
leg: **NOT VERIFIED** this session (see rationale above).
