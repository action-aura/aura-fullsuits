package com.actionaura.retail.printer

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothSocket
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import java.io.IOException
import java.util.UUID

/**
 * [ReceiptPrinterAdapter] over Bluetooth Classic RFCOMM / SPP -- the way every
 * cheap 58 mm ESC/POS printer (Xprinter, Rongta, ...) takes a job. The printer
 * must already be PAIRED in Android's own Bluetooth settings; this app lists
 * bonded devices only (no scanning UI, no location permission).
 *
 * PERMISSIONS: API 31+ needs the runtime BLUETOOTH_CONNECT; on API 26-30 the
 * manifest's BLUETOOTH / BLUETOOTH_ADMIN (maxSdkVersion 30) are normal
 * permissions granted at install. [requiredPermissions] reflects that split so
 * a caller requests exactly what this OS version needs.
 *
 * `BluetoothSocket.connect()` has NO timeout of its own -- it blocks until the
 * link is up or fails, and a printer that is off or out of range can keep it
 * blocked for tens of seconds. A cashier is waiting, so a side job closes the
 * socket after [CONNECT_TIMEOUT_MS], which makes connect() throw.
 *
 * Flush then wait [DRAIN_MS] before closing: closing right after write() makes
 * cheap printers drop the tail of the job (the last band, the cut, the drawer
 * pulse). That delay is field lore, not measured here -- NOT PROVEN ON HARDWARE.
 *
 * Not retried, for the same reason as [NetworkPrinterAdapter]: a retry after a
 * partial write risks a second partial copy or a second drawer pulse.
 */
class BluetoothPrinterAdapter(
    private val context: Context,
    private val mac: String,
) : ReceiptPrinterAdapter {

    override val displayName: String = "Bluetooth printer ($mac)"

    override fun isSupportedOnThisDevice(): Boolean =
        context.packageManager.hasSystemFeature(PackageManager.FEATURE_BLUETOOTH)

    override fun requiredPermissions(): List<String> =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) listOf(Manifest.permission.BLUETOOTH_CONNECT)
        else emptyList()

    override suspend fun print(bytes: ByteArray): Result<Unit> = withContext(Dispatchers.IO) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED
        ) {
            return@withContext Result.failure(PrinterError("Bluetooth permission is not granted. Allow Nearby devices for this app."))
        }
        val adapter: BluetoothAdapter? = (context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter
        if (adapter == null) return@withContext Result.failure(PrinterError("This device has no Bluetooth."))
        if (!adapter.isEnabled) return@withContext Result.failure(PrinterError("Bluetooth is switched off."))
        if (!PrinterSelection.isMac(mac)) return@withContext Result.failure(PrinterError("No Bluetooth printer is chosen."))

        var socket: BluetoothSocket? = null
        try {
            val device: BluetoothDevice = adapter.getRemoteDevice(mac)
            socket = device.createRfcommSocketToServiceRecord(SPP_UUID)
            val s = socket
            coroutineScope {
                val killer = launch { delay(CONNECT_TIMEOUT_MS); runCatching { s.close() } }
                try { s.connect() } finally { killer.cancel() }
            }
            val out = s.outputStream
            out.write(bytes)
            out.flush()
            delay(DRAIN_MS)
            Result.success(Unit)
        } catch (e: SecurityException) {
            Result.failure(PrinterError("Bluetooth permission was refused: ${e.message}", e))
        } catch (e: IllegalArgumentException) {
            Result.failure(PrinterError("Bad Bluetooth address: $mac", e))
        } catch (e: IOException) {
            Result.failure(PrinterError("Couldn't reach the Bluetooth printer. Is it on and in range? (${e.message})", e))
        } finally {
            runCatching { socket?.close() }
        }
    }

    /** A paired Bluetooth device as Settings lists it. */
    data class Bonded(val name: String, val mac: String)

    companion object {
        /**
         * Paired devices, or empty when Bluetooth is absent/off or the API-31+
         * runtime permission is not granted yet (Settings asks for it first).
         * Every bonded device is listed: cheap printers often advertise no
         * useful device class, and hiding one would be worse than a longer list.
         */
        fun bondedDevices(context: Context): List<Bonded> {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                ContextCompat.checkSelfPermission(context, Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED
            ) return emptyList()
            return try {
                val adapter = (context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter
                adapter?.bondedDevices.orEmpty().map { Bonded(it.name ?: it.address, it.address) }.sortedBy { it.name }
            } catch (e: SecurityException) {
                emptyList()
            }
        }

        /** The standard Serial Port Profile UUID every SPP printer advertises. */
        val SPP_UUID: UUID = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB")
        const val CONNECT_TIMEOUT_MS = 5000L
        const val DRAIN_MS = 300L
    }
}
