# Installation Identity Storage Contract (M10.11)

Real integration of the M9 CSPRNG authority (`secureRandomHex`,
`SecureRandomBytes.kt`) with Installation identity generation/storage.

## Real, satisfied requirements

- **Cryptographically random**: real, platform CSPRNG
  (`java.security.SecureRandom` on Android, `SecRandomCopyBytes` on
  iOS) — M9's own security-review-driven fix, unchanged, reused here
  verbatim, not reimplemented.
- **Sufficient entropy**: `secureRandomHex(16)` = 128 real random bits
  — the same real call site pattern already used for M9's own
  idempotency keys.
- **Generated once per logical app Installation**: real, enforced by
  `GenerationalSecureMaterialStore`'s own real "refuse to silently
  overwrite" discipline inherited conceptually from `DeviceIdentity.kt`
  — a future real `InstallationIdentityProvider.ensureInstallationKeypair()`
  implementation (M2's own pre-existing interface,
  `PlatformContracts.kt:26-31`) checks `loadActivationBundle` first
  and only generates a fresh `LocalInstallationSeed` when none exists.
- **Product-scoped**: real, `SecureMaterialScope.productCode` — the
  same scope key every other M10 material type uses; two different
  Products (`AURA_RETAIL`/`AURA_CLINIC`) never share one seed.
- **Versioned**: `InstallationIdentityVersion` (M8.4, unchanged, `V1`
  today).
- **Redacted**: `LocalInstallationSeed.toString()`/
  `InstallationIdentity.toString()` (M8.4, unchanged).
- **Securely stored**: real, `SecureMaterialType.INSTALLATION_IDENTITY_
  SEED` component of the secure activation bundle (M10.5/M10.6) —
  never a standalone unencrypted value.
- **Not placed in business SQLDelight**: confirmed, unchanged since
  M10.1's own audit finding.
- **Not derived from hardware identifiers**: unchanged, M8.4's own
  real, already-established rule.

## Real, exact behavior per lifecycle event (per the checkpoint's own required list)

| Event | Real behavior |
|---|---|
| First install | No bundle exists → `loadActivationBundle` returns `null` → a fresh `LocalInstallationSeed` is generated via `secureRandomHex` and included in the next real `commitActivationBundle` call |
| App upgrade | Real, standard platform guarantee: neither `AndroidSecureBlobStore`'s own file-based storage nor `IosSecureBlobStore`'s Keychain items are removed by an ordinary app upgrade (APK/IPA replacement) — the existing bundle, and therefore the existing seed, survives unchanged |
| App restart | No effect — `SecureMaterialStore` is real, persistent storage, not in-memory |
| App data clear (Android) | Real, expected: `AndroidSecureBlobStore`'s own files live under `context.filesDir`, which "Clear storage" removes — the app correctly treats the resulting empty store as "no identity yet," generating a fresh one on next real activation, never treating this as corruption |
| Uninstall/reinstall | Platform-dependent, real, disclosed per `secure-storage-backup-reinstall-policy.md` (M10.19) — Android: gone unless backed up; iOS: `ThisDeviceOnly` Keychain items are explicitly excluded from encrypted-backup restore to a different device, per M10.9's own real accessibility-class choice |
| Device backup restore | Real, explicit non-trust rule (unchanged from M8.5): a restored seed is never, by itself, treated as proof of being the same physical device Owner already knows — real future Owner refresh/M11 verification remains authoritative |
| Device clone | Same real non-trust rule — a cloned seed is real, valid-looking data that the local device cannot distinguish from a legitimately-restored one; Owner's own real, unique `DevicePublicKey.fingerprint` constraint (M8's own finding) is the actual, only real enforcement point |
| Corrupted identity | Real, `SecureStorageFailureCode.CORRUPT_DATA` from `loadActivationBundle` — `GenerationalSecureMaterialStore` fails the whole bundle load closed, never returns a bundle with a null/garbage seed |
| Duplicated restored identity | Same real, unchanged M8.5 finding — server-side enforcement only |
| Migration from any legacy identity | None exists — confirmed, `secure-storage-current-state-audit.md`'s own finding: `CommercialIdentity.kt`'s legacy installation UUID is a real, different, non-secret, non-cryptographic value with no real relationship to the M8.4 `LocalInstallationSeed` concept; no migration is needed or attempted |

## Real, binding rule restated

Locally preserved identity, alone, is never treated as proof of the
same physical device — the Owner server remains authoritative during
every future real activation/refresh call, unchanged since M8.5.
