package com.actionaura.retail.printer

/**
 * The receipt the "Test print" button draws. It is a hand-built model in the
 * SAME shape the server produces (core/retail/receipt_model.py), so a test print
 * exercises the real renderer without needing a sale: an Arabic item and an
 * English item with the quantity token as its own field, a struck-through gross,
 * 3-decimal JOD amounts with the bidi isolates the server emits, and a rule.
 * Never carries a QR and never kicks.
 */
object SampleReceipt {
    private const val LRI = "⁦"
    private const val PDI = "⁩"

    fun model(rtl: Boolean): ReceiptModel {
        fun money(v: String) = if (rtl) "${LRI}د.أ${PDI}$v" else "JD$v"
        fun tr(en: String, ar: String) = if (rtl) ar else en
        return ReceiptModel(
            lang = if (rtl) "ar" else "en",
            rtl = rtl,
            lines = listOf(
                ReceiptLine(kind = "text", text = "Aura Retail", align = "center", bold = true, size = "large"),
                ReceiptLine(kind = "text", text = tr("Test print", "طباعة تجريبية"), align = "center"),
                ReceiptLine(kind = "rule"),
                ReceiptLine(kind = "row", start = "شاي نعناع كبير", end = money("5.000"), qty = "2×"),
                ReceiptLine(kind = "row", start = "Mint tea", end = money("2.500"), strike = money("3.000"), qty = "1×"),
                ReceiptLine(kind = "rule"),
                ReceiptLine(kind = "row", start = tr("Total", "الإجمالي"), end = money("7.500"), bold = true),
                ReceiptLine(kind = "rule"),
                ReceiptLine(kind = "text", text = tr("Thank you", "شكراً لكم"), align = "center"),
            ),
        )
    }
}
