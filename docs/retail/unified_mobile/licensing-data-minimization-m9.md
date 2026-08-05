# Licensing Data Minimization (M9.22)

Real proof that every M9 request type cannot include prohibited
business data — extending M7.16/M8.7's own established allowlist
discipline to the new transport/orchestration layer.

## Real allowlisted request categories (per the checkpoint's own list)

External Customer identity fields (`CustomerRegisterRequest`/
`CustomerSignInRequest`/etc.), License serial during bounded claim/
activation (`LicenseClaimRequest`), Product/Platform
(`LicensingProductCode`/`LicensingPlatform`, both closed enums),
Installation identity (`InstallationIdentity`), bounded device
metadata (`DeviceMetadata`, the real, closed 8-field allowlist from
M8.7), app version/build (`DeviceMetadata.appVersion`/
`appBuildNumber`), release channel, idempotency identity
(`ActivationCommand.idempotencyKey`), session/Installation
authorization (`ExternalCustomerSessionId`), lease refresh data
(`LeaseRefreshRequest`).

## Real, structural proof — every M9 request type is closed

Every real M9 request type (`CustomerRegisterRequest`,
`CustomerSignInRequest`, `LicenseClaimRequest`, `ActivationCommand`,
`LeaseRefreshRequest`, `DeactivateInstallationRequest`, etc.) is a
plain Kotlin `data class` with explicit, named, typed fields — **none
contains a `Map<String, Any>`, a `JsonObject`, or any other open field
shape** (confirmed by direct inspection of every file under
`licensing/transport/`). This is the same structural guarantee
`ActivationCommandContracts.kt`'s own doc comment already states for
`ActivationCommand` specifically, true of every other M9 request type
for the identical reason: each field's own type was already, in a
prior milestone, proven closed and minimal.

## Real serialization allowlist test (M9.29)

`activationCommandFieldsMatchOnlyAllowedCategories` and
`deviceMetadataSerializesToExactlyTheEightAllowedFields` (M8, still
green, unchanged) together prove the two real, `@Serializable` request
shapes (`DeviceMetadata` fully, `ActivationCommand` transitively via
its own field types) — the non-`@Serializable` types
(`ActivationCommand` itself, `LicenseClaimRequest`, etc.) are proven
closed by Kotlin's own compile-time `data class` field enumeration
instead (no serialization round-trip needed to prove a plain Kotlin
class has no extra field — the constructor signature is the proof).

## No prohibited category reachable

Products/Categories/Suppliers/local Customers/inventory/Sales/Sale
Items/Returns/receipts/reports/imported files/import results/local
database/backup archives/dashboard metrics/unrelated local user
activity — none of these concepts have a corresponding field on any
M9 request type; introducing one would require modifying a request
class's own constructor, an explicit, reviewable, textual change, not
something that can happen silently through an open field.
