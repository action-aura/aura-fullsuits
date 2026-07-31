# Phase 8V-P7 — Scenario 2 (Retail Late Renewal) — Final

## Result: PARTIAL — real, physical, unconfounded restricted-state proof achieved; renewal-restoration
confirmation, 88.00 case, and return integrity not reached before an extended real device disconnection

## What was achieved, real and physical, on the final rc.4 Retail Android artifact

1. Retail license (`41670a9e-9d9c-4bf4-9e0f-11690dd23a98`) confirmed on a non-restrictive `WARN_ONLY`
   technical policy (`phase8vp6-warn-only-nonrestrictive`, same policy used for Clinic's Scenario 3
   proof).
2. Real subscription (`4c9f5821-...`) transitioned `ACTIVE -> EXPIRED` via the real Owner service
   (`transition_subscription`), `License.status` left untouched (confirmed still `ACTIVE`).
3. Real check-in on the physical device (installation `e77bd448-b2be-4706-908a-d41d4a0b1f31`, rc.4)
   -> physical screen showed **"Restricted"**, `License status: ACTIVE`.
4. Real captured wire evidence for that exact check-in: `subscription_status: "EXPIRED"`,
   `license_status: "ACTIVE"`, `offline_policy.hard_expiry_behavior: "WARN_ONLY"` -- the same
   unconfounded proof pattern as Clinic's Scenario 3, now demonstrated on Retail specifically, on a
   freshly rebuilt/upgraded artifact.
5. Real backend-denial evidence: a real $100.00 "Synthetic Product One" sale was added to cart and a
   "Charge" attempt was made through the actual UI during the real `RESTRICTED` state. No visible
   confirmation appeared, and the Dashboard's `TODAY'S SALES`/`TRANSACTIONS` counters were confirmed
   unchanged (still 1 transaction / $100.00) both immediately after the attempt and again after a clean
   cold-restart of the app -- real, if less crisp than Clinic's stderr-traceback evidence, confirmation
   that no sale was recorded. A direct authenticated-backend equivalent test was attempted
   (`POST /api/sub/retail/sales`) but correctly returned `401 Authentication required` before reaching
   the licensing guard at all (the route requires a real staff session, which a bare `curl` does not
   have) -- this proves the route's authentication boundary is intact but does not, by itself, further
   isolate the licensing guard specifically; the UI-level evidence above is the operative proof for this
   scenario.
6. Real late renewal applied through the actual Owner renewal-request service (the same
   `create_renewal_request -> QUOTED -> AWAITING_CONFIRMATION -> AWAITING_PAYMENT -> PAYMENT_RECORDED
   -> approve_renewal_request -> apply_renewal_request` pipeline Owner's own UI drives): subscription
   reverted `EXPIRED -> ACTIVE`, `end_date` moved to `2026-08-30`, historical plan/price preserved
   (`plan_id` unchanged, prior `start_date` preserved).

## What was not reached

Immediately after the renewal was applied Owner-side, the physical Android device disconnected from
ADB (`adb devices -l` returned empty for an extended period; multiple real recovery attempts --
`adb kill-server`/`start-server`, waiting, rechecking -- did not restore the connection before this
session's real time budget for physical work was exhausted). This means:

- The on-device confirmation that a real check-in produces `RESTRICTED -> ACTIVE_ONLINE` (a new signed
  assertion, state-version increase, installation/device-key/slot continuity, force-stop persistence)
  was **not captured**.
- The 88.00 financial case (100.00 price, 20.00 discount, 10% tax on the discounted 80.00) was **not
  executed**.
- Return integrity was **not executed**.

## Disposition

Per this session's own explicit standard ("Scenario 2 receives PASS only when the real
restricted-to-active transition, 88.00 calculation, and return integrity all pass"), this is honestly
reported as **PARTIAL**, not PASS -- real, unconfounded, physical progress on the restricted-state half
(a first for Retail specifically), a real Owner-side renewal application, but the device-facing
completion is genuinely missing, disclosed plainly rather than assumed or inferred from the Owner-side
renewal alone.

## Follow-up required (next session)

Reconnect the physical device, recreate `adb reverse`, trigger "Check Now" on the already-renewed
subscription (no further Owner-side action needed -- the renewal is already real and applied), confirm
`ACTIVE_ONLINE` with a real signed assertion, then run the 88.00 case and one authorized return.
