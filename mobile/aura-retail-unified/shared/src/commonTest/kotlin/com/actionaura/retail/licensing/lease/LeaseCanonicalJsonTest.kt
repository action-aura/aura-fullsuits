package com.actionaura.retail.licensing.lease

import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.putJsonObject
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

/**
 * M11.1/M11.35 -- real cross-runtime compatibility proof, not merely
 * internal self-consistency. `crossVerifiedAgainstRealPythonCanonicalizeOutput`
 * asserts against the EXACT, real, captured output of
 * `commercial_runtime/licensing_contracts/canonical.py::canonicalize()`
 * for this exact payload, executed via this repo's own `.venv`:
 *
 * ```
 * python -c "from commercial_runtime.licensing_contracts.canonical import canonicalize; print(canonicalize({...}))"
 * ```
 *
 * A real, decisive comparison, not an assumption the Kotlin port
 * matches -- this is the same discipline `signed-lease-cross-runtime-
 * compatibility.md` documents at greater length. This single test
 * caught one real bug during implementation: a float source value
 * (`5.0`) was being stripped to `"5"`, which would have produced a
 * byte-different canonical string from Owner's own real output for
 * any lease payload containing a float -- fixed before this test was
 * ever green.
 */
class LeaseCanonicalJsonTest {

    @Test
    fun crossVerifiedAgainstRealPythonCanonicalizeOutput() {
        val payload = buildJsonObject {
            put("assertion_id", JsonPrimitive("a-123"))
            put("product_code", JsonPrimitive("AURA_RETAIL"))
            putJsonObject("entitlements") {
                put("reports", JsonPrimitive(true))
                put("multi_branch", JsonPrimitive(false))
            }
            put("allowed_device_count", JsonPrimitive(3))
            put("issued_at", JsonPrimitive("2026-08-06T00:00:00+00:00"))
            put("note", JsonPrimitive("café"))
            put("zero_float", JsonPrimitive(5.0))
        }

        val expected = "{\"allowed_device_count\":3,\"assertion_id\":\"a-123\",\"entitlements\":{\"multi_branch\":false,\"reports\":true},\"issued_at\":\"2026-08-06T00:00:00+00:00\",\"note\":\"café\",\"product_code\":\"AURA_RETAIL\",\"zero_float\":5.0}"

        assertEquals(expected, LeaseCanonicalJson.canonicalize(payload))
    }

    @Test
    fun keysAreSortedRegardlessOfInsertionOrder() {
        val a = buildJsonObject { put("z", JsonPrimitive(1)); put("a", JsonPrimitive(2)) }
        val b = buildJsonObject { put("a", JsonPrimitive(2)); put("z", JsonPrimitive(1)) }
        assertEquals(LeaseCanonicalJson.canonicalize(a), LeaseCanonicalJson.canonicalize(b))
    }

    @Test
    fun noInsignificantWhitespaceIsEverEmitted() {
        val payload = buildJsonObject { put("a", JsonPrimitive(1)); put("b", JsonPrimitive("x")) }
        val result = LeaseCanonicalJson.canonicalize(payload)
        assertEquals("{\"a\":1,\"b\":\"x\"}", result)
    }

    @Test
    fun nonAsciiCharactersAreEmittedLiterallyNeverAsUnicodeEscapes() {
        val payload = buildJsonObject { put("name", JsonPrimitive("café")) }
        val result = LeaseCanonicalJson.canonicalize(payload)
        assertEquals("{\"name\":\"café\"}", result)
    }

    @Test
    fun excessiveNestingIsRejected() {
        var innermost = buildJsonObject { put("v", JsonPrimitive(1)) }
        repeat(20) { innermost = buildJsonObject { put("n", innermost) } }
        assertFailsWith<LeaseCanonicalizationError> { LeaseCanonicalJson.canonicalize(innermost) }
    }

    @Test
    fun controlCharactersAreEscaped() {
        val payload = buildJsonObject { put("a", JsonPrimitive("line1\nline2\ttab")) }
        val result = LeaseCanonicalJson.canonicalize(payload)
        assertEquals("{\"a\":\"line1\\nline2\\ttab\"}", result)
    }
}
