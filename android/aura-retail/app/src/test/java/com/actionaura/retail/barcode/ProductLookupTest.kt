package com.actionaura.retail.barcode

import com.actionaura.retail.net.Product
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Phase 4F: unit tests for the exact barcode/SKU lookup logic PosScreen's
 * scanner result handler calls (extracted, not reimplemented).
 *
 * RETIRED COVERAGE (launch-readiness, "the POS scale fix"): PosScreen's scan
 * handlers no longer call [findProductByCode] -- see that function's own doc
 * comment in ProductLookup.kt. These tests are kept exactly as they were and
 * still pass, but they now prove only that this now-unused pure matcher's
 * case-insensitive barcode/SKU semantics are correct in isolation. They can
 * NO LONGER prove anything about what a live scan resolves to, or that the
 * scan path even reaches the network; that guarantee belongs to
 * ProductLookupResultTest, against [lookupProductByCode]. */
class ProductLookupTest {

    private val catalog = listOf(
        Product(id = "1", sku = "SKU-100", barcode = "0123456789012", name = "Widget"),
        Product(id = "2", sku = "SKU-200", barcode = "9999999999999", name = "Gadget"),
    )

    @Test
    fun known_barcode_matches_the_right_product() {
        val p = findProductByCode(catalog, "0123456789012")
        assertEquals("1", p?.id)
    }

    @Test
    fun known_sku_matches_when_barcode_differs() {
        val p = findProductByCode(catalog, "SKU-200")
        assertEquals("2", p?.id)
    }

    @Test
    fun barcode_match_is_case_insensitive() {
        val p = findProductByCode(
            listOf(Product(id = "3", sku = "ABC", barcode = "AbCdEf")), "abcdef",
        )
        assertEquals("3", p?.id)
    }

    @Test
    fun unknown_barcode_returns_null_not_a_crash() {
        assertNull(findProductByCode(catalog, "does-not-exist"))
    }

    @Test
    fun empty_catalog_returns_null() {
        assertNull(findProductByCode(emptyList(), "0123456789012"))
    }
}
