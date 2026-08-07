package com.actionaura.retail.licensing

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * Final-review Fix 3 (2026-08-07): `Canonical.kt`'s float rendering must be
 * byte-identical to Python's `json.dumps`, because Owner re-derives the
 * canonical bytes with `json.dumps` (see
 * `owner/app/licensing_service/canonical.py` /
 * `commercial_runtime/licensing_contracts/canonical.py`) and rejects any
 * signature computed over different bytes with `INVALID_SIGNATURE`.
 *
 * Before this fix a whole-number `Double` was rendered as a bare `12`, while
 * Python renders `12.0`. Harmless for licensing (whose signed bodies carry no
 * floats) but NOT for multi-device sync, which signs arbitrary business
 * payloads drained from the Python outbox -- the next synced entity
 * (Products, whose `cost_price`/`sell_price` are SQLite `REAL`) would have
 * hit it on the first whole-number price.
 *
 * EVERY expected string below is literal output of the real interpreter, run
 * in this project's own venv -- never hand-derived:
 *
 *     .venv/Scripts/python.exe -c "import json; print(json.dumps(12.0))"
 *     12.0
 */
class CanonicalDoubleFormatTest {

    /** Canonicalizes a single float field and returns just the rendered
     * number, since `formatDouble` itself is file-private -- this exercises
     * the real signing path rather than an internal shortcut. */
    private fun rendered(value: Double): String =
        canonicalize(mapOf("v" to value)).removePrefix("""{"v":""").removeSuffix("}")

    @Test
    fun `a whole number keeps its decimal point exactly as json_dumps writes it`() {
        // This is the regression: the old implementation produced "12".
        assertThat(rendered(12.0)).isEqualTo("12.0")
        assertThat(canonicalize(mapOf("v" to 12.0))).isEqualTo("""{"v":12.0}""")
    }

    @Test
    fun `whole numbers across the fixed-notation range match json_dumps`() {
        assertThat(rendered(0.0)).isEqualTo("0.0")
        assertThat(rendered(-0.0)).isEqualTo("-0.0")
        assertThat(rendered(-3.0)).isEqualTo("-3.0")
        assertThat(rendered(-12.0)).isEqualTo("-12.0")
        assertThat(rendered(15.0)).isEqualTo("15.0")
        assertThat(rendered(100.0)).isEqualTo("100.0")
        assertThat(rendered(750.0)).isEqualTo("750.0")
        assertThat(rendered(1e7)).isEqualTo("10000000.0")
        assertThat(rendered(12345678.0)).isEqualTo("12345678.0")
        assertThat(rendered(1e15)).isEqualTo("1000000000000000.0")
        assertThat(rendered(1234567890123456.0)).isEqualTo("1234567890123456.0")
        assertThat(rendered(9007199254740992.0)).isEqualTo("9007199254740992.0")
    }

    @Test
    fun `real money-shaped values match json_dumps`() {
        // The exact shape the next synced entity (Products) will carry.
        assertThat(rendered(1199.99)).isEqualTo("1199.99")
        assertThat(rendered(0.5)).isEqualTo("0.5")
        assertThat(rendered(12.5)).isEqualTo("12.5")
        assertThat(rendered(0.1)).isEqualTo("0.1")
        assertThat(rendered(0.001)).isEqualTo("0.001")
        assertThat(rendered(0.0001)).isEqualTo("0.0001")
        assertThat(rendered(0.30000000000000004)).isEqualTo("0.30000000000000004")
    }

    @Test
    fun `exponential notation uses Python's thresholds, sign and two-digit exponent`() {
        // Python switches to exponential at 1e16 and below 1e-4; Java's own
        // toString switches at 1e7 and 1e-3, so these are exactly the values
        // a naive `Double.toString()` would have got wrong in the other
        // direction.
        assertThat(rendered(1e16)).isEqualTo("1e+16")
        assertThat(rendered(1e17)).isEqualTo("1e+17")
        assertThat(rendered(1e22)).isEqualTo("1e+22")
        assertThat(rendered(1.5e22)).isEqualTo("1.5e+22")
        assertThat(rendered(1e308)).isEqualTo("1e+308")
        assertThat(rendered(1e-5)).isEqualTo("1e-05")
        assertThat(rendered(1e-7)).isEqualTo("1e-07")
        assertThat(rendered(2.5e-10)).isEqualTo("2.5e-10")
        assertThat(rendered(1.2345678901234568e17)).isEqualTo("1.2345678901234568e+17")
    }

    @Test
    fun `a float nested in a realistic product sync payload canonicalizes exactly`() {
        // json.dumps({"cost_price":750.0,"id":"cat-1","name":"Laptop",
        //             "sell_price":1199.99,"tax_rate":15.0},
        //            sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        val canonical = canonicalize(
            mapOf(
                "id" to "cat-1",
                "name" to "Laptop",
                "cost_price" to 750.0,
                "sell_price" to 1199.99,
                "tax_rate" to 15.0,
            )
        )
        assertThat(canonical).isEqualTo(
            """{"cost_price":750.0,"id":"cat-1","name":"Laptop","sell_price":1199.99,"tax_rate":15.0}"""
        )
    }

    @Test
    fun `a Float is widened and rendered on the same rules as a Double`() {
        assertThat(rendered(12.0f.toDouble())).isEqualTo("12.0")
        assertThat(canonicalize(mapOf("v" to 12.0f))).isEqualTo("""{"v":12.0}""")
    }

    @Test
    fun `integer types are still rendered without a decimal point`() {
        // Python's json.dumps(12) is "12" -- widening ints to floats here
        // would break the signature in the opposite direction.
        assertThat(canonicalize(mapOf("v" to 12))).isEqualTo("""{"v":12}""")
        assertThat(canonicalize(mapOf("v" to 12L))).isEqualTo("""{"v":12}""")
    }

    @Test
    fun `non-finite values are still rejected`() {
        for (bad in listOf(Double.NaN, Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY)) {
            try {
                canonicalize(mapOf("v" to bad))
                throw AssertionError("expected CanonicalizationError for $bad")
            } catch (exc: CanonicalizationError) {
                // expected
            }
        }
    }
}
