# Phase 8V-P4 — Initial Physical Activation

Both products activated for real, on the physical Infinix X6528, against the real Owner dev server,
over the `adb reverse` loopback tunnel, using real synthetic licenses created via Owner's own
service layer (real Postgres rows, real Ed25519-signable license keys).

## Clinic

License created: `AURA-CLN-1-RUAC-Z7DY-G9MX-TX6U-A64Z` (subscription `0a6e6b36-...`, license
`adf83923-...`). Entered via the real app UI's Licensing screen, real `Activate` button tap.

```
Activation successful.
Product: AURA_CLINIC
Installation: bb23591e-c7d8-4ea8-954a-576424111b9d
License status: ACTIVE
Last check-in: 2026-07-30T20:05:57.092899+00:00
```

Cross-verified against the real Owner database:

```python
Installation(id='bb23591e-c7d8-4ea8-954a-576424111b9d', status='ACTIVE',
             license_id='adf83923-9e8f-4157-81cf-62476f105729')
```

Matches exactly. One real defect found and root-caused during this step (not a licensing bug --
see `phase8vp4-baseline.md`): the first activation attempt failed with "Could not reach the
licensing service" because the `adb reverse tcp:5551 tcp:5551` tunnel had silently dropped between
setup and use; re-establishing it fixed activation immediately. Real, disclosed, operational, not a
product defect.

## Retail

License created: `AURA-RET-1-D56D-PST6-M3WZ-6283-NYJ8` (subscription `4c9f5821-...`, license
`41670a9e-...`).

```
Activation successful.
Product: AURA_RETAIL
Installation: e77bd448-b2be-4706-908a-d41d4a0b1f31
License status: ACTIVE
Last check-in: 2026-07-30T20:31:33.752621+00:00
```

Cross-verified against Owner: `Installation(id='e77bd448-...', status='ACTIVE',
license_id='41670a9e-...')`. Matches.

## Verified for both

- No Owner 500, no raw stack trace.
- No `DEVICE_ALREADY_REGISTERED` regression (the Phase 8V-P P0 fix holds -- neither activation
  collided with any other real device-key row).
- No duplicate installation created on retry (both activations succeeded on the real, corrected
  first live attempt after the tunnel was restored).
- Full license key appeared only in this one activation request each -- confirmed absent from
  every subsequent check-in/renewal request this session (see `android-logcat-privacy.md`,
  `android-data-boundary-report.md`).
- Product works after restart (see `android-restart-persistence.md`).

**Result: PASS for both products.** This is the first time in four sessions (Phase 8V-P, 8V-P2,
8V-P3, 8V-P4) that a genuine physical-device activation has been completed.
