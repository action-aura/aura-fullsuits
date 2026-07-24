package com.actionaura.retail.licensing

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class CanonicalTest {

    @Test
    fun `sorted keys`() {
        assertThat(canonicalize(linkedMapOf("b" to 1, "a" to 2))).isEqualTo("{\"a\":2,\"b\":1}")
    }

    @Test
    fun `compact separators no whitespace`() {
        val out = canonicalize(linkedMapOf("a" to listOf(1, 2, 3), "b" to linkedMapOf("c" to 1)))
        assertThat(out).doesNotContain(" ")
    }

    @Test
    fun `nfc normalization makes equivalent strings identical`() {
        val composed = linkedMapOf<String, Any?>("name" to "café") // NFC (single codepoint)
        val decomposed = linkedMapOf<String, Any?>("name" to "café") // NFD (e + combining acute)
        assertThat(canonicalize(composed)).isEqualTo(canonicalize(decomposed))
    }

    @Test
    fun `array order preserved`() {
        assertThat(canonicalize(linkedMapOf("a" to listOf(3, 1, 2)))).isEqualTo("{\"a\":[3,1,2]}")
    }

    @Test
    fun `null preserved not stripped`() {
        val payload = HashMap<String, Any?>()
        payload["a"] = null
        assertThat(canonicalize(payload)).isEqualTo("{\"a\":null}")
    }

    @Test(expected = CanonicalizationError::class)
    fun `rejects nan`() {
        canonicalize(linkedMapOf("a" to Double.NaN))
    }

    @Test(expected = CanonicalizationError::class)
    fun `rejects infinity`() {
        canonicalize(linkedMapOf("a" to Double.POSITIVE_INFINITY))
    }

    @Test(expected = CanonicalizationError::class)
    fun `rejects excessive nesting`() {
        var payload: Map<String, Any?> = mapOf("leaf" to 1)
        for (i in 0 until 20) {
            payload = mapOf("next" to payload)
        }
        canonicalize(payload)
    }

    @Test
    fun `matches known python vector`() {
        // Cross-checked by hand against
        // commercial_runtime/licensing_contracts/tests/test_canonical.py::test_matches_owner_known_vector
        val payload = linkedMapOf(
            "contract_version" to "v1",
            "product_code" to "AURA_RETAIL",
            "nonce" to "abc123",
            "timestamp" to "2026-07-22T10:00:00+00:00",
        )
        val expected = "{\"contract_version\":\"v1\",\"nonce\":\"abc123\"," +
            "\"product_code\":\"AURA_RETAIL\",\"timestamp\":\"2026-07-22T10:00:00+00:00\"}"
        assertThat(canonicalize(payload)).isEqualTo(expected)
    }

    @Test
    fun `print vector for python cross-verification`() {
        val payload = linkedMapOf<String, Any?>(
            "b_field" to "value with \"quotes\" and \\backslash\\",
            "a_field" to listOf(1, 2, 3),
            "z_field" to null,
            "unicode_field" to "café ééé 中文",
            "nested" to linkedMapOf("y" to true, "x" to 42),
        )
        println("CANONICAL_OUTPUT=" + canonicalize(payload))
    }
}
