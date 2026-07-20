package com.actionaura.retail.barcode

/**
 * Wave 1B (Part L/M): normalized USB-OTG / Bluetooth HID ("keyboard wedge")
 * barcode scanner detection for Android, ported from the identical,
 * already-working algorithm in products/retail/frontend/subsystem-retail.js's
 * "BARCODE SCANNER ENGINE" (Windows desktop build) -- this project already
 * has one real, driver-free HID implementation; this is the same logic, not
 * a redesign, so both platforms behave identically for a cashier who's used
 * to one and switches to the other.
 *
 * Such scanners emit keystrokes exactly like very fast, very regular manual
 * typing, terminated by Enter. Human typing has irregular gaps; a scanner's
 * gaps are all sub-[timeoutMs]. That timing gap is the only reliable
 * distinguishing signal -- there is no separate "this is a scanner" event on
 * a HID device, it is indistinguishable from a keyboard at the OS level.
 *
 * Pure and platform-independent on purpose (no Android KeyEvent/Compose
 * types here) so it's unit-testable without a device or any hardware --
 * see HidScanDetectorTest.kt. The actual KeyEvent wiring into a real Compose
 * screen is a separate, thin adapter; this class is the part that can be
 * proven correct without physical hardware, which does not exist for this
 * wave (see docs/hardware/barcode-scanner-compatibility-matrix.md -- this
 * path is PROTOCOL SUPPORTED, unit-tested, NOT physically verified).
 */
class HidScanDetector(
    private val timeoutMs: Long = 50L,
    private val minLength: Int = 3,
) {
    private val buffer = StringBuilder()
    private var firstCharAtMs: Long = 0L
    private var lastCharAtMs: Long = 0L

    /** Feed one printable character. Call [onEnter] separately when Enter arrives. */
    fun onChar(ch: Char, nowMs: Long) {
        val gap = nowMs - lastCharAtMs
        lastCharAtMs = nowMs
        if (gap > timeoutMs) {
            buffer.setLength(0)
            firstCharAtMs = nowMs
        }
        buffer.append(ch)
    }

    /**
     * Call when Enter arrives. Returns the decoded code if this sequence
     * looks like a genuine fast HID burst (not human typing), or null
     * otherwise -- in which case the caller should let Enter behave
     * normally (e.g. submit a search field) rather than treating it as a scan.
     */
    fun onEnter(nowMs: Long): String? {
        val code = buffer.toString()
        val elapsed = nowMs - firstCharAtMs
        // Same formula as the proven Windows engine: total elapsed time must
        // stay within timeoutMs per character (plus 2 chars of slack for the
        // first keystroke's inherently-unmeasured gap and Enter itself).
        val fastBurst = firstCharAtMs != 0L && elapsed <= timeoutMs * (code.length + 2)
        buffer.setLength(0)
        firstCharAtMs = 0L
        return if (code.length >= minLength && fastBurst) code else null
    }

    fun reset() {
        buffer.setLength(0)
        firstCharAtMs = 0L
        lastCharAtMs = 0L
    }
}
