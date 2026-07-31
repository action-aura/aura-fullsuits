# Phase 8V-P9 — Final Build/Installation Report

## SUPERSEDED — see revision below

The original version of this document (written after Part E/F, before Part K) concluded "no rebuild
required." That conclusion is **no longer correct** and is retained below only for audit trail.

## Revised decision: rebuild IS required (rc.5 / versionCode 6)

Part K's real finding -- no stale-assertion/replay guard existed in the real client ingestion path --
was fixed in `commercial_runtime/licensing_contracts/checkin_scheduler.py` (new
`_is_stale_assertion()` monotonicity check, wired into `ingest_checkin_response()`) and
`commercial_runtime/licensing_contracts/events.py` (new `ASSERTION_STALE_REJECTED` event type).

Per the same artifact-dependency analysis this doc originally cited: Android embeds
`commercial_runtime` via `stagedPythonSources`; Windows embeds it via PyInstaller freeze. Unlike the
Part E/F changes (confined to `owner/app`, a separate server process never bundled into any product
artifact), **this change is inside `commercial_runtime`, which IS bundled into every Android and
Windows product artifact**. Every already-built rc.4 artifact (Clinic Android, Retail Android, Clinic
Windows, Retail Windows) now runs stale client-side licensing logic missing the real monotonicity
guard. A rebuild + immutable version bump (rc.4 -> rc.5, versionCode 5 -> 6) is required before the
stale-assertion behavior can be proven or shipped as fixed.

## Original (now-superseded) reasoning, kept for audit trail

This session's two source changes (`owner/app/commercial_ops/device_slot_ops.py`,
`owner/app/commercial_ops/preflight.py`) are both **Owner-side only**. Neither `commercial_runtime`
nor any product-specific Android/Windows source changed at that point in the session. Confirmed by
`git status` immediately before and after those edits -- only the two `owner/app/` files and their
test files had changed **as of that point in the session**. The Part K fix made after this was
written invalidated that basis.

## Confirmed unchanged as of Part E/F, now stale as of Part K

- Clinic Android APK/AAB: rc.4/versionCode 5, cert `35508048...` (Phase 8V-P7) -- **now stale**.
- Retail Android APK/AAB: rc.4/versionCode 5, cert `cae6b18450...` (Phase 8V-P7) -- **now stale**.
- Clinic Windows exe: rc.4, `daec4bf4...` (Phase 8V-P7) -- **now stale**.
- Retail Windows exe: rc.4, `35c37a0b...` (Phase 8V-P7) -- **now stale**.

## What triggered the rebuild

Not Retail discount-entry UI or Export (both Branch B, no product-code change). The trigger is the
Part K `commercial_runtime` stale-assertion fix -- a real client-side security-relevant source change
shared by both products and both platforms.
