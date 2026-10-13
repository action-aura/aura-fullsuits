"""Aura Retail -- receipt LAYOUT MODEL (pure, no I/O).

WHY THIS MODULE EXISTS (PRINT-1)

The silent receipt is drawn by the browser on a <canvas>
(`frontend/receipt-raster.js`) because Chromium already shapes and joins
Arabic correctly; ESC/POS text mode cannot (see `escpos_receipt`'s "TEXT
ENCODING" section). The canvas needs to be told WHAT to draw, and that must be
the same content the HTML receipt (`subsystem-retail.js::_printReceipt`)
shows -- a second, drifting idea of "what is on the receipt" is how a paper
slip ends up disagreeing with the screen. So the layout is built here, on the
server, from the same stored sale data, and the browser only paints it.

This function mirrors `_printReceipt` row for row:

  branding block -> "Receipt #<n>" + date -> cashier/customer (only when the
  shop switched those on) -> rule -> lines (name x qty, amount; struck gross
  when a promotion discounted the line; extras and promotion as priceless
  sub-rows) -> rule -> Subtotal / Promotions / Discount / Tax / Total ->
  one row per tender when the sale was split, else Paid -> Change due -> rule
  -> Thank you -> footer -> e-invoice status.

Differences from the HTML slip, on purpose:
  * the e-invoice QR is NOT drawn into the bitmap. It is returned as
    `qr_payload` and the printer draws it natively (`GS ( k`), which is sharper
    and scans better than a thresholded canvas QR. It therefore prints after
    the footer instead of between footer and status text.
  * the shop logo is not drawn (not built in this wave).

LOGICAL SIDES. `start` / `end` are the reading-order sides of a row, not
physical ones: for an Arabic receipt (`rtl: true`) `start` is the RIGHT edge.
The HTML slip behaves the same way (flex `space-between` inside an RTL page),
so the two stay mirror images of each other.

LANGUAGE. The receipt's language is a SHOP setting (`branding_receipt_language`
'auto' | 'en' | 'ar'); 'auto' means "whatever the till is showing", which only
the browser knows, so the caller passes it as `lang`. The Arabic labels below
are copied from `frontend/locales/ar.json` (the catalogue the HTML receipt
translates through) and pinned to it by a test, so a label edited there cannot
silently diverge from the paper. NATIVE-READER REVIEW STILL OWED: they were
inherited, not re-reviewed here.

MONEY goes through `money_format.format_money` (3 decimals for JOD), never an
f-string -- see that module's docstring for the 12.35-instead-of-12.345 bug.
"""
from __future__ import annotations

import re

from core.retail import money_format, pricing

MODEL_VERSION = 1

#: Receipt labels -> Arabic. Source of truth is frontend/locales/ar.json; the
#: parity test (retail_receipt_model_test.py) fails if they drift.
AR_LABELS = {
    "Receipt": "إيصال",
    "Subtotal": "المجموع الفرعي",
    "Promotions": "العروض الترويجية",
    "Discount": "الخصم",
    "Tax": "الضريبة",
    "Total": "الإجمالي",
    "Paid": "المدفوع",
    "Change due": "الباقي",
    "Thank you": "شكراً لكم",
    "Cashier": "أمين صندوق",
    "Customer": "العميل",
    "Cash": "نقدًا",
    "Card": "بطاقة",
    "Mobile / CliQ": "محفظة إلكترونية / كليك",
    "Transfer": "حوالة مصرفية",
    "Credit": "آجل",
    "Voucher": "قسيمة",
    "On account": "آجل",
    "Item": "صنف",
    "JD": "د.أ",
}

#: Same method -> label table as `RetailSystem.POS_PAYMENT_METHODS`.
_METHOD_LABELS = {
    "cash": "Cash", "card": "Card", "mobile": "Mobile / CliQ",
    "transfer": "Transfer", "credit": "Credit", "voucher": "Voucher",
}

#: Unicode bidi isolates: keep a number / amount a self-contained LTR island
#: inside an RTL line instead of letting neighbours reorder its digits.
LRI, PDI = "\u2066", "\u2069"
#: First-strong isolate: lets a product name pick its own direction.
FSI = "\u2068"
#: Left-to-right mark: a strong-LTR character with no glyph. Measured on the
#: real artefact (Chromium canvas, 2026-10-12): an isolate alone did NOT keep
#: "x2" in order beside Arabic text -- it printed "2x". Flanking the quantity
#: with LRMs makes the bidi algorithm resolve the neutral multiplication sign
#: and the digit as left-to-right, so the isolate is kept and this is added.
LRM = "\u200e"


#: The long legacy sale-number shape (`SALE-000016-<company8>-<device8>`).
#: Mirrors `RetailSystem._docNo` in the frontend: only the first two parts are
#: what a person reads off the receipt.
_LEGACY_DOC_NUMBER = re.compile(r"^([A-Za-z]+-\d+)-[0-9a-f]{8}-[0-9a-f]{8}$")


def barcode_value_for(sale_number) -> str | None:
    """The text the receipt's Code 128 carries, or None for "print no barcode".

    It is the number the customer SEES (`SALE-000016`, or a per-till series
    `MAI1-000001`), not the 30-character legacy string: that one is 365 modules
    wide, which is 1 dot per module on a 58 mm head -- a barcode most scanners
    will not read. The Returns lookup already accepts the short form and, when two
    tills minted the same short number, REFUSES with the device tags listed
    instead of guessing (see `_findSaleForReturn`), so the shorter value costs a
    clear message in the one ambiguous case, never a wrong refund.

    Only printable ASCII is encodable (Code 128 subset B, see code128.js): a
    number with anything else prints no barcode rather than one that decodes to
    something other than the text beside it.
    """
    text = "" if sale_number is None else str(sale_number).strip()
    if not text:
        return None
    m = _LEGACY_DOC_NUMBER.match(text)
    value = m.group(1) if m else text
    if not all(32 <= ord(ch) <= 126 for ch in value):
        return None
    return value


def _tr(text: str, lang: str) -> str:
    return AR_LABELS.get(text, text) if lang == "ar" else text


def _fmt_money(value, currency, lang) -> str:
    """`format_money`, with the currency MARK translated the way the till's
    `_currencyPrefix()` does (JD -> د.أ on an Arabic receipt)."""
    text = money_format.format_money(value, currency)
    sym = pricing.currency_symbol(currency)
    if lang == "ar" and sym in AR_LABELS and text.startswith(sym):
        # The mark is isolated: an Arabic letter run followed by Western digits
        # makes the bidi algorithm turn the digits into "Arabic numbers" and
        # print the amount as "7.000 JD-mark" (mark on the wrong side).
        # Measured on the real canvas 2026-10-12; an isolate keeps the mark
        # first, as the till shows it.
        text = LRI + AR_LABELS[sym] + PDI + text[len(sym):]
    return text


def _negative(text: str) -> str:
    return "-" + text


def _qty_text(qty) -> str:
    try:
        q = float(qty)
    except (TypeError, ValueError):
        return str(qty)
    return str(int(q)) if q == int(q) else ("%.3f" % q).rstrip("0").rstrip(".")


def _half_unit(currency) -> float:
    digits = pricing.CURRENCY_MINOR_UNITS.get(
        (currency or "").strip().upper(), pricing.CURRENCY_DECIMALS)
    return 0.5 / (10 ** digits)


def _text(text, *, align="center", bold=False, size="normal") -> dict:
    return {"kind": "text", "text": str(text), "align": align, "bold": bold, "size": size}


def _row(start, end, *, bold=False, strike=None, qty=None) -> dict:
    row = {"kind": "row", "start": str(start), "end": str(end), "bold": bold}
    if qty:
        row["qty"] = str(qty)
    if strike:
        row["strike"] = str(strike)
    return row


def _resolve_lang(branding: dict, requested: str) -> str:
    setting = (branding or {}).get("branding_receipt_language")
    if setting in ("en", "ar"):
        return setting
    return "ar" if requested == "ar" else "en"


def build_receipt_model(
    *,
    sale: dict,
    items: list,
    branding: dict,
    currency: str,
    requested_lang: str = "en",
    width_dots: int = 576,
    tenders: list | None = None,
    employee_name: str | None = None,
    customer_name: str | None = None,
    einvoice: dict | None = None,
) -> dict:
    """The receipt as data.

    `sale`: sale_number, created_at, subtotal, discount_amount, tax_amount,
    total, amount_paid, change_amount, customer_id.
    `items`: name, quantity, unit_price, list_price (None on pre-v54 sales), modifiers
    [{name}], applied_promotion (None, or {name, discount_amount}).
    `branding`: the `branding_*` settings plus `business_name` (signup
    fallback). `tenders`: [{method, amount, tendered}] for a split sale.
    `einvoice`: None, or {'cleared': bool, 'qr_payload': str|None}.
    """
    branding = branding or {}
    lang = _resolve_lang(branding, requested_lang)
    rtl = lang == "ar"
    half = _half_unit(currency)

    def money(v):
        return _fmt_money(v, currency, lang)

    def tr(label):
        return _tr(label, lang)

    lines: list[dict] = []

    # Branding block -- same fallback chain as `_brandingReceiptBlock`.
    name = branding.get("branding_business_name") or branding.get("business_name") or "Aura Retail"
    lines.append(_text(name, bold=True, size="large"))
    if branding.get("branding_address"):
        lines.append(_text(branding["branding_address"], size="small"))
    if branding.get("branding_phone"):
        # Isolated: on an RTL line the digit groups of a phone number would
        # otherwise come out in reverse order ("9485 006 78 962+").
        lines.append(_text(f"{LRI}{branding['branding_phone']}{PDI}", size="small"))
    if branding.get("branding_tax_number"):
        lines.append(_text(f"Tax #: {LRI}{branding['branding_tax_number']}{PDI}", size="small"))
    if branding.get("branding_receipt_header"):
        lines.append(_text(branding["branding_receipt_header"], size="small"))

    # "#" goes INSIDE the isolate, like the HTML slip's <bdi dir="ltr">#n</bdi>;
    # outside it, an RTL line prints the hash on the wrong side of the number.
    lines.append(_text(f"{tr('Receipt')} {LRI}#{sale.get('sale_number', '')}{PDI}"))
    if sale.get("created_at"):
        lines.append(_text(f"{LRI}{sale['created_at']}{PDI}"))

    if branding.get("branding_receipt_show_cashier") == "true" and employee_name:
        lines.append(_text(f"{tr('Cashier')}: {employee_name}", size="small"))
    if (branding.get("branding_receipt_show_customer") == "true"
            and sale.get("customer_id") and customer_name):
        lines.append(_text(f"{tr('Customer')}: {customer_name}", size="small"))

    lines.append({"kind": "rule"})

    promotions = 0.0
    for item in items:
        qty = float(item.get("quantity") or 0)
        gross = round(float(item.get("unit_price") or 0) * qty, 6)
        promo = item.get("applied_promotion")
        discount = float(promo.get("discount_amount") or 0) if isinstance(promo, dict) else 0.0
        if isinstance(promo, dict):
            promotions += discount
        label = item.get("name") or tr("Item")
        # The quantity is its OWN field (`qty`), not text inside the label.
        # Isolates were tried first and the real canvas still put the "2\u00d7" token on
        # the other side, and flipped its glyphs, when the name was Arabic
        # (measured 2026-10-12, receipt-576-ar.png). The renderer draws `qty` as its own
        # LTR run at the line start and the name beside it, so every item line has
        # the quantity in the same place whatever the name script.
        qty_text = f"{_qty_text(qty)}\u00d7"
        # UI-3a: a line sold at a price other than the product's LIST price (schema v54
        # `list_price`, a snapshot taken at sale time) shows the list price struck through,
        # exactly as a promotion does. NULL (a sale from before v54) shows nothing -- unknown
        # is not "no override". A promotion, when present, keeps the strike for itself.
        list_price = item.get("list_price")
        list_gross = (round(float(list_price) * qty, 6) if list_price is not None else None)
        if discount > half:
            lines.append(_row(label, money(gross - discount), strike=money(gross), qty=qty_text))
        elif list_gross is not None and abs(list_gross - gross) > half:
            lines.append(_row(label, money(gross), strike=money(list_gross), qty=qty_text))
        else:
            lines.append(_row(label, money(gross), qty=qty_text))
        extras = [m.get("name") for m in (item.get("modifiers") or []) if m and m.get("name")]
        if extras:
            lines.append(_text(" · ".join(extras), align="start", size="small"))
        if isinstance(promo, dict) and promo.get("name"):
            lines.append(_text(f"% {promo['name']}", align="start", size="small"))

    lines.append({"kind": "rule"})

    lines.append(_row(tr("Subtotal"), money(sale.get("subtotal"))))
    # Promotions come from each line's own snapshot; whatever the stored
    # sale-level discount holds beyond them is the cashier's manual discount.
    # (The HTML slip splits by summing per-line `discount_amount`, a field a
    # REPRINT's lines do not carry -- so a reprinted HTML slip silently drops
    # both rows. The sale row is the authority, so this path does not.)
    manual_discount = max(0.0, float(sale.get("discount_amount") or 0) - promotions)
    if promotions > half:
        lines.append(_row(tr("Promotions"), _negative(money(promotions))))
    if manual_discount > half:
        # UI-3a: "Discount (Damaged)" -- the reason the cashier gave (schema v54
        # `sales.discount_reason`) travels to the paper. Absent on older sales.
        reason = str(sale.get("discount_reason") or "").strip()
        label = f"{tr('Discount')} ({reason})" if reason else tr("Discount")
        lines.append(_row(label, _negative(money(manual_discount))))
    tax = float(sale.get("tax_amount") or 0)
    if tax > 0:
        lines.append(_row(tr("Tax"), money(tax)))
    lines.append(_row(tr("Total"), money(sale.get("total")), bold=True))

    if tenders and len(tenders) >= 2:
        for t in tenders:
            label = tr("On account") if t.get("method") == "credit" else tr(
                _METHOD_LABELS.get(t.get("method"), str(t.get("method"))))
            shown = t.get("tendered") if t.get("method") == "cash" and t.get("tendered") is not None else t.get("amount")
            lines.append(_row(label, money(shown)))
    else:
        lines.append(_row(tr("Paid"), money(sale.get("amount_paid"))))
    change = float(sale.get("change_amount") or 0)
    if change > 0:
        lines.append(_row(tr("Change due"), money(change)))

    lines.append({"kind": "rule"})
    lines.append(_text(tr("Thank you")))
    if branding.get("branding_receipt_footer"):
        lines.append(_text(branding["branding_receipt_footer"], size="small"))

    # UI-2 item 2: a Code 128 of the sale number, so a scanner can load THIS sale
    # into the Returns dialog. The renderer draws it; the model only says WHAT.
    barcode_value = barcode_value_for(sale.get("sale_number"))
    if barcode_value:
        lines.append({"kind": "barcode", "value": barcode_value})

    qr_payload = None
    if einvoice is not None:
        if einvoice.get("cleared") and einvoice.get("qr_payload"):
            qr_payload = einvoice["qr_payload"]
            lines.append({"kind": "rule"})
            lines.append({"kind": "qr", "payload": qr_payload})
            lines.append(_text("Jordan e-invoice cleared", size="small"))
        else:
            lines.append({"kind": "rule"})
            lines.append(_text("Jordan e-invoice: pending government clearance", size="small"))

    return {
        "version": MODEL_VERSION,
        "lang": lang,
        "rtl": rtl,
        "width_dots": width_dots,
        "lines": lines,
        "qr_payload": qr_payload,
    }
