# Secure Material Data Classification (M10.3)

Real classification of every licensing-related value, extending the
already-real M7-M9 type inventory — no new value is invented here,
only classified.

## A. Highly sensitive secret

`ExternalCustomerAccessCredential`/`ExternalCustomerRefreshCredential`
(M9.6), `InstallationCredentialMaterial` (M9.13, the real Installation
credential), the password parameter during an active `signIn` request
(M9.7, never persisted at all — not even in category A storage;
exists only for the duration of one function call), a future reset/
verification token (real, not-yet-implemented server contract,
M8.10's own spec item 12).

**Store**: `SecureMaterialStore` (M10.4) only, `HIGHLY_SENSITIVE`
material class, platform-Keystore/Keychain-backed, associated-data-
bound to Product/Platform/scope.

## B. Signed but sensitive commercial material

`SignedAssertionEnvelope` (M7.17) — the raw signed lease. Its own
`payload` sub-fields not intended for ordinary UI exposure (real,
already-established in `activation-response-processing.md`: "no
ordinary feature code gets direct lease bytes").

**Store**: `SecureMaterialStore`, `COMMERCIAL_MATERIAL` class —
protected identically to category A for storage purposes (same
platform-backed protection), but conceptually distinct because M10
never *trusts* this value (M11's own scope) the way it would trust a
live credential.

## C. Private Installation identity

`LocalInstallationSeed` (M8.4) — the real random seed.
`InstallationIdentity.status`/`generatedAt` are borderline (see
category D) but the `seed` field itself is category C.

**Store**: `SecureMaterialStore`, `INSTALLATION_IDENTITY` class.

## D. Non-secret security metadata

`InstallationDescriptor.installationId` (server-assigned, opaque, not
itself a credential), `LicensingProductCode`/`LicensingPlatform`
(closed enums), `ActivationCommand.clientContractVersion`,
`SecureMaterialRevision`/credential-revision/lease-revision counters
(M10.4/M10.16), key alias/version identifiers (M10.16, when not
sensitive-structure-revealing — see the threat model's own "do not log
key aliases when they reveal sensitive installation structure" rule),
last-successful-secure-commit timestamp, safe retry/idempotency state
(M9.11's own real finding: the idempotency key itself is non-secret).

**Store**: may live in the same `SecureMaterialStore` (bundled
alongside its own category A/B/C material, per M10.5's own bundle
design) or in ordinary, non-encrypted app-private storage — **never**
required to be encrypted, though co-locating it with its own
encrypted bundle for atomicity purposes (M10.6) is real, permitted
design, not a security requirement violation.

## E. Presentation-safe state

`ActivationState` (M9.9, a plain enum), `DevicePolicyPresentationState`
(M8.12), `LicenseStatus`/`InstallationStatus` display values,
`DeviceMetadata.deviceLabel`/`osVersionMajor`/etc. (M8.7, the real,
already-closed 8-field allowlist), a future post-M11 expiry display
value.

**Store**: ordinary Compose `UiState`/`ViewModel` state — never
requires `SecureMaterialStore` at all; this is exactly the material
`ActivationUiState` (M9.18) already correctly holds today.

## Real, binding placement rule

Categories A, B, and C must never appear in: ordinary SQLDelight
business tables (none exist for licensing material — confirmed,
M10.1), plain `SharedPreferences`/`NSUserDefaults` (confirmed absent
for any secret value, M10.1), navigation arguments (no `AuraRoute`
type references any of these, unchanged since M9), Compose `UiState`
(confirmed by direct inspection of `ActivationUiState`, M9.18 — no
field of categories A/B/C exists there), logs (M9.23's own zero-
logging-call finding, extended to M10), or analytics/crash metadata
(`CrashRedaction`'s own real, pre-existing contract, M2, exists
specifically to prevent this).

## Real mapping to already-existing M7-M9 types — nothing reclassified incorrectly

Every M7.17/M8/M9 type that was already redacted in `toString()`
(`ExternalCustomerAccessCredential`, `InstallationCredentialMaterial`,
`LocalInstallationSeed`, `SignedAssertionEnvelope`, License
serial/key fields) is category A, B, or C here — confirming the
redaction discipline already applied in those milestones was the
*correct* discipline for what M10 now formally classifies, not a
retroactive correction.
