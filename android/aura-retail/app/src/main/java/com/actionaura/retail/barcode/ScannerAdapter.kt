package com.actionaura.retail.barcode

/**
 * Wave 1B (Part N): contract for barcode input sources beyond HID and the
 * camera, none of which are implemented this wave -- deliberately, per
 * instruction ("do not build every vendor integration now"). This exists so
 * the *shape* of a future adapter is decided (and reviewable) ahead of time,
 * without committing to Serial/BLE/vendor-SDK implementation work this wave.
 *
 * No class in this file is instantiated or referenced anywhere in the app.
 * Adding a real implementation later means: implement this interface, then
 * register it somewhere that opts a user into it explicitly (a future
 * Settings toggle) -- never enabled by default, since each of these needs
 * a real Android permission grant (Bluetooth, USB) that must not be
 * requested unless the user has actually asked for that adapter.
 */
interface ScannerAdapter {
    /** Human-readable name for a device picker / settings UI, e.g. "Zebra DS2208". */
    val displayName: String

    /** Whether this adapter's underlying transport is available on this device
     * at all (e.g. Bluetooth hardware present) -- checked before showing any
     * UI offering to use it, not a connection-state check. */
    fun isSupportedOnThisDevice(): Boolean

    /** Android permissions this adapter needs (e.g. BLUETOOTH_CONNECT) --
     * requested only when the user opts into this specific adapter. */
    fun requiredPermissions(): List<String>

    suspend fun connect(deviceId: String): Result<Unit>
    suspend fun disconnect()

    /** True once connect() has succeeded and no disconnect/error has occurred since. */
    fun isConnected(): Boolean

    /** Stable identifier for the currently-connected device, if any (e.g. a
     * Bluetooth MAC or Serial port name) -- for "last known scanner" reconnect. */
    fun connectedDeviceId(): String?

    /** Called by the adapter's own transport-level listener when a full,
     * decoded barcode arrives. Implementations post to [HidScanBus] or an
     * equivalent bus so callers (PosScreen etc.) never need per-adapter code. */
    fun setOnScan(listener: (String) -> Unit)

    /** Called on a transport-level failure (device unplugged, BLE link lost,
     * malformed data). Never silently retried an unbounded number of times --
     * see [ReconnectPolicy]. */
    fun setOnError(listener: (ScannerAdapterError) -> Unit)

    val reconnectPolicy: ReconnectPolicy
}

data class ScannerAdapterError(val message: String, val cause: Throwable? = null)

/** Bounded, explicit reconnect behavior -- no adapter may retry forever
 * without the user being able to see and cancel it. */
data class ReconnectPolicy(
    val autoReconnect: Boolean = false,
    val maxAttempts: Int = 3,
    val backoffMs: Long = 2000L,
)

// ── Not implemented this wave -- contracts only, per Part N ─────────────────

/** USB Serial/COM scanner (Windows-side equivalent lives in
 * docs/hardware/barcode-input-architecture.md as a documentation-only
 * contract, since the Windows build has no Kotlin to implement an interface
 * in). */
interface SerialScannerAdapter : ScannerAdapter

/** Bluetooth SPP (classic, non-HID) scanner. */
interface BluetoothSppScannerAdapter : ScannerAdapter

/** Bluetooth Low Energy scanner using a vendor-specific GATT service (not
 * standard HID-over-BLE, which is already covered by the existing HID path). */
interface BleScannerAdapter : ScannerAdapter

/** Android intent-broadcast scanners (some handheld Android devices broadcast
 * a scan as an Intent rather than emulating a keyboard or exposing BLE). */
interface IntentBroadcastScannerAdapter : ScannerAdapter

/** A vendor SDK integration (e.g. Zebra DataWedge). Requires that vendor's
 * own SDK as a dependency -- deliberately not added to build.gradle this
 * wave, since pulling in a vendor SDK "just in case" bloats every build
 * whether or not a customer owns that hardware. */
interface VendorSdkScannerAdapter : ScannerAdapter {
    val vendorName: String
}
