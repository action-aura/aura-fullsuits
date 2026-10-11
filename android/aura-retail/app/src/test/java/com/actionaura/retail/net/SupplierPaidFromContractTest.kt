package com.actionaura.retail.net

import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response

/**
 * MONEY-3 M2 (phone side): what the supplier-payment request puts ON THE WIRE for
 * `paid_from`. Pure JVM; the assertions are on the Gson-serialized JSON (the same Gson the
 * Retrofit converter uses by default), not on the Kotlin object, because "the key is
 * absent" is a property of the serialized body only.
 *
 * Server contract (retail_api._resolve_supplier_paid_from): 'drawer' | 'office' for cash,
 * ignored otherwise; an absent key lets the server choose.
 */
class SupplierPaidFromContractTest {

    private val gson = Gson()
    private fun json(req: Any) = gson.toJsonTree(req).asJsonObject

    @Test
    fun cash_from_the_till_sends_paid_from_drawer() {
        val j = json(supplierPaymentRequest(25.0, "cash", PAID_FROM_DRAWER))
        assertThat(j.get("paid_from").asString).isEqualTo("drawer")
        assertThat(j.get("method").asString).isEqualTo("cash")
        assertThat(j.get("amount").asDouble).isEqualTo(25.0)
    }

    @Test
    fun cash_from_the_office_sends_paid_from_office() {
        val j = json(supplierPaymentRequest(25.0, "cash", PAID_FROM_OFFICE))
        assertThat(j.get("paid_from").asString).isEqualTo("office")
    }

    @Test
    fun a_non_cash_method_sends_no_paid_from_key_at_all() {
        // Even if the UI still holds a "drawer" selection when the method flips to cheque,
        // the key must be ABSENT (not null, not "drawer").
        for (method in listOf("cheque", "bank_transfer", "card")) {
            val j = json(supplierPaymentRequest(25.0, method, PAID_FROM_DRAWER))
            assertThat(j.has("paid_from")).isFalse()
            assertThat(j.keySet()).containsExactly("amount", "method", "notes")
        }
    }

    @Test
    fun an_unknown_or_missing_choice_sends_no_key_so_the_server_picks_its_default() {
        assertThat(json(supplierPaymentRequest(5.0, "cash", null)).has("paid_from")).isFalse()
        assertThat(json(supplierPaymentRequest(5.0, "cash", "safe")).has("paid_from")).isFalse()
    }

    @Test
    fun a_customer_payment_body_has_no_paid_from_key() {
        // Customers and purchase orders keep using PaymentRequest, unchanged on the wire.
        val j = json(PaymentRequest(10.0, "cash"))
        assertThat(j.has("paid_from")).isFalse()
        assertThat(j.keySet()).containsExactly("amount", "method", "notes")
    }

    @Test
    fun no_open_drawer_409_reads_as_a_plain_sentence_not_the_server_text() {
        val body = """{"status":"error","code":"NO_OPEN_DRAWER","reason_code":"NO_OPEN_DRAWER","message":"server words"}"""
        val e = HttpException(Response.error<Any>(409, body.toResponseBody("application/json".toMediaType())))
        val msg = apiErrorMessage(e)
        assertThat(msg).contains("no open cash drawer")
        assertThat(msg).doesNotContain("server words")
    }
}
