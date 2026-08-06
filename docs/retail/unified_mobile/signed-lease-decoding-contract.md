# Signed Lease Decoding Contract (M11.4)

Real, bounded, fail-closed decoding (`LeaseDecoder.kt`), executed
before any cryptographic verification is attempted on the payload
portion of a [ProtectedSignedLease](../../mobile/aura-retail-unified/shared/src/commonMain/kotlin/com/actionaura/retail/licensing/lease/ProtectedSignedLease.kt).

## Real, enforced limits (`LeaseDecodingLimits`)

| Limit | Value | Rationale |
|---|---|---|
| Payload JSON | 32,768 chars | Real production leases (27 allowlisted fields, none large) fit comfortably under 2 KB; this is a wide, generous margin against pathological input, never a legitimate-lease constraint. |
| Signature (base64) | 256 chars | A real Ed25519 signature is 64 raw bytes (~88 base64 chars); generous margin. |
| Key identifier | 256 chars | Real key IDs (`owner-ed25519-<timestamp>-<8hex>`) are ~40 chars. |
| Algorithm string | 64 chars | `"ed25519"` is 7 chars. |
| Any string field | 4,096 chars | Generous per-field ceiling. |
| Any collection (object/array) | 256 entries | `entitlements`/`offline_policy` have single-digit real entry counts. |
| Nesting depth | 16 | Matches `canonical.py`'s own `MAX_CANONICAL_DEPTH` exactly. |

Every limit above rejects pathological/malicious input; none constrain
a real, legitimate lease by any meaningful margin.

## Real decode order

1. Bound every envelope-level field (payload length, signature length, key-ID length, algorithm length) — non-cryptographic, cheap, first.
2. **Duplicate top-level key detection** (`hasDuplicateTopLevelKeys`) — a real, deliberately-scoped tokenizer (tracks brace/bracket depth, flags a repeated quoted key at the *same* nesting level) run directly against the raw JSON string, *before* parsing — catches `{"expires_at":"...","expires_at":"..."}`-class ambiguity that a naive parser would silently resolve to the last value. Real, disclosed scope limit: this is not a full alternate JSON parser; it correctly ignores same-named keys at *different* nesting levels (legitimate), and has not been exhaustively fuzzed against every possible malformed-JSON edge case.
3. Parse via `kotlinx.serialization.json` (`ignoreUnknownKeys = false`, `isLenient = false`) — malformed JSON fails immediately.
4. Recursive bounds check (collection size, string length, nesting depth) over the full parsed tree.
5. Field allowlist check — every key must be in `LeasePayloadFields.ALLOWED` (the exact 27-field port of `ALLOWED_PAYLOAD_FIELDS`).
6. Required-field presence check — a narrower subset (`LeasePayloadFields.REQUIRED`) the verifier's own pipeline structurally needs (matches what `assertion_verifier.py` unconditionally indexes rather than `.get()`s).
7. Forbidden-marker scan (`obj.toString().lowercase()` substring check against the same 16 markers `assertion_verifier.py::FORBIDDEN_ASSERTION_MARKERS` defines) — real defense in depth, independent of the allowlist.

## Real, tested rejections (`SignedLeaseVerifierTest.kt`)

- Duplicate top-level key → `DUPLICATE_FIELD` (real test:
  `duplicateTopLevelKeyIsRejected`, injects a raw duplicate directly
  into the JSON string, bypassing `JsonObject`'s own natural dedup).
- Oversized payload → `OVERSIZED_PAYLOAD`.
- Unknown field outside the allowlist → `UNKNOWN_REQUIRED_FIELD`.
- Forbidden marker present → `FORBIDDEN_FIELD`.
- Malformed base64 signature → rejected (generic decode failure).
- Empty signature → `MALFORMED_SIGNATURE`.

## Real, disclosed gaps

- Integer-overflow handling relies on Kotlin/JVM's own `Long` bounds
  and `kotlinx.serialization`'s own numeric parsing — not separately,
  explicitly fuzz-tested for pathological large-integer input in this
  milestone (`lease-fuzz-property-test-report.md` records this as a
  real, open item, not silently claimed covered).
- Unicode/UTF-8 validity is delegated to Kotlin's own `String`/JSON
  parser; no separate, explicit "reject invalid UTF-8" test exists
  beyond what malformed-JSON parsing already catches structurally.
