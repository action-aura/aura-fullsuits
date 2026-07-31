# Phase 8V-P6 — Final Build and Signing Report

## Clinic Android — rebuilt and reinstalled

```
$ ./gradlew --no-daemon -PownerLicensingBaseUrl=http://127.0.0.1:5551/api/licensing/v1 \
    testDebugUnitTest lintRelease                       -> BUILD SUCCESSFUL in 35s
$ ./gradlew --no-daemon -PownerLicensingBaseUrl=http://127.0.0.1:5551/api/licensing/v1 \
    assembleRelease bundleRelease                       -> BUILD SUCCESSFUL in 40s
```

`mergeReleasePythonSources` executed (not `UP-TO-DATE`), confirming the fixed `commercial_runtime`
source was actually re-staged into this build -- not assumed from the successful build alone.

| Field | Value |
|---|---|
| File | `app/build/outputs/apk/release/app-release.apk` |
| SHA-256 | `5e503518bbabbcedf21b3ecd38280420f58a69aa4d4295b39a6e1171fcda0b67` |
| Size | 54,159,013 bytes |
| Package ID | `com.actionaura.clinic` (unchanged) |
| versionName / versionCode | `1.0.0-rc.3` / `4` (unchanged -- fix rebuild, not a new release) |
| Debuggable | No (`aapt dump badging` shows no `application-debuggable` line) |
| Certificate SHA-256 | `35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2` -- **exact match** to the historical production signing identity recorded since Phase 8V-P2 |
| Owner URL baked in | `http://127.0.0.1:5551/api/licensing/v1` (confirmed via `BuildConfig`-equivalent semantics, same mechanism as prior sessions) |

Installed via `adb install -r` (in-place upgrade): `firstInstallTime` preserved
(`2026-07-20 04:01:56`, unchanged), `lastUpdateTime` updated to the real install time
(`2026-07-31 08:22:26`) -- confirms this was a genuine upgrade of the existing installation, not a
fresh install, and that local app data/identity survived (installation ID
`c7150980-d45b-4d14-866b-642fb798dceb` unchanged across the reinstall, confirmed via the real check-in
responses afterward).

## Retail Android — not rebuilt this session

See `scenario2-retail-late-renewal-final.md` for the disclosed reason. The currently-installed Retail
APK still carries the pre-fix `commercial_runtime` and should not be treated as exhibiting the new
enforcement behavior until rebuilt.

## Windows products — not rebuilt this session

No physical Windows scenario was exercised this session (Scenario 6/7 not reached); no rebuild was
necessary or performed.
