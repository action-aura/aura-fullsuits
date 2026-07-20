# Wave 1C -- Release Candidate Identity Verification (Part A)

## Git state at audit start
- Branch: `master`
- HEAD commit: `157521c46bc89dee10d44dd097534fce7461d135`
- Tag `commercial-packaging-wave1b-complete` resolves to the same commit (`git rev-parse commercial-packaging-wave1b-complete^{commit}` == HEAD). **Confirmed: the artifacts audited below are exactly the Wave 1B-tagged commit, with zero drift.**
- `git status --porcelain`: empty (clean tree) at audit start.

## Version / product identity metadata (read from source, not from prior docs)
| Product | `APP_VERSION` | `PRODUCT_CODE` | Source |
|---|---|---|---|
| Retail | `1.0.0-rc.1` | `AURA_RETAIL` | `products/retail/backend/config.py` |
| Clinic | `1.0.0-rc.1` | `AURA_CLINIC` | `products/clinic/backend/config.py` |

Both also import `SCHEMA_VERSION` from `commercial_runtime/backup/service.py` (shared schema-versioning module) and Retail additionally imports `CALCULATION_VERSION` from `core/retail/pricing.py` (financial-contract version marker).

## Release candidates evaluated
1. **Aura Retail Windows 1.0.0-rc.1** -- `dist/installers/AuraRetail-Setup-1.0.0-rc.1.exe`
2. **Aura Retail Android 1.0.0-rc.1** -- `android/aura-retail/app/build/outputs/{apk,bundle}/release/`
3. **Aura Clinic Windows 1.0.0-rc.1** -- `dist/installers/AuraClinic-Setup-1.0.0-rc.1.exe`
4. **Aura Clinic Android 1.0.0-rc.1** -- `android/aura-clinic/app/build/outputs/{apk,bundle}/release/`

## Checksums recomputed at Wave 1C audit start (SHA-256)
| Artifact | Checksum | vs. Wave 1B manifest |
|---|---|---|
| `AuraRetail-Setup-1.0.0-rc.1.exe` | `4318def84d8beb14ccdd74ff0c1a949304fe6f4a1ac6fc85ffdacb35cd70cb2e` | MATCH |
| `AuraClinic-Setup-1.0.0-rc.1.exe` | `5fd10135825840580dd7169c244221471bc12363c62d101937a894cd036cdcba` | MATCH |
| `aura-retail/app-release.apk` | `fd307cb417a35bbe96358579e1d329c059b3f7a395da42fb5cf4097bb6545471` | MATCH |
| `aura-retail/app-release.aab` | `1b00c667f66d326d0523a10dca5159f3a376aae4df8ddd0961369c6aa21d75f4` | MATCH |
| `aura-clinic/app-release.apk` | `49a4c337217beadb844639aa50168b30abf0ca7f739a560264a1848ac2221c76` | MATCH |
| `aura-clinic/app-release.aab` | `7a15e00984c1bfe422799c853952cb7cc0d02d9a243c05c4551df06bcaf88723` | MATCH |

All six artifacts on disk today are byte-identical to the Wave 1B release-candidate-manifest.md checksums. **No untracked rebuild, no silent artifact substitution since the tag.**

Fresh rebuilds were separately performed under this audit (Android: full `assembleRelease`/`bundleRelease` re-run; see `android-release-gate-report.md`) to confirm the *source* still produces working signed artifacts, independent of the pre-existing binaries above -- Gradle release builds are not guaranteed bit-reproducible, so a checksum difference on a *rebuilt* artifact is not itself a defect and is reported separately, not conflated with this identity-verification table.

## Traceability requirement (per spec Part A.5)
Every artifact evaluated in this audit traces to: product (Retail/Clinic) -> version (`1.0.0-rc.1`) -> commit (`157521c`) -> checksum (table above) -> build result (Wave 1B `build-and-test-gates-report.md` + this wave's fresh reruns) -> signing state (Windows: unsigned by explicit choice; Android: signed, production keystore, verified in `android-release-gate-report.md`). No artifact failing this chain is evaluated in this audit.
