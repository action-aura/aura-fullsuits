package com.actionaura.retail.printer

import android.graphics.Bitmap
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.gson.Gson
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/**
 * PRINT-2: renders the SAME sample receipt the desktop uses (Arabic shop name,
 * "قهوة عربية" x2, "Mint Tea" x1, JOD at 3 dp; the models come from the real
 * Python receipt_model via products/retail/tests/tools/gen_receipt_model_samples.py)
 * with the REAL [ReceiptRasterizer] on a real Android text stack, at 576 and 384
 * dots, in Arabic and English.
 *
 * Each render is (a) saved as a PNG into the app's external files dir and
 * (b) encoded and sent through the REAL [NetworkPrinterAdapter] to a capture
 * server on the host (emulator alias 10.0.2.2:19101) so the bytes the printer
 * would receive can be decoded back to a picture on the host. A JVM unit test
 * cannot do either: there is no Canvas/StaticLayout there.
 *
 * Run: gradlew connectedDebugAndroidTest with the host capture server up
 * (see the PRINT-2 notes). Asserting only what is machine-checkable here (the
 * bitmap has ink, the job was accepted); legibility and direction are judged by
 * the host-side image checker and by eye.
 */
@RunWith(AndroidJUnit4::class)
class ReceiptRenderInstrumentedTest {
    private val instr = InstrumentationRegistry.getInstrumentation()
    private val target = instr.targetContext

    private fun model(lang: String): ReceiptModel =
        instr.context.assets.open("sample_$lang.json").use {
            Gson().fromJson(String(it.readBytes(), Charsets.UTF_8), ReceiptModel::class.java)
        }

    @Test fun render_and_send_every_variant() {
        val out = File(target.getExternalFilesDir(null), "print2").apply { mkdirs() }
        val rasterizer = ReceiptRasterizer(target)
        for (lang in listOf("ar", "en")) for (width in listOf(576, 384)) {
            val r = rasterizer.render(model(lang), width)
            File(out, "render_${lang}_$width.png").outputStream().use {
                r.bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)
            }
            assertTrue("no ink in $lang/$width", r.packed.any { b -> b.toInt() != 0 })
            val bytes = EscPosRaster.wrap(r.packed, r.widthDots, r.height, r.qrPayload, cut = true, kick = true)
            val sent = runBlocking { NetworkPrinterAdapter("10.0.2.2", 19101).print(bytes) }
            assertTrue("send failed for $lang/$width: ${sent.exceptionOrNull()}", sent.isSuccess)
        }
    }
}
