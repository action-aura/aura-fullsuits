package com.actionaura.retail.licensing.lease

import kotlinx.serialization.Serializable

/**
 * M11.3 -- the real, raw, NOT-YET-TRUSTED signed lease as held by M10
 * secure storage. Distinguishes the raw protected bytes from every
 * downstream concept the checkpoint requires kept separate: decoded
 * envelope, signed payload bytes, parsed claims, signature, key
 * identifier, algorithm identifier.
 *
 * [payloadCanonicalJson] is the exact string whose UTF-8 bytes were
 * (or, for a real production lease, are claimed to have been)
 * canonicalized and signed -- signature verification operates
 * directly on THESE bytes, never on a re-derivation from a typed,
 * already-parsed object (see `LeaseCanonicalJson`'s own KDoc for why
 * re-derivation is unsafe). No ordinary application feature may
 * receive this type -- only [SignedLeaseVerifier] may read it, and
 * only via M10's secure-storage boundary.
 */
@Serializable
data class ProtectedSignedLease(
    val payloadCanonicalJson: String,
    val signingKeyId: String,
    val algorithm: String,
    val signature: String,
) {
    /** Real, deliberate redaction -- the payload may contain Customer/commercial claims and the signature is secret-adjacent (a valid signature over a forged payload would be a real security incident if ever logged). */
    override fun toString(): String =
        "ProtectedSignedLease(signingKeyId=$signingKeyId, algorithm=$algorithm, payload=<redacted, ${payloadCanonicalJson.length} chars>, signature=<redacted>)"
}
