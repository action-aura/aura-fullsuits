package com.actionaura.retail.licensing.lease

import com.google.crypto.tink.subtle.Ed25519Sign
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlin.io.encoding.Base64
import kotlin.io.encoding.ExperimentalEncodingApi
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertTrue

/**
 * M11.7/M11.32 -- real, executed proof of the signature verification
 * pipeline against REAL Ed25519 cryptography (Tink, the same real
 * primitive the production Android adapter uses -- not a mock). Every
 * test in this file signs a real payload with a real key and verifies
 * through the real, unmodified [GenerationalSignedLeaseVerifier].
 */
@OptIn(ExperimentalEncodingApi::class)
class SignedLeaseVerifierTest {

    private fun deterministicClock() = MonotonicClock { 0L }

    private fun verifierWith(vararg keys: TrustedLeaseKey) =
        GenerationalSignedLeaseVerifier(InMemoryLeaseKeyRing(keys.toList()), deterministicClock())

    @Test
    fun validRealSignedLeaseVerifiesSuccessfully() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val lease = LeaseTestFixtures.sign(LeaseTestFixtures.defaultPayload(), keyPair, trustedKey.keyId)
        val result = verifierWith(trustedKey).verify(lease, LeaseTestFixtures.defaultContext())
        assertIs<LeaseVerificationResult.Verified>(result, "real regression: a genuinely valid, real-Ed25519-signed lease must verify successfully")
        assertEquals(com.actionaura.retail.licensing.LicensingProductCode.AURA_RETAIL, result.claims.productCode)
    }

    @Test
    fun oneByteFlippedInPayloadInvalidatesTheSignature() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val lease = LeaseTestFixtures.sign(LeaseTestFixtures.defaultPayload(), keyPair, trustedKey.keyId)
        val tampered = lease.copy(payloadCanonicalJson = lease.payloadCanonicalJson.replaceFirst("ACTIVE", "ACTIV3"))
        val result = verifierWith(trustedKey).verify(tampered, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.SIGNATURE_INVALID)
    }

    @Test
    fun oneByteFlippedInSignatureIsRejected() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val lease = LeaseTestFixtures.sign(LeaseTestFixtures.defaultPayload(), keyPair, trustedKey.keyId)
        val rawSig = Base64.decode(lease.signature).also { it[0] = (it[0].toInt() xor 0xFF).toByte() }
        val tampered = lease.copy(signature = Base64.encode(rawSig))
        val result = verifierWith(trustedKey).verify(tampered, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.SIGNATURE_INVALID)
    }

    @Test
    fun unknownSigningKeyIsRejectedBeforeAnyCryptography() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val lease = LeaseTestFixtures.sign(LeaseTestFixtures.defaultPayload(), keyPair, trustedKey.keyId)
        // Real key ring that does NOT contain this key -- empty.
        val result = GenerationalSignedLeaseVerifier(InMemoryLeaseKeyRing(), deterministicClock())
            .verify(lease, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.UNKNOWN_SIGNING_KEY)
    }

    @Test
    fun leaseSignedByAWrongKeyIsRejectedEvenIfClaimedKeyIdIsKnown() = runTest {
        val (keyPairA, trustedKeyA) = LeaseTestFixtures.freshKeyPair("key-a")
        val (keyPairB, _) = LeaseTestFixtures.freshKeyPair("key-b")
        // Sign with B's private key but CLAIM key-a's id -- a real forgery attempt.
        val lease = LeaseTestFixtures.sign(LeaseTestFixtures.defaultPayload(), keyPairB, trustedKeyA.keyId)
        val result = verifierWith(trustedKeyA).verify(lease, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.SIGNATURE_INVALID)
    }

    @Test
    fun retiredKeyStillVerifiesWithinLeaseValidity() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val retired = trustedKey.copy(status = LeaseKeyStatus.RETIRED)
        val lease = LeaseTestFixtures.sign(LeaseTestFixtures.defaultPayload(), keyPair, trustedKey.keyId)
        val result = verifierWith(retired).verify(lease, LeaseTestFixtures.defaultContext())
        assertIs<LeaseVerificationResult.Verified>(result, "real regression: a RETIRED-but-still-trusted key must still validate a lease it legitimately signed")
    }

    @Test
    fun productMismatchIsRejected() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val lease = LeaseTestFixtures.sign(LeaseTestFixtures.defaultPayload(productCode = "AURA_CLINIC"), keyPair, trustedKey.keyId)
        val result = verifierWith(trustedKey).verify(lease, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.PRODUCT_MISMATCH)
    }

    @Test
    fun platformMismatchIsRejected() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val lease = LeaseTestFixtures.sign(LeaseTestFixtures.defaultPayload(platform = "IOS"), keyPair, trustedKey.keyId)
        val result = verifierWith(trustedKey).verify(lease, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.PLATFORM_MISMATCH)
    }

    @Test
    fun installationMismatchIsRejected() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val lease = LeaseTestFixtures.sign(LeaseTestFixtures.defaultPayload(installationPublicId = "someone-elses-installation"), keyPair, trustedKey.keyId)
        val result = verifierWith(trustedKey).verify(lease, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.INSTALLATION_MISMATCH)
    }

    @Test
    fun expiredLeaseIsRejected() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val lease = LeaseTestFixtures.sign(
            LeaseTestFixtures.defaultPayload(notBeforeIso = "2020-01-01T00:00:00Z", expiresAtIso = "2021-01-01T00:00:00Z"),
            keyPair, trustedKey.keyId,
        )
        val result = verifierWith(trustedKey).verify(lease, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.EXPIRED)
    }

    @Test
    fun notYetValidLeaseIsRejected() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val lease = LeaseTestFixtures.sign(
            LeaseTestFixtures.defaultPayload(notBeforeIso = "2099-01-01T00:00:00Z", expiresAtIso = "2100-01-01T00:00:00Z"),
            keyPair, trustedKey.keyId,
        )
        val result = verifierWith(trustedKey).verify(lease, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.NOT_YET_VALID)
    }

    @Test
    fun withinSixtySecondClockSkewToleranceStillVerifies() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val notBefore = "2026-06-01T00:00:30Z" // 30s after the fixture's own "now"
        val lease = LeaseTestFixtures.sign(LeaseTestFixtures.defaultPayload(notBeforeIso = notBefore), keyPair, trustedKey.keyId)
        val result = verifierWith(trustedKey).verify(lease, LeaseTestFixtures.defaultContext())
        assertIs<LeaseVerificationResult.Verified>(result, "real regression: the real 60s clock-skew tolerance must accept a lease valid 30s in the future")
    }

    @Test
    fun unsupportedAlgorithmIsRejected() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val lease = LeaseTestFixtures.sign(LeaseTestFixtures.defaultPayload(), keyPair, trustedKey.keyId).copy(algorithm = "rsa-pss")
        val result = verifierWith(trustedKey).verify(lease, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.UNSUPPORTED_ALGORITHM)
    }

    @Test
    fun forbiddenFieldMarkerIsRejectedBeforeCryptography() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val payload = buildJsonObject {
            LeaseTestFixtures.defaultPayload().forEach { (k, v) -> put(k, v) }
            put("issuer", "owner mentions sale_total here") // forbidden marker substring
        }
        val lease = LeaseTestFixtures.sign(payload, keyPair, trustedKey.keyId)
        val result = verifierWith(trustedKey).verify(lease, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.FORBIDDEN_FIELD)
    }

    @Test
    fun unknownFieldOutsideAllowlistIsRejected() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val payload = buildJsonObject {
            LeaseTestFixtures.defaultPayload().forEach { (k, v) -> put(k, v) }
            put("not_a_real_field", "x")
        }
        val lease = LeaseTestFixtures.sign(payload, keyPair, trustedKey.keyId)
        val result = verifierWith(trustedKey).verify(lease, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.UNKNOWN_REQUIRED_FIELD)
    }

    @Test
    fun duplicateTopLevelKeyIsRejected() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val validJson = LeaseCanonicalJson.canonicalize(LeaseTestFixtures.defaultPayload())
        // Real, deliberate duplicate key injected directly into the raw string (bypassing JsonObject's own natural dedup) -- the exact ambiguous-duplicate attack the decoder must catch.
        val withDuplicate = validJson.dropLast(1) + ",\"license_status\":\"REVOKED\"}"
        val signature = Ed25519Sign(keyPair.privateKey).sign(withDuplicate.encodeToByteArray())
        val lease = ProtectedSignedLease(withDuplicate, trustedKey.keyId, "ed25519", Base64.encode(signature))
        val result = verifierWith(trustedKey).verify(lease, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.DUPLICATE_FIELD)
    }

    @Test
    fun oversizedPayloadIsRejected() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val huge = buildJsonObject {
            LeaseTestFixtures.defaultPayload().forEach { (k, v) -> put(k, v) }
            put("release_channel", "x".repeat(LeaseDecodingLimits.MAX_PAYLOAD_JSON_CHARS))
        }
        val lease = LeaseTestFixtures.sign(huge, keyPair, trustedKey.keyId)
        val result = verifierWith(trustedKey).verify(lease, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.OVERSIZED_PAYLOAD)
    }

    @Test
    fun clinicLeasePresentedToRetailIsRejected() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val lease = LeaseTestFixtures.sign(LeaseTestFixtures.defaultPayload(productCode = "AURA_CLINIC"), keyPair, trustedKey.keyId)
        val result = verifierWith(trustedKey).verify(lease, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.PRODUCT_MISMATCH)
    }

    @Test
    fun androidLeasePresentedToIosContextIsRejected() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val lease = LeaseTestFixtures.sign(LeaseTestFixtures.defaultPayload(platform = "ANDROID"), keyPair, trustedKey.keyId)
        val iosContext = LeaseTestFixtures.defaultContext().copy(expectedPlatform = com.actionaura.retail.licensing.LicensingPlatform.IOS)
        val result = verifierWith(trustedKey).verify(lease, iosContext)
        assertRejectedWith(result, LeaseFailureCode.PLATFORM_MISMATCH)
    }

    @Test
    fun malformedBase64SignatureIsRejected() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val lease = LeaseTestFixtures.sign(LeaseTestFixtures.defaultPayload(), keyPair, trustedKey.keyId).copy(signature = "not-valid-base64!!!")
        val result = verifierWith(trustedKey).verify(lease, LeaseTestFixtures.defaultContext())
        assertTrue(result is LeaseVerificationResult.Rejected)
    }

    @Test
    fun emptySignatureIsRejected() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val lease = LeaseTestFixtures.sign(LeaseTestFixtures.defaultPayload(), keyPair, trustedKey.keyId).copy(signature = "")
        val result = verifierWith(trustedKey).verify(lease, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.MALFORMED_SIGNATURE)
    }

    @Test
    fun unsupportedContractVersionIsRejected() = runTest {
        val (keyPair, trustedKey) = LeaseTestFixtures.freshKeyPair()
        val lease = LeaseTestFixtures.sign(LeaseTestFixtures.defaultPayload(contractVersion = "99.0"), keyPair, trustedKey.keyId)
        val result = verifierWith(trustedKey).verify(lease, LeaseTestFixtures.defaultContext())
        assertRejectedWith(result, LeaseFailureCode.UNSUPPORTED_CLAIM_VERSION)
    }

    private fun assertRejectedWith(result: LeaseVerificationResult, expected: LeaseFailureCode) {
        assertIs<LeaseVerificationResult.Rejected>(result)
        assertEquals(expected, result.failure.code, "expected rejection code $expected but got ${result.failure.code} (${result.failure.safeDiagnosticReason})")
    }
}
