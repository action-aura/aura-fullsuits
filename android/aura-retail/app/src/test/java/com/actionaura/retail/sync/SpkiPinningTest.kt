package com.actionaura.retail.sync

import okhttp3.tls.HeldCertificate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertThrows
import org.junit.Test
import java.security.cert.X509Certificate

/**
 * Pure JVM tests for [SpkiPinning] -- no device, no emulator, no
 * Robolectric. Real self-signed [X509Certificate]s are generated with
 * `okhttp3.tls.HeldCertificate`, already on this module's test classpath
 * (see [SyncRelayClientTest]'s identical use for real-TLS testing) --
 * exactly the "certificate-building facility already on the test classpath"
 * the plan for this task asked for, rather than a hand-rolled DER fixture.
 */
class SpkiPinningTest {

    private fun heldCert(commonName: String, keyPair: java.security.KeyPair? = null): HeldCertificate {
        val builder = HeldCertificate.Builder().commonName(commonName)
        if (keyPair != null) builder.keyPair(keyPair)
        return builder.build()
    }

    // ── pinOf ────────────────────────────────────────────────────────────

    @Test
    fun pinOf_is_stable_across_repeated_calls_on_the_same_certificate() {
        val cert = heldCert("hub").certificate

        val first = SpkiPinning.pinOf(cert)
        val second = SpkiPinning.pinOf(cert)

        assertEquals(first, second)
    }

    @Test
    fun pinOf_differs_for_two_certificates_holding_different_keys() {
        val certA = heldCert("hub-a").certificate
        val certB = heldCert("hub-b").certificate

        assertNotEquals(SpkiPinning.pinOf(certA), SpkiPinning.pinOf(certB))
    }

    /**
     * THE PROPERTY THE WHOLE DESIGN RESTS ON: a certificate reissued over
     * the SAME key (new common name here standing in for whatever changes
     * on reissue in production -- a new serial number, a new validity
     * window, a SAN list that changes because the hub's DHCP-assigned IP
     * changed) must still produce the SAME pin. If it did not, every device
     * paired to a hub would be silently stranded the moment that hub's
     * certificate was ever reissued for any reason.
     *
     * MUTATION PROOF (reported verbatim per the plan): changing [pinOf] to
     * hash `certificate.encoded` (the whole certificate DER) instead of
     * `certificate.publicKey.encoded` (the SubjectPublicKeyInfo alone) turns
     * this test RED -- confirmed by temporarily applying that one-line
     * change and re-running just this test class, see the task report.
     */
    @Test
    fun pinOf_is_unchanged_when_a_certificate_is_reissued_over_the_same_key() {
        val original = heldCert("hub-v1-old-ip")
        val reissued = heldCert("hub-v2-new-ip-after-dhcp-renewal", keyPair = original.keyPair)

        assertEquals(SpkiPinning.pinOf(original.certificate), SpkiPinning.pinOf(reissued.certificate))
    }

    // ── trustManager / checkServerTrusted ───────────────────────────────

    @Test
    fun checkServerTrusted_does_not_throw_when_the_presented_certificate_matches_the_pin() {
        val cert = heldCert("hub")
        val pin = SpkiPinning.pinOf(cert.certificate)
        val trustManager = SpkiPinning.trustManager(pin)

        // Must not throw -- an exception here is a test failure.
        trustManager.checkServerTrusted(arrayOf(cert.certificate), "RSA")
    }

    /**
     * MUTATION PROOF (reported verbatim per the plan): changing
     * `checkServerTrusted` to return without ever comparing the computed
     * pin to [expectedPin] (i.e. deleting the `if (!MessageDigest.isEqual
     * (...))` guard) turns this test RED -- confirmed by temporarily
     * applying that change and re-running just this test class, see the
     * task report.
     */
    @Test
    fun checkServerTrusted_throws_SpkiPinMismatchException_when_the_pin_does_not_match() {
        val presentedCert = heldCert("hub-real")
        val wrongPin = SpkiPinning.pinOf(heldCert("hub-attacker").certificate)
        val trustManager = SpkiPinning.trustManager(wrongPin)

        assertThrows(SpkiPinMismatchException::class.java) {
            trustManager.checkServerTrusted(arrayOf(presentedCert.certificate), "RSA")
        }
    }

    @Test
    fun checkServerTrusted_throws_rather_than_passing_an_empty_chain() {
        val trustManager = SpkiPinning.trustManager("anyPinAtAll==")

        assertThrows(SpkiPinMismatchException::class.java) {
            trustManager.checkServerTrusted(emptyArray(), "RSA")
        }
    }

    @Test
    fun checkClientTrusted_always_throws() {
        val trustManager = SpkiPinning.trustManager("anyPinAtAll==")
        val cert = heldCert("some-client").certificate

        assertThrows(java.security.cert.CertificateException::class.java) {
            trustManager.checkClientTrusted(arrayOf(cert), "RSA")
        }
    }

    @Test
    fun pinnedPair_socketFactory_and_trustManager_share_the_same_pin() {
        val cert = heldCert("hub")
        val pin = SpkiPinning.pinOf(cert.certificate)
        val (factory, trustManager) = SpkiPinning.pinnedPair(pin)

        assertNotNull("socketFactory must never be null", factory)
        // The trust manager returned alongside the factory must be the same
        // pinned logic exercised above -- proven by using it directly here
        // rather than merely checking it is non-null.
        trustManager.checkServerTrusted(arrayOf(cert.certificate), "RSA")
        assertThrows(SpkiPinMismatchException::class.java) {
            trustManager.checkServerTrusted(arrayOf(heldCert("someone-else").certificate), "RSA")
        }
    }
}
