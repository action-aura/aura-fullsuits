# Phase 8V-P5 — Stale-Assertion Rejection — Physical Proof

## Result: NOT VERIFIED this session

## Why

The real wire-capture middleware built this session (`owner_capture_server.py`) is architecturally
capable of the preferred method the spec describes -- it sits directly in the real Owner WSGI pipeline
and could delay delivery of one real signed response while a newer commercial transition is applied and
a second real response obtained, then release the delayed one -- but that specific controlled
delayed-delivery orchestration (holding a real HTTP response in-flight, applying a real Owner-side state
change, then releasing it) was not implemented or executed this session; the middleware currently only
observes and logs, it does not hold/replay. Building and exercising that control safely (without
corrupting the real device's ordinary check-in cadence, and without accidentally leaving the device in
an inconsistent state) needs dedicated real time this session did not have left.

## What is NOT claimed

No stale-assertion rejection evidence, physical or otherwise, is claimed for this session. No forged
signature, no manually-edited assertion, and no unit-test-only evidence is substituted for this
requirement -- consistent with the spec's explicit prohibition on those substitutes.

## Follow-up required (next session)

Extend `owner_capture_server.py` (or a small dedicated variant) to optionally hold one real response in
memory for controlled release; run the exact sequence in the governing spec's Part K: valid state ->
assertion A issued (state version N) -> delay A's delivery -> apply a real newer commercial transition
-> assertion B issued and delivered/accepted (state version N+1+) -> release delayed A -> confirm the
device rejects A as stale, confirms identity unchanged, and logs the rejection safely (no secret
leakage). Record assertion IDs, state versions, issuance times, delivery order, and the accept/reject
result for each.
