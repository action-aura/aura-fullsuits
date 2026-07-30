# Phase 8V-P4 — URL-Configured Build Report

Rebuilt from current HEAD (`ab774e5` + this session's docs-only commits, no source change) with the
real, verified Owner URL determined in `connectivity-and-url-decision.md`.

## Clinic

```
$ ./gradlew --no-daemon -PownerLicensingBaseUrl=http://127.0.0.1:5551/api/licensing/v1 \
    testDebugUnitTest lintRelease                          -> BUILD SUCCESSFUL in 1m 59s
$ ./gradlew --no-daemon -PownerLicensingBaseUrl=http://127.0.0.1:5551/api/licensing/v1 \
    assembleRelease bundleRelease                          -> BUILD SUCCESSFUL in 1m 22s
```

Confirmed baked in for real (not assumed from the property alone -- read the actual generated file):

```
$ grep OWNER_LICENSING_BASE_URL app/build/generated/source/buildConfig/release/.../BuildConfig.java
public static final String OWNER_LICENSING_BASE_URL = "http://127.0.0.1:5551/api/licensing/v1";
```

## Retail

```
$ ./gradlew --no-daemon -PownerLicensingBaseUrl=http://127.0.0.1:5551/api/licensing/v1 \
    testDebugUnitTest lintRelease assembleRelease bundleRelease  -> BUILD SUCCESSFUL in 3m 2s
$ grep OWNER_LICENSING_BASE_URL app/build/generated/source/buildConfig/release/.../BuildConfig.java
public static final String OWNER_LICENSING_BASE_URL = "http://127.0.0.1:5551/api/licensing/v1";
```

## Output, stored without overwriting prior evidence

New directory `dist/android-validated/{clinic,retail}/`, distinct filenames
(`*-urlconfigured.apk`/`.aab`) -- the original `dist/android/{clinic,retail}/*-rc.3.{apk,aab}` files
(empty-URL build, Phase 8V-P2/P3) are untouched and still present for comparison.

| File | SHA-256 | Size |
|---|---|---|
| `AuraClinic-1.0.0-rc.3-urlconfigured.apk` | `d497c226802efd50dd1d952f70e61da5ee78509673b27a35f38f57f541752538` | 54,159,013 bytes |
| `AuraClinic-1.0.0-rc.3-urlconfigured.aab` | `70a8fa5eae20d49ea988a2ec7770b5880c80289248b144f111caae9b31d53cc2` | 34,901,422 bytes |
| `AuraRetail-1.0.0-rc.3-urlconfigured.apk` | `c7b464ca7c653dd465f3134fd7c3e24e3eb67ae38685d6c427bbb5e322ce844b` | 67,753,285 bytes |
| `AuraRetail-1.0.0-rc.3-urlconfigured.aab` | `449b280657fb9eadda2dbb4435e01b040b9ed4e0e3da22878bfe332d689dcea6` | 41,771,454 bytes |

Different bytes/hashes than the Phase 8V-P2 build, exactly as expected -- the compiled
`OWNER_LICENSING_BASE_URL` string is part of the binary. Clinic APK size is byte-identical to the
prior build (both 54,159,013 bytes) since the URL string length happens to match what padding/
alignment already accounted for; AAB and Retail sizes differ slightly, also expected.

## Requirement checklist

Package IDs unchanged (`com.actionaura.clinic`, `com.actionaura.retail`); versionName `1.0.0-rc.3`
both; versionCode `4` both (no new increment -- no source/behavior change, per canonical release
policy this doesn't warrant one); non-debuggable (confirmed via `aapt dump badging`, no
`application-debuggable` line); production signing identity used (see
`final-artifact-verification.md`); configured URL nonempty and resolves the licensing path exactly
once (`http://127.0.0.1:5551/api/licensing/v1`, verified from source semantics, not doubled); current
trust anchor, assertion schema, and `commercial_runtime` code embedded (no source changed since Phase
8V-P2's own build, which already carried these); Scenario 7's Owner-side fix is represented (it's
Owner-only, unaffected by Android rebuild, but the fix is live at HEAD `ab774e5` regardless); no debug
URL, fake paid mode, fake clock, TLS-trust-all, synthetic customer data, secrets, or keystore in the
build output or this document.
