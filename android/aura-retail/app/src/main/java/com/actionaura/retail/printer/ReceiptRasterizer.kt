package com.actionaura.retail.printer

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.text.Layout
import android.text.StaticLayout
import android.text.TextDirectionHeuristics
import android.text.TextPaint
import androidx.core.content.res.ResourcesCompat
import com.actionaura.retail.R
import kotlin.math.ceil

/**
 * Draws a [ReceiptModel] onto a white [Bitmap] and packs it to 1 bit (PRINT-2).
 *
 * WHY A BITMAP: ESC/POS text mode prints Arabic as '?' and no code page shapes
 * Arabic across printer brands. Android shapes and joins Arabic natively
 * (StaticLayout), so we draw the slip ourselves and ship pixels -- the same
 * approach the desktop takes with a <canvas> (frontend/receipt-raster.js).
 * Positions come from the pure [ReceiptLayout] (unit-tested); this class only
 * measures text and paints what the plan says.
 *
 * FONT: the app's own bundled merged face (res/font/aura_sans_*, Latin from
 * Jakarta + Arabic from Plex, one file per weight -- see ui/theme/Type.kt for
 * why it is merged). SemiBold/Bold on purpose: a 1-bit threshold eats the thin
 * strokes of a regular weight, Arabic joins at 22 px on 58 mm paper first.
 *
 * Every text op is a one-line StaticLayout with the base direction forced
 * (RTL for an Arabic receipt / Arabic run, LTR otherwise) so Unicode isolates
 * in the model keep a number's digits in order inside an RTL line.
 *
 * NOT PROVEN ON HARDWARE and not rendered in a JVM unit test (no Robolectric
 * in this project): the glyph shaping, the bold weight at 1-bit and the
 * isolate handling on API 26-28 are exactly what a phone and a printer must
 * confirm.
 */
class ReceiptRasterizer(context: Context) {
    private val appContext = context.applicationContext
    private val regular: Typeface = load(R.font.aura_sans_semibold)
    private val bold: Typeface = load(R.font.aura_sans_bold)

    class Rendered(val bitmap: Bitmap, val packed: ByteArray, val widthDots: Int, val height: Int, val qrPayload: String?)

    private fun load(res: Int): Typeface =
        ResourcesCompat.getFont(appContext, res) ?: Typeface.DEFAULT_BOLD

    private fun paint(isBold: Boolean, sizePx: Int) = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = if (isBold) bold else regular
        textSize = sizePx.toFloat()
        color = Color.BLACK
    }

    private fun layoutOf(text: String, isBold: Boolean, sizePx: Int, rtl: Boolean): StaticLayout {
        val p = paint(isBold, sizePx)
        val w = ceil(Layout.getDesiredWidth(text, p)).toInt().coerceAtLeast(1) + 2
        return StaticLayout.Builder.obtain(text, 0, text.length, p, w)
            .setAlignment(Layout.Alignment.ALIGN_NORMAL)
            .setTextDirection(if (rtl) TextDirectionHeuristics.RTL else TextDirectionHeuristics.LTR)
            .setIncludePad(false)
            .setMaxLines(1)
            .build()
    }

    fun render(model: ReceiptModel, widthDots: Int): Rendered {
        require(widthDots == 384 || widthDots == 576) { "widthDots must be 384 or 576" }
        val plan = ReceiptLayout.plan(model, widthDots) { text, isBold, size, rtl ->
            layoutOf(text, isBold, size, rtl).getLineWidth(0)
        }
        val bmp = Bitmap.createBitmap(widthDots, plan.height, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bmp)
        canvas.drawColor(Color.WHITE)
        val ink = Paint().apply { color = Color.BLACK; style = Paint.Style.FILL }

        for (op in plan.ops) {
            when (op) {
                is ReceiptLayout.Op.Rule ->
                    canvas.drawRect(plan.pad.toFloat(), op.y.toFloat(), (widthDots - plan.pad).toFloat(), (op.y + 2).toFloat(), ink)
                is ReceiptLayout.Op.Text -> {
                    val l = layoutOf(op.text, op.bold, op.sizePx, op.rtl)
                    val w = l.getLineWidth(0)
                    val x = when (op.align) {
                        ReceiptLayout.Side.CENTER -> (widthDots - w) / 2f
                        ReceiptLayout.Side.RIGHT -> widthDots - plan.pad - op.inset - w
                        ReceiptLayout.Side.LEFT -> (plan.pad + op.inset).toFloat()
                    }
                    drawLayout(canvas, l, x, op.baselineY.toFloat())
                }
                is ReceiptLayout.Op.Strike -> {
                    val l = layoutOf(op.text, op.bold, op.sizePx, false)
                    val w = l.getLineWidth(0)
                    val edge = if (op.side == ReceiptLayout.Side.RIGHT) widthDots - plan.pad - op.offset.toFloat()
                    else (plan.pad + op.offset).toFloat()
                    val x = if (op.side == ReceiptLayout.Side.RIGHT) edge - w else edge
                    drawLayout(canvas, l, x, op.baselineY.toFloat())
                    val barLeft = if (op.side == ReceiptLayout.Side.RIGHT) edge - op.w else edge
                    val barY = op.baselineY - Math.round(op.sizePx * 0.35f)
                    canvas.drawRect(barLeft, barY.toFloat(), barLeft + op.w, (barY + 2).toFloat(), ink)
                }
            }
        }
        val px = IntArray(widthDots * plan.height)
        bmp.getPixels(px, 0, widthDots, 0, 0, widthDots, plan.height)
        val packed = EscPosRaster.packPixels(px, widthDots, plan.height)
        return Rendered(bmp, packed, widthDots, plan.height, model.qr_payload)
    }

    /** Draws a one-line layout so its first baseline lands on [baselineY]. */
    private fun drawLayout(canvas: Canvas, l: StaticLayout, x: Float, baselineY: Float) {
        canvas.save()
        canvas.translate(x, baselineY - l.getLineBaseline(0))
        l.draw(canvas)
        canvas.restore()
    }
}
