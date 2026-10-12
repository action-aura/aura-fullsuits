package com.actionaura.retail.printer

/**
 * Wire shape of `GET api/sub/retail/printer/receipt-model`
 * (core/retail/receipt_model.py::build_receipt_model). The REAL server model is
 * the contract -- not the sketch in the design doc: lines are
 * `kind` = text | row | rule | qr | blank, a row has logical `start`/`end` sides
 * and (for item rows) a separate `qty` token and optional `strike` gross.
 *
 * Property names are the server's own snake_case keys, as everywhere in net/.
 * [ReceiptModel.kick_allowed] is the server's drawer verdict for THIS sale (the
 * same `_drawer_refusal` rule print-raster uses); null/absent is treated as no.
 */
data class ReceiptLine(
    val kind: String = "",
    val text: String? = null,
    val align: String? = null,
    val bold: Boolean = false,
    val size: String? = null,
    val start: String? = null,
    val end: String? = null,
    val qty: String? = null,
    val strike: String? = null,
    val payload: String? = null,
)

data class ReceiptModel(
    val version: Int = 1,
    val lang: String = "en",
    val rtl: Boolean = false,
    val width_dots: Int = 576,
    val lines: List<ReceiptLine> = emptyList(),
    val qr_payload: String? = null,
    val kick_allowed: Boolean? = null,
)

data class ReceiptModelResponse(val status: String = "", val message: String? = null, val data: ReceiptModel? = null)
