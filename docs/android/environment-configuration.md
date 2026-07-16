# Android Build Environments (Phase 4J)

Both `aura-retail` and `aura-clinic` define the same three Gradle build types, each
producing an installable variant with a distinct `applicationIdSuffix` (so
debug/staging/release of the same product can be installed side-by-side on one
device without colliding):

| | `debug` | `staging` | `release` |
|---|---|---|---|
| `applicationId` suffix | `.debug` | `.staging` | (none) |
| `debuggable` | `true` | `false` | `false` |
| `BuildConfig.BUILD_ENV` | `"debug"` | `"staging"` | `"release"` |
| Minification | off | off | off (Chaquopy; kept simple per source's own choice) |
| Signing | Android auto-debug-key | production key if `keystore.properties` present, else unsigned | production key if `keystore.properties` present, else unsigned |
| Intended endpoint | local embedded server only (`127.0.0.1:<port>`) | local embedded server only | local embedded server only |
| Intended use | local development | internal install-and-verify testing | production distribution |

## What "endpoint placeholder" means right now

Every build type talks **only** to its own on-device embedded Flask server over
`http://127.0.0.1:<port>/` (`ServerBootstrap.baseUrl()`), regardless of build type —
there is no remote "Owner Server" endpoint wired up yet (explicitly out of scope for
this phase; see `android-migration-plan.md`'s exclusions). The three build types
therefore do not currently differ in *what* they talk to, only in packaging/signing/
debuggability. When a future phase introduces real remote connectivity, that phase
should add a per-build-type `BuildConfig` field for the remote endpoint (following the
same `buildConfigField` pattern already established here for `BUILD_ENV` and
`PRODUCT_CODE`) — `network_security_config.xml` already enforces HTTPS-only for any
non-loopback host, so no TLS-bypass work would be needed.

## Logging level

Neither product logs anything today (verified by source grep — zero
`android.util.Log`/`println` calls in either tree; see
`docs/privacy/clinic-android-data-boundary.md`). There is therefore no
per-build-type logging *level* to configure yet; this section exists so a future
phase that adds logging knows to gate verbosity by `BuildConfig.BUILD_ENV` /
`BuildConfig.DEBUG` rather than always-on.

## How to build each

```
cd android/aura-retail    # or aura-clinic
./gradlew assembleDebug
./gradlew assembleStaging
./gradlew assembleRelease
```

See `android-{retail,clinic}-build-report.md` for the actual commands run and their
results in this phase, and `signing-and-release-guide.md` for signing setup.
