# Platform Contract Reconciliation (M8.0)

Real audit of every platform representation found across the whole
codebase, and the one shared mobile `Platform` identifier this
milestone establishes to replace them going forward (in the Unified
Mobile client only — no existing representation elsewhere is modified).

## Every real platform representation found

| Location | Representation | Real values found | Citation |
|---|---|---|---|
| Owner `owner_platforms` table | DB-backed table, `platform_code` string column | `WINDOWS`, `ANDROID` (seeded) | `platform-authority-audit.md` (M7.8), unchanged |
| Owner JSON schemas | `enum` string | `["WINDOWS","ANDROID"]` | `activation-request-v1.schema.json`, `installation-registration-v1.schema.json` |
| Owner `License.allowed_platforms` | comma-separated string, no type | Free string, observed values `WINDOWS`/`ANDROID` | `license-state-machine-contract.md` |
| Owner `activation_governance.DEVICE_POLICY_PLATFORM_CATEGORIES` | Python tuple of strings | `WINDOWS`, `ANDROID`, `IOS`, `MOBILE` (category concept, not `owner_platforms`) | `platform-authority-audit.md` |
| Owner `employees.PRESENCE_PLATFORMS` | Python tuple of strings | `WEB`, `ANDROID`, `IOS` (staff presence, unrelated to licensing) | `platform-authority-audit.md` |
| `commercial_runtime/licensing_contracts` production code | Opaque `str` parameter, no enum type anywhere in non-test source (`assertion_verifier.py`, `activation.py`, `client.py`, `device_identity.py`) | Whatever the caller passes; the package itself defines no closed platform type | Grep across `commercial_runtime/licensing_contracts/*.py` (excluding `tests/`) for `WINDOWS`/`ANDROID`/`IOS` found matches only in `test_support.py` and `tests/`, never in production modules |
| `commercial_runtime/licensing_contracts` test fixtures | String literal | `WINDOWS` (default), `ANDROID` (used in several tests) — never `IOS` | `tests/test_*.py` (24 files) |
| Legacy Android app (`android/aura-retail`, `android/aura-clinic`) `OwnerClient.activate(...)` | Raw `String` parameter, no Kotlin enum | `"ANDROID"` (only value ever passed) | `android/aura-retail/app/src/test/java/.../OwnerClientTest.kt:84,176-177` (Clinic app mirrors this exactly) |
| Retail Windows (via `commercial_runtime`) | Same opaque string mechanism | `"WINDOWS"` (only value ever passed in that product's own real usage) | Inferred from `commercial_runtime` test defaults (`platform="WINDOWS"` is the fixture default throughout) |
| M7 shared `LicensingPlatform` enum (`shared/.../licensing/LicensingEnums.kt`) | Kotlin `enum class` | `WINDOWS`, `ANDROID` | M7.17, this repository |
| M7 fixtures (`LicensingFixtures.kt`) | Uses the M7 enum | `WINDOWS`, `ANDROID` | M7.18, this repository |
| Clinic client | No independent representation — mirrors the Retail Android client's own raw-string mechanism exactly | `"ANDROID"` | Same `OwnerClientTest.kt` pattern in `android/aura-clinic` |

## Real conclusion

**No existing representation anywhere in the codebase — Owner,
`commercial_runtime`, or either legacy client — is a closed,
exhaustive, IOS-inclusive type today.** Every real production platform
value observed is a bare string (`"WINDOWS"` or `"ANDROID"`), never
validated against a closed enum client-side; only Owner's JSON-schema
`enum` and the M7.17 Kotlin enum are closed. Neither of those two
closed representations includes `IOS`.

## The one shared mobile Platform identifier (M8, this repository only)

`shared/src/commonMain/kotlin/com/actionaura/retail/licensing/
LicensingEnums.kt`'s `LicensingPlatform` enum is extended in M8 to
add `IOS` as a real, explicit third case — **client-side contract
modeling only**, per M8's own scope: it does not imply Owner accepts
it (`ios-platform-readiness-state.md` governs that distinction). No
`ALL`, `ANY`, `MOBILE`, or open-string case is added — confirmed
absent from every real production representation surveyed above, so
none is invented here either.

A new `PlatformDecodeResult` sealed type (M8.0, this repository)
represents parsing an arbitrary platform string from a server response
or local input:

- `Known(LicensingPlatform)` — for `WINDOWS`/`ANDROID`/`IOS`.
- `UnsupportedPlatform(raw: String)` — for anything else, including
  `"ALL"`, `"ANY"`, `"MOBILE"`, empty string, wrong case, or any future
  value this client doesn't yet recognize.

`UnsupportedPlatform` is never coerced to an existing case, never
defaults to `ANDROID`, and is never treated as an activation-eligible
result — matching this document's own real-evidence finding that no
existing system anywhere silently maps an unrecognized platform to a
fallback (this was already independently confirmed for Owner's own
server-side behavior in `platform-authority-audit.md`, M7.8's "no
ALL-platform / unknown-platform-fallback / case-insensitive bypass"
proof; M8 extends the same discipline to the client's own parsing).

## What this reconciliation does NOT do

- Does not modify `owner_platforms`, any Owner schema, or any Owner
  route.
- Does not modify `commercial_runtime/licensing_contracts`.
- Does not modify the legacy Android/Clinic clients.
- Does not claim Owner accepts `IOS` today — see
  `ios-platform-readiness-state.md` for the real, current readiness
  classification (`CONTRACT_SUPPORTED` + `OWNER_PLATFORM_UNSEEDED` +
  ...), unchanged from M7.8's `SCHEMA_READY_BUT_UNSEEDED` finding.
