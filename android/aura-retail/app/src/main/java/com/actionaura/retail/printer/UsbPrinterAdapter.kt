package com.actionaura.retail.printer

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.resume

/**
 * [ReceiptPrinterAdapter] over USB host mode (OTG): a printer-class (7)
 * interface with a bulk OUT endpoint. Covers desk printers on a USB-OTG cable
 * and the USB port of Sunmi/iMin-class terminals.
 *
 * Permission is per device and asked at print time through the system dialog.
 * The PendingIntent is FLAG_IMMUTABLE, so the system cannot stuff the result
 * into the broadcast; we therefore ignore the broadcast's extras and simply
 * ask [UsbManager.hasPermission] once the broadcast arrives. (A user who taps
 * "Remember" is not asked again.)
 *
 * Bulk transfers go out in [CHUNK] byte pieces with a [TIMEOUT_MS] timeout
 * each: one giant bulkTransfer of a tall raster can exceed what a controller
 * accepts in one request, and a stalled endpoint (paper out, cover open) must
 * not hang the cashier forever.
 *
 * NOT PROVEN ON HARDWARE: no USB printer has received a byte from this class.
 */
class UsbPrinterAdapter(
    private val context: Context,
    private val vendorId: Int,
    private val productId: Int,
) : ReceiptPrinterAdapter {

    override val displayName: String = "USB printer ($vendorId:$productId)"

    override fun isSupportedOnThisDevice(): Boolean =
        context.packageManager.hasSystemFeature(PackageManager.FEATURE_USB_HOST)

    // USB access is granted per device through a system dialog, not a manifest permission.
    override fun requiredPermissions(): List<String> = emptyList()

    override suspend fun print(bytes: ByteArray): Result<Unit> = withContext(Dispatchers.IO) {
        val manager = context.getSystemService(Context.USB_SERVICE) as? UsbManager
            ?: return@withContext Result.failure(PrinterError("This device has no USB host support."))
        val device = manager.deviceList.values.firstOrNull { it.vendorId == vendorId && it.productId == productId }
            ?: return@withContext Result.failure(PrinterError("The USB printer isn't plugged in."))
        val (intf, endpoint) = findOutEndpoint(device)
            ?: return@withContext Result.failure(PrinterError("That USB device has no printer output."))

        if (!manager.hasPermission(device) && !askPermission(manager, device)) {
            return@withContext Result.failure(PrinterError("USB access to the printer was not allowed."))
        }
        val conn = manager.openDevice(device)
            ?: return@withContext Result.failure(PrinterError("Couldn't open the USB printer."))
        try {
            if (!conn.claimInterface(intf, true)) {
                return@withContext Result.failure(PrinterError("Another app is using the USB printer."))
            }
            var offset = 0
            while (offset < bytes.size) {
                val n = minOf(CHUNK, bytes.size - offset)
                val sent = conn.bulkTransfer(endpoint, bytes, offset, n, TIMEOUT_MS)
                if (sent <= 0) {
                    return@withContext Result.failure(PrinterError("The USB printer stopped accepting data (paper out or cover open?)."))
                }
                offset += sent
            }
            Result.success(Unit)
        } finally {
            runCatching { conn.releaseInterface(intf) }
            conn.close()
        }
    }

    private suspend fun askPermission(manager: UsbManager, device: UsbDevice): Boolean {
        val answered = withTimeoutOrNull(PERMISSION_WAIT_MS) {
            suspendCancellableCoroutine<Unit> { cont ->
                val receiver = object : BroadcastReceiver() {
                    override fun onReceive(c: Context?, i: Intent?) {
                        runCatching { context.unregisterReceiver(this) }
                        if (cont.isActive) cont.resume(Unit)
                    }
                }
                ContextCompat.registerReceiver(context, receiver, IntentFilter(ACTION_USB_PERMISSION),
                    ContextCompat.RECEIVER_NOT_EXPORTED)
                cont.invokeOnCancellation { runCatching { context.unregisterReceiver(receiver) } }
                val pi = PendingIntent.getBroadcast(
                    context, 0, Intent(ACTION_USB_PERMISSION).setPackage(context.packageName),
                    PendingIntent.FLAG_IMMUTABLE)
                manager.requestPermission(device, pi)
            }
        }
        return answered != null && manager.hasPermission(device)
    }

    companion object {
        const val ACTION_USB_PERMISSION = "com.actionaura.retail.USB_PRINTER_PERMISSION"
        const val CHUNK = 16 * 1024
        const val TIMEOUT_MS = 5000
        const val PERMISSION_WAIT_MS = 30_000L

        /** Every attached device with a printer-class interface (what Settings lists). */
        fun listPrinters(context: Context): List<UsbDevice> {
            val manager = context.getSystemService(Context.USB_SERVICE) as? UsbManager ?: return emptyList()
            return manager.deviceList.values.filter { d -> findOutEndpoint(d) != null }
        }

        /** The first printer-class (7) interface and its bulk OUT endpoint, or null. */
        fun findOutEndpoint(device: UsbDevice): Pair<UsbInterface, UsbEndpoint>? {
            for (i in 0 until device.interfaceCount) {
                val intf = device.getInterface(i)
                if (intf.interfaceClass != UsbConstants.USB_CLASS_PRINTER) continue
                for (e in 0 until intf.endpointCount) {
                    val ep = intf.getEndpoint(e)
                    if (ep.type == UsbConstants.USB_ENDPOINT_XFER_BULK && ep.direction == UsbConstants.USB_DIR_OUT) {
                        return intf to ep
                    }
                }
            }
            return null
        }
    }
}
