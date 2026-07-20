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
