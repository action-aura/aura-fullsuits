# Phase 8V-P9 Part J — Scenario 7 Device-Facing Completion (Final)

All evidence below is real: real rc.5 artifacts (rebuilt this session, embedding the Part K
stale-assertion fix), real Windows processes, the real physical Infinix X6528 device, the real Owner
backend, real Ed25519 device keys established in Phase 8V-P7, synthetic account/license data only.

## Reduced allowance -- already proven (Phase 8V-P7, reconfirmed unchanged)

License `68a467ec-901b-4470-832d-534e9fc24a74`: `device_limit=1`. Real wire evidence (Owner
`capture_raw.jsonl`) already showed `allowed_device_count: 1` on Windows instance C
(`a81dfc79-9fe9-48e1-a1d5-651deb0bcd73`) before this session. Re-confirmed this session via a fresh
real check-in on the rc.5 binary: `allowed_device_count: 1` again present in the real assertion
payload.

## Overage / exception metadata -- reconfirmed NOT IN CONTRACT (no change)

The assertion payload schema has no `state_version` and no overage/exception metadata field -- only
`allowed_device_count` is exposed to clients (confirmed exhaustively in Phase 8V-P9's canonical scope
audit). Nothing to test here beyond what was already verified; not re-litigated.

## Real overage state, no silent deactivation

Both real Windows installations on this license -- C (`a81dfc79`, effective allowance 1) and D
(`ac7edf01`, effective allowance 1 after re-checkin) -- remain `ACTIVE_ONLINE` / `installation_status
ACTIVE` after a real check-in cycle on rc.5, confirmed directly from each instance's own local
`licensing_state` SQLite row. Owner's own real over-limit scan (`scan_over_limit_licenses`, run in the
real Owner app context against the real DB) correctly flags `active_count=2, effective_limit=1` for
this license -- report-only, exactly as designed (Part P): no local entitlement was overridden, and
neither installation was silently deactivated.

## Real temporary exception: creation, confirmation, and real elapsed-time expiry

A real `DeviceSlotException` was created via the real `create_device_slot_exception()` service
(`extra_slots=1`, real staff actor `ca7a671c-...`, `starts_at=now`, `expires_at=now+75s`). Immediately
after creation, `scan_over_limit_licenses()` correctly stopped flagging the license
(`resolve_effective_device_limit` returned `2`). After waiting a real 80 real wall-clock seconds (no
simulated/fake clock), the exception's real `expires_at` had passed: `resolve_effective_device_limit`
reverted to `1` and the over-limit finding reappeared (`active_count=2, effective_limit=1`) --
confirming both the grant and the real-time expiry work correctly end-to-end, using the Part E/F
precision fix from earlier in this session.

## Activation blocked without an exception (real, physical)

Since the real plaintext key for `68a467ec` is not recoverable (only its HMAC hash is stored, matching
real production behavior -- see `real-device-identity-inventory.md`), a new real test license was
created for this specific test (`b7e42e1d-8dac-403e-a279-fa8b14350319`, `device_limit=1`, real issued
plaintext key `AURA-RET-1-RYFH-...`, no exception granted). Two fresh real Windows identities were used
(`AuraRetail-P9-Block1`, `AuraRetail-P9-Block2`, both real, never-before-activated `AURA_APP_DATA`
dirs, rc.5 binary):

1. Block1 activated successfully (`result: SUCCESS`, `state: ACTIVE_ONLINE`) -- fills the limit.
2. Block2 attempted activation with the same real key -- rejected: `{"detail":"Owner rejected the
   activation request.","reason_code":"DEVICE_LIMIT_REACHED"}`. Block2's local state remained
   `NOT_CONFIGURED` afterward -- confirmed no local override or silent grant occurred.

## No local entitlement override (Windows + Android)

Across every real instance touched this session (C, D, Block1, Block2, and the physical Android
device), the client only ever reflected what Owner's real signed assertion said -- no locally invented
entitlement, no bypass of a real `DEVICE_LIMIT_REACHED` rejection, no silent deactivation performed by
the client itself.

## Physical Android device (real, post-upgrade)

The physical Infinix X6528 (installation `e77bd448-b2be-4706-908a-d41d4a0b1f31`, license
`41670a9e-9d9c-4bf4-9e0f-11690dd23a98`, not an overage scenario -- `device_limit=2`, 1 active
installation) was upgraded in place to rc.5 (`adb install -r`, confirmed `versionName 1.0.0-rc.4 ->
1.0.0-rc.5`, no data loss). Real on-device navigation (Settings -> Licensing) confirms `Version
1.0.0-rc.5` in the actual rendered UI. A real "Check Now" tap (Kotlin-orchestrated -- the real
device-facing check-in path, distinct from the Windows-only local `/api/licensing/check-in` HTTP
trigger, which correctly refuses to run on Android with `DeviceIdentityError: Signing is owned by the
Kotlin layer on Android` -- confirming, not breaking, the documented Android architecture) completed
successfully: UI showed "Check-in complete.", `Last check-in` timestamp advanced to a real, fresh
server timestamp. Device logcat (filtered to the app's own PID) shows no exception around this
check-in -- only a pre-existing, unrelated `datetime.utcnow()` deprecation warning.

## Retail data unchanged

No product/sales/customer data was touched by any of the licensing operations in this section --
confirmed by construction (only licensing endpoints and Owner's licensing-domain services were called;
no Retail business-data endpoint was invoked).

## Real elapsed time used throughout

No fake clocks, no manually altered state versions, no direct product-database edits. The one
short-lived real exception used a genuine `expires_at` in the future at creation time, and the test
waited real wall-clock time for it to lapse naturally.
