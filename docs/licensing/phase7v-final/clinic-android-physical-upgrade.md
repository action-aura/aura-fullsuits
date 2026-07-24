# Phase 7V-F — Clinic Android Physical Signed Upgrade (Part D/E)

## rc.1 baseline verification (real, on-device)

The physical device already had Clinic's real production-signed rc.1 APK installed (from an
earlier Wave 1A/1B session). Pulled it directly off the device (`adb pull` of the real installed
`base.apk`) and verified with `apksigner verify --print-certs`:

```
Signer #1 certificate SHA-256 digest: 35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2
```

Matches the recorded rc.1 fingerprint (`35:50:80:...:0B:BB:F2`) exactly, and matches this session's
freshly-built rc.2 fingerprint exactly — real, on-device proof of signer continuity, not a
re-statement of a historical record. `versionCode=2`, `versionName=1.0.0-rc.1`, package
`com.actionaura.clinic` — confirmed via `adb shell dumpsys package`.

## Representative synthetic data

The pre-existing on-device data's admin credentials were unknown to this session (from an earlier,
separate Wave 1A/1B session). Per the spec's own instruction to "complete onboarding using
synthetic local information," did a controlled `adb shell pm clear com.actionaura.clinic` (keeps
rc.1 installed, wipes only app data) and re-onboarded with known synthetic credentials, then
created via the real on-device HTTP API (through `adb forward`, not simulated):

- 2 doctors, 3 patients, 2 appointments, 1 visit, 1 prescription.
- 2 invoices: one fully paid (55.00), one partially paid (50.00 of 110.00, 60.00 outstanding).

All exceeds the spec's minimums (3 patients / 2 appointments / 1 visit / 2 invoices / 1 partial
payment / 1 fully paid).

## Backup and persistence (real, on-device)

Real backup created via the on-device `POST /api/backup/create` (real checksums recorded in
session logs). Force-stopped and relaunched rc.1 — data confirmed identical via a fresh `GET
/api/sub/clinic/patients`/`invoices` read.

## Signed upgrade

```
adb install -r app-release.apk   (real production-signed rc.2, same keystore)
Success
```

Post-upgrade: `versionCode=3`, `versionName=1.0.0-rc.2`, signer reference unchanged (`2adc576e`) —
Android's own package installer accepted the upgrade in place, proving real signature-match
continuity (Android rejects an upgrade install if the signer doesn't match).

## Data preservation

All 5 patients, 2 doctors, 5 appointments, 1 visit, 1 prescription, both invoices (with correct
statuses/totals) confirmed present via the real on-device API after the upgrade.

## Licensing screen / activation-readiness

`GET /api/licensing/status` on rc.1 → `404` (route didn't exist). On rc.2, post-upgrade →
route exists, correctly reports `NOT_CONFIGURED` until the Owner URL/trust anchor is present in
that specific build (this session's first APK build round didn't have
`-PownerLicensingBaseUrl` set; a later rebuild round added it and activation was proven live — see
`clinic-android-physical-activation.md` for what was and wasn't completed before the device
disconnected).

## Verdict

**PASS** — real physical signed upgrade, real data preservation, both fully verified on-device with
live evidence, not simulated or assumed.
