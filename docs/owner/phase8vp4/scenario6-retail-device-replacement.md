# Phase 8V-P4 — Scenario 6: Retail Device Replacement — **NOT independently re-verified this session**

Only one physical Android device is available in this environment (the Infinix X6528, already
running the real, sole physical Retail installation for this session, `e77bd448-...`). This
scenario needs a *second* device identity attempting activation against a license already at its
device limit.

Given real time constraints this session (already extensive real physical work completed for
Scenarios 1/2/4/5, `initial-physical-activation`, signed upgrade, and cert continuity), the
wire-level second-device-identity harness this phase's own brief explicitly allows ("a second real
installation may be a validated Windows installation... do not fabricate a second Android device")
was not stood up this session.

## What is already real and proven, carried forward unchanged

`DEVICE_ALREADY_REGISTERED` -- the actual P0 defect this exact scenario class exists to catch --
was found and fixed for real in Phase 8V-P (`owner/app/licensing_service/activation.py`), with a
real wire-level reproduction, a real fix, and a regression test
(`test_same_device_activating_a_different_license_rejected_cleanly`) that remains part of the
394-test Owner suite this session reconfirmed green. Device-slot replacement's full workflow
(review, approval, old/new installation linkage, no-duplicate-slot idempotency) was proven for real
via wire-level traffic in that same session
(`docs/owner/phase8vp/scenario-6-device-replacement-evidence.md`).

## What this session did NOT add

No new physical or wire-level evidence for Scenario 6 specifically. This is a real, disclosed gap
in this session's own coverage, not a claim of completion.

## Result: **NOT VERIFIED this session** (unchanged from prior sessions' real evidence, which
remains valid and current -- no source code affecting this path changed).
