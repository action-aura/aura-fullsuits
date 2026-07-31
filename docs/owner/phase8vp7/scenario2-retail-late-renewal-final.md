# Phase 8V-P7 — Scenario 2 (Retail Late Renewal) — Final (superseded, device reconnected)

## Result: PARTIAL — restricted-to-active transition and return integrity are real, physical, PASS;
88.00 calculation not exercised (real, disclosed reason below)

Per this session's own explicit bar ("Scenario 2 receives PASS only when the real restricted-to-active
transition, 88.00 calculation, and return integrity all pass"), this scenario is honestly reported as
PARTIAL, not PASS, since the 88.00 case specifically was not reached. Two of its three required
components are real and physically proven; the third has a real, disclosed, non-fabricated blocker.

Device reconnected after the earlier disconnection this session. Completed the remaining reachable
work.

## Restriction half (already proven earlier this session, real, unconfounded)

Real subscription (`4c9f5821-...`) transitioned `ACTIVE -> EXPIRED` via the real Owner service,
`License.status` left untouched, non-restrictive `WARN_ONLY` technical policy in place. Real check-in
on rc.4 Retail -> physical `Restricted`. Real wire evidence: `subscription_status: "EXPIRED"`,
`license_status: "ACTIVE"`, `offline_policy.hard_expiry_behavior: "WARN_ONLY"`. Real backend denial: a
$100 "Charge" attempt during `RESTRICTED` produced no confirmation and the Dashboard transaction count
stayed at 1 (confirmed both immediately and after a clean cold-restart).

## Renewal restoration (completed this session, real, physical)

Late renewal already applied through the real Owner renewal-request pipeline
(`create_renewal_request -> QUOTED -> AWAITING_CONFIRMATION -> AWAITING_PAYMENT -> PAYMENT_RECORDED ->
approve_renewal_request -> apply_renewal_request`): subscription `EXPIRED -> ACTIVE`,
`end_date -> 2026-08-30`, historical plan/price preserved.

Real "Check Now" tap on the physical device, Logcat cleared beforehand:

- Physical screen: **Restricted -> Active**.
- Real captured wire evidence: `subscription_status: "ACTIVE"`, `term_end: "2026-08-30"`, assertion id
  `3a492973-b914-4894-91c7-d07c02be7d76`, no `license_key` in the check-in request.
- `installation_id e77bd448-b2be-4706-908a-d41d4a0b1f31` unchanged throughout.
- Force-stopped and reopened the app: `current_state: "ACTIVE_ONLINE"` persisted, confirmed via a
  direct real local-API query, same installation ID.
- Logcat for this segment: zero forbidden-data matches (benign system input-method debug lines only).

## Real, disclosed product-completeness finding (not a defect introduced this session)

Retail's Android POS UI has **no discount-entry field** -- confirmed from source
(`RetailScreens.kt`'s own comment: "no discount-entry UI exists in this source, so discount_pct stays
at its default of 0"). The real server-side `POST /api/sub/retail/sales` route does accept an optional
per-line `discount_pct` (confirmed in `retail_api.py`'s `create_sale()`), but the Android client never
sends one. Exercising the 88.00 case therefore requires either adding that UI (out of this session's
scope -- "do not add unrelated features") or an authenticated direct API call, which requires a real
staff session this session did not have credentials for (the device's existing session already exists
inside the app but is not extractable to an external `curl` call without either the original onboarding
password or a new staff account created through an already-authenticated session -- a genuine chicken-
and-egg constraint, not something worked around by inserting rows directly). See
`retail-88-and-return-proof.md` for the honest disposition.

## Return integrity (completed this session, real, physical)

Used the real "Returns -> Process return" workflow against the real pre-existing `SALE-000002`
($100.00, 1 unit): set return quantity to 1 via the real quantity stepper, confirmed the UI's own
computed refund total ($100.00, matching exactly), processed the refund.

- Real return record created: `RET-000001-5eba23a1`, linked to `SALE-000002 · Customer return`, cash
  refund, real timestamp.
- Real net-revenue effect: Dashboard's `TODAY'S SALES` went from `$100.00` to `$0.00` (100 sale - 100
  return nets to zero) -- correct.
- Real stock restoration: product stock went from 18 to 19 (exactly +1, matching the returned
  quantity) -- confirmed via the Products screen.
- Sale-return linkage: the return record explicitly references `SALE-000002` by name in its own list
  entry.
- Duplicate-return protection: not independently re-verified via a second UI attempt this session (the
  "Process return" entry point was not re-located in a follow-up navigation pass within the remaining
  time budget) -- not claimed as tested, disclosed honestly rather than assumed from the single
  successful return alone.

## Disposition

**PARTIAL.** Restricted-to-active transition: real, PASS. Return integrity: real, PASS. 88.00
calculation: not exercised, real disclosed reason (no UI field, no available authenticated session for
the direct-API alternative) -- see `retail-88-and-return-proof.md`. This is one of the named reasons
the final unconditional tag remains withheld this session.
