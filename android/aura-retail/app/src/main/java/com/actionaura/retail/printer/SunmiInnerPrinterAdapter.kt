package com.actionaura.retail.printer

import android.content.Context
import android.content.Intent
import android.os.Build
import com.sunmi.peripheral.printer.InnerPrinterCallback
import com.sunmi.peripheral.printer.InnerPrinterManager
import com.sunmi.peripheral.printer.InnerResultCallback
import com.sunmi.peripheral.printer.SunmiPrinterService
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

/**
 * The built-in printer of Sunmi terminals (V2s, T2, D2, ...), through Sunmi's
 * PUBLISHED library (`com.sunmi:printerlibrary`, pinned in build.gradle) -- never
 * a hand-copied AIDL, whose positional transaction ids silently call the wrong
 * method (the hazard recorded in [NetworkPrinterAdapter]).
 *
 * API NOTE: the design sketch named `IWoyouService`; the library's real surface
 * (read from the resolved 1.0.24 classes.jar) is [InnerPrinterManager.bindService]
 * with an [InnerPrinterCallback], and a [SunmiPrinterService] exposing
 * printBitmap / printQRCode / lineWrap / cutPaper / openDrawer / getPrinterPaper.
 * Callbacks are [InnerResultCallback]s (onRunResult / onRaiseException /
 * onPrintResult / onReturnString).
 *
 * Prints the DRAWN BITMAP (so Arabic is correct) through [BitmapPrinter]; the
 * byte-stream [print] door is refused on purpose -- raw ESC/POS into the inner
 * printer is a different, per-model code path this wave does not take.
 * The QR is printed by the printer (`printQRCode`) like the byte path does.
 * The drawer opens through the library's `openDrawer` only when [PrintJob.kick].
 *
 * Stateless like the other adapters: bind, print, unbind, every call.
 *
 * NOT PROVEN ON HARDWARE: no Sunmi device was available; call order, the
 * cutPaper behaviour on models with no cutter (guarded, failure ignored) and
 * openDrawer on a model with no drawer port are all unconfirmed.
 */
class SunmiInnerPrinterAdapter(private val context: Context) : ReceiptPrinterAdapter, BitmapPrinter {

    override val displayName: String = "Built-in printer"

    override fun isSupportedOnThisDevice(): Boolean = isAvailable(context)

    override fun requiredPermissions(): List<String> = emptyList()

    override suspend fun print(bytes: ByteArray): Result<Unit> =
        Result.failure(PrinterError("The built-in printer prints from the drawn receipt, not raw bytes."))

    override suspend fun printJob(job: PrintJob): Result<Unit> = withContext(Dispatchers.Main) {
        val connected = CompletableDeferred<SunmiPrinterService>()
        val callback = object : InnerPrinterCallback() {
            override fun onConnected(service: SunmiPrinterService) { connected.complete(service) }
            override fun onDisconnected() { /* a stateless call: nothing to recover */ }
        }
        try {
            if (!InnerPrinterManager.getInstance().bindService(context, callback)) {
                return@withContext Result.failure(PrinterError("The built-in printer service could not be started."))
            }
            val service = withTimeoutOrNull(BIND_TIMEOUT_MS) { connected.await() }
                ?: return@withContext Result.failure(PrinterError("The built-in printer did not answer."))
            withContext(Dispatchers.IO) { run(service, job) }
        } catch (e: Exception) {
            Result.failure(PrinterError("Built-in printer error: ${e.message}", e))
        } finally {
            runCatching { InnerPrinterManager.getInstance().unBindService(context, callback) }
        }
    }

    private suspend fun run(service: SunmiPrinterService, job: PrintJob): Result<Unit> {
        val done = CompletableDeferred<String?>()   // null = ok, else the printer's complaint
        val last = object : InnerResultCallback() {
            override fun onRunResult(isSuccess: Boolean) { if (isSuccess) done.complete(null) else done.complete("The printer refused the job.") }
            override fun onReturnString(result: String?) {}
            override fun onRaiseException(code: Int, msg: String?) { done.complete(msg ?: "Printer error $code") }
            override fun onPrintResult(code: Int, msg: String?) {}
        }
        val rendered = job.rendered
        if (rendered != null) {
            service.setAlignment(0, null)
            service.printBitmap(rendered.bitmap, null)
            val qr = rendered.qrPayload
            if (!qr.isNullOrEmpty()) {
                service.setAlignment(1, null)
                service.printQRCode(qr, QR_MODULE_SIZE, QR_ERROR_LEVEL_M, null)
                service.setAlignment(0, null)
            }
            service.lineWrap(FEED_LINES, null)
            if (job.cut) runCatching { service.cutPaper(null) }   // models with no cutter refuse; not an error
        }
        // The LAST call carries the callback: Sunmi queues calls in order, so its
        // result is the job's result.
        if (job.kick) service.openDrawer(last) else service.lineWrap(1, last)
        val verdict = withTimeoutOrNull(RESULT_TIMEOUT_MS) { done.await() }
            ?: return Result.failure(PrinterError("The built-in printer did not confirm the job."))
        return if (verdict == null) Result.success(Unit) else Result.failure(PrinterError(verdict))
    }

    companion object {
        const val SERVICE_PACKAGE = "woyou.aidlservice.jiuiv5"
        const val SERVICE_ACTION = "woyou.aidlservice.jiuiv5.IWoyouService"
        const val BIND_TIMEOUT_MS = 5000L
        const val RESULT_TIMEOUT_MS = 15_000L
        const val FEED_LINES = 3
        const val QR_MODULE_SIZE = 6
        const val QR_ERROR_LEVEL_M = 1   // Sunmi: 0=L 1=M 2=Q 3=H

        /** Sunmi hardware AND the printer service actually resolves (needs the manifest <queries> entry). */
        fun isAvailable(context: Context): Boolean {
            val resolves = context.packageManager.resolveService(
                Intent().setPackage(SERVICE_PACKAGE).setAction(SERVICE_ACTION), 0) != null
            return PrinterSelection.builtInOffered(Build.MANUFACTURER, resolves)
        }
    }
}
