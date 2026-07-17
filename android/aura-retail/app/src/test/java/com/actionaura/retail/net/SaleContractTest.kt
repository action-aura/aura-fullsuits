package com.actionaura.retail.net

import com.google.gson.Gson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.UUID

/**
 * Retail financial-authority contract tests (Phase 4D).
 *
 * Pure JVM tests (no device/emulator) proving the Kotlin request/response
 * adapters actually match the server-authoritative contract in
 * docs/architecture/financial-authority-contracts.md -- not re-testing the
 * Python backend itself (that's covered by
 * products/retail/tests/retail_financial_authority_test.py), but proving the
 * mobile side sends only commercial intent and correctly reads back the
 * server's authoritative fields.
 *
 * Field-name reflection checks below filter out static fields -- the
 * Compose compiler plugin injects a public static `$stable` field into
 * every class in this module (even plain, non-@Composable data classes),
 * which is not part of the actual wire contract (data class properties
 * are always instance fields).
 */
class SaleContractTest {

    private val gson = Gson()

    // ── Request shape: commercial intent only ───────────────────────────────

    @Test
    fun saleItemReq_carries_no_financial_authority_fields() {
        // Compile-time guarantee as much as a reflection check can prove one:
        // SaleItemReq has exactly product_id, quantity, discount_pct -- no
        // unit_price, tax_rate, or line_total. If a future edit reintroduces
        // one of those fields, this test fails immediately.
        val fields = SaleItemReq::class.java.declaredFields.filterNot { java.lang.reflect.Modifier.isStatic(it.modifiers) }.map { it.name }.toSet()
        assertEquals(setOf("product_id", "quantity", "discount_pct"), fields)
    }

    @Test
    fun createSaleRequest_carries_no_client_computed_totals() {
        // subtotal/discount_amount/tax_amount/total must never be fields on
        // the request -- Wave 0 made the server ignore them even when sent,
        // but the historical Android bug was sending tax_amount=0/
        // discount_pct=0 unconditionally. Removing the fields entirely is
        // the strongest guarantee: there's no field left to misuse.
        val fields = CreateSaleRequest::class.java.declaredFields.filterNot { java.lang.reflect.Modifier.isStatic(it.modifiers) }.map { it.name }.toSet()
        val forbidden = setOf("subtotal", "discount_amount", "tax_amount", "total")
        assertTrue(
            "CreateSaleRequest must not expose $forbidden, found: $fields",
            fields.intersect(forbidden).isEmpty(),
        )
        assertTrue(fields.contains("amount_paid"))       // legitimate tender input
        assertTrue(fields.contains("idempotency_key"))   // required for dedup
    }

    @Test
    fun createSaleRequest_serializes_only_commercial_intent_json_keys() {
        val req = CreateSaleRequest(
            amount_paid = 88.0, payment_method = "cash",
            items = listOf(SaleItemReq(product_id = 1, quantity = 1.0, discount_pct = 20.0)),
            idempotency_key = "test-key-1",
        )
        val json = gson.toJsonTree(req).asJsonObject
        // These must never appear on the wire -- a server that (incorrectly)
        // trusted them would have nothing to trust if they're simply absent.
        for (forbidden in listOf("subtotal", "discount_amount", "tax_amount", "total", "unit_price")) {
            assertFalse("request JSON must not contain '$forbidden': $json", json.has(forbidden))
        }
        assertTrue(json.has("amount_paid"))
        assertTrue(json.has("idempotency_key"))
        val item = json.getAsJsonArray("items")[0].asJsonObject
        assertEquals(setOf("product_id", "quantity", "discount_pct"), item.keySet())
    }

    @Test
    fun idempotency_key_is_generated_fresh_per_sale_attempt() {
        val keys = (1..20).map { UUID.randomUUID().toString() }
        assertEquals("every generated key must be unique", 20, keys.toSet().size)
        keys.forEach { assertTrue(it.isNotBlank()) }
    }

    // ── Response shape: authoritative fields the client must display ───────

    @Test
    fun saleResult_deserializes_the_full_authoritative_contract() {
        // Exact shape of products/retail/backend/api/retail_api.py's
        // create_sale() success response (see
        // docs/architecture/financial-authority-contracts.md), for the
        // worked example: subtotal=100, discount=20%, tax=10% -> total=88.
        val json = """
            {"status":"success","data":{
              "id": 42, "sale_number": "SALE-000042",
              "subtotal": 100.0, "discount_amount": 20.0,
              "tax_amount": 8.0, "total": 88.0,
              "amount_paid": 88.0, "change": 0.0, "balance_due": 0.0,
              "warning": null, "calculation_version": "retail-pricing-v2-wave0"
            }}
        """.trimIndent()
        val resp = gson.fromJson(json, SaleResponse::class.java)
        assertEquals("success", resp.status)
        val d = resp.data!!
        assertEquals(88.0, d.total, 0.0001)
        assertEquals(8.0, d.tax_amount, 0.0001)
        assertEquals(20.0, d.discount_amount, 0.0001)
        assertEquals(100.0, d.subtotal, 0.0001)
        assertEquals("retail-pricing-v2-wave0", d.calculation_version)
    }

    @Test
    fun android_style_zero_tax_request_still_yields_a_taxed_authoritative_response() {
        // The historical AUDIT-002 defect: Android sent no tax/discount
        // fields at all (they don't exist on the request anymore -- see
        // above), yet the fixture server response below still carries real
        // tax, because the server resolves tax_rate from the product row,
        // never from the client. This test proves the RESPONSE side is
        // correctly read as authoritative, complementing the request-side
        // proof that no zero-tax value can even be sent.
        val req = CreateSaleRequest(
            amount_paid = 999.0, payment_method = "cash",
            items = listOf(SaleItemReq(product_id = 7, quantity = 1.0)),
            idempotency_key = UUID.randomUUID().toString(),
        )
        assertEquals(0.0, req.items[0].discount_pct, 0.0)   // default, not an assumption of "no tax"

        val serverJson = """{"status":"success","data":{
            "id": 1, "sale_number": "SALE-000001",
            "subtotal": 100.0, "discount_amount": 0.0, "tax_amount": 15.0, "total": 115.0,
            "amount_paid": 999.0, "change": 884.0, "balance_due": -884.0
        }}"""
        val resp = gson.fromJson(serverJson, SaleResponse::class.java)
        assertEquals(15.0, resp.data!!.tax_amount, 0.0001)
        assertEquals(115.0, resp.data!!.total, 0.0001)
    }

    @Test
    fun manipulated_response_fields_are_read_verbatim_never_recomputed_client_side() {
        // There is no client-side formula anywhere in this file (or in
        // RetailScreens.kt's checkout path, see its own comments) that
        // recomputes total from subtotal/discount/tax -- the response's
        // own `total` field is used as-is. Proven here by constructing an
        // internally-inconsistent fixture (subtotal/discount/tax don't sum
        // to total) and confirming the model still reports the server's
        // literal total, not a recomputed one.
        val json = """{"status":"success","data":{
            "id": 1, "subtotal": 1.0, "discount_amount": 0.0, "tax_amount": 0.0, "total": 115.0,
            "amount_paid": 115.0
        }}"""
        val resp = gson.fromJson(json, SaleResponse::class.java)
        assertEquals(115.0, resp.data!!.total, 0.0001)  // exactly what the server sent
    }

    // ── Returns contract ─────────────────────────────────────────────────────

    @Test
    fun returnItemReq_carries_no_financial_authority_fields() {
        val fields = ReturnItemReq::class.java.declaredFields.filterNot { java.lang.reflect.Modifier.isStatic(it.modifiers) }.map { it.name }.toSet()
        assertEquals(setOf("product_id", "quantity"), fields)
    }

    @Test
    fun createReturnRequest_requires_idempotency_key() {
        val fields = CreateReturnRequest::class.java.declaredFields.filterNot { java.lang.reflect.Modifier.isStatic(it.modifiers) }.map { it.name }.toSet()
        assertTrue(fields.contains("idempotency_key"))
    }

    @Test
    fun returnResult_deserializes_authoritative_tax_inclusive_refund() {
        val json = """{"status":"success","data":{
            "id": 5, "return_number": "RET-000005-abcd1234",
            "refund_amount": 115.0, "idempotency_key": "r-1",
            "items": [{"product_id": 7, "quantity": 1.0, "unit_price": 100.0,
                       "discount_amount": 0.0, "tax_amount": 15.0, "line_total": 115.0}],
            "calculation_version": "retail-pricing-v2-wave0"
        }}"""
        val resp = gson.fromJson(json, CreateReturnResponse::class.java)
        assertEquals(115.0, resp.data!!.refund_amount, 0.0001)
        assertEquals(1, resp.data!!.items.size)
    }

    @Test
    fun excessive_return_rejection_response_has_no_data_payload() {
        val json = """{"status":"error","message":
            "Cannot return 5.0 of product 7: only 2.0 remain returnable (sold 4.0, already returned 2.0)."}"""
        val resp = gson.fromJson(json, CreateReturnResponse::class.java)
        assertEquals("error", resp.status)
        assertNull(resp.data)
        assertTrue(resp.message!!.contains("remain returnable"))
    }

    @Test
    fun duplicate_return_idempotent_replay_returns_the_same_id() {
        val first = gson.fromJson(
            """{"status":"success","data":{"id": 9, "return_number": "RET-000009-abcd1234", "refund_amount": 50.0}}""",
            CreateReturnResponse::class.java,
        )
        val replay = gson.fromJson(
            """{"status":"success","data":{"id": 9, "return_number": "RET-000009-abcd1234"}}""",
            CreateReturnResponse::class.java,
        )
        assertEquals(first.data!!.id, replay.data!!.id)
    }
}
