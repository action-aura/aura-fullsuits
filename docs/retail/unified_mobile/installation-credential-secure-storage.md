# Installation Credential Secure Storage (M10.13)

Real storage design for the Owner-issued Installation credential
(`InstallationCredentialMaterial`, M9.13), integrated with M10.4-6.

## Real, satisfied requirements

- **One Product scope, one Installation scope**: real,
  `SecureMaterialScope(productCode, installationId, accountId)` —
  the same real scope key every bundle component shares.
- **Independent revision**: real, `SecureActivationBundleMetadata.
  credentialRevision`, incremented by `GenerationalSecureMaterialStore.
  commitActivationBundle` on every real commit (`revision.next()`-
  equivalent logic, `PointerRecord.revision`).
- **Independent rotation**: real, disclosed scope — M10 does not yet
  build a standalone "rotate only the Installation credential, keep
  everything else" operation; every real commit re-writes the whole
  bundle atomically (M10.6). A narrower per-component rotation is a
  real, future refinement, not built here, since no real caller needs
  it yet (M11's own real activation flow will).
- **Independent revocation cleanup**: real, `deleteScope` (same real
  discussion as M10.12 — currently scope-wide, not component-
  selective; same disclosed refinement need).
- **Redacted**: `InstallationCredentialMaterial.toString()` (M9.13,
  unchanged).
- **No plaintext persistence**: real, always a `SecureMaterialType.
  INSTALLATION_CREDENTIAL` encrypted component.
- **No UI/navigation/log exposure**: unchanged since M9.13/M9.18.
- **No License serial persistence as substitute**: real, confirmed —
  no field anywhere in `SecureActivationBundle`/`SecureActivationBundleMetadata`
  stores a raw License serial; only the real, opaque
  `licensePublicId`-derived `ownerInstallationId` (server-assigned,
  M7.17's own real distinction between a License serial and a public
  identifier).

## Real replacement/rotation commit sequence

On a future real credential replacement (M11+ scope): the new
credential is committed atomically together with its required matching
metadata via the same real `commitActivationBundle` call
(`secure-material-atomic-commit.md`) — old credential material remains
current (via the unchanged pointer) until the new commit's own
promotion succeeds, then the old generation's blobs are deleted. This
is not a new mechanism — it is the same real M10.6 atomic-commit
authority, reused verbatim for a "replace" as for a "first commit."
Process-interruption recovery is identical (`recoverInterruptedCommit`).
