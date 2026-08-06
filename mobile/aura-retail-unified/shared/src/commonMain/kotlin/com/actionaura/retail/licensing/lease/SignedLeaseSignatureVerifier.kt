package com.actionaura.retail.licensing.lease

/**
 * M11.2/M11.7/M11.8 -- the real platform Ed25519 verification primitive.
 * `expect`/`actual` per this codebase's own established pattern (M9's
 * `secureRandomBytes`, M10's `SecureBlobStore`) -- one real platform
 * implementation per target, never a shared/mocked fallback. Returns a
 * plain boolean (`true` = cryptographically valid), never throws for
 * an invalid signature (a malformed key/signature/algorithm is
 * expected input from a potentially-tampered lease, not an
 * exceptional program state) -- callers map `false` to
 * [LeaseFailureCode.SIGNATURE_INVALID].
 *
 * Real, closed contract: `publicKeyRaw` and `signatureRaw` are already
 * base64-decoded raw bytes (32 and 64 bytes respectively for Ed25519);
 * a wrong-length input is real, valid "false" input, not a caller bug
 * -- implementations must return `false`, never throw, for a
 * wrong-length key or signature.
 */
expect fun verifyEd25519Signature(publicKeyRaw: ByteArray, signatureRaw: ByteArray, message: ByteArray): Boolean
