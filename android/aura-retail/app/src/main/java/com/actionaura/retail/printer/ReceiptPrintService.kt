package com.actionaura.retail.printer

import android.content.Context
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.net.apiErrorMessage
import com.actionaura.retail.ui.i18n.AppLocale
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Glue between the screens and the printing pieces (PRINT-2):
 *
 *   server receipt model -> [ReceiptRasterizer] (1-bit bitmap) -> [EscPosRaster]
 *   (bytes, QR, cut, kick) -> whichever [ReceiptPrinterAdapter] the owner chose.
 *
 * Never throws: every failure is a [Result.failure] with a message a cashier can
 * read, because a printer problem must never look like a failed sale.
 */
object ReceiptPrintService {

    /** The adapter for the saved choice, or null when nothing usable is set. */
    fun adapterFor(ctx: Context): ReceiptPrinterAdapter? = when (PrinterPrefs.getType(ctx)) {
        PrinterType.NONE -> null
        PrinterType.NETWORK ->
            PrinterPrefs.getHost(ctx).takeIf { it.isNotBlank() }
                ?.let { NetworkPrinterAdapter(it, PrinterPrefs.getPort(ctx)) }
        PrinterType.BLUETOOTH ->
            PrinterPrefs.getBluetoothMac(ctx).takeIf { PrinterSelection.isMac(it) }
                ?.let { BluetoothPrinterAdapter(ctx.applicationContext, it) }
        PrinterType.USB ->
            if (PrinterPrefs.getUsbVendorId(ctx) >= 0 && PrinterPrefs.getUsbProductId(ctx) >= 0)
                UsbPrinterAdapter(ctx.applicationContext, PrinterPrefs.getUsbVendorId(ctx), PrinterPrefs.getUsbProductId(ctx))
            else null
        PrinterType.BUILTIN -> SunmiInnerPrinterAdapter(ctx.applicationContext)
    }

    /**
     * Prints one sale's receipt. [isAutoPrint] is true only for the print that
     * follows a sale; a manual reprint passes false and can NEVER open the
     * drawer ([PrinterSelection.kickDecision]).
     */
    suspend fun printSale(ctx: Context, saleId: Int, isAutoPrint: Boolean): Result<Unit> {
        val adapter = adapterFor(ctx) ?: return Result.failure(PrinterError("No printer is set up."))
        val widthDots = PrinterPrefs.getWidthDots(ctx)
        val lang = if (AppLocale.isRtl) "ar" else "en"
        val resp = try {
            ApiClient.get().receiptModel(saleId, widthDots, lang)
        } catch (e: Exception) {
            return Result.failure(PrinterError(apiErrorMessage(e), e))
        }
        val model = resp.data
        if (resp.status != "success" || model == null) {
            return Result.failure(PrinterError(resp.message ?: "Couldn't load the receipt."))
        }
        val kick = PrinterSelection.kickDecision(PrinterPrefs.getAutoKick(ctx), isAutoPrint, model.kick_allowed)
        return render(ctx, adapter, model, widthDots, kick)
    }

    /** Test print: a fixed sample (Arabic + English item), never kicks. */
    suspend fun printSample(ctx: Context, rtl: Boolean): Result<Unit> {
        val adapter = adapterFor(ctx) ?: return Result.failure(PrinterError("No printer is set up."))
        return render(ctx, adapter, SampleReceipt.model(rtl), PrinterPrefs.getWidthDots(ctx), kick = false)
    }

    /** Admin-only test: a drawer pulse and nothing else (no paper). */
    suspend fun openDrawerTest(ctx: Context): Result<Unit> {
        val adapter = adapterFor(ctx) ?: return Result.failure(PrinterError("No printer is set up."))
        val bytes = byteArrayOf(0x1B, '@'.code.toByte()) + EscPosRaster.drawerKick()
        return send(adapter, PrintJob(bytes, rendered = null, kick = true, cut = false))
    }

    private suspend fun render(
        ctx: Context, adapter: ReceiptPrinterAdapter, model: ReceiptModel, widthDots: Int, kick: Boolean,
    ): Result<Unit> {
        val job = try {
            withContext(Dispatchers.Default) {
                val rendered = ReceiptRasterizer(ctx.applicationContext).render(model, widthDots)
                val bytes = EscPosRaster.wrap(
                    rendered.packed, rendered.widthDots, rendered.height,
                    qrPayload = rendered.qrPayload, cut = true, kick = kick)
                PrintJob(bytes, rendered, kick)
            }
        } catch (e: Exception) {
            return Result.failure(PrinterError("Couldn't draw the receipt: ${e.message}", e))
        }
        return send(adapter, job)
    }
}
