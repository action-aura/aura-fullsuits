package com.actionaura.retail.printer

import java.io.ByteArrayOutputStream

/**
 * Kotlin port of `core/retail/escpos_raster.py::wrap_raster` (PRINT-2).
 *
 * WHY A PORT AND NOT A SERVER CALL: the phone must be able to print with the
 * embedded backend not involved in the byte layout (the printer is local to
 * the phone, and the desktop route needs a Windows spooler or a LAN host). Two
 * hand-written encoders drift silently, so this one is pinned BYTE FOR BYTE to
 * the Python module by [EscPosRasterGoldenTest]: golden files are produced by
 * the Python module (`products/retail/tests/tools/gen_escpos_golden.py`) and
 * this class must reproduce them exactly. Change the Python wire format and
 * that test goes red until this file follows.
 *
 * Layout of a job: `ESC @`, the bitmap in <=255-row `GS v 0` bands, an optional
 * native `GS ( k` QR (centred), the partial cut, and -- only when [kick] -- the
 * drawer pulse AFTER the cut (the paper is already out, so the drawer pops as
 * the customer is handed the slip; one job means no receipt/drawer race).
 *
 * BAND SIZE 255: cheap Xprinter/Rongta-class firmwares drop `GS v 0` commands
 * taller than 255 rows. Do not "optimise" it upward.
 */
object EscPosRaster {
    const val BAND_ROWS = 255
    const val MAX_HEIGHT_ROWS = 16000
    val VALID_WIDTHS_DOTS = intArrayOf(384, 576)

    private const val ESC = 0x1B
    private const val GS = 0x1D

    class RasterException(message: String) : IllegalArgumentException(message)

    /** `ESC p 0 25 100` -- pin 0, 50 ms on, 200 ms off (Python drawer_kick() defaults). */
    fun drawerKick(): ByteArray = byteArrayOf(ESC.toByte(), 'p'.code.toByte(), 0, 25, 100)

    /** `GS V 66 3` -- feed to the cut position and PARTIAL cut. */
    fun partialCut(): ByteArray = byteArrayOf(GS.toByte(), 'V'.code.toByte(), 0x42, 0x03)

    fun wrap(
        packedBits: ByteArray,
        widthDots: Int,
        height: Int,
        qrPayload: String? = null,
        cut: Boolean = true,
        kick: Boolean = false,
    ): ByteArray {
        if (widthDots !in VALID_WIDTHS_DOTS) {
            throw RasterException("width_dots must be one of 384/576, got $widthDots")
        }
        if (height < 1 || height > MAX_HEIGHT_ROWS) {
            throw RasterException("height must be in 1..$MAX_HEIGHT_ROWS, got $height")
        }
        val rowBytes = widthDots / 8
        if (packedBits.size != rowBytes * height) {
            throw RasterException(
                "bitmap is ${packedBits.size} bytes; $widthDots dots x $height rows needs ${rowBytes * height}")
        }

        val out = ByteArrayOutputStream(packedBits.size + 128)
        out.write(byteArrayOf(ESC.toByte(), '@'.code.toByte()))
        var start = 0
        while (start < height) {
            val rows = minOf(BAND_ROWS, height - start)
            out.write(band(rowBytes, rows))
            out.write(packedBits, start * rowBytes, rows * rowBytes)
            start += rows
        }

        if (!qrPayload.isNullOrEmpty()) {
            out.write(byteArrayOf(ESC.toByte(), 'a'.code.toByte(), 1))
            out.write(qrCommand(qrPayload))
            out.write(byteArrayOf(0x0A))
            out.write(byteArrayOf(ESC.toByte(), 'a'.code.toByte(), 0))
        }
        if (cut) out.write(partialCut())
        if (kick) out.write(drawerKick())
        return out.toByteArray()
    }

    /** `GS v 0 m xL xH yL yH` (m=0 normal density): width in BYTES, rows in this band. */
    private fun band(rowBytes: Int, rows: Int): ByteArray = byteArrayOf(
        GS.toByte(), 'v'.code.toByte(), '0'.code.toByte(), 0,
        (rowBytes and 0xFF).toByte(), ((rowBytes shr 8) and 0xFF).toByte(),
        (rows and 0xFF).toByte(), ((rows shr 8) and 0xFF).toByte(),
    )

    /** Non-ASCII becomes '?' exactly like Python's `encode('ascii', errors='replace')`. */
    private fun ascii(text: String): ByteArray {
        val out = ByteArrayOutputStream(text.length)
        var i = 0
        while (i < text.length) {
            val cp = text.codePointAt(i)
            out.write(if (cp < 0x80) cp else '?'.code)   // one '?' per code point, like Python
            i += Character.charCount(cp)
        }
        return out.toByteArray()
    }

    /** Full model-2 QR sequence; mirrors `escpos_receipt._qr_command` (module 6, EC level M). */
    fun qrCommand(payload: String, moduleSize: Int = 6, ecLevel: Int = 49): ByteArray {
        val data = ascii(payload)
        // pL pH cover `cn fn m` (3 bytes) PLUS the payload: len + 3. The single
        // most common hand-rolled ESC/POS QR bug is forgetting the 3.
        val length = data.size + 3
        if (length > 0xFFFF) throw RasterException("QR payload too long: ${data.size} bytes")
        val out = ByteArrayOutputStream(data.size + 40)
        fun gsk(vararg b: Int) { out.write(GS); out.write('('.code); out.write('k'.code); b.forEach { out.write(it) } }
        gsk(0x04, 0x00, 0x31, 0x41, 0x32, 0x00)               // select model 2
        gsk(0x03, 0x00, 0x31, 0x43, moduleSize and 0xFF)        // module size
        gsk(0x03, 0x00, 0x31, 0x45, ecLevel and 0xFF)           // error correction
        gsk(length and 0xFF, (length shr 8) and 0xFF, 0x31, 0x50, 0x30)   // store
        out.write(data)
        gsk(0x03, 0x00, 0x31, 0x51, 0x30)                      // print
        return out.toByteArray()
    }

    /**
     * ARGB pixels -> MSB-first packed 1-bit rows, 1 = black. Fixed threshold
     * (luma < [threshold]) and NO dithering: dithering turns Arabic hairlines
     * into noise. A transparent pixel is composited on white (paper), like the
     * desktop `receipt-raster.js::packBits`.
     */
    fun packPixels(argb: IntArray, width: Int, height: Int, threshold: Int = THRESHOLD): ByteArray {
        require(width % 8 == 0) { "width must be a multiple of 8" }
        require(argb.size == width * height) { "pixel count does not match width x height" }
        val rowBytes = width / 8
        val out = ByteArray(rowBytes * height)
        for (y in 0 until height) {
            for (x in 0 until width) {
                val p = argb[y * width + x]
                val a = (p ushr 24 and 0xFF) / 255.0
                val r = p shr 16 and 0xFF
                val g = p shr 8 and 0xFF
                val b = p and 0xFF
                val lum = (0.299 * r + 0.587 * g + 0.114 * b) * a + 255 * (1 - a)
                if (lum < threshold) {
                    val i = y * rowBytes + (x shr 3)
                    out[i] = (out[i].toInt() or (0x80 shr (x and 7))).toByte()
                }
            }
        }
        return out
    }

    /** Below this luminance (0 black .. 255 white) a pixel prints; 160 not 128 because thermal heads under-print grey. */
    const val THRESHOLD = 160
}
