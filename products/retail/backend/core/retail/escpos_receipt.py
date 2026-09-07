"""Aura Retail -- ESC/POS receipt byte layer (pure, no I/O).

WHY THIS MODULE EXISTS

Measured 2026-09-08: the till prints receipts by handing an HTML page to the
OS print spooler (`subsystem-retail.js::_printReceipt`, a hidden iframe +
`window.print()`). That prints paper, but a cash drawer is not a printer --
it opens only when the printer receives the ESC/POS drawer-kick byte
sequence (`drawer_kick` below), which a spooled HTML page never contains
(the OS print pipeline rasterises/rewrites the page; it does not forward
arbitrary bytes a driver would pass straight through). So today a
shopkeeper opens the drawer by hand on every single sale -- the number one
thing blocking a first paying shop.

This module builds those bytes. It is deliberately a pure function layer:
no Flask, no sqlite3, no filesystem or socket access, so it can be
unit-tested without booting the app and reused unchanged from any caller
(a route, a desktop launcher, a background job) once one exists. Getting
those bytes to an actual printer is `escpos_transport.py`'s job, not this
module's. Wiring either of them to a UI button is a deliberate follow-up
(see ROADMAP.md) -- this task is the byte layer and the transport only.

MONEY

Every amount is rendered through `core.retail.money_format.format_money`,
never a hand-rolled f-string. That module already carries the scar tissue
for why: a JOD sale (three decimal places, 1000 fils) printed as `12.35`
instead of `12.345` once already, in a different notification channel, and
cost real trust in the shop's own numbers (see `money_format.py`'s
docstring). A receipt is the customer-facing, printed-on-paper version of
that same number; it must not reopen that bug on a third channel.

TEXT ENCODING -- LATIN/ASCII ONLY, AND ARABIC IS NOT SOLVED HERE

ESC/POS printers select a text code page per model/firmware, and there is
no single code page that reliably renders Arabic shaping (initial/medial/
final glyph forms) across printer brands the way there is for Latin
Windows-125x pages. Getting Arabic right needs the printer's GRAPHICS/RASTER
path (rendering the line as a bitmap and sending it as an image command),
which is a materially different code path from the text commands this
module emits and is explicitly OUT OF SCOPE here -- see ROADMAP.md for the
follow-up.

Because the text path is ASCII-only, any character outside 7-bit ASCII is
replaced with `?` (Python's `str.encode('ascii', errors='replace')`), never
transliterated. Two reasons: (1) transliteration is a guess -- there is no
universally correct ASCII stand-in for an arbitrary Unicode character, and
a wrong guess reads as more "correct" than a `?` while actually being just
as lossy, and (2) this module follows the same never-raise, degrade-to-a-
safe-default posture as `pricing.currency_quantum`/`money_format.format_money`
elsewhere in this codebase: a customer or product name containing an
accented or Arabic character must still produce a printed receipt for the
money that matters, not a crashed print job. Pinned by
`test_non_ascii_name_degrades_to_question_marks` in the paired test file.

QR CODE

`einvoice_qr`, when supplied, is rendered as a real ESC/POS QR symbol using
the `GS ( k` "model 2" command family (Epson's documented command set, also
implemented by the large majority of ESC/POS-compatible thermal printers):
select model, set module size, set error-correction level, store the
symbol data, then print the stored symbol. The data-store command's length
prefix (`pL pH`, little-endian) counts the 3 command bytes (`cn fn m`) that
follow it IN ADDITION to the payload -- `len(data) + 3`, not `len(data)` --
which is the single most common ESC/POS QR bug (miscounting it either
truncates the payload or feeds the printer's parser garbage). See
`_qr_store_command`'s docstring and the QR byte-layout test.

CUT STYLE

`render_receipt` ends with `GS V` function 66 (`0x42`, ASCII 'B') --
"feed paper and PARTIAL cut" -- rather than function 65/full cut. A partial
cut leaves a small uncut tab holding the ticket to the paper roll, so it
does not drop into the printer's bin before a distracted cashier reaches
for it; a full cut severs it completely on every sale, which is more
paper-waste-tolerant hardware behaviour than a small counter till needs.
This is the printer-manufacturer-recommended default for POS use.
"""
from __future__ import annotations

from core.retail import money_format

#: ESC/POS control-code prefixes. Named exactly as the ESC/POS command
#: reference spells them, so a comment like "GS ( k" or "ESC p" in this
#: module reads the same as the byte sequence it produces.
ESC = b"\x1b"
GS = b"\x1d"


def drawer_kick(pin: int = 0, on_ms: int = 50, off_ms: int = 200) -> bytes:
    """`ESC p m t1 t2` -- pulse a cash-drawer kick-out pin.

    `pin` selects which of the printer's two drawer-kick connector pins to
    pulse (0 or 1 -- most single-drawer setups wire the drawer to pin 0,
    the printer's default). `on_ms`/`off_ms` are the pulse's ON and OFF
    durations in milliseconds; the wire format encodes them as `t1`/`t2` in
    units of 2 ms, each clamped to a single byte (0-255, i.e. 0-510 ms).

    Defaults (50 ms on, 200 ms off) are conservative, standard ESC/POS
    datasheet values, NOT a value measured against a real drawer in this
    environment -- there is no drawer attached to this dev machine (see
    ROADMAP.md: "proof on real hardware ... which no test here can
    supply"). They are exposed as parameters rather than hardcoded for
    exactly that reason: drawer-kick pulse timing is NOT standardised
    across drawer/printer brands, and a pulse that is too SHORT can click
    the solenoid audibly without actually releasing the latch -- a shop
    that finds the default doesn't reliably pop their specific drawer needs
    to be able to lengthen `on_ms` without a code change, not be stuck with
    a hardcoded byte string.
    """
    m = 0 if int(pin) <= 0 else 1
    t1 = max(0, min(255, round(on_ms / 2)))
    t2 = max(0, min(255, round(off_ms / 2)))
    return ESC + b"p" + bytes([m, t1, t2])


def _ascii(text: str) -> bytes:
    """Encode `text` for the printer's Latin/ASCII code page.

    `errors='replace'` turns any non-ASCII character into a literal `?`
    (one per character) instead of raising -- see the module docstring's
    "TEXT ENCODING" section for why that degrade, not a transliteration or
    an exception, is the deliberate choice here.
    """
    return str(text).encode("ascii", errors="replace")


def _fit(text: str, width: int) -> str:
    """Truncate `text` to at most `width` characters.

    A truncated string ends in `...` (three ASCII periods -- NOT the
    Unicode ellipsis character `...`/U+2026, which would itself need the
    same non-ASCII handling as everything else this module prints) unless
    `width` is too small to fit the marker, in which case it hard-clips
    with no marker at all. Every printed line in `render_receipt` -- header,
    item, total, footer -- is routed through this (directly or via `_row`)
    so no line can ever exceed the physical paper width.
    """
    text = str(text)
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _row(left: str, right: str, width: int) -> str:
    """Compose one `width`-character-wide receipt line: `left` hugging the
    left margin, `right` hugging the right margin, and the whole line never
    longer than `width`.

    Chosen behaviour for an oversized `left`: TRUNCATE (via `_fit`), not
    wrap onto a second line. Wrapping would turn one sold line item into
    two-or-more printed rows, which would break the "one line item -> one
    printed row" invariant every caller of this helper relies on. The
    amount column (`right`) is never touched by this truncation -- money
    must stay whole -- except in the pathological case where the amount
    string alone is `width` characters or longer, in which case the label
    is dropped entirely rather than mis-aligning a row that cannot fit.
    """
    if width < 1:
        raise ValueError(f"width must be >= 1, got {width}")
    left = str(left)
    right = str(right)
    if len(right) >= width:
        return right[-width:]
    max_left = width - len(right)
    return _fit(left, max_left).ljust(max_left) + right


def _line(text: str, width: int) -> bytes:
    """One printed line: fit to `width`, encode, terminate with `\\n`."""
    return _ascii(_fit(text, width)) + b"\n"


def _qr_store_command(data: bytes) -> bytes:
    """`GS ( k` model-2 "store QR code symbol data" command.

    The length prefix (`pL pH`, little-endian) covers `cn fn m` (3 bytes)
    PLUS the payload -- `len(data) + 3` -- not `len(data)` alone, because
    those 3 bytes are part of this command's body as far as the printer's
    parser is concerned: it reads exactly `pL + pH*256` bytes following
    `pH` as "the rest of this command". Undercounting by 3 truncates the
    last 3 bytes of the QR payload; overcounting reads 3 bytes past the end
    of `data` as if they were still payload. This is the single most common
    hand-rolled-ESC/POS-QR bug.
    """
    length = len(data) + 3
    if length > 0xFFFF:
        raise ValueError(f"QR payload too long for a 2-byte length prefix: {len(data)} bytes")
    p_l = length & 0xFF
    p_h = (length >> 8) & 0xFF
    return GS + b"(k" + bytes([p_l, p_h]) + b"\x31\x50\x30" + data


def _qr_command(payload: str, *, module_size: int = 6, ec_level: int = 49) -> bytes:
    """Full `GS ( k` model-2 QR sequence: select model 2, set module size,
    set error-correction level, store the data, print the stored symbol.

    `module_size=6` and `ec_level=49` ('1' = level M, ~15% recovery) are
    Epson's own documented mid-range defaults: small enough that a
    reasonably-sized e-invoicing QR payload still fits on 58 mm paper,
    dense enough error correction to survive the odd thermal-paper scuff
    without needing the largest (and slowest to print) modules.

    `payload` goes through the same ASCII-only encoding as every other
    string this module prints (see module docstring) -- an e-invoicing QR
    payload is itself a base64/TLV string, which is already ASCII, so this
    never actually degrades real data; it is here so a malformed caller
    input degrades instead of raising, matching this module's posture
    everywhere else.
    """
    data = _ascii(payload)
    select_model = GS + b"(k\x04\x00\x31\x41\x32\x00"
    set_size = GS + b"(k\x03\x00\x31\x43" + bytes([module_size & 0xFF])
    set_ec = GS + b"(k\x03\x00\x31\x45" + bytes([ec_level & 0xFF])
    store = _qr_store_command(data)
    print_symbol = GS + b"(k\x03\x00\x31\x51\x30"
    return select_model + set_size + set_ec + store + print_symbol


def render_receipt(
    sale: dict,
    *,
    width_chars: int = 42,
    currency: str = "JOD",
    shop: dict | None = None,
    einvoice_qr: str | None = None,
    kick: bool = False,
) -> bytes:
    """Render `sale` into a complete ESC/POS receipt byte stream.

    `sale` follows the same keys `subsystem-retail.js::_printReceipt`
    already reads off its `saleData` for the HTML receipt it replaces --
    `lines` (each with `name`/`product_id`, `quantity`, `line_total`),
    `subtotal`, `discount_amount`, `tax_amount`, `total`, `amount_paid`,
    `change` -- deliberately, so a future caller can hand this function the
    exact same object the HTML path already builds.

    `shop` is an optional `{'name', 'address', 'phone'}` dict for the
    receipt header; when omitted (or `name` is absent) the header block is
    skipped entirely rather than printing a hardcoded brand name -- this is
    a generic byte layer, not a place to bake in "Aura Retail" as a
    fallback shop identity.

    `width_chars` is 42 for 80 mm paper, 32 for 58 mm (matches the
    `paperWidth` setting `_printerCfg()` already stores client-side).

    `kick`, default `False`: the drawer-kick bytes (`drawer_kick()`,
    appended last) are ONLY included when the caller explicitly asks for
    them. A receipt render must never silently open the drawer -- printing
    a duplicate copy, a receipt for looking-up purposes, or an emailed/
    filed PDF-from-bytes rendering must not pop the till each time.
    """
    if width_chars < 1:
        raise ValueError(f"width_chars must be >= 1, got {width_chars}")
    shop = shop or {}
    sale = sale or {}

    out = bytearray()
    out += ESC + b"@"  # initialise: reset the printer to its power-on state

    shop_name = shop.get("name")
    if shop_name:
        out += ESC + b"a\x01"  # justify: center
        out += ESC + b"!\x10"  # double-height, normal width
        out += _line(shop_name, width_chars)
        out += ESC + b"!\x00"  # back to normal size
        for key in ("address", "phone"):
            value = shop.get(key)
            if value:
                out += _line(str(value), width_chars)
        out += ESC + b"a\x00"  # justify: left, for the body

    # Receipt number and timestamp, centred -- the same two lines, in the same
    # order, that subsystem-retail.js::_printReceipt already prints above its
    # first rule ("Receipt #<sale_number>" then created_at). Rendering the
    # paper WITHOUT them was this module's one real defect when its output was
    # first read as paper rather than asserted on: a receipt with no number and
    # no date is not a receipt a shop can hand over, and Jordan's own invoice
    # requirements name a sequential number and a date explicitly (see the
    # e-invoicing notes in docs/). Caught by rendering a realistic JOD sale and
    # looking at it, not by any assertion in this file's first test pass.
    #
    # No `or datetime.now()` fallback, deliberately, even though the HTML path
    # has one: a pure byte-rendering function that reads the clock cannot be
    # tested for a fixed expected output, and every real caller has
    # `created_at` on the object POST /sales already returned. A missing value
    # omits its line rather than inventing one.
    sale_number = sale.get("sale_number")
    created_at = sale.get("created_at")
    if sale_number or created_at:
        out += ESC + b"a\x01"  # justify: center
        if sale_number:
            out += _line(f"Receipt #{sale_number}", width_chars)
        if created_at:
            out += _line(str(created_at), width_chars)
        out += ESC + b"a\x00"  # justify: left, for the body

    out += _line("-" * width_chars, width_chars)

    for item in (sale.get("lines") or []):
        name = item.get("name") or f"#{item.get('product_id', '?')}"
        qty = item.get("quantity", 1)
        amount = money_format.format_money(item.get("line_total", 0), currency)
        out += _ascii(_row(f"{name} x{qty}", amount, width_chars)) + b"\n"

    out += _line("-" * width_chars, width_chars)

    out += _ascii(_row("Subtotal", money_format.format_money(sale.get("subtotal", 0), currency), width_chars)) + b"\n"

    discount = sale.get("discount_amount") or 0
    if discount > 0:
        out += _ascii(_row("Discount", "-" + money_format.format_money(discount, currency), width_chars)) + b"\n"

    tax = sale.get("tax_amount") or 0
    if tax > 0:
        out += _ascii(_row("Tax", money_format.format_money(tax, currency), width_chars)) + b"\n"

    out += ESC + b"E\x01"  # emphasis (bold) on, for the total line only
    out += _ascii(_row("Total", money_format.format_money(sale.get("total", 0), currency), width_chars)) + b"\n"
    out += ESC + b"E\x00"  # emphasis off

    out += _ascii(_row("Paid", money_format.format_money(sale.get("amount_paid", 0), currency), width_chars)) + b"\n"

    change = sale.get("change") or 0
    if change > 0:
        out += _ascii(_row("Change", money_format.format_money(change, currency), width_chars)) + b"\n"

    if einvoice_qr:
        out += _line("-" * width_chars, width_chars)
        out += ESC + b"a\x01"  # center the QR block
        out += _qr_command(einvoice_qr)
        out += b"\n"
        out += ESC + b"a\x00"

    out += ESC + b"a\x01"
    out += _line("Thank you", width_chars)
    out += ESC + b"a\x00"

    # Feed to the cut position and PARTIAL cut (function 66/'B') -- see
    # module docstring's "CUT STYLE" section for why partial, not full.
    out += GS + b"V" + bytes([0x42, 0x03])

    if kick:
        out += drawer_kick()

    return bytes(out)
