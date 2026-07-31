# Phase 8V-P5 — Clinic Invoice/Payment Integrity — Final

## Result: NOT VERIFIED this session

## Why

Not reached within this session's real time budget. The Clinic installation used for Scenario 5 was a
fresh installation created specifically to isolate the emergency-extension mechanism cleanly (see
`scenario5-emergency-extension-and-expiry-final.md`) and does not carry the synthetic
unpaid/partial/completed-payment invoice baseline this part requires; that baseline was never created.

## What is NOT claimed

No invoice/payment integrity evidence, physical or otherwise, is claimed for this session.

## Follow-up required (next session)

Create the synthetic invoice/payment baseline (minimum: one unpaid, one partial, one completed-payment
invoice, per the governing spec's Part E), record totals/paid/balance/status/count, then run Part M in
full: during a real `RESTRICTED` state (Scenario 5's mechanism), confirm existing history stays readable
and a new protected financial mutation is denied with no partial invoice/payment row; after restoring to
real `ACTIVE`, perform one authorized payment operation and confirm totals/status update correctly with
no duplicate and no change to prior history.
