package com.actionaura.retail.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure JVM tests for [parsePairingPayload] only. [HubPrefs]'s storage
 * half (`getBaseUrl`/`getSpkiPin`/`set`/`clear`/`isConfigured`) needs a real
 * `android.content.Context` to reach `SharedPreferences`, and this module
 * has no Robolectric shadow registered anywhere (confirmed: no
 * `org.robolectric` dependency in `app/build.gradle`, and no other test file
 * in this module references it) -- so, per the plan for this task, the
 * storage half is deliberately NOT unit-tested here. Wiring in Robolectric
 * to cover it is outside this task's file set.
 */
class HubPrefsTest {

    private fun payloadJson(
        baseUrl: String = "https://192.168.1.50:8443",
        spkiPin: String = "abc123PinValue==",
        hubDevicePublicKey: String = "deviceKeyBase64==",
        hubInstallationId: String = "install-uuid-1234",
        pairingCode: String = "short-lived-code",
    ): String = """
        {
          "base_url": "$baseUrl",
          "spki_pin": "$spkiPin",
          "hub_installation_id": "$hubInstallationId",
          "hub_device_public_key": "$hubDevicePublicKey",
          "pairing_code": "$pairingCode"
        }
    """.trimIndent()

    // ── Test 8: well-formed payload ─────────────────────────────────────

    @Test
    fun parsePairingPayload_parses_a_well_formed_payload_with_every_field_populated() {
        val result = parsePairingPayload(
            payloadJson(
                baseUrl = "https://192.168.1.50:8443",
                spkiPin = "abc123PinValue==",
                hubDevicePublicKey = "deviceKeyBase64==",
                hubInstallationId = "install-uuid-1234",
                pairingCode = "short-lived-code",
            )
        )

        assertEquals("https://192.168.1.50:8443", result.baseUrl)
        assertEquals("abc123PinValue==", result.spkiPin)
        assertEquals("deviceKeyBase64==", result.hubDevicePublicKey)
        assertEquals("install-uuid-1234", result.hubInstallationId)
        assertEquals("short-lived-code", result.pairingCode)
    }

    // ── Test 9: each missing/blank required field is rejected by name ──

    @Test
    fun parsePairingPayload_rejects_missing_base_url_naming_the_field() {
        val json = """{"spki_pin":"p","hub_installation_id":"i","hub_device_public_key":"k","pairing_code":"c"}"""
        val error = assertThrows(IllegalArgumentException::class.java) { parsePairingPayload(json) }
        assertTrue(error.message ?: "", (error.message ?: "").contains("base_url"))
    }

    @Test
    fun parsePairingPayload_rejects_blank_spki_pin_naming_the_field() {
        val json = payloadJson(spkiPin = "")
        val error = assertThrows(IllegalArgumentException::class.java) { parsePairingPayload(json) }
        assertTrue(error.message ?: "", (error.message ?: "").contains("spki_pin"))
    }

    @Test
    fun parsePairingPayload_rejects_missing_hub_device_public_key_naming_the_field() {
        val json = """{"base_url":"https://h","spki_pin":"p","hub_installation_id":"i","pairing_code":"c"}"""
        val error = assertThrows(IllegalArgumentException::class.java) { parsePairingPayload(json) }
        assertTrue(error.message ?: "", (error.message ?: "").contains("hub_device_public_key"))
    }

    @Test
    fun parsePairingPayload_rejects_blank_hub_installation_id_naming_the_field() {
        val json = payloadJson(hubInstallationId = "   ")
        val error = assertThrows(IllegalArgumentException::class.java) { parsePairingPayload(json) }
        assertTrue(error.message ?: "", (error.message ?: "").contains("hub_installation_id"))
    }

    @Test
    fun parsePairingPayload_rejects_missing_pairing_code_naming_the_field() {
        val json = """{"base_url":"https://h","spki_pin":"p","hub_installation_id":"i","hub_device_public_key":"k"}"""
        val error = assertThrows(IllegalArgumentException::class.java) { parsePairingPayload(json) }
        assertTrue(error.message ?: "", (error.message ?: "").contains("pairing_code"))
    }

    // ── Test 10: http:// base_url is rejected ───────────────────────────

    /**
     * MUTATION PROOF (reported verbatim per the plan): removing the
     * `startsWith("https://")` guard in [parsePairingPayload] (i.e. letting
     * any non-blank `base_url` through) turns this test RED -- confirmed by
     * temporarily deleting that check and re-running just this test class,
     * see the task report.
     */
    @Test
    fun parsePairingPayload_rejects_an_http_base_url() {
        val json = payloadJson(baseUrl = "http://192.168.1.50:8080")
        val error = assertThrows(IllegalArgumentException::class.java) { parsePairingPayload(json) }
        assertTrue(error.message ?: "", (error.message ?: "").contains("https://"))
    }

    // ── Test 11: malformed JSON never escapes as a raw parser exception ─

    @Test
    fun parsePairingPayload_rejects_malformed_json_as_IllegalArgumentException() {
        assertThrows(IllegalArgumentException::class.java) { parsePairingPayload("not json at all") }
        assertThrows(IllegalArgumentException::class.java) { parsePairingPayload("{\"base_url\": \"https://h\"") }
        assertThrows(IllegalArgumentException::class.java) { parsePairingPayload("[]") }
        assertThrows(IllegalArgumentException::class.java) { parsePairingPayload("") }
        assertThrows(IllegalArgumentException::class.java) {
            parsePairingPayload("""{"base_url": "https://h",}""")
        }
    }
}
