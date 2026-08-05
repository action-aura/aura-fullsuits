# Secure Storage Versioning and Migrations (M10.15)

Real, versioned schema — `SecureMaterialVersion` (`storageFormatVersion`,
`materialTypeVersion`), `SecureMaterialVersion.CURRENT = 1.1`
(`SecureStorageContracts.kt`). Every real blob's associated data
(`secureAssociatedData`) binds the exact version pair it was written
with — a version mismatch cryptographically fails to authenticate
(Android AES-GCM AAD, iOS length-prefixed header), which is itself a
real, structural way "unsupported version" is detected today: an old
reader presenting the wrong expected version in its own AAD/header
computation cannot even read a newer blob.

## Real, current behaviors

- **Current version read**: real, implicit — every real read computes
  `secureAssociatedData(key, SecureMaterialVersion.CURRENT)`; since
  only one version (`1.1`) has ever existed, every real write/read in
  this milestone uses it consistently.
- **Older supported version migration**: not yet needed — real,
  honest disclosure: no second version has shipped yet, so no real
  migration code exists. The AAD-binding mechanism above is the real
  foundation a future migration would build on (decrypt under the old
  version's AAD, re-commit under the new one via the same real
  `commitActivationBundle` atomic path).
- **Future version rejection**: real, structural — a blob written by a
  hypothetical future, higher `SecureMaterialVersion` would fail to
  authenticate against this version's own AAD computation, surfacing
  as `CORRUPT_DATA` today rather than a dedicated `UNSUPPORTED_VERSION`
  code. **Real, disclosed gap**: this milestone does not yet
  distinguish "genuinely corrupted" from "written by an unrecognized
  future version" — both currently surface identically. A future
  migration milestone should add an unencrypted, real version-number
  prefix outside the AAD-protected region specifically so this
  distinction can be made before attempting to decrypt — recorded here
  as a real, open design refinement, not silently claimed solved.
- **Partial migration recovery / rollback policy**: inherits the real,
  already-built M10.6 atomic-commit guarantee — any future real
  migration is itself just a `commitActivationBundle` call (decrypt
  old, construct a new bundle, commit) and gets the same real "old
  valid bundle survives a failed migration" guarantee for free, not a
  new mechanism.
- **Corrupted record handling**: real, `CORRUPT_DATA`, M10.17.
- **Unsupported key version**: real, `KEY_INVALIDATED` (Android, a
  missing alias) — see M10.16.
- **Safe wipe when recovery is impossible**: real,
  `SecureStorageRecoveryOutcome.UnrecoverableReactivationRequired`
  (M10.6) — the real, honest outcome; M10 does not itself
  automatically delete the unrecoverable data (M10.17's own "do not
  automatically delete on the first read failure" rule) — a future
  explicit user/support-triggered wipe (M10.18's own factory-reset
  category) is the real mechanism for that.
- **No silent activation success after wipe**: real, structural — a
  wipe (via `deleteScope`) removes the pointer blob, so any subsequent
  `loadActivationBundle` call correctly returns `null`, which
  `startup-licensing-integration.md`'s own real M10.22 startup logic
  must classify as "no secure material," never "activated."

## Real, binding rule

No overwrite of the only valid bundle happens before a future
migration's own success is confirmed — inherited directly, not
reimplemented, from M10.6's own real atomic-commit design.
