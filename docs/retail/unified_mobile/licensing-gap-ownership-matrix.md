# Licensing Gap Ownership Matrix (M7.20)

Every real gap discovered during M7, classified by real ownership.
None of these gaps are fixed in M7 — a mobile-client gap is never
patched by changing Owner, and an Owner gap is never worked around
inside the mobile client.

| # | Gap | Classification | Evidence | Why this owner |
|---|---|---|---|---|
| 1 | Four independent device-limit representations (`Plan.max_device_count`, `Subscription.device_allowance`, `PlanEntitlement(max_devices)`, `License.device_limit`) with no reconciliation | `OWNER_SERVER` | `subscription-entitlement-license-map.md` | Only Owner's own catalog/subscription/licensing services write these rows; a mobile client only ever reads the effective, enforced value. |
| 2 | No customer-facing login/portal identity — precise classification: `EXTERNAL_CUSTOMER_IDENTITY_AND_LICENSING_BOUNDARY_MISSING` (**not** `AURA_OWNER_EMPLOYEE_AUTHENTICATION_MISSING` — Owner's own internal employee/staff authentication is fully implemented and unaffected; see `owner-internal-vs-customer-boundary.md`) | `OWNER_SERVER` | `customer-authentication-gap-analysis.md` | Would require new `Customer`-scoped auth model, session table, and routes in `owner/app/` — structurally separate from `StaffUser`/`StaffSession`, never a replacement or extension of them. |
| 3 | iOS not accepted — `["WINDOWS","ANDROID"]` hard-enumerated in JSON schemas and `License.allowed_platforms` defaults | `OWNER_SERVER` | `platform-authority-audit.md` | Localized but real Owner-side change (schema enum + form default), not a mobile-client workaround. |
| 4 | No minimum-app-version enforcement contract | `OWNER_SERVER` + `RELEASE_PIPELINE` | `mobile-release-version-contract.md` | Would require a new comparison field/route in Owner and a real publication process feeding it. |
| 5 | `GET /product-version-check` is prototype-only, disabled by default, unauthenticated | `OWNER_SERVER` + `RELEASE_PIPELINE` | `mobile-release-version-contract.md`, `remote-licensing-api-contract-map.md` | The route exists but is explicitly non-production per its own docstring; making it live is Owner/release-process work. |
| 6 | `commercial_runtime/licensing_contracts/canonical.py`'s own docstring references a shared fixture file (`tests/fixtures/canonical_vectors.json`) that does not exist | `COMMERCIAL_RUNTIME` (file itself still missing upstream; schema recovered in M8) | `offline-license-lease-contract-audit.md`, `missing-commercial-runtime-fixture-investigation.md` (M8.11) | Owned by that package's own test suite/maintainers for the file itself. M8.11 recovered the real vector schema from three cross-checked implementations and mirrored it into this branch's own `CanonicalVectorFixture.kt` — the schema question is resolved; the upstream file's own absence is not this branch's to fix. |
| 7 | No Kotlin port of the offline assertion-verification logic (`assertion_verifier.py`/`policy_evaluator.py`/`trust_store.py`) | `UNIFIED_MOBILE_COMMON` | `local-authorization-vs-license-entitlement.md`, `installation-credential-contract.md` | M7.17 deliberately stops at typed contract models per the checkpoint's own "do not implement HTTP execution yet" restriction; verification logic is real, needed, future `commonMain` work. |
| 8 | No Android platform implementation of secure device-key storage/generation | `ANDROID_ADAPTER` | `installation-identity-privacy-contract.md` | Explicitly deferred by the governing checkpoint ("do not implement secure storage yet"); will need an Android Keystore-backed `DeviceIdentityProvider`. |
| 9 | No iOS adapter exists at all (no `iosMain` compiled on this host, per M6's own disclosed constraint) | `IOS_ADAPTER` | M6 `ios-presentation-readiness.md` (carried forward, unchanged by M7) | Requires macOS/Xcode, outside this host's real capability; also blocked upstream by gap 3. |
| 10 | `DEVICE_POLICY_PLATFORM_CATEGORIES` (including a `MOBILE` combined cap) exists as real schema but was not found wired into `process_activation()`'s real enforcement path | `OWNER_SERVER` | `multi-device-license-policy-audit.md` | If a combined Android+iOS cap is ever desired, the wiring is Owner's own `licensing_service` work, not something a client can locally approximate correctly (the client cannot see other installations' platforms without a server round-trip). |
| 11 | `License` FKs `customer_id`/`subscription_id`/`product_id`/`plan_id` independently with no DB constraint enforcing mutual consistency | `OWNER_SERVER` | `subscription-entitlement-license-map.md` | Schema/constraint-level fix, entirely server-side. |
| 12 | No dedicated per-subscription grace-period column (policy-driven instead, at `CommercialPolicy`) | Not a gap — real, intentional design | `owner-licensing-authority-audit.md` | Recorded for completeness; no ownership action needed. |

## Real, deliberately out-of-scope-for-M7 items (already known, not new findings)

- Full device activation implementation — deferred per M7's own scope
  prohibition, will span `UNIFIED_MOBILE_COMMON` + `ANDROID_ADAPTER`
  (+ `IOS_ADAPTER` once gap 3/9 close) in a later milestone.
- Secure storage — `ANDROID_ADAPTER`/`IOS_ADAPTER`, later milestone.
- Offline lease verification wiring into the app's runtime — 
  `UNIFIED_MOBILE_COMMON`, later milestone (builds on gap 7).

## External infrastructure

No gap discovered in M7 was classified `EXTERNAL_INFRASTRUCTURE` —
every real finding traces to Owner code, `commercial_runtime`, or
future mobile-side work; nothing depends on a third-party service
outside this codebase's own control.
