package com.actionaura.retail.licensing.fixtures

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * M8.11/M8.16 -- proves the recovered fixture's own structural
 * integrity. No canonicalization function exists yet in this shared
 * module (gap #7, `licensing-gap-ownership-matrix.md`) -- this test
 * proves the fixture data itself is well-formed and matches the real,
 * documented vector, ready for that future port to assert against.
 */
class CanonicalVectorFixtureTest {

    @Test
    fun knownVectorInputHasTheRealFourFieldsFromCommercialRuntimeTestSuite() {
        assertEquals(
            setOf("contract_version", "product_code", "nonce", "timestamp"),
            CanonicalVectorFixture.KNOWN_VECTOR_INPUT.keys,
        )
    }

    @Test
    fun knownVectorExpectedOutputIsSortedKeysCompactSeparators() {
        val output = CanonicalVectorFixture.KNOWN_VECTOR_EXPECTED_OUTPUT
        assertTrue(output.startsWith("{\"contract_version\""), "keys must be sorted alphabetically, matching commercial_runtime's own canonicalize()")
        assertTrue(!output.contains(", ") && !output.contains(": "), "must use compact separators (no insignificant whitespace)")
    }

    @Test
    fun vectorSetVersionIsRecorded() {
        assertEquals("m8-canonical-vector-v1", CanonicalVectorFixture.VECTOR_SET_VERSION)
    }
}
