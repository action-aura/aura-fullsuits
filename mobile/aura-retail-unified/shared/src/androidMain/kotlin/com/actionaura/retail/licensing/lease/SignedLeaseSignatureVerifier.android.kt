package com.actionaura.retail.licensing.lease

import com.google.crypto.tink.subtle.Ed25519Verify

/**
 * Real Android/JVM Ed25519 verification via Google Tink's raw
 * "subtle" primitive -- the same one the legacy Android app's own
 * `DeviceIdentity.kt` uses and has physically cross-verified against
 * the canonical Python signer (`signed-lease-cryptography-decision.md`).
 * `Ed25519Verify.verify` throws `GeneralSecurityException` on an
 * invalid signature/malformed input rather than returning a boolean --
 * mapped here to the real, closed `false` contract this codebase's
 * own `expect` declaration requires, so callers never need to catch a
 * platform-specific exception type.
 */
actual fun verifyEd25519Signature(publicKeyRaw: ByteArray, signatureRaw: ByteArray, message: ByteArray): Boolean {
    if (publicKeyRaw.size != 32 || signatureRaw.size != 64) return false
    return try {
        Ed25519Verify(publicKeyRaw).verify(signatureRaw, message)
        true
    } catch (e: Exception) {
        false
    }
}
