# Phase 8V-P — Final Artifact Build Report (Part E, Windows half)

## Real builds, this session, from final HEAD (after the `DEVICE_ALREADY_REGISTERED` fix)

| Artifact | Path | SHA-256 | Version | Size |
|---|---|---|---|---|
| Clinic frozen app | `dist/AuraClinic/AuraClinic.exe` | (see installer below; frozen dir not independently hashed, installer wraps it) | `1.0.0-rc.3` (confirmed via `Get-Item ... VersionInfo.ProductVersion`) | — |
| Clinic installer | `dist/installers/AuraClinic-Setup-1.0.0-rc.3.exe` | `94671480bc6718e5ebf7461370572ca011b2a6ace113ff4fc6aa37fa92bfbecd` | `1.0.0-rc.3` | 14,655,663 bytes |
| Retail frozen app | `dist/AuraRetail/AuraRetail.exe` | (installer wraps it) | `1.0.0-rc.3` | — |
| Retail installer | `dist/installers/AuraRetail-Setup-1.0.0-rc.3.exe` | `63c1f11857a11ba3fedbf385fce99a866185e2785f4e170e94dce86337b1ba6d` | `1.0.0-rc.3` | 15,434,075 bytes |

Built via the real, existing toolchain: `pyinstaller products/{clinic,retail}/packaging/aura_{clinic,retail}.spec --noconfirm`, then `ISCC.exe products/{clinic,retail}/packaging/aura_{clinic,retail}_setup.iss`. Existing `dist/installers/*-rc.1.exe` and `*-rc.2.exe` files untouched, sitting alongside.

## Verified real, not assumed

- Both frozen `.exe`s actually launched as real Windows processes (`Get-Process AuraClinic`/
  `AuraRetail` showed real PIDs), served real `/api/health`/`/api/version` responses, and completed
  five real end-to-end commercial scenarios against a real Owner server — see `scenario-*-evidence.md`.
- `ProductVersion` file resource on both frozen `.exe`s confirmed `1.0.0-rc.3` via
  `(Get-Item ...).VersionInfo.ProductVersion`.
- No secrets, no synthetic customer PII beyond intentionally-labeled test data ("Phase 8V-P Synthetic
  ... Co"), no developer-only endpoints baked in — `AURA_OWNER_LICENSING_URL` is runtime environment
  configuration, never hardcoded into the build.
- No TLS-bypass code shipped — `OWNER_LICENSING_VERIFY_TLS` remains hardcoded `True` for any frozen
  build regardless of environment variables (see `real-traffic-evidence.md`).
- No fake-clock, no hidden free-mode, no debug flag baked into the frozen build (PyInstaller
  `--noconfirm` uses the spec files' own existing, unmodified `debug=False` settings).

## Android APK/AAB

Not built this session — user's explicit choice, deferred to the device-access session (source
version bump already landed, see `final-product-version-decision.md`).

## Windows code signing

Unchanged from every prior wave's disclosed state: no real Authenticode certificate available in
this environment, both installers remain **UNSIGNED FOR PRODUCTION** (same disclosed limitation as
every rc.1/rc.2 build before them).
