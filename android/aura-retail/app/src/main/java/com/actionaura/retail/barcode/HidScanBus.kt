package com.actionaura.retail.barcode

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/**
 * Wave 1B (Part L): the "one normalized event, POS doesn't care about the
 * source" requirement, made concrete for Android. [MainActivity]'s
 * `dispatchKeyEvent` feeds raw HID keystrokes through [HidScanDetector] and
 * posts completed scans here; PosScreen/ProductsScreen observe [lastScan]
 * exactly the same way they already handle a CameraX/ML Kit result -- a
 * screen that reacts to a scanned code has no way to tell, and does not
 * need to care, whether it came from the camera or a physical HID scanner.
 *
 * A monotonic [ScanEvent.seq] (not just the code string) is included so two
 * consecutive scans of the identical barcode still produce a distinct,
 * observable event -- otherwise Compose state equality would treat the
 * second identical scan as "no change" and silently drop it.
 */
data class ScanEvent(val code: String, val seq: Long)

/**
 * [HidScanBus.lastScan] is a process-wide singleton with no "only while POS is
 * the visible screen" gate (unlike the desktop engine's
 * SubsystemApp.active === 'retail' check in subsystem-retail.js) -- so a screen
 * whose LaunchedEffect keys directly off [HidScanBus.lastScan] would otherwise
 * replay whatever scan is already sitting there the moment it (re-)enters
 * composition (e.g. the cashier scanned while on another tab, or is simply
 * revisiting POS after a scan earlier in the session), silently applying a
 * stale scan to whatever cart exists now.
 *
 * A screen must remember the highest [ScanEvent.seq] it has already consumed
 * (captured at the moment it entered composition, since local `remember` state
 * doesn't survive leaving the screen) and only act on a strictly newer one.
 */
fun isUnconsumedScan(event: ScanEvent, lastConsumedSeq: Long): Boolean = event.seq > lastConsumedSeq

object HidScanBus {
    var lastScan by mutableStateOf<ScanEvent?>(null)
        private set

    private var seq = 0L
    private val detector = HidScanDetector()

    fun onChar(ch: Char, nowMs: Long) = detector.onChar(ch, nowMs)

    fun onEnter(nowMs: Long) {
        val code = detector.onEnter(nowMs) ?: return
        seq += 1
        lastScan = ScanEvent(code, seq)
    }

    fun reset() = detector.reset()
}
