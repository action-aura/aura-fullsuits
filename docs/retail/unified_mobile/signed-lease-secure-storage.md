# Signed Lease Secure Storage (M10.14)

Real storage design for the raw signed lease (`SignedAssertionEnvelope`,
M7.17), integrated with M10.4-6. **M10 stores it but does not trust
it** — real, binding, unchanged since M9.14's own disclosed scope
boundary.

## Real, satisfied requirements

- **Raw lease never placed in Compose state**: unchanged, M9.18's own
  real `ActivationUiState` field audit.
- **Raw lease never logged**: unchanged, M9.23's own zero-logging-call
  finding, re-confirmed for M10's own new code.
- **Raw lease never treated as verified**: real, structural —
  `SecureActivationBundle.rawSignedLease` is typed as the real
  `SignedAssertionEnvelope` (an unverified envelope type, M7.17), never
  a hypothetical "VerifiedLease" type; nothing in M10's own code calls
  or references any verification function (none exists yet — M11
  scope).
- **Versioned**: real, `SecureActivationBundleMetadata.leaseRevision`.
- **Lease revision stored**: same field, real.
- **Matching Installation identifier stored**: real,
  `SecureActivationBundleMetadata.ownerInstallationId`, committed
  atomically alongside the lease as one real generation
  (`secure-material-atomic-commit.md`'s own "no lease becomes current
  without its matching Installation identity" guarantee).
- **Matching Product and Platform stored**: real,
  `SecureActivationBundleMetadata.productCode`/`platform`.
- **Corruption fails closed**: real, `loadGeneration`'s own decode
  try/catch around `SignedAssertionEnvelope` deserialization — a
  malformed stored lease produces `SecureStorageFailureCode.CORRUPT_
  DATA`, never a partially-decoded object.
- **Unknown version preserved only when safe and classified
  unsupported**: real, disclosed scope — M10's own `SecureMaterialVersion`
  check (`M10.15`) rejects an unrecognized `storageFormatVersion`
  before attempting to decode the lease at all, so an "unknown version"
  lease is never partially parsed and misinterpreted.
- **New lease replacement is atomic**: real, same M10.6 mechanism as
  M10.13's own credential replacement.
- **Previous verified lease policy**: explicitly deferred to M11, per
  the checkpoint's own instruction — no such policy exists in M10.

## Real narrow internal verification boundary (for M11)

M10 exposes the raw lease only through `SecureMaterialStore.
loadActivationBundle`'s own `SecureActivationBundle.rawSignedLease`
field — real, ordinary feature code has no separate, narrower path to
"just the lease bytes without the rest of the bundle." A real, future
M11 verification boundary (a dedicated function/class consuming
exactly `SignedAssertionEnvelope` + a real, ported `assertion_verifier`
equivalent, per `licensing-gap-ownership-matrix.md` gap #7) is not
built in M10 — this document records that the *storage* layer already
provides everything M11 will need (an intact, corruption-checked,
generation-consistent envelope), without M10 itself performing any
verification.

## Ordinary feature code never gets direct lease bytes

Confirmed by design: no M9/M10 code outside `securestorage`/
`ActivationResponseProcessor` (M9.12, which only ever *writes* the
lease via `SignedLeaseSink`, never reads a stored one back) references
`rawSignedLease` or attempts to interpret its contents.
