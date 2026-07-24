# Phase 7V — Windows Commercial-Build Security Review (Part G)

## Real gap found

`OWNER_LICENSING_VERIFY_TLS` in both `products/clinic/backend/config.py` and
`products/retail/backend/config.py` was computed **purely from the `AURA_OWNER_LICENSING_INSECURE`
environment variable**, with no regard for whether the process was a real frozen commercial build
or a source/dev run. Both files already compute `IS_STANDALONE` from `sys.frozen`, but the TLS
flag never consulted it.

**Impact**: on a customer's machine running the real packaged `.exe`, setting
`AURA_OWNER_LICENSING_INSECURE=1` in the environment (accidentally, by a well-meaning support
technician debugging connectivity, or by malware) would have silently disabled TLS certificate
verification for the real Owner activation/check-in traffic — a genuine commercial-build bypass of
exactly the kind this part exists to catch. This was not previously exploitable through the UI or
license flow; it required environment-variable control of the host process, but that is a real
attack surface for a desktop application, not a hypothetical.

## Fix

`config.py` (both products): `OWNER_LICENSING_VERIFY_TLS` is now `True` unconditionally when
`sys.frozen` is set (i.e. running as the real PyInstaller-packaged executable), regardless of the
env var. The dev-only escape hatch is preserved for unfrozen source runs (`python app.py`, pytest,
the live e2e harnesses used throughout Phase 7/7V), which is required — those legitimately test
against a local Owner over plain HTTP or self-signed HTTPS.

```python
OWNER_LICENSING_VERIFY_TLS = True if getattr(sys, 'frozen', False) else (
    os.environ.get('AURA_OWNER_LICENSING_INSECURE') != '1'
)
```

## Verification (real execution, not static review)

1. **Simulated-frozen module execution** — ran the real `config.py` twice in separate interpreters,
   once with `sys.frozen = True` set before import (matching exactly what PyInstaller's bootloader
   sets) and `AURA_OWNER_LICENSING_INSECURE=1`, once without `sys.frozen`:
   ```
   mode=frozen sys.frozen=True OWNER_LICENSING_VERIFY_TLS=True
   PASS: frozen build ignores the insecure override, TLS always verified
   mode=source sys.frozen=False OWNER_LICENSING_VERIFY_TLS=False
   PASS: unfrozen (source/dev) run still honors the escape hatch
   ```

2. **Live network-layer proof** — stood up a real HTTPS server on a real localhost port with a
   genuine self-signed certificate (via `openssl`), then called the real
   `commercial_runtime.licensing_contracts.client.LicensingClient.fetch_service_info()` against it:
   - `verify_tls=True` (the forced state for a frozen build): real `SSLCertVerificationError`
     surfaced as `NetworkError(reason_code=TLS_VERIFICATION_FAILED)` — connection rejected.
   - `verify_tls=False` (the source/dev-only escape hatch): connected successfully, real JSON
     response received.
   ```
   PASS: verify_tls=True rejected the self-signed cert -- TLS_VERIFICATION_FAILED: ... SSLCertVerificationError ...
   PASS: verify_tls=False (dev-only) connected successfully -- {'service': 'owner-licensing', ...}
   LIVE TLS-BYPASS-CONTROL PROOF: PASS
   ```

3. **Frozen artifact rebuilt** — both `dist/AuraClinic/AuraClinic.exe` and `dist/AuraRetail/AuraRetail.exe`
   were rebuilt from the fixed source via PyInstaller after this change, so the shipped rc.2
   installers (Part E) bake in the fix, not the pre-fix behavior.

## Other required controls, checked and confirmed clean

- **No raw `licensed=true` unlock exists** — every mutation route's gate goes through
  `capability_guard.evaluate_capability()`, which derives its answer from the persisted, previously
  signature-verified `LicenseState`, never from a boolean flag settable independently.
- **No trust-on-first-use** — `OwnerTrustStore`/`trust_anchor_loader` only ever accept keys present
  in the pre-generated `trust_anchor.json`; there is no code path that adds a key just because an
  Owner endpoint claims it is active (Phase 7 Part D).
- **No arbitrary trust-anchor path from user-writable configuration** — `LICENSING_TRUST_ANCHOR_PATH`
  in both `config.py` files is derived from `commercial_runtime.licensing_contracts.__file__`
  (a fixed package-relative path), not from any environment variable or user-editable setting.
- **Android has zero TLS-bypass code path** — `grep` across both products'
  `net/*.kt` OkHttp client code for `trustAll`/`TrustManager`/`checkServerTrusted`/
  `HostnameVerifier`/`sslSocketFactory` returns no matches. Android's `OwnerClient.kt` uses the
  platform default `OkHttpClient` with no custom trust manager at all — stricter than Windows,
  which at least has an explicit (now frozen-build-safe) dev escape hatch.
- **Localhost development behavior remains explicitly separated** — the escape hatch only ever
  applies to unfrozen runs; a frozen build talking to `http://127.0.0.1` for local Owner testing
  (as this session's own e2e harnesses do) is a deliberate developer action outside the shipped
  product, not a shipped code path.

## Outcome

Real P1 commercial-build bypass found and fixed, verified via both a simulated-frozen module
execution and a real live-network TLS-rejection test, not by static source reading alone. Frozen
artifacts rebuilt to include the fix.
