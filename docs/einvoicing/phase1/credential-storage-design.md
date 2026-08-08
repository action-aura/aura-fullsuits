# Credential storage design

`commercial_runtime/einvoicing/credentials.py`. Each install's JoFotara
`client_id`/`client_secret` — never in the database, never logged, never
synced anywhere (in particular: never to the Owner Control Center, see
`product-to-owner-data-boundary-einvoicing.md`).

## Two backends, selected by platform

**`WindowsDpapiSecretBox`** (Windows). Reuses
`commercial_runtime/licensing_contracts/device_identity.py`'s
`_dpapi_protect_verified` / `_dpapi_unprotect` verbatim — does not
reimplement DPAPI wrapping, and inherits that module's documented
`os.O_BINARY` fix (Windows text-mode file writes silently corrupt binary
blobs containing `0x0A` bytes without it — a real bug that module already
found and fixed once). Verified against **real** `CryptProtectData`/
`CryptUnprotectData` in `test_credentials.py` — this environment is
genuinely Windows, not mocked.

**`AppSecretDerivedSecretBox`** (Android and any non-Windows platform).
AES-GCM via `cryptography` (already a Chaquopy dependency), key derived by
HKDF-SHA256 from the installation's own
`commercial_runtime/security/app_secret.py` secret, with a fixed
`info=b"aura-einvoicing-credentials-v1"` so this key is cryptographically
distinct from the Flask `SECRET_KEY` use of the same underlying secret.

**Honest limitation:** on Android this rests on the app-private storage
sandbox plus device full-disk encryption, not hardware-backed Keystore
wrapping. See `../phase2/phase2-seam.md` for the AndroidKeystore upgrade
path — not built in Phase 1, and Phase 1 shipped with no Android UI at all
(see the repository's session notes / `phase1-residual-risk-register.md`),
so this limitation has no live exposure yet regardless.

## Write path

Both backends write via `tmp` file + `os.replace` (atomic) and
`os.chmod(0o600)` best-effort — the exact pattern
`security/app_secret.py::_generate_and_persist` already uses. `os.O_BINARY`
is included on the open flags unconditionally (a no-op on POSIX).

## Failure mode

`load()` raises `CredentialsUnavailable` — never crashes, never falls back
to a default or plaintext value — when: nothing is stored, the stored blob
is corrupted, or (`AppSecretDerivedSecretBox` only) the underlying
`secret.key` was regenerated after its own corruption
(`app_secret.py`'s documented fail-safe). The UI must surface "re-enter
your JoFotara credentials" in every one of these cases; `describe()`
reports `configured: true, readable: false` for this exact scenario rather
than propagating the exception to a status-check caller.

## What `describe()` returns (safe to expose over HTTP)

```json
{"configured": true, "readable": true, "client_id_last4": "c123",
 "stored_at": "2026-08-04T...", "backend": "dpapi"}
```

Never the secret itself. `test_credentials.py::test_describe_never_contains_the_secret`
and `test_routes.py`'s credential tests both assert the secret substring
never appears anywhere in an HTTP response body.

## Redaction

`EInvoiceCredentials.__repr__`/`__str__` are overridden to print
`EInvoiceCredentials(client_id='***', client_secret='***')` — defense in
depth against an accidental `repr()` in a log line or traceback.
`test_no_secret_substring_in_logs_across_full_lifecycle` asserts no secret
substring appears in any log record across store/load/describe/wipe.
