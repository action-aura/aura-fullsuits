# Device Metadata Minimization (M8.7)

Maximum bounded metadata the client may later send, extending M7's own
`owner-data-minimization-contract.md` allowlist with device-management
context specifically. Every field below is classified; nothing outside
this list is permitted by the shared model.

## Allowed fields

| Field | Purpose | Necessity | Retention expectation | Logging policy | Classification |
|---|---|---|---|---|---|
| `deviceLabel` | User-facing identification of a device in a device list | Support/UX | Until device is deactivated/replaced | Never logged with other PII-adjacent fields | Optional |
| `platform` | Real, required activation/policy field (M7.17) | Required by Owner's real schema | Lifetime of Installation | Safe to log (not sensitive) | Required |
| `osVersionMajor` | Support diagnostics only — major version, not full build string | Diagnostic | Refreshed on check-in | Safe to log | Optional |
| `appVersion` | Real, required activation field (M7.17) | Required by Owner's real schema | Lifetime of Installation, refreshed on check-in | Safe to log | Required |
| `appBuildNumber` | Support diagnostics | Diagnostic | Refreshed on check-in | Safe to log | Optional |
| `deviceModelFamily` | Support diagnostics only when genuinely needed to reproduce a device-specific bug (e.g. "Pixel", never a full serial-bearing model string) | Diagnostic, only when necessary | Refreshed on check-in | Safe to log | Optional |
| `locale` | Support diagnostics / future localized error messages | Diagnostic | Refreshed on check-in | Safe to log | Optional |
| `timezone` | Only when genuinely required for licensing diagnostics (e.g. explaining an apparent clock-rollback false-positive to support staff) | Diagnostic, narrowly scoped | Refreshed on check-in | Safe to log | Optional |

## Explicitly prohibited (real, enumerated, matching the checkpoint's own list)

Exact location, contacts, files, local Products, local Customers,
inventory, Sales, receipts, barcode history, imported file names
(except within Import Center's own local-only UI, never sent to
licensing per `owner-data-minimization-contract.md`), advertising
identifiers, telecom identifiers (IMEI/phone number), Wi-Fi
identifiers (MAC/SSID), hardware fingerprints/serials. None of these
appear in any shared licensing model, in M7.17 or M8.

## Shared model

`DeviceMetadata` (`shared/.../licensing/DeviceManagementContracts.kt`)
— a closed data class containing only the allowed fields above, all
except `platform`/`appVersion` nullable/optional. No open `Map` field
exists on this type (matching M7.16's own discipline).

## Real serialization allowlist test (M8.15/M8.16)

Mirrors `activationRequestSerializesToExactlyTheRealOwnerAllowlist`
(M7.18): `DeviceMetadata` is `@Serializable`, and a test asserts its
JSON key set is exactly the 8 allowed field names above — no more, no
less. A second test asserts none of the prohibited category strings
(`"location"`, `"contacts"`, `"imei"`, `"mac_address"`, etc.) ever
appear as a key in any serialized shared licensing model, across every
`@Serializable` type in the `licensing` package — a structural,
executable guard, not merely documentation.
