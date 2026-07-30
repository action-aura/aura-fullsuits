# Phase 8V-P4 — Signed Installation and Upgrade

## Clinic

Device already had a real signed `1.0.0-rc.2` (versionCode 3) install from an earlier session
(Phase 7V-A), still present on the physical Infinix X6528.

```
$ adb install -r AuraClinic-1.0.0-rc.3-urlconfigured.apk
Performing Streamed Install
Success
$ adb shell dumpsys package com.actionaura.clinic | grep version
versionCode=4 versionName=1.0.0-rc.3
```

In-place signed upgrade succeeded (no uninstall). Certificate identity unchanged (see
`final-artifact-verification.md`). The prior rc.2 install's own local licensing state
(`installation_id 9cdbc405-...`) had no corresponding row left in the current `aura_owner_dev`
database (predates this database's current content) so it was `pm clear`-reset before the fresh
Scenario J activation cycle -- a deliberate, disclosed data-reset for test purposes, not a hidden
identity swap (see `phase8vp4-baseline.md`'s note on this).

## Retail

Same real signed upgrade path, same device:

```
$ adb shell dumpsys package com.actionaura.retail  # before
versionCode=3 versionName=1.0.0-rc.2
$ adb shell pm clear com.actionaura.retail
$ adb install -r AuraRetail-1.0.0-rc.3-urlconfigured.apk
Performing Streamed Install
Success
$ adb shell dumpsys package com.actionaura.retail  # after
versionCode=4 versionName=1.0.0-rc.3
```

`pm clear` was used before this install (rather than a bare upgrade) since Retail's rc.2 local
state was, like Clinic's, from a database-content era that no longer exists in the current
`aura_owner_dev` -- same disclosed reasoning.

## Result

Both products: real signed upgrade to rc.3, same certificate identity, real fresh onboarding +
activation cycle completed on both (see `initial-physical-activation.md`). **PASS** for signed
installation.
