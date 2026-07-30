# Phase 8V-P3 — Final Android Artifact Verification

No rebuild performed this session -- source unchanged since Phase 8V-P2, no artifact/source
mismatch, signing identity not in question, no defect required a fix. Per this phase's own Part D
rule ("Do not rebuild unless...") none of the four conditions apply to a rebuild-for-its-own-sake;
they will apply once the URL configuration below is addressed for a real device session.

## SHA-256 re-hash, compared against `docs/owner/phase8vp2/final-android-artifact-evidence.md`

| File | SHA-256 | Matches recorded evidence |
|---|---|---|
| `dist/android/clinic/AuraClinic-1.0.0-rc.3.apk` | `daaaaaa30fe6630fa10d38c3b1ec951eca70f123cbd8b57c7f70a3f427be5f50` | Yes |
| `dist/android/clinic/AuraClinic-1.0.0-rc.3.aab` | `c5453af7e8f6ecff379163c40cf08a2ce1c49c0c99a270afbb3db81701ac4240` | Yes |
| `dist/android/retail/AuraRetail-1.0.0-rc.3.apk` | `3746fdff94672f80500c94abd11513a0a22dafd97b5b0ffef1322ab87cf647d6` | Yes |
| `dist/android/retail/AuraRetail-1.0.0-rc.3.aab` | `b0eb43e32bf0ff39135e229c8c213970c4be35535ce58e85c6152d4784836c9c` | Yes |

No drift. Certificate SHA-256 fingerprints reconfirmed against the full (not abbreviated) values
already on record: Clinic `35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2`, Retail
`cae6b18450c14a71eba47545e5b3ba52089eef0397d5881f4c1dc7d5a4b7d32d` -- both match the historical
production signing identity (see `docs/owner/phase8vp2/android-certificate-continuity.md` for the
original `apksigner`/`jarsigner` runs; not re-run this session since the artifact bytes themselves
are unchanged, confirmed by the hash match above).

Package IDs (`com.actionaura.clinic`, `com.actionaura.retail`), versionName (`1.0.0-rc.3`),
versionCode (`4`), non-debuggable status: all unchanged, all previously verified, all still correct.

## Real finding this session: licensing URL is currently unconfigured in these artifacts

```
$ grep OWNER_LICENSING_BASE_URL .../BuildConfig.java   (both products, release variant)
public static final String OWNER_LICENSING_BASE_URL = "";
```

This is the deliberate, documented fail-safe default (`build.gradle`'s own comment: "Empty by
default... never a hidden fallback URL", mirroring `products/clinic/backend/config.py`'s identical
rule for Windows) -- **not a bug**, and not something this session should or did "fix" by hardcoding
a URL into source (that would violate the same "never a hidden fallback URL" principle these
artifacts were deliberately built to honor). It does mean, correctly and as designed: **installing
either current rc.3 artifact as-is will show licensing `NOT_CONFIGURED`**, not a working connection
to Owner.

### Exact rebuild command for the next device session

Once a device is connected and `adb reverse tcp:5551 tcp:5551` is set up (mapping the phone's own
`127.0.0.1:5551` to this machine's real Owner server over USB -- no LAN or public exposure, matches
this phase's own "controlled localhost, adb reverse, or isolated LAN connectivity" requirement):

```
cd android/aura-clinic
./gradlew --no-daemon -PownerLicensingBaseUrl=http://127.0.0.1:5551/api/licensing/v1 \
    testDebugUnitTest lintRelease assembleRelease bundleRelease

cd ../aura-retail
./gradlew --no-daemon -PownerLicensingBaseUrl=http://127.0.0.1:5551/api/licensing/v1 \
    testDebugUnitTest lintRelease assembleRelease bundleRelease
```

The `/api/licensing/v1` suffix is mandatory in the property value itself -- the client library
(`commercial_runtime/licensing_contracts/client.py`) treats `base_url` as the complete path and does
not append the suffix automatically. This is the exact class of mistake a prior phase's own notes
warn against repeating; the value above already includes it correctly.

This rebuild will produce **different APK/AAB bytes and a different SHA-256** than the values
recorded in `docs/owner/phase8vp2/final-android-artifact-evidence.md` (the compiled
`OWNER_LICENSING_BASE_URL` string changes the binary) -- expected, not a regression. Certificate
identity will be unchanged (same keystore, same signing config) and should be re-verified after
rebuild as a matter of course, not because a mismatch is expected.

## Conclusion

Certificate continuity and artifact/source alignment: **PASS, reconfirmed**. Readiness for a real
licensing physical-validation session: **NOT YET** -- a targeted rebuild with
`-PownerLicensingBaseUrl` is required first, now that a device is available. This is a new, real,
one-command precondition discovered this session, not previously documented.
