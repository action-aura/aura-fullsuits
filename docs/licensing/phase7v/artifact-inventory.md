# Phase 7V — Artifact Inventory (baseline)

## Windows

| Artifact | Path | Version | Signed | Notes |
|---|---|---|---|---|
| Clinic frozen exe | `dist/AuraClinic/AuraClinic.exe` | rc.2 (Phase 7 smoke-tested) | N/A (not an installer) | PyInstaller onedir build from Phase 7 Part Z |
| Retail frozen exe | `dist/AuraRetail/AuraRetail.exe` | rc.2 (Phase 7 smoke-tested) | N/A | same |
| Clinic installer | `dist/installers/AuraClinic-Setup-1.0.0-rc.1.exe` | **rc.1** | Unsigned | Wave 1B artifact, kept as the upgrade-from baseline for Part F |
| Retail installer | `dist/installers/AuraRetail-Setup-1.0.0-rc.1.exe` | **rc.1** | Unsigned | same |
| Clinic rc.2 installer | *(not yet built)* | rc.2 | — | Built in Part E |
| Retail rc.2 installer | *(not yet built)* | rc.2 | — | Built in Part E |

## Android

| Artifact | Path | Version | Signed | Notes |
|---|---|---|---|---|
| Clinic rc.1 APK/AAB | not present on disk | rc.1 | Signed (Wave 1B) | Cert fingerprint on record: `35:50:80:...:0B:BB:F2` (`docs/release/wave1b/release-candidate-manifest.md`) |
| Retail rc.1 APK/AAB | not present on disk | rc.1 | Signed (Wave 1B) | Cert fingerprint on record: `CA:E6:B1:...:B7:D3:2D` |
| Clinic rc.2 staging APK | built during Phase 7 Part Z (not retained as a named artifact) | rc.2 | Staging cert (test-only) | Not production-signed; superseded by Part H |
| Retail rc.2 staging APK | same | rc.2 | Staging cert | same |
| Clinic rc.2 signed APK/AAB | *(not yet built)* | rc.2 | — | Built in Part H with production keystore |
| Retail rc.2 signed APK/AAB | *(not yet built)* | rc.2 | — | Built in Part H |

## Signing material (outside Git, not copied into the repo)

- `C:\Users\Dell\AuraSigningKeys\clinic-release.keystore` + `clinic.pass`
- `C:\Users\Dell\AuraSigningKeys\retail-release.keystore` + `retail.pass`
- `android/aura-clinic/keystore.properties`, `android/aura-retail/keystore.properties` (gitignored,
  point at the files above)

## Release manifest history

- `docs/release/wave1b/release-candidate-manifest.md` — rc.1 manifest, all four platform/product
  combinations, checksums and certificate fingerprints recorded. Not overwritten by Phase 7V; the
  rc.2 manifest is a new file (`rc2-release-candidate-manifest.md`).

## Gap list carried into Part B onward

1. rc.2 Windows installers not built (Part E).
2. rc.2 Android production-signed artifacts not built (Part H).
3. No physical device connected (Part J gate).
4. Inno Setup not installed (Part B).
5. `pg_dump` not portably discoverable by Owner's backup service (Part B/C).
