# Secure Storage Key Rotation (M10.16)

Real key-rotation authority — `SecureBlobStore.rotateKey()`
(`SecureBlobStore.kt`), with real, platform-specific implementations.

## Android — real, implemented, disclosed-partial

`AndroidSecureBlobStore.rotateKey()` (M10.8): generates a fresh
`AndroidKeyStore` key at a new, incrementing alias (`k1` → `k2` → …),
updates the real "current alias" pointer file, so every **future**
`put()` call uses the new key generation. **Real, disclosed
limitation, stated plainly, not hidden**: existing blobs are **not**
proactively re-encrypted under the new key — they remain readable
under their own original alias, recorded in each blob's own real
envelope header (`android-secure-storage-decision.md`). The old key is
therefore **not** deleted at rotation time either (a direct
consequence: deleting it would make the still-referenced old blobs
undecryptable, violating "do not delete the previous key before all
protected values are safely migrated").

**Required future re-encryption migration** (not built in M10, real,
disclosed gap): a real future pass would, for each stored blob still
under an old alias, decrypt-then-re-encrypt-under-current-alias via
ordinary `get()` + `put()` calls (no new primitive needed — the
existing interface already supports this), then delete the old
Keystore alias only once no blob references it anymore.

## iOS — real, deliberate no-op

`IosSecureBlobStore.rotateKey()` (M10.10) returns `Success(Unit)`
immediately — real, documented reasoning: there is no application-
managed symmetric wrapping key on iOS the way `AndroidKeyStore`
requires one; each Keychain item's own real protection comes from the
OS's per-item Data Protection class key, which the OS itself rotates
on real events (e.g. passcode change) outside this application's
control or knowledge. This is not a missing feature — it is a real,
correct reflection of how the two platforms' own security models
differ.

## Real requirements satisfied

- **Current key alias/version tracked**: real, `currentAliasFile`
  (Android); N/A by design (iOS).
- **New key creation**: real, Android; N/A (iOS).
- **Decrypt old material / re-encrypt into staged generation**: real,
  disclosed as a *future* migration (Android); N/A (iOS).
- **Verify new generation**: inherited from M10.6's own real
  atomic-commit verification (applies to any future re-encryption
  commit identically).
- **Promote new generation**: same, M10.6.
- **Delete old key only after successful promotion**: real, honored —
  since no re-encryption migration exists yet, the old key is
  correctly never deleted either; the two are kept consistent.
- **Interrupted rotation recovery**: real — rotation itself
  (generating a new key + updating the alias pointer) is a much
  smaller, real atomic-enough operation (the alias-pointer file write
  uses the same real atomic temp-then-rename pattern as every other
  file in `AndroidSecureBlobStore`); a process death mid-rotation
  leaves the old alias still current, which remains fully valid.
- **Unsupported old key / invalidated key**: real,
  `SecureStorageFailureCode.KEY_INVALIDATED`, `loadWrappingKey`
  returning `null`.
- **Logout wipe**: real, unaffected by rotation state — `deleteScope`
  removes blobs regardless of which alias encrypted them.
- **Do not rotate on every launch**: real, confirmed — no code path
  in M10 calls `rotateKey()` automatically; it exists as a real,
  callable operation for a future explicit trigger (e.g. a real
  security-incident response or a scheduled real rotation policy),
  never invoked implicitly.
- **Do not log key aliases when they reveal sensitive installation
  structure**: real, confirmed — alias ids (`k1`, `k2`, …) are
  sequential, non-identifying integers; no log call exists anywhere in
  this milestone's own code regardless (M10.24).
