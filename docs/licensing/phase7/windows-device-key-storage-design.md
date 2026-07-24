# Phase 7 -- Windows Device Key Storage Design

## Decision

Ed25519 keypair generated locally with `cryptography` (already a Phase 6 Owner dependency; will be added to the product Windows `requirements` -- Windows Python has no wheel-availability constraint the way Chaquopy/Android does). Private key encrypted at rest with **Windows DPAPI** (`CryptProtectData`/`CryptUnprotectData` via the `pywin32` or `ctypes`-direct binding -- no new heavyweight dependency required, `ctypes.windll.crypt32` is stdlib-adjacent) using **current-user scope**, matching the existing deployment model.

## Why current-user scope, not machine scope

`AURA_APP_DATA` already resolves to `%LOCALAPPDATA%\AuraRetail` / `AuraClinic` (per-user, not per-machine) -- `launcher_retail.py` confirms this today. DPAPI machine-scope would let any user on a shared machine decrypt the key; user-scope matches the product's existing single-user-per-Windows-profile assumption exactly. If a future multi-user-per-machine deployment model is adopted, this decision is revisited then -- not speculated about now.

## Storage layout

`%LOCALAPPDATA%\Aura<Product>\licensing\device_key.dpapi` -- DPAPI-blob bytes (opaque, includes its own integrity check; DPAPI decryption fails closed on any tampering, no separate HMAC needed). Directory ACL: inherited from `%LOCALAPPDATA%`, which is already user-exclusive by Windows default -- no additional `icacls` hardening needed beyond confirming (and testing) that default inheritance actually applies, since `%LOCALAPPDATA%` is not attacker-writable by other standard (non-admin) users by default.

Companion metadata file `device_key_meta.json` (plaintext, safe fields only): `algorithm`, `public_key_fingerprint` (SHA-256 hex over the raw public key, same derivation as Owner's own `device_identity.py::fingerprint_of()`), `created_at`, `owner_installation_id` (once assigned), `status` (`ACTIVE`/`RESET_PENDING`/`REVOKED_LOCALLY`).

## Corruption detection

On every launcher startup: attempt `CryptUnprotectData`. Failure modes distinguished:
- File missing entirely -> `ACTIVATION_REQUIRED` (first run, or explicit prior reset).
- File present, DPAPI decryption fails (corruption, or DPAPI master key unavailable e.g. profile/user-SID mismatch after a Windows reinstall) -> `LOCAL_STATE_CORRUPT`, **not** silently treated as first-run. No new key is generated automatically.
- File present, decrypts, but fails Ed25519 key-material validation (wrong length, invalid point) -> `LOCAL_STATE_CORRUPT`, same non-silent handling.

## Controlled reset flow (not silent regeneration)

`LOCAL_STATE_CORRUPT` and a user-initiated "Deactivate/Reset This Device" both route through the same explicit flow (Part X): show the current state clearly, require confirmation, then generate a *new* keypair and go through the full re-activation/device-replacement request against Owner -- Owner's own device-limit accounting is what actually retires the old device slot (Part D's `replace_device_key()`), never a local-only decision to just start using a fresh key silently. The old DPAPI blob is retained (not deleted) until Owner confirms the replacement succeeded, so a failed replacement attempt doesn't strand the installation with neither key valid.

## Real bug found and fixed during implementation

`test_device_identity.py` caught a genuine ~35-45% intermittent failure decrypting a freshly-written device key file. Root cause, found after ruling out several more exotic hypotheses (a ctypes buffer-lifetime GC hazard, missing `argtypes`/`restype` on the `CryptProtectData`/`CryptUnprotectData` ctypes declarations -- both real secondary issues, fixed regardless): `generate_new_key()`'s `os.open()` call was missing `os.O_BINARY`. Windows' C runtime defaults a file opened without that flag to **text mode**, which silently translates every `0x0A` byte written to `0x0D 0x0A` -- for an essentially-random DPAPI ciphertext, any blob containing at least one `0x0A` byte (a large majority of them, at this blob's length) was corrupted on write, surfacing later as an apparently-random `CryptUnprotectData` failure on read. Fixed at the write site (`binary_flag = getattr(os, "O_BINARY", 0)`, ORed into the `os.open()` flags, a no-op on POSIX). Verified with a 60-iteration real-provider repro (0/60 failures after the fix, versus consistent ~35-47% before) and 10 consecutive full pytest runs, all green. The `_dpapi_protect_verified`/`_dpapi_unprotect` self-verification-with-retry wrappers were built while chasing this bug and are kept as legitimate defense-in-depth against genuine transient DPAPI failures, but they were never the actual fix -- retrying a blob already corrupted at write time cannot succeed, which is exactly why 8 retries didn't resolve the symptom until the write-mode bug itself was found.

## What is never done

- The private key is never written to `%TEMP%`, never included in `logs\*.log` (log statements around key operations log only the public-key fingerprint, matching Owner's own Phase 6 convention), never included in the existing backup/restore mechanism (`commercial_runtime/backup`) since Part J explicitly excludes device private keys from the licensing state repository and backups operate over the product's SQLite database, not the DPAPI file -- confirmed as a design invariant to be tested directly (`test_backup_excludes_device_key`), not just asserted.
- No environment variable ever holds the raw private key.
- No hardware identifier (MAC, disk serial, motherboard serial, Windows username) is used as identity or as an input to key derivation -- the key is pure locally-generated Ed25519 entropy, identity is established solely by proof of possession over that key, exactly matching Owner's own device-identity model (`device-identity-design.md`).
