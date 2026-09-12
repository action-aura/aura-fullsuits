package com.actionaura.retail.printer

/**
 * Wave 1B follow-up (retail-hardware-viewports): contract for sending an
 * already-rendered ESC/POS receipt byte stream to a physical printer.
 *
 * Modeled on barcode/ScannerAdapter.kt's adapter-contract pattern (Part N)
 * -- docs/hardware/receipt-printer-architecture.md names that file as the
 * template a future ReceiptPrinterAdapter should follow -- but this
 * interface is deliberately SMALLER than ScannerAdapter's shape, and that
 * is a decision, not an oversight:
 *
 * ScannerAdapter models a LONG-LIVED input session: a barcode scanner stays
 * connected for the length of a shift, can drop and need to reconnect
 * mid-session, and callers need to ask `isConnected()` at any moment
 * between scans -- which is exactly why it carries `connect(deviceId)`,
 * `disconnect()`, `isConnected()`, `connectedDeviceId()` and a bounded
 * [com.actionaura.retail.barcode.ReconnectPolicy]. None of that is true of
 * a receipt print. Printing a receipt is ONE short-lived, self-contained
 * operation -- open a connection, send the bytes, close it -- triggered
 * once per completed sale, with nothing "connected" in between. A
 * connection-lifecycle API here would be state every implementation has to
 * define and get right for zero behavioural benefit: nothing would ever
 * legitimately observe "connected but idle", because nothing stays
 * connected between prints. Modeling a stateless operation as a stateful
 * one is exactly the kind of extra state that invites a bug no test would
 * think to cover -- so [print] owns its own connection end to end, on
 * every single call, and there is no lifecycle to ask about outside it.
 *
 * The only implementation this wave is [NetworkPrinterAdapter] -- see its
 * own doc comment for why a plain TCP/network transport was chosen over
 * Bluetooth or a vendor SDK.
 */
interface ReceiptPrinterAdapter {

    /** Human-readable name for a settings UI, e.g. "Network printer (192.168.1.50:9100)". */
    val displayName: String

    /** Whether this adapter's transport exists on this device at all (e.g.
     * a network stack, or Bluetooth hardware for a future adapter) --
     * checked before showing any UI that offers to use it. This is a
     * capability check, not a connection-state check (there is no
     * connection state -- see the class doc above). */
    fun isSupportedOnThisDevice(): Boolean

    /** Android permissions this adapter needs, requested only when the
     * user opts into it. Empty for [NetworkPrinterAdapter] -- see its own
     * doc comment for why INTERNET does not belong in this list. */
    fun requiredPermissions(): List<String>

    /**
     * Sends [bytes] -- an already-rendered ESC/POS byte stream; this
     * adapter never builds or interprets receipt content, only transports
     * it -- to the printer and reports success or failure.
     *
     * Never throws: every failure path returns [Result.failure] carrying a
     * [PrinterError], so a caller can always show the cashier a message
     * instead of crashing the payment-success screen over a printer that
     * happens to be switched off.
     */
    suspend fun print(bytes: ByteArray): Result<Unit>
}

/**
 * Mirrors [com.actionaura.retail.barcode.ScannerAdapterError]'s shape --
 * a human-readable message plus the original cause, if any -- but as a
 * real [Throwable] rather than a plain data holder, because [print]'s
 * `Result<Unit>` needs a [Throwable] to carry through [Result.failure].
 * Inventing a second wrapper exception just to hold this data class would
 * only add a layer every caller has to unwrap for no benefit.
 */
data class PrinterError(override val message: String, override val cause: Throwable? = null) :
    Exception(message, cause)
