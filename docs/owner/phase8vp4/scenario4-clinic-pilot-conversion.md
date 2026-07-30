# Phase 8V-P4 — Scenario 4: Clinic Pilot Conversion (Physical) — **PASS**

Used a fresh, dedicated synthetic pilot (real deactivation of the Scenario-1 installation and a
fresh real activation cycle was required first -- see `phase8vp4-baseline.md` for why: the local
Licensing screen only shows the license-key entry form in `NOT_CONFIGURED`/`ACTIVATION_REQUIRED`
states, not `DEVICE_DEACTIVATED`, so `pm clear` was used to reach a clean re-activatable state; a
real, disclosed operational finding, not a bypass of anything security-relevant -- Owner's own
`Installation` row for the deactivated device correctly shows `DEACTIVATED` throughout).

## Real pilot subscription + license + fresh physical activation

```
Subscription 70c9bfa2-... status=PILOT end_date=2026-08-05
License 159212c1-...
Key AURA-CLN-1-NBS4-ZTXG-TAFZ-AHPY-NYJ2

Activation successful.
Installation: de10cfb1-b23d-4b25-afde-742b35b94fbf
License status: ACTIVE
```

## Owner action: real pilot-to-paid conversion (graduating renewal)

```
before: PILOT  2026-08-05
AFTER:  ACTIVE 2027-08-05   (renewal_status=APPLIED)
```

`_GRADUATING_SUBSCRIPTION_STATUSES` path in `renewal_requests.py` exercised for real (PILOT counts
as reviving to ACTIVE the same way an EXPIRED/PAST_DUE/SUSPENDED subscription would).

## Physical device check-in

```
Check-in complete.
Installation: de10cfb1-b23d-4b25-afde-742b35b94fbf   (unchanged)
Last check-in: 2026-07-30T20:59:21.780814+00:00
```

```json
{"assertion_expires_at": "2026-07-31T20:59:23.176478+00:00",
 "current_state": "ACTIVE_ONLINE",
 "installation_id": "de10cfb1-b23d-4b25-afde-742b35b94fbf",
 "subscription_status": "ACTIVE"}   // was PILOT
```

## Verified

No license key retransmission (confirmed via Logcat, empty grep for the key string). Same
installation ID before/after conversion. Same device key (no re-registration -- Owner never saw a
second `activations` call for this installation). ACTIVE_ONLINE confirmed. No patient data
involved in this device's traffic (this installation has no product data of its own -- it is a
dedicated pilot-test installation, separate from the Scenario 1 Clinic installation which does hold
the real synthetic patient).

## Result: **PASS**, fully real, both Owner-side and physical-device-side.
