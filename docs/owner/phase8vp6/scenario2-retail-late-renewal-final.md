# Phase 8V-P6 — Scenario 2 (Retail Late Renewal) — Final

## Result: NOT VERIFIED physically this session (fix applies identically by design; not
independently re-confirmed on Retail hardware)

## What is true, and why confidence is high despite no new Retail-specific physical run

The fix implemented this session lives entirely in `commercial_runtime/licensing_contracts/` (shared
by both products) and `owner/app/licensing_service/` (shared, product-agnostic). Nothing about it is
Clinic-specific: `evaluate()`'s new subscription-status/license-SUSPENDED logic and
`serialize_policy_for_subscription()`'s extension override run identically regardless of
`product_code`. The automated regression (`test_policy_evaluator.py`,
`test_checkin_protocol.py`) exercises this logic at the `commercial_runtime`/Owner level, independent
of which product's Android app happens to call it.

## What was not done, and why

Retail Android was not rebuilt this session (Clinic was prioritized since it already had the fresh,
uncomplicated installation from Phase 8V-P5's Scenario 5 work, making it the faster path to a clean,
unconfounded first physical proof of the new logic). Physically confirming the same result on Retail,
and completing the full late-renewal-through-Owner-UI + 88.00 + returns sequence the scenario requires,
was not reached within this session's real time budget.

## Disposition

Per this session's own stated standard (real physical evidence, not inference from shared code, is
required for a scenario PASS), this remains **NOT VERIFIED**. The architectural confidence is real and
stated honestly above, but is explicitly not substituted for physical proof.

## Follow-up required (next session)

1. Rebuild and reinstall Retail Android with the current `commercial_runtime` (same command pattern as
   this session's Clinic rebuild, see `final-build-and-signing-report.md`).
2. Repeat the Scenario 3-style proof on Retail specifically (fast, ~10 minutes given the pattern is now
   established) as a quick confirmation.
3. Complete the full late-renewal Owner-UI workflow (create renewal, apply late-renewal date rule, link
   CONFIRMED payment, approve, apply), trigger check-in, confirm `RESTRICTED -> ACTIVE_ONLINE` via a real
   signed assertion.
4. Run the 88.00 financial case (100.00 price, 20.00 discount, 10% tax on the discounted 80.00) and one
   authorized return, verifying sale/stock/return integrity.
