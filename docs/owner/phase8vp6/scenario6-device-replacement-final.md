# Phase 8V-P6 — Scenario 6 (Device Replacement) — Final

## Result: NOT VERIFIED this session

## Why

Not reached within this session's time budget, which was concentrated on root-causing, fixing, testing,
and physically proving the two P1-class commercial-enforcement gaps (the phase's actual mandate) --
that work alone (source investigation, implementation, 17 new automated tests, a full regression re-run,
an Android rebuild/reinstall cycle, and two full physical scenario walks with wire-evidence
verification) consumed the available session time. This scenario's own requirement -- a second real
installation (the real Windows Retail product, `dist/AuraRetail/AuraRetail.exe`, previously confirmed
present) at a license's device limit, walking the full replacement-review/approval/idempotency sequence
-- was not started.

## What is NOT claimed

No device-replacement evidence, physical or otherwise, is claimed for this session. Nothing in this
session's source changes touches device-slot/replacement logic
(`device_slot_ops.py`/`replace_device_slot()`), so no regression risk is introduced by omission, but no
new evidence is claimed either.

## Follow-up required (next session)

Unchanged from Phase 8V-P5's own follow-up note: start `AuraRetail.exe`, activate it against a license
already holding the physical Android installation's slot at the license's device limit, walk the
governing spec's Part Q sequence in full, using the real wire-capture middleware (already proven working
this session) throughout.
