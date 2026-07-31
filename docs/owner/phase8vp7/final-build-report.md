# Phase 8V-P7 — Final Build Report

## Clinic Android

```
$ ./gradlew --no-daemon clean                                          -> BUILD SUCCESSFUL
$ ./gradlew --no-daemon -PownerLicensingBaseUrl=... testDebugUnitTest lintRelease
                                                                        -> BUILD SUCCESSFUL in 3m 24s
$ ./gradlew --no-daemon -PownerLicensingBaseUrl=... assembleRelease bundleRelease
                                                                        -> (part of the same run above)
```

## Retail Android

```
$ ./gradlew --no-daemon clean                                          -> BUILD SUCCESSFUL in 45s
$ ./gradlew --no-daemon -PownerLicensingBaseUrl=... testDebugUnitTest lintRelease
                                                                        -> BUILD SUCCESSFUL in 8m 42s
$ ./gradlew --no-daemon -PownerLicensingBaseUrl=... assembleRelease bundleRelease
                                                                        -> BUILD SUCCESSFUL in 3m 25s
```

Only deprecation warnings (AutoMirrored icon variants, `menuAnchor()` overload) -- zero test failures,
zero lint errors blocking the release variant.

## Retail Windows

```
$ .venv/Scripts/python.exe -m PyInstaller products/retail/packaging/aura_retail.spec --noconfirm
  -> "Building because ...assertion_verifier.py changed" (positive confirmation the fix was re-frozen)
  -> Build complete
```

## Clinic Windows

```
$ .venv/Scripts/python.exe -m PyInstaller products/clinic/packaging/aura_clinic.spec --noconfirm
  -> "Building because ...assertion_verifier.py changed"
  -> Build complete
```

## Version alignment applied before every build

All 8 canonical version sources bumped `1.0.0-rc.3 -> 1.0.0-rc.4` (Android `versionCode 4 -> 5`) --
see `version-alignment-decision.md`. Confirmed live post-build: Retail Windows `/api/version` ->
`"app_version":"1.0.0-rc.4"`; both Android apps' Settings screens show `Version 1.0.0-rc.4` in their
own real UI.

## Real smoke launches performed as part of this build cycle

- Retail Windows exe: launched, confirmed listening, `/api/version` responded correctly -- three
  additional real instances launched later in the session for Scenario 6/7 (see those docs).
- Clinic Windows exe: not separately smoke-launched this session (no Clinic-Windows scenario planned;
  build success and the PyInstaller change-detection log line are the evidence for this artifact).
