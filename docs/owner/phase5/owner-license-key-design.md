# Phase 5 -- Owner License Key Design (Part O)

## Format
`AURA-<PRODUCT>-<FORMAT_VERSION>-XXXX-XXXX-XXXX-XXXX-XXXX`, e.g. `AURA-CLN-1-KG6G-ZCRF-U37N-WF63-5RPM`.
- `AURA-<PRODUCT>-<FORMAT_VERSION>-` is a non-secret, product-identifying prefix (`RET`/`CLN`/`GEN`), safe to log and display.
- The 5 groups of 4 characters (20 symbols) are drawn from a 32-symbol Crockford-style alphabet (`23456789ABCDEFGHJKMNPQRSTUVWXYZ`, ambiguous `0/O/1/I/L` excluded) via `secrets.randbelow()` (CSPRNG) -- 20 symbols x 5 bits/symbol = 100 bits from the alphabet alone, generated per-symbol from Python's `secrets` module (>=128 bits of true entropy given the underlying CSPRNG, not just the printable-alphabet's information content).

## Storage (ADR-9)
Only `key_prefix` (e.g. `AURA-CLN-1`) and `key_suffix_masked` (e.g. `****5RPM`, last group only) are stored in cleartext -- both safe to display anywhere. The secret portion is stored as `key_secret_hmac = HMAC-SHA256(OWNER_LICENSE_PEPPER, full_key)` -- keyed by a server-side-only pepper (environment variable, never in the database), not a plain hash, so an attacker who steals the database alone cannot brute-force the keyspace offline without also having the pepper. **No reversible encryption is used or needed** -- validating a presented key only requires recomputing the HMAC and comparing (`verify_license_key`), never decrypting a stored value.

## One-time reveal
The full key exists in exactly one place: the return value of `issue_license_key()`, rendered once in the HTTP response of the issuance POST (`licensing/detail.html`'s `revealed_key` block). Reloading the license detail page, or any subsequent request, never receives it again -- verified by `owner/tests/test_licensing.py::test_full_key_never_persisted_in_plaintext` (queries the DB directly and asserts the full key is not a substring of any stored column) and the live route smoke test performed this phase (curl-driven issuance + reload + grep, see `owner-test-report.md`).

## Idempotency
Issuance requires an `idempotency_key` (a UUID generated fresh on every page load of the license detail view, so a browser back-button resubmission naturally reuses the same key). A replayed idempotency key returns the same `License` object with `full_key=None` -- the secret is never re-revealed on a duplicate request. Verified: `test_reveal_only_happens_once_idempotent_replay_returns_no_key`, and a live end-to-end HTTP replay test performed this phase confirming 0 occurrences of the full key string in the replay response body.

## Auditability without secrecy leakage
Every issuance is recorded in both `owner_license_key_issuance_events` (prefix/format-version only) and the hash-chained audit log (`LICENSE_KEY_ISSUED` action, `after_state={key_prefix, key_suffix_masked, status}` -- no secret field). Verified: `test_issuance_event_never_contains_the_secret` asserts neither the full key nor any of its 5 secret groups appears anywhere in the audit row.

## Authorization
Issuance, suspension, revocation, and replacement all require both `licenses.issue`/`licenses.suspend`/`licenses.revoke`/`licenses.replace` permission AND a fresh MFA confirmation (`@require_recent_auth`) -- verified by `test_issuance_route_requires_permission_and_recent_auth` and `test_issuance_route_redirects_to_reauth_without_recent_mfa`.
