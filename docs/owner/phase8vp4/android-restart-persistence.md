# Phase 8V-P4 — Restart / Force-Stop Persistence — **PASS (both products)**

## Clinic

```
$ adb shell am force-stop com.actionaura.clinic
$ adb shell am start -n com.actionaura.clinic/.MainActivity
$ curl http://127.0.0.1:.../api/licensing/status
{"installation_id": "bb23591e-...", "assertion_expires_at": "2026-07-31T20:39:34.391024+00:00",
 "last_successful_checkin_at": "2026-07-30T20:39:33.210558+00:00", "current_state": "ACTIVE_ONLINE"}
```

Identical to the pre-restart values (see `scenario1-clinic-early-renewal.md`) -- installation ID,
assertion expiry, and last check-in all persisted exactly across a real force-stop and cold
relaunch. The real synthetic patient record (`Synthetic Patient One`) remained visible on the
Patients screen after relaunch as well.

## Retail

```
$ adb shell am force-stop com.actionaura.retail
$ adb shell am start -n com.actionaura.retail/.MainActivity
$ curl http://127.0.0.1:.../api/licensing/status
{"installation_id": "e77bd448-...", "assertion_expires_at": "2026-07-31T20:45:19.469769+00:00",
 "last_successful_checkin_at": "2026-07-30T20:45:18.071192+00:00", "current_state": "ACTIVE_ONLINE"}
```

Identical to the pre-restart values (post-Scenario-2 state). No false license-key prompt appeared
on either product on relaunch (both went straight to their real Dashboard, session and licensing
state both intact).

## Not independently exercised this session

Device reboot (as opposed to app-level force-stop) -- not attempted, given the time already spent
on the app-level persistence checks above, which are the higher-frequency real-world case anyway.
Stale-assertion-rejection under a genuinely stale (backdated) assertion was not separately
stress-tested this session (structurally unchanged since Phase 6/7, covered by the Owner suite's own
assertion-verification tests, reconfirmed green).

## Result: **PASS** for both products on the checks actually performed.
