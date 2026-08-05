package com.actionaura.retail.licensing.transport

/**
 * M9.20 follow-up (security review finding) -- real platform
 * cryptographically-secure random bytes, replacing the earlier
 * `kotlin.random.Random`-based idempotency key generator.
 * `kotlin.random.Random.Default` is not a CSPRNG and was flagged as a
 * weak-cryptographic-primitive risk; an idempotency key is not itself
 * authentication material (the real security boundary is the server's
 * own signature/nonce/timestamp protocol, `activation-idempotency-
 * contract.md`), but there is no real reason to accept a weaker
 * source when a real platform CSPRNG is one `expect`/`actual` pair
 * away. This is this codebase's first `expect`/`actual` pair --
 * introduced here specifically because a security reviewer's finding
 * makes the tradeoff worth it, not for its own sake.
 */
expect fun secureRandomBytes(size: Int): ByteArray

fun secureRandomHex(size: Int): String =
    secureRandomBytes(size).joinToString("") { (it.toInt() and 0xFF).toString(16).padStart(2, '0') }
