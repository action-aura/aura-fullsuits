# Phase 7V — Release Artifact Security Inspection (Part P)

Inspected all four rc.2 release artifact families: Windows frozen executables + installers
(Clinic, Retail), Android signed APK/AAB (Clinic, Retail).

## Method

- `strings` scan of both Windows installers and both frozen `.exe` files for license-key patterns,
  hardcoded localhost/dev ports, the insecure-TLS env var, and password-shaped strings.
- Full recursive file listing of both Windows `dist/` trees for `secret.key`, `trust_anchor.json`,
  database files.
- Both Android APKs unzipped and inspected: `assets/`, `BuildConfig` (`DEBUG`,
  `OWNER_LICENSING_BASE_URL`), and the Chaquopy Python source archive
  (`assets/chaquopy/app.imy`) listed for any bundled secrets/config.

## Windows (both products)

| Check | Result |
|---|---|
| Plaintext license keys | None found |
| Private device keys | None (device keys are generated per-install at first run, not present in a build artifact) |
| Owner signing private keys | None — Owner's signing keys live only in `owner/var/signing-keys/`, never referenced by either product's PyInstaller spec |
| Production keystore material | N/A (Windows uses DPAPI, not a keystore file) |
| `secret.key` | None (generated at first run, gitignored, never a build input) |
| Synthetic customer/patient/sales data | None — clean build from source, no bundled databases |
| Internal shared secret (Android-only concept) | N/A for Windows |
| Developer machine paths / hardcoded IPs | None found in `strings` scan or loose frontend assets |
| HTTP production endpoints | None — `OWNER_LICENSING_BASE_URL` defaults to empty string, operator-configured at deployment, never compiled in |
| TLS verification bypass reachable in a commercial build | **Was reachable before this session's fix** (Part G) — now confirmed closed: frozen builds force `verify_tls=True` regardless of environment variables |
| Trust-all certificate logic | None found |
| Demo license / bypass flag / raw `licensed=true` | None — every gate resolves through `capability_guard.evaluate_capability()` against a signature-verified state, never a boolean flag |
| Debug mode | `PyInstaller` release build, no debug flags in either `.spec` |
| Staging/expired trust anchor | None bundled — trust anchor is absent from these particular build artifacts (see note below) |
| Wrong application ID / version metadata | Verified correct: `1.0.0-rc.2` embedded in both installers' version resource and `APP_VERSION` |

**Note on trust anchor**: the final shipped `dist/AuraClinic`/`dist/AuraRetail` trees and their
installers do **not** contain a `trust_anchor.json` (confirmed absent by `find`). This is correct
for this environment — no real production Owner instance exists to generate one against, and
Phase 7V's live activation testing intentionally used an ephemeral test Owner's key, which was
generated, used, and then explicitly removed before the final rebuild (see
`windows-rc1-to-rc2-installer-validation.md`) specifically so it would never ship. A real release
cut requires running `scripts/generate_trust_anchor.py` against the real production Owner
immediately before the final build — documented as a required release step, not performed here
since no such Owner instance exists in this environment.

## Android (both products)

| Check | Result |
|---|---|
| `trust_anchor.json` | Not bundled (same reasoning as Windows — no production Owner to generate against) |
| `keystore.properties` / private key material | Not present in either APK (confirmed via `unzip -l` + `find`) |
| Database credentials | N/A (products use local SQLite, no DB credentials exist) |
| `secret.key` | Not present (per-process, generated at runtime, never in the APK) |
| Synthetic patient/sales data | None bundled |
| Internal shared secret | Not in `BuildConfig`, not in `assets/` (generated fresh per process at runtime, per `android-authority-boundary-physical-report.md`) |
| Developer machine paths / hardcoded IPs | None found |
| `OWNER_LICENSING_BASE_URL` in `BuildConfig` | Empty string (`""`) in both release builds — confirmed via decompiled `BuildConfig.java` |
| `DEBUG` flag | `false` in both release builds |
| TLS bypass / trust-all logic | None — confirmed absent in `net/*.kt` (Part G), platform-default `OkHttpClient`, no custom `TrustManager` |
| Demo license / bypass flag | None |
| Expired/staging trust anchor | N/A, none bundled |
| Wrong application ID / version metadata | Verified correct: `applicationId` unchanged, `versionName "1.0.0-rc.2"`, `versionCode 3` |

**Minor hygiene observation (not a security finding)**: both APKs' bundled Python source archive
(`assets/chaquopy/app.imy`) includes `commercial_runtime/licensing_contracts/tests/*.py` (test
source files, e.g. `test_generate_trust_anchor.py`, `test_trust_anchor_loader.py`) — these are test
*code*, contain no secrets or data, but do add unnecessary size and expose internal test
structure/naming to anyone who unpacks the APK. Logged in
`phase7v-residual-risk-register.md` as a low-priority packaging-hygiene item, not fixed in this
session (out of the P0/P1 minimal-fix scope).

## Public keys vs private key leakage (explicit distinction, per spec instruction)

No public Owner trust-anchor content was present in any artifact to evaluate in this pass (see
above) — the distinction between "public key present" (allowed) and "private key present"
(forbidden) did not arise, since no key material of either kind was found in any of the four
artifact families.

## Verdict

**PASS** — no secrets, no private key material, no hardcoded dev endpoints, no debug flags, no
licensing bypass found in any of the four rc.2 release artifact families. The one real
commercial-build bypass this inspection was designed to catch (Windows insecure-TLS override) was
found and fixed earlier in this session (Part G), and this inspection confirms it is closed in the
actual shipped binaries, not just in source.
