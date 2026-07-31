# Phase 8V-P5 — Scenario 6 (Device Replacement) — Final

## Result: NOT VERIFIED this session

## Why

This scenario requires two real, validated installations at a license's device limit -- the plan was
to use the physical Android installation plus the real installed Windows Retail product
(`dist/AuraRetail/AuraRetail.exe`, confirmed present on disk this session) as the second device. This
session's real time budget was consumed by: the device-gate/connectivity/identity reconfirmation, the
architectural investigation into why RESTRICTED was never reached in four prior sessions (a necessary,
non-optional prerequisite for Scenarios 2/3/5), the real timezone bug found/fixed/regression-tested in
`emergency_extensions.py`, and Scenario 5's two full physical device cycles (initial confounded attempt,
self-corrected retry). Starting the Windows product, activating it against the same license family at
its device limit, and walking the full replacement-review/approval/idempotency sequence was not reached.

## What is NOT claimed

No device-replacement evidence, physical or otherwise, is claimed for this session. The source-level
mechanics (`owner` replacement-review routes, `Installation` linking) were not re-read or re-verified
this session either -- this is a full gap, not a partial one.

## Follow-up required (next session)

Start `AuraRetail.exe`, activate it against a license already holding the physical Android
installation's slot at the license's device limit, and walk the sequence exactly as specified in the
governing spec's Part I: confirm slot exhaustion, attempt new activation, confirm safe
device-limit/replacement-review behavior (no 500, no `DEVICE_ALREADY_REGISTERED` regression), open the
Owner replacement workflow, approve, confirm old/new installation linkage and canonical
replaced/deactivated state, confirm idempotent retry, confirm real traffic captured throughout via the
existing wire-capture middleware (already running and proven this session).
