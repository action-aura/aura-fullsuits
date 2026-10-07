package com.actionaura.retail.net

import com.actionaura.retail.ui.screens.PartialReceive
import com.actionaura.retail.ui.screens.buildPartialReceive
import com.actionaura.retail.ui.screens.buildTransferReceive
import com.actionaura.retail.ui.screens.poRemainingQty
import com.actionaura.retail.ui.screens.receiveAllRequest
import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response

/**
 * ANDROID-1007: wire shapes and refusal wording for partial purchase-order
 * receiving and stock transfers. Pure JVM, same approach as SaleContractTest
 * (Gson on the request classes) and ApiErrorsTest (a real HttpException).
 *
 * Proves what the phone SENDS and how it WORDS a refusal; the server side of
 * the same contract lives in products/retail/tests.
 */
class PoTransferContractTest {

    private val gson = Gson()

    private fun conflict(code: String?, message: String = "server words"): HttpException {
        val codePart = if (code == null) "" else ""","code":"$code""""
        val body = """{"status":"error","message":"$message"$codePart}"""
        return HttpException(Response.error<Any>(409, body.toResponseBody("application/json".toMediaType())))
    }

    private fun line(id: Int, qty: Double, received: Double = 0.0) =
        PoLine(id = id, product_name = "P$id", quantity = qty, received_qty = received)

    // ── PO receive request shape ─────────────────────────────────────────────

    @Test
    fun a_partial_receive_sends_item_ids_quantities_and_the_receipt_key() {
        val plan = buildPartialReceive(
            listOf(line(7, 10.0), line(8, 5.0)), mapOf(7 to "4", 8 to "5"), "key-abc-12345")
        val ready = plan as PartialReceive.Ready
        val json = gson.toJsonTree(ready.request).asJsonObject
        assertThat(json.get("receipt_key").asString).isEqualTo("key-abc-12345")
        val lines = json.getAsJsonArray("lines")
        assertThat(lines.size()).isEqualTo(2)
        assertThat(lines[0].asJsonObject.keySet()).containsExactly("item_id", "quantity")
        assertThat(lines[0].asJsonObject.get("item_id").asInt).isEqualTo(7)
        assertThat(lines[0].asJsonObject.get("quantity").asDouble).isEqualTo(4.0)
    }

    @Test
    fun receive_all_omits_the_lines_key_entirely() {
        // The server reads an ABSENT `lines` as "everything remaining"; an
        // empty list would be a different (and refused) request.
        val json = gson.toJsonTree(receiveAllRequest("key-abc-12345")).asJsonObject
        assertThat(json.has("lines")).isFalse()
        assertThat(json.get("receipt_key").asString).isEqualTo("key-abc-12345")
    }

    @Test
    fun zero_and_blank_lines_are_omitted_from_a_partial_receive() {
        val plan = buildPartialReceive(
            listOf(line(1, 3.0), line(2, 3.0), line(3, 3.0)), mapOf(1 to "0", 2 to "", 3 to "2"), "key-abc-12345")
        val sent = (plan as PartialReceive.Ready).request.lines!!
        assertThat(sent.map { it.item_id }).containsExactly(3)
    }

    @Test
    fun a_quantity_above_what_is_still_on_order_is_refused_before_sending() {
        // 10 ordered, 7 already in: 3 remain, so 4 must not leave the phone.
        val plan = buildPartialReceive(listOf(line(1, 10.0, received = 7.0)), mapOf(1 to "4"), "key-abc-12345")
        assertThat(plan).isEqualTo(PartialReceive.Invalid(1, overRemaining = true))
        // ...and exactly the remainder is fine.
        assertThat(buildPartialReceive(listOf(line(1, 10.0, received = 7.0)), mapOf(1 to "3"), "key-abc-12345"))
            .isInstanceOf(PartialReceive.Ready::class.java)
        assertThat(poRemainingQty(line(1, 10.0, received = 12.0))).isEqualTo(0.0)
    }

    @Test
    fun nothing_typed_is_nothing_to_send() {
        assertThat(buildPartialReceive(listOf(line(1, 2.0)), mapOf(1 to "0"), "key-abc-12345"))
            .isEqualTo(PartialReceive.NothingEntered)
    }

    @Test
    fun a_po_line_reads_its_row_id_and_received_quantity_from_the_server_json() {
        val l = gson.fromJson(
            """{"id":42,"uid":"u-1","product_name":"Tea","quantity":10,"received_qty":4}""", PoLine::class.java)
        assertThat(l.id).isEqualTo(42)
        assertThat(l.received_qty).isEqualTo(4.0)
    }

    // ── Transfer receive request shape ───────────────────────────────────────

    private fun tItem(id: String, sent: Double) = TransferItem(id = id, quantity = sent, quantity_sent = sent)

    @Test
    fun a_transfer_receive_lists_every_line_and_keeps_a_zero() {
        val body = buildTransferReceive(listOf(tItem("a", 5.0), tItem("b", 2.0)), mapOf("a" to "5", "b" to "0"))!!
        val json = gson.toJsonTree(body).asJsonObject.getAsJsonArray("items")
        assertThat(json.size()).isEqualTo(2)
        assertThat(json[0].asJsonObject.keySet()).containsExactly("id", "quantity_received")
        assertThat(json[1].asJsonObject.get("quantity_received").asDouble).isEqualTo(0.0)
    }

    @Test
    fun a_transfer_receive_with_a_blank_or_negative_line_is_not_built() {
        val items = listOf(tItem("a", 5.0), tItem("b", 2.0))
        assertThat(buildTransferReceive(items, mapOf("a" to "5", "b" to ""))).isNull()
        assertThat(buildTransferReceive(items, mapOf("a" to "5", "b" to "-1"))).isNull()
    }

    @Test
    fun a_create_transfer_request_carries_branch_ids_and_product_lines_only() {
        val json = gson.toJsonTree(CreateTransferRequest(1, 2, listOf(CreateTransferLine("p-uuid", 3.0)))).asJsonObject
        assertThat(json.keySet()).containsExactly("source_branch_id", "destination_branch_id", "items")
        assertThat(json.getAsJsonArray("items")[0].asJsonObject.keySet()).containsExactly("product_id", "quantity")
    }

    // ── Refusal code -> plain sentence ───────────────────────────────────────

    @Test
    fun every_po_and_transfer_conflict_code_has_a_friendly_message() {
        val syncing = "This hasn't finished syncing from your other device yet. Try again in a moment."
        for (code in listOf(
            "PO_LINES_NOT_ARRIVED", "PO_RECEIPTS_NOT_ARRIVED", "PO_BOOKS_NOT_ARRIVED",
            "TRANSFER_LINES_NOT_ARRIVED", "TRANSFER_SEND_NOT_ARRIVED", "TRANSFER_BOOKS_NOT_ARRIVED",
        )) {
            assertThat(apiErrorMessage(conflict(code))).isEqualTo(syncing)
        }
        assertThat(apiErrorMessage(conflict("OVER_RECEIPT")))
            .isEqualTo("You cannot receive more than is still on order.")
        assertThat(apiErrorMessage(conflict("CLOSE_SHORT_NOT_PARTIAL")))
            .isEqualTo("Only an order that is partly received can be closed short.")
        assertThat(apiErrorMessage(conflict("CLOSE_SHORT_NEEDS_SUPPLIER")))
            .isEqualTo("This order needs a supplier before it can be closed short.")
    }

    @Test
    fun an_unknown_or_missing_409_code_falls_back_to_the_servers_own_message() {
        assertThat(apiErrorMessage(conflict("SOMETHING_NEW", "Already received."))).isEqualTo("Already received.")
        assertThat(apiErrorMessage(conflict(null, "Purchase order already received"))).isEqualTo("Purchase order already received")
    }

    @Test
    fun a_permission_403_with_a_numeric_code_is_still_a_permission_message() {
        // mt_auth puts the NUMBER 403 in `code`; it must not be read as a string code.
        val body = """{"status":"error","error":"forbidden","message":"no","code":403}"""
        val e = HttpException(Response.error<Any>(403, body.toResponseBody("application/json".toMediaType())))
        assertThat(apiErrorMessage(e)).isEqualTo("You don't have permission to do this.")
    }
}
