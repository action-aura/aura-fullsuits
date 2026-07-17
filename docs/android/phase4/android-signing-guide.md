# Phase 4N — Android Signing Guide (re-verification)

Status: **PROVEN**, re-verified this phase. Full setup/rotation/backup
instructions already exist at `docs/android/signing-and-release-guide.md`
and are unchanged — this document records this phase's fresh, real
verification that the guidance there still holds.

## What was checked this phase

- `find android -iname "keystore.properties" -o -iname "*.jks" -o -iname "*.keystore"`
  (excluding `app/build/`) → **zero results**, both products. No secret was
  ever committed or is present on disk.
- `git status`/`git diff` reviewed before every commit this phase — no
  signing material staged.
- Real `assembleRelease`/`bundleRelease` run for both products (see
  `retail-build-report.md`/`clinic-build-report.md`) → both release APKs
  and both release AABs verified **unsigned** via `jarsigner -verify` /
  `apksigner verify`, not merely assumed from the absence of a keystore
  file.

## Generating a keystore (unchanged procedure)

See `docs/android/signing-and-release-guide.md` for the full walkthrough
(`keytool -genkeypair`, `keystore.properties.template` → `keystore.properties`,
secure offsite backup, never reuse one key across `aura-retail`/`aura-clinic`).
Not repeated verbatim here to avoid the two documents drifting out of sync
— that file is the canonical source.

## Consequences of key loss (unchanged)

Android ties app-update continuity to the signing certificate, not the
file. Losing the key means every existing install can never receive a
signed update from that key again — only a new listing under a new
`applicationId`. No production key exists yet, so this risk has not yet
been incurred.

## Build → APK → check signature (exact commands used this phase)

```
cd android/aura-retail   # or aura-clinic
./gradlew.bat assembleRelease
jarsigner -verify app/build/outputs/apk/release/app-release-unsigned.apk
apksigner verify --print-certs app/build/outputs/apk/release/app-release-unsigned.apk

./gradlew.bat bundleRelease
jarsigner -verify -verbose app/build/outputs/bundle/release/app-release.aab
```

## Rotating credentials

Not applicable — no credentials exist to rotate yet. When a real key is
generated, rotation means: generate a *new* keystore, publish a new
`applicationId`/listing (existing installs cannot be silently migrated to
a new key), and update `keystore.properties` on every build machine.

## Honesty statement

**Neither product is production-signed.** No `assembleRelease`/
`bundleRelease` artifact produced in this phase or the prior migration
phase should be distributed to a real user. This phase's contribution is
re-confirming that fact with fresh cryptographic verification, not
changing it.
