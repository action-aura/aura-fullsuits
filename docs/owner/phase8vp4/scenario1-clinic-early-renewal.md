# Phase 8V-P4 — Scenario 1: Clinic Early Renewal (Physical) — **PASS**

## Before

```
installation_id: bb23591e-c7d8-4ea8-954a-576424111b9d
current_state: ACTIVE_ONLINE
assertion_expires_at: 2026-07-31T20:05:58.011783+00:00
subscription end_date (Owner): 2026-08-30
```

## Owner action (real, `apply_renewal_request()` full pipeline: create -> quote -> confirm -> pay
-> approve -> apply, real Postgres, real staff actors)

```
before: end_date=2026-08-30 status=ACTIVE
AFTER:  status=APPLIED sub.end_date=2026-09-30
```

## Physical device check-in

Tapped `Check Now` on the real Licensing screen:

```
Check-in complete.
Installation: bb23591e-c7d8-4ea8-954a-576424111b9d   (unchanged)
License status: ACTIVE
Last check-in: 2026-07-30T20:39:33.210558+00:00        (was 20:05:57)
```

Full status via local API confirms the refresh:

```json
{"assertion_expires_at": "2026-07-31T20:39:34.391024+00:00",  // moved forward, real new assertion
 "current_state": "ACTIVE_ONLINE",
 "installation_id": "bb23591e-c7d8-4ea8-954a-576424111b9d",   // same
 "last_successful_checkin_at": "2026-07-30T20:39:33.210558+00:00",
 "subscription_status": "ACTIVE"}
```

## Verified

- No license key in the check-in request (protocol-level guarantee, reconfirmed by Logcat review --
  see `android-logcat-privacy.md`).
- Installation ID unchanged.
- Device key unchanged (same installation record, no re-registration).
- Same active slot count (no new slot consumed by a renewal).
- No duplicate installation created.
- New signed assertion issued (`assertion_expires_at` moved forward).
- ACTIVE_ONLINE maintained throughout.
- Force-stop + cold reopen: state persisted exactly (same installation ID, same
  `assertion_expires_at`, same `last_successful_checkin_at` -- see `android-restart-persistence.md`).
- Clinic patient data (`Synthetic Patient One`) unaffected by any of the above.

## Result: **PASS**, fully real, both Owner-side and physical-device-side.
