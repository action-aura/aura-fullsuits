package com.actionaura.retail.sync

import com.google.common.truth.Truth.assertThat
import com.google.crypto.tink.subtle.Ed25519Sign
import com.google.crypto.tink.subtle.Ed25519Verify
import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import org.junit.Test
import java.util.Base64

/**
 * AUDIT-033 / S-AUTH (D3, Android): proof of [signAuthorityEvents].
 *
 * The fixture `authority_cross_language.json` is produced by Python
 * (commercial_runtime/sync/tests/_make_authority_cross_fixture.py) and read by
 * BOTH languages. Ed25519 is deterministic, so signing the fixture's `tbs` with
 * its fixed seed here must give byte-identical `sig_b64`; Python's
 * test_sync_authority_android.py then verifies that same signature through
 * `authority.verify_event`. Pure JVM: Tink's subtle Ed25519 stands in for the
 * Keystore-backed [com.actionaura.retail.licensing.DeviceIdentity.sign], which
 * produces the same raw RFC 8032 signatures (see Ed25519CrossVerifyTest).
 */
class AuthoritySigningTest {

    private val b64e = Base64.getEncoder()
    private val b64d = Base64.getDecoder()

    private val fx: JsonObject = JsonParser.parseReader(
        javaClass.classLoader!!.getResourceAsStream("authority_cross_language.json")!!.reader()
    ).asJsonObject

    private val keyPair = Ed25519Sign.KeyPair.newKeyPairFromSeed(b64d.decode(fx["device_seed_b64"].asString))
    private val signFn: (ByteArray) -> String = { b64e.encodeToString(Ed25519Sign(keyPair.privateKey).sign(it)) }

    /** A fresh copy of the page the local route would send. */
    private fun page(): JsonArray =
        JsonArray().apply { add(JsonParser.parseString(fx["page_event"].toString())) }

    private fun payloadOf(page: JsonArray) = page[0].asJsonObject.getAsJsonObject("payload")

    @Test
    fun `the fixture key is the key the lease names`() {
        assertThat(b64e.encodeToString(keyPair.publicKey)).isEqualTo(fx["device_pub_b64"].asString)
    }

    @Test
    fun `signs the page bytes, sets _auth sig and removes auth_tbs_b64`() {
        val events = page()
        val signed = signAuthorityEvents(events, signFn)

        assertThat(signed).isEqualTo(1)
        val event = events[0].asJsonObject
        assertThat(event.has("auth_tbs_b64")).isFalse()
        val sig = payloadOf(events).getAsJsonObject("_auth")["sig"].asString
        // verifies over the exact bytes Python sent...
        Ed25519Verify(keyPair.publicKey).verify(b64d.decode(sig), b64d.decode(fx["tbs_b64"].asString))
        // ...and (cross-language) is identical to what Python's own key produced
        assertThat(sig).isEqualTo(fx["sig_b64"].asString)
    }

    @Test
    fun `numbers inside the payload keep their original spelling`() {
        val events = page()
        signAuthorityEvents(events, signFn)

        val payload = payloadOf(events)
        // Gson keeps the source text of numbers: 2 must not become 2.0, 2.0 not 2.
        assertThat(payload["epoch"].asJsonPrimitive.asString).isEqualTo("2")
        assertThat(payload["ratio"].asJsonPrimitive.asString).isEqualTo("2.0")
        assertThat(payload["half"].asJsonPrimitive.asString).isEqualTo("1.5")
        assertThat(payload["big"].asJsonPrimitive.asString).isEqualTo("9007199254740993")
        assertThat(payload["note"].asString).isEqualTo("caf\u00e9")
        // and the serialized text (what jsonToCanonical / the push body start from)
        assertThat(payload.toString()).contains("\"epoch\":2,")
        assertThat(payload.toString()).contains("\"ratio\":2.0,")
    }

    @Test
    fun `a signing failure leaves the event unsigned, drops the hint and reports once per event`() {
        val events = page()
        val failures = mutableListOf<Throwable>()
        val signed = signAuthorityEvents(events, { throw IllegalStateException("keystore locked") }) { failures.add(it) }

        assertThat(signed).isEqualTo(0)
        assertThat(failures).hasSize(1)
        assertThat(events[0].asJsonObject.has("auth_tbs_b64")).isFalse()
        assertThat(payloadOf(events).getAsJsonObject("_auth").has("sig")).isFalse()
    }

    @Test
    fun `a corrupt hint does not stop the other events from being signed`() {
        val events = page()
        events[0].asJsonObject.addProperty("auth_tbs_b64", "%%%not base64%%%")
        events.add(JsonParser.parseString(fx["page_event"].toString()))
        val failures = mutableListOf<Throwable>()

        val signed = signAuthorityEvents(events, signFn) { failures.add(it) }

        assertThat(signed).isEqualTo(1)
        assertThat(failures).hasSize(1)
        assertThat(payloadOf(events).getAsJsonObject("_auth").has("sig")).isFalse()
        assertThat(events[1].asJsonObject.getAsJsonObject("payload").getAsJsonObject("_auth").has("sig")).isTrue()
    }

    @Test
    fun `events without a hint are left exactly as they were`() {
        val sale = JsonParser.parseString(
            """{"id":"s1","entity_type":"sale","entity_id":"x","event_type":"create","payload":{"total":5,"r":2.0}}"""
        )
        val before = sale.toString()
        val events = JsonArray().apply { add(sale) }

        assertThat(signAuthorityEvents(events, signFn)).isEqualTo(0)
        assertThat(events[0].toString()).isEqualTo(before)
    }
}
