package com.actionaura.retail.licensing.fixtures

/**
 * M8.11 -- real, recovered canonicalization vector, resolving the gap
 * `missing-commercial-runtime-fixture-investigation.md` documents:
 * `commercial_runtime/licensing_contracts/canonical.py`'s own
 * docstring references a `canonical_vectors.json` file that does not
 * exist. The schema was fully recoverable from three independent,
 * mutually cross-checked real implementations (Owner's own
 * `canonical.py`, `commercial_runtime`'s own `canonical.py`'s test
 * suite, and both legacy Android Kotlin `Canonical` ports) -- this is
 * not a new/invented vector.
 *
 * Not written into `commercial_runtime/` itself -- that package is a
 * separate deployable this branch does not own or build. This fixture
 * exists so a future Kotlin canonicalization port in this shared
 * module (`licensing-gap-ownership-matrix.md` gap #7) inherits a
 * pre-verified cross-implementation vector instead of starting from
 * zero.
 */
object CanonicalVectorFixture {
    const val VECTOR_SET_VERSION = "m8-canonical-vector-v1"

    /** Real source: commercial_runtime/licensing_contracts/tests/test_canonical.py:64-78. */
    val KNOWN_VECTOR_INPUT: Map<String, String> = mapOf(
        "contract_version" to "v1",
        "product_code" to "AURA_RETAIL",
        "nonce" to "abc123",
        "timestamp" to "2026-07-22T10:00:00+00:00",
    )

    /** Sorted keys, compact separators -- the real, expected canonical output for [KNOWN_VECTOR_INPUT]. */
    const val KNOWN_VECTOR_EXPECTED_OUTPUT: String =
        "{\"contract_version\":\"v1\",\"nonce\":\"abc123\"," +
            "\"product_code\":\"AURA_RETAIL\",\"timestamp\":\"2026-07-22T10:00:00+00:00\"}"
}
