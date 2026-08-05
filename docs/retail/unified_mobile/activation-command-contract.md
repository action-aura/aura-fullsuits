# Activation Command Contract (M9.10)

`ActivationCommand` (`shared/.../licensing/transport/
ActivationCommandContracts.kt`) — immutable, explicit typed fields
only, no `Map<String, Any>`.

## Real fields

`customerSessionId`, `licenseClaimReference`, `productCode`,
`platform`, `installationIdentity` (real M8 `InstallationIdentity`),
`deviceMetadata` (real M8 `DeviceMetadata`, the closed 8-field
allowlist), `idempotencyKey`, `clientContractVersion` (default `"v1"`).

## Real, structural exclusions (compile-time, not merely documented)

No field on this type can represent: client-selected device limit,
client-selected active count, client-selected remaining slots,
client-selected License expiry, client-selected entitlement, local
Products, inventory, Sales, Returns, Customer database, imported
files, or backup data — because `ActivationCommand`'s own field list
above is exhaustive and every field's own type
(`ResolvedDevicePolicy`, `DeviceMetadata`, etc.) was already, in a
prior milestone, proven closed and minimal (M8.15/M8.16's own
structural proofs). Adding any prohibited field here would require
first violating one of those already-tested invariants.

## Redaction

`toString()` redacts `licenseClaimReference` (the real, indirect
license-claim reference, kept out of logs the same way
`LicenseClaimRequest`/`ActivationRequest` already redact their own
license-serial/license-key fields).
