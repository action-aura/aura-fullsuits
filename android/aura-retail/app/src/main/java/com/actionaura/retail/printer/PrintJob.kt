package com.actionaura.retail.printer

/**
 * One finished print: the ESC/POS [bytes] every byte-stream transport sends,
 * plus -- when the receipt was drawn -- the [rendered] bitmap that a vendor SDK
 * (Sunmi's built-in printer) wants instead of raw bytes. [kick] and [cut] are
 * already decided; no adapter re-derives the drawer rule.
 *
 * [rendered] is null for a drawer-only job (the "Open drawer" test).
 */
class PrintJob(
    val bytes: ByteArray,
    val rendered: ReceiptRasterizer.Rendered?,
    val kick: Boolean,
    val cut: Boolean = true,
)

/**
 * An adapter that prints from the drawn bitmap instead of a byte stream. The
 * [ReceiptPrinterAdapter] contract (bytes in, Result out) is unchanged; this is
 * an optional second door for vendor SDKs, picked by [send].
 */
interface BitmapPrinter {
    suspend fun printJob(job: PrintJob): Result<Unit>
}

/** Sends [job] through whichever door [adapter] offers. */
suspend fun send(adapter: ReceiptPrinterAdapter, job: PrintJob): Result<Unit> =
    if (adapter is BitmapPrinter) adapter.printJob(job) else adapter.print(job.bytes)
