# Android Production Signing Policy

## Decision: separate keys per product
Retail and Clinic each have their own production signing keystore — no shared organizational key. Rationale: these are commercially independent products with independent release cadences; a shared key would mean a compromise or loss affecting one product forces re-keying both, and would let one product's APK be trivially confused for the other's by signature.

## What exists
| Product | Keystore | Alias | Algorithm | Validity |
|---|---|---|---|---|
| Aura Retail | `C:\Users\Dell\AuraSigningKeys\retail-release.keystore` | `aura-retail-release` | RSA 2048 | 9,855 days (~27 years) from 2026-07-20 |
| Aura Clinic | `C:\Users\Dell\AuraSigningKeys\clinic-release.keystore` | `aura-clinic-release` | RSA 2048 | 9,855 days (~27 years) from 2026-07-20 |

27-year validity intentionally exceeds Google Play's own minimum requirement (certificates must remain valid past October 22, 2033) by a wide margin, so this is a one-time generation that should never need to be redone for validity reasons.

## Certificate fingerprints (safe to record — public, not secret)
| Product | SHA-256 |
|---|---|
| Aura Retail | `CA:E6:B1:84:50:C1:4A:71:EB:A4:75:45:E5:B3:BA:52:08:9E:EF:03:97:D5:88:1F:4C:1D:C7:D5:A4:B7:D3:2D` |
| Aura Clinic | `35:50:80:48:CE:E7:77:6C:A9:4A:45:A9:F0:4C:6A:87:0E:DF:98:72:94:29:48:2A:8A:D4:4E:89:DD:0B:BB:F2` |

## Where secrets live
- Keystore files: `C:\Users\Dell\AuraSigningKeys\` — outside the Git repository entirely (not merely gitignored inside it).
- Passwords: `C:\Users\Dell\AuraSigningKeys\{retail,clinic}.pass` — plain-text files, same directory, same "outside Git" protection. Randomly generated (32 chars, mixed case/digits/symbols), never typed by a human, never logged, never printed to a terminal transcript in full.
- Gradle wiring: `android/aura-retail/keystore.properties` and `android/aura-clinic/keystore.properties` (both git-ignored — verified via `git check-ignore -v` before creation, not assumed) reference the keystore path and read the password from the same file. Neither `.iss`/`.gradle`/any tracked file contains a password or the keystore itself.
- Template committed to Git (contains no secrets): `android/{aura-retail,aura-clinic}/keystore.properties.template`.

## A real bug found and fixed while setting this up
The pre-existing Gradle signing scaffold (from an earlier phase, never previously exercised with a real keystore) declared `signingConfigs { release {...} }` *after* `buildTypes {}` in both `build.gradle` files. Groovy evaluates the `android {}` closure top-to-bottom, and `buildTypes` referenced `signingConfigs.release` — a forward reference that fails once the `if (keystorePropsFile.exists())` guard actually evaluates to `true` for the first time (it never had, before this wave, since no `keystore.properties` had ever existed). Fixed by moving `signingConfigs {}` before `buildTypes {}` in both files.

A second real mistake, caught and self-corrected before it mattered: the first password-generation attempt used PowerShell's `-Encoding utf8`, which writes a UTF-8 **byte-order mark** on Windows PowerShell 5.1 — corrupting the saved password file relative to the password actually used to encrypt the keystore (which lived only in an in-memory session variable that doesn't persist between tool invocations). Caught by a byte-count check before any real use, both keystores were discarded and regenerated with BOM-free ASCII encoding, with an **immediate keytool-based verification reading the password back from the file** (not from memory) built into the same generation step this time.

## Build verification performed
- `apksigner verify --print-certs` on both release APKs: certificate DN and SHA-256 fingerprint exactly match the keystores above.
- `jarsigner -verify` on both release AABs: `jar verified` (warnings about self-signed/no-timestamp are expected and benign — normal for Android app signing, which doesn't use a CA chain or Authenticode-style timestamping the way Windows code signing does).

## Key-loss consequences
If either keystore or its password is permanently lost: **that product can never be updated again** on any device where a version signed with it is already installed — Android requires every update to a given `applicationId` to be signed with the same key. The only recovery is publishing a new application ID (a "new app" from every existing user's perspective, with no automatic upgrade path and no shared Play Store listing history). This is why the keystores are stored outside Git, backed up (see `android-key-backup-checklist.md`), and never regenerated casually.
