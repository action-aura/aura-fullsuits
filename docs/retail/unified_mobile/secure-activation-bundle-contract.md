# Secure Activation Bundle Contract (M10.5)

`SecureActivationBundle`/`SecureActivationBundleMetadata`
(`shared/.../securestorage/SecureActivationBundle.kt`) — real
components, aligned exactly with the already-accepted M7-M9 contracts,
no new business field invented.

## Real components

- `customerAccessCredential`/`customerRefreshCredential`
  (`ExternalCustomerAccessCredential`/`RefreshCredential`, M9.6,
  nullable — see M10.12's own "may be memory-only" real design choice)
- `installationIdentity` (`InstallationIdentity`, M8.4)
- `installationCredential` (`InstallationCredentialMaterial`, M9.13)
- `rawSignedLease` (`SignedAssertionEnvelope`, M7.17)
- `metadata` (`SecureActivationBundleMetadata`) — real non-secret
  fields: `productCode`, `platform`, `ownerInstallationId`,
  `customerAccountId`, `contractVersion`, `credentialRevision`,
  `leaseRevision`, `createdAtIso8601`, `lastCommittedAtIso8601`,
  `safeServerTimeObservationIso8601`, `resolvedPolicyVersion`,
  `resolvedPolicySnapshotTrusted`, `releaseChannel`

## Real secret/metadata separation

`SecureActivationBundle` is the in-memory, pre-commit/post-load shape
only — `GenerationalSecureMaterialStore` (M10.6) never serializes it
as one plaintext blob; each secret component is committed as its own
separately-encrypted `SecureBlobStore` blob, keyed and associated-
data-bound independently (`buildComponents`,
`GenerationalSecureMaterialStore.kt`). Metadata is its own real,
separately-committed component (`ACTIVATION_BUNDLE_METADATA`), also
encrypted (not left plaintext merely because its fields are
individually non-secret — real, deliberate defense-in-depth, since
`credentialRevision`/`leaseRevision`/timestamps are still useful
reconnaissance material an attacker should not get for free).

## `ResolvedDevicePolicy` snapshot — real, explicit "untrusted until refreshed" rule

The full `ResolvedDevicePolicy` (M8.1) is **not** stored as permanent
commercial authority in this bundle. Only `resolvedPolicyVersion` (a
bare version number) and `resolvedPolicySnapshotTrusted` (always
`false` for any snapshot this milestone writes) exist on
`SecureActivationBundleMetadata` — real, deliberate: M10 never
persists a full policy snapshot as something a future read could
mistake for current, live commercial authority. Any future milestone
that does retain a fuller policy snapshot for UI/recovery purposes
must version it and preserve this same "untrusted until refreshed"
marker, per the checkpoint's own explicit requirement.

## Real redaction

`SecureActivationBundle.toString()` redacts every one of the five
secret/identity fields — only `metadata` (itself non-secret) and the
literal string `<redacted>` for each other field appear.
