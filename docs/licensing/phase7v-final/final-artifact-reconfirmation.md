# Phase 7V-F — Final Artifact Reconfirmation (Part R)

Rebuilt multiple times this session as real defects were found and fixed (Android `cryptography`
dependency gap, `setIsStrongBoxBacked` lint blocker, Windows/Android trusted-time anchor-caching
P0). These are the FINAL checksums, after every fix, confirmed working via live testing.

## Windows

| Artifact | SHA-256 | Notes |
|---|---|---|
| `dist/installers/AuraClinic-Setup-1.0.0-rc.2.exe` | `ff9668c587f2d01d580365f30745a665be0cc50538bd1201de148ba6e172613e` | Includes the trusted-time fix; live-verified reaching RESTRICTED |
| `dist/installers/AuraRetail-Setup-1.0.0-rc.2.exe` | `756d92813ebd7ed7746bc13b142a2dd2fb7843937ea92b27deac2927c379f970` | Same |

rc.1 installers untouched, not overwritten.

## Android

| Artifact | SHA-256 | Size |
|---|---|---|
| Clinic APK (`app-release.apk`) | `bed04b713e86d73fa771591bd40cc451d8c51ffad52b143c7e25f1ca8d0a8fe7` | 54,142,629 bytes |
| Retail APK (`app-release.apk`) | `69aefff2b1ee24f5ee428974e8fc2ffcd42fb7f27b796182915e1a7355f24f7a` | 67,753,281 bytes |

Both include the `cryptography` fix, the `setIsStrongBoxBacked` lint fix, the real Owner URL
(`-PownerLicensingBaseUrl`), the real trust anchor, and the trusted-time fix. Certificate
fingerprints re-confirmed via `apksigner verify` this round: Clinic `35508048...0bbbf2`, Retail
`cae6b184...b7d32d` — both exactly matching rc.1 and every earlier rc.2 build this session.

**AAB not rebuilt in this final round** (only `assembleRelease` was re-run after the trusted-time
fix, to save time given this session's length) — the last AAB build (from the round with the
`cryptography` + lint fix, before the trusted-time fix) is stale relative to the final APK. Logged
as a residual item in `final-residual-risk-register.md`: rebuild AAB via `bundleRelease` before any
real Play Console submission.

## Reconfirmed for every rebuild this session

- Package IDs unchanged (`com.actionaura.clinic`, `com.actionaura.retail`).
- `versionName "1.0.0-rc.2"`, `versionCode 3` — unchanged, no bump needed.
- No secret leakage, no development endpoint baked in beyond this session's own deliberate
  validation-only `-PownerLicensingBaseUrl` (pointed at a local test Owner, not a real production
  instance — this build is not intended for real distribution as-is).
- No insecure-transport override reachable (Windows: `sys.frozen` forces `verify_tls=True`,
  re-confirmed unaffected by this session's changes; Android: still zero TLS-bypass code path).
- No debug build, no bypass flag, no synthetic data baked into any artifact.

## Not overwritten

rc.1 artifacts (Windows installers, and the real production-signed Android APKs still installed on
the physical device) were never touched or replaced by this session's builds.
