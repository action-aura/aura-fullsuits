package com.actionaura.retail.net

import com.google.gson.Gson
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.util.Base64

/**
 * Pins the wire contract for `GET api/sub/retail/printer/receipt-payload`
 * (products/retail/backend/api/retail_api.py::printer_receipt_payload)
 * against the Kotlin models that decode it on the Android side --
 * modeled on SaleContractTest.kt's style. A drift between the backend's
 * JSON keys and these property names would previously only surface on a
 * real device against a real printer; this is a pure JVM test that catches
 * it without either.
 */
class ReceiptPayloadContractTest {

    private val gson = Gson()

    @Test
    fun receiptPayloadResponse_deserializes_the_documented_success_shape() {
        val originalBytes = "Aura Retail\nSALE-000042\nTotal 12.340 JOD\n".toByteArray(Charsets.US_ASCII)
        val encoded = Base64.getEncoder().encodeToString(originalBytes)
        val json = """
            {"status":"success","data":{
              "payload_b64":"$encoded",
              "width_chars":42,
              "byte_count":${originalBytes.size}
            }}
        """.trimIndent()

        val resp = gson.fromJson(json, ReceiptPayloadResponse::class.java)

        assertEquals("success", resp.status)
        val data = resp.data!!
        assertEquals(42, data.width_chars)
        assertEquals(originalBytes.size, data.byte_count)

        // java.util.Base64, NOT android.util.Base64 -- see RetailScreens.kt's
        // printReceipt() doc comment for why: android.util.Base64 is a
        // stubbed Android framework class that throws in a plain JVM unit
        // test, which is exactly what makes this decode step untestable
        // with it. This assertion is the actual proof that the real,
        // production decode call (java.util.Base64.getDecoder()) round-
        // trips the server's own base64 encoding correctly.
        val decoded = Base64.getDecoder().decode(data.payload_b64)
        assertArrayEquals(originalBytes, decoded)
    }

    @Test
    fun receiptPayloadResponse_narrow_width_and_zero_kick_shape_deserializes() {
        // width=32 (58mm paper) is the other documented value besides the
        // 42-char default -- pinned separately so a future change that
        // special-cases 42 somewhere can't silently break 32.
        val json = """{"status":"success","data":{"payload_b64":"","width_chars":32,"byte_count":0}}"""
        val resp = gson.fromJson(json, ReceiptPayloadResponse::class.java)
        assertEquals(32, resp.data!!.width_chars)
        assertEquals(0, resp.data!!.byte_count)
    }

    @Test
    fun receiptPayloadResponse_error_shape_has_no_data_payload() {
        // Exact shape printer_receipt_payload returns for a sale_id that
        // doesn't belong to this company (or doesn't exist at all): 404
        // with {"status":"error","message":...}, no "data" key at all.
        val json = """{"status":"error","message":"Sale not found."}"""
        val resp = gson.fromJson(json, ReceiptPayloadResponse::class.java)
        assertEquals("error", resp.status)
        assertNull(resp.data)
        assertEquals("Sale not found.", resp.message)
    }
}
