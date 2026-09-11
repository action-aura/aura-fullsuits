package com.actionaura.retail.printer

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.IOException
import java.net.InetSocketAddress
import java.net.Socket

/**
 * [ReceiptPrinterAdapter] over a plain TCP socket -- the standard way an
 * ESC/POS network/LAN receipt printer accepts a job: raw bytes to port
 * 9100 (a de facto standard, sometimes called "JetDirect"/"raw" printing),
 * no protocol beyond "write the bytes and the printer prints them."
 *
 * WHY NETWORK/TCP AND NOT BLUETOOTH OR A VENDOR SDK: INTERNET is already a
 * normal (non-runtime) permission this app declares, so this transport
 * needs no new manifest entry and no dependency; and unlike a vendor AIDL
 * interface -- whose transaction IDs are positional, so a hand-written
 * partial copy can silently call the wrong method -- a network printer is
 * nothing more than a socket. That makes it the only transport genuinely
 * VERIFIABLE without real printer hardware: [NetworkPrinterAdapterTest]
 * proves this end to end against a real `java.net.ServerSocket` on
 * localhost. Bluetooth and vendor SDKs are deliberately out of scope here.
 *
 * See [ReceiptPrinterAdapter]'s doc comment for why this class has no
 * connect/disconnect/isConnected lifecycle: every [print] call opens and
 * closes its own socket.
 */
class NetworkPrinterAdapter(
    private val host: String,
    private val port: Int = DEFAULT_PORT,
) : ReceiptPrinterAdapter {

    override val displayName: String = "Network printer ($host:$port)"

    // Any device running this app has a network stack -- there is no
    // hardware capability to probe here the way a Bluetooth radio would need.
    override fun isSupportedOnThisDevice(): Boolean = true

    // INTERNET is a NORMAL permission (declared once in the manifest,
    // granted at install time), not a dangerous/runtime permission like
    // BLUETOOTH_CONNECT -- there is nothing for a caller to request.
    override fun requiredPermissions(): List<String> = emptyList()

    override suspend fun print(bytes: ByteArray): Result<Unit> = withContext(Dispatchers.IO) {
        // Android throws NetworkOnMainThreadException for blocking socket
        // I/O on the main thread -- this withContext is not an
        // optimization, it is required for this to run at all when called
        // from a UI-thread coroutine scope (as it is, right after a
        // completed sale).
        try {
            Socket().use { socket ->
                // Explicit connect timeout: a receipt printer that is
                // powered off, unplugged from the LAN, or simply not
                // listening on this port would otherwise leave a bare
                // connect() blocking indefinitely. This call happens
                // synchronously right after a completed sale -- a cashier
                // must never be stuck staring at a frozen success screen
                // because of a printer.
                socket.connect(InetSocketAddress(host, port), CONNECT_TIMEOUT_MS)
                // soTimeout bounds a blocking write the same way the
                // connect timeout bounds the handshake: a printer that
                // accepts the TCP connection and then never drains its
                // receive buffer (paper out, cover open, jammed) would
                // otherwise hang the write forever instead of the connect.
                socket.soTimeout = WRITE_TIMEOUT_MS
                socket.getOutputStream().use { out ->
                    // No special case for an empty payload: connecting and
                    // writing zero bytes is harmless and succeeds like any
                    // other print. This adapter's job is transport, not
                    // receipt validation -- an empty receipt is a caller
                    // bug, not a transport failure.
                    out.write(bytes)
                    out.flush()
                }
            }
            Result.success(Unit)
        } catch (e: IOException) {
            // Deliberately NOT retried. A half-written receipt silently
            // reprinted by this adapter is worse than a failed print the
            // cashier notices and retries on purpose: an automatic retry
            // after a partial write to a printer that DID receive some
            // bytes risks a second partial copy, a paper jam, or (worse) a
            // duplicate drawer-kick byte sequence if this payload had
            // kick=1. One attempt, one honest result.
            Result.failure(
                PrinterError("Couldn't reach printer at $host:$port: ${e.message}", e),
            )
        }
    }

    companion object {
        const val DEFAULT_PORT = 9100
        const val CONNECT_TIMEOUT_MS = 5000
        const val WRITE_TIMEOUT_MS = 5000
    }
}
