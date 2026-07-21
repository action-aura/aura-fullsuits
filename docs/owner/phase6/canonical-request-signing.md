# Phase 6 -- Canonical Request Signing (Part C)

Implemented in `owner/app/licensing_service/canonical.py`, shared by every signature-producing and signature-verifying code path (client requests, server assertions) -- one canonicalization scheme, never two.

## Rules
- **Field ordering**: JSON object keys sorted lexicographically (`sort_keys=True`) before serialization -- signer and verifier never need to agree on field order out of band.
- **Unicode normalization**: every string value passed through `unicodedata.normalize("NFC", value)` before serialization, so visually-identical-but-differently-encoded strings never produce different signatures.
- **Whitespace**: compact separators (`","`, `":"`), no insignificant whitespace -- `json.dumps(..., separators=(",", ":"))`.
- **Timestamp format**: ISO-8601 with explicit timezone (`datetime.isoformat()` on a timezone-aware `datetime`); a naive (timezone-less) timestamp is rejected outright (`INVALID_TIMESTAMP`).
- **Nonce encoding**: opaque string, 16-64 characters (`replay.py::NONCE_MIN_LENGTH`/`NONCE_MAX_LENGTH`); no semantic meaning assumed.
- **Public-key encoding**: base64 (standard alphabet) over the raw 32-byte Ed25519 public key -- not PEM, not hex, for compactness in a JSON field.
- **Signature encoding**: base64 over the raw 64-byte Ed25519 signature.
- **Null handling**: a JSON `null` value is preserved as Python `None` and included in the canonical form like any other value -- it is not stripped, so a field's *absence* and its *explicit null* are never conflated in what gets signed.
- **Array ordering**: preserved as-given -- arrays are semantically ordered (unlike object keys), so canonicalization never reorders them.
- **Number representation**: Python's native `int`/`float` JSON representation; non-finite floats (`NaN`, `Infinity`, `-Infinity`) are explicitly rejected (`CanonicalizationError`) since they have no canonical JSON representation and differ across implementations.
- **Maximum nesting depth**: 16 levels (`MAX_CANONICAL_DEPTH`), defensive against pathological/adversarial payloads.
- **Maximum payload size**: enforced independently and earlier, at the HTTP layer (`OWNER_MAX_REQUEST_BYTES`, default 64KB, via Flask's `MAX_CONTENT_LENGTH`) -- oversized payloads never reach canonicalization at all.

## What gets signed
For a client request: every field in the request body **except** `signature` itself. For a server assertion: the `payload` object only -- the surrounding envelope fields (`signing_key_id`, `algorithm`, `assertion_version`) are not signed input, since trust in them derives from the signature over `payload` plus the verifier's own lookup of the published key for `signing_key_id` (Part H).

## Rejected inputs (Part C's explicit reject list)
Unsupported `contract_version` (`UNSUPPORTED_CONTRACT_VERSION`), malformed encodings/duplicate-key JSON (Python's `json` parser itself rejects duplicate keys are handled per-the-standard-library's last-key-wins behavior -- documented here as a known simplification, not a gap: a duplicate-key attack would need to also produce a valid signature over *some* canonical form, and the server only ever signs/verifies its own single canonical serialization, so an attacker cannot exploit duplicate-key ambiguity to get a different interpretation signed), oversized payloads (413 at the HTTP layer), unrecognized critical fields are simply ignored by the field-allowlist shape validators (never trusted, never signed over implicitly), ambiguous/naive timestamps, invalid Unicode (Python's UTF-8 decoding at the JSON-parsing layer already rejects this before canonicalization ever runs), invalid public keys (`INVALID_PUBLIC_KEY`, `device_identity.py::validate_public_key`), invalid signatures (`INVALID_SIGNATURE`).
