"""core/retail/escpos_receipt.py -- pure byte-layer unit tests.

WHY THIS FILE EXISTS

`escpos_receipt.py` is the byte layer that turns a sale dict into the
ESC/POS commands a real receipt printer understands -- see that module's
own docstring for the 2026-09-08 measurement motivating it (the till's
HTML-print path can never open a cash drawer because it never emits these
bytes at all). This file is pure unit coverage: no Flask, no sqlite, no app
boot needed, matching Part A of retail_pricing_test.py's convention for a
core/retail module with no I/O.

Self-booting, matching the convention here (there is no shared conftest.py
for products/retail/tests/). Run on its own, from the repo root:

    <python> -m pytest products/retail/tests/retail_escpos_receipt_test.py -q
"""
import re
import sys
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]         # products/retail
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent                     # aura-fullsuits
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail import escpos_receipt as receipt  # noqa: E402
from core.retail import money_format  # noqa: E402
from core.retail import pricing  # noqa: E402


def _jod_sale(amount: float = 12.345) -> dict:
    """One line item, no discount/tax, everything equal to `amount` -- the
    simplest sale that still exercises every money-bearing field
    (line_total, subtotal, total, amount_paid) with the SAME figure, so a
    precision bug in any one of them is visible.
    """
    return {
        'lines': [
            {'name': 'Widget', 'product_id': 1, 'quantity': 1, 'line_total': amount},
        ],
        'subtotal': amount,
        'discount_amount': 0,
        'tax_amount': 0,
        'total': amount,
        'amount_paid': amount,
        'change': 0,
    }


# ═════════════════════════════════════════════════════════════════════════
# Money precision -- the exact bug class this codebase has already shipped
# ═════════════════════════════════════════════════════════════════════════

def test_jod_sale_renders_three_decimals_everywhere():
    out = receipt.render_receipt(_jod_sale(12.345), currency="JOD")
    text = out.decode("ascii")
    assert "12.345" in text
    assert "12.35" not in text


def test_jod_currency_symbol_present():
    # Sanity check that money is actually going through money_format (which
    # prints "JD" for JOD) and not some other hand-rolled formatter.
    out = receipt.render_receipt(_jod_sale(12.345), currency="JOD")
    assert b"JD 12.345" in out


# Manual mutation proof (see task report for the actual red/green command
# transcript -- pytest itself does not mutate shared money_format.py/
# pricing.py on disk, since those files are outside this task's edit scope
# and other work may be in flight against them). This test asserts the
# NORMAL (unmutated) behaviour, i.e. the "green" side of that proof.
def test_three_decimal_precision_is_the_jod_default_not_incidental():
    assert pricing.CURRENCY_MINOR_UNITS['JOD'] == 3
    assert money_format.format_money(12.345, 'JOD') == 'JD 12.345'


# ═════════════════════════════════════════════════════════════════════════
# _row -- the right-aligned amount column
# ═════════════════════════════════════════════════════════════════════════

def test_row_with_long_name_keeps_amount_intact_and_respects_width():
    width = 32
    amount = "JD 12.345"
    row = receipt._row("A" * 60, amount, width)
    assert len(row) == width
    assert row.endswith(amount)


def test_row_short_name_pads_to_width():
    row = receipt._row("Item", "1.00", 20)
    assert len(row) == 20
    assert row.startswith("Item")
    assert row.endswith("1.00")


def test_row_rejects_non_positive_width():
    with pytest.raises(ValueError):
        receipt._row("x", "1.00", 0)


# ═════════════════════════════════════════════════════════════════════════
# Paper widths
# ═════════════════════════════════════════════════════════════════════════

# ESC/GS control sequences this module emits, for stripping before measuring
# a line's PRINTABLE width -- a control byte occupies no space on paper, so
# counting it toward the width_chars budget would be measuring the wrong
# thing. Fixed-length only (this suite emits no QR/variable-length command
# in these particular receipts): ESC @ (2B), ESC a <n> (3B), ESC ! <n> (3B),
# ESC E <n> (3B), GS V <m> <n> (4B).
_CONTROL_RE = re.compile(rb"\x1b@|\x1ba.|\x1b!.|\x1bE.|\x1dV..", re.DOTALL)


@pytest.mark.parametrize("width", [32, 42])
def test_receipt_lines_never_exceed_paper_width(width):
    sale = {
        'lines': [
            {'name': 'A very long product name that will not fit on the line', 'product_id': 1,
             'quantity': 3, 'line_total': 99.999},
        ],
        'subtotal': 99.999, 'discount_amount': 5.5, 'tax_amount': 16.0,
        'total': 110.499, 'amount_paid': 120.0, 'change': 9.501,
    }
    out = receipt.render_receipt(
        sale, width_chars=width, currency="JOD",
        shop={'name': 'A Very Long Shop Name That Also Does Not Fit', 'address': '123 Some Street', 'phone': '+962-6-0000000'},
    )
    text = _CONTROL_RE.sub(b"", out).decode("ascii")
    for line in text.split("\n"):
        assert len(line) <= width, f"line exceeded {width} chars: {line!r}"


# ═════════════════════════════════════════════════════════════════════════
# drawer_kick byte arithmetic
# ═════════════════════════════════════════════════════════════════════════

def test_drawer_kick_default_bytes():
    # ESC 'p' m t1 t2; m=0 (pin 0); t1 = 50ms/2 = 25 = 0x19;
    # t2 = 200ms/2 = 100 = 0x64. Verified by hand against the ESC/POS
    # `ESC p m t1 t2` spec: t1/t2 are in units of 2 ms.
    assert receipt.drawer_kick() == b"\x1b\x70\x00\x19\x64"


def test_drawer_kick_clamps_to_a_single_byte():
    out = receipt.drawer_kick(pin=1, on_ms=10_000, off_ms=10_000)
    assert out == b"\x1b\x70\x01\xff\xff"


def test_drawer_kick_pin_is_boolean_ish():
    assert receipt.drawer_kick(pin=0)[2] == 0
    assert receipt.drawer_kick(pin=1)[2] == 1


# ═════════════════════════════════════════════════════════════════════════
# kick=False/True -- both directions of "must not open the drawer unless asked"
# ═════════════════════════════════════════════════════════════════════════

def test_receipt_without_kick_never_contains_drawer_command():
    out = receipt.render_receipt(_jod_sale(), kick=False)
    assert b"\x1b\x70" not in out  # ESC 'p' never appears


def test_receipt_with_kick_ends_with_drawer_command():
    out = receipt.render_receipt(_jod_sale(), kick=True)
    assert out.endswith(receipt.drawer_kick())
    assert out.count(b"\x1b\x70") == 1  # exactly once, appended once


# ═════════════════════════════════════════════════════════════════════════
# QR byte layout
# ═════════════════════════════════════════════════════════════════════════

def test_qr_store_command_length_prefix_is_data_length_plus_three():
    data = b"TEST"
    cmd = receipt._qr_store_command(data)
    # GS ( k pL pH cn fn m <data>; pL=len(data)+3=7, pH=0; cn=0x31 fn=0x50 m=0x30
    assert cmd == b"\x1d\x28\x6b\x07\x00\x31\x50\x30TEST"


def test_qr_store_command_length_prefix_is_little_endian():
    data = b"x" * 300  # forces pH to be nonzero: 300+3=303=0x012F -> pL=0x2F pH=0x01
    cmd = receipt._qr_store_command(data)
    # cmd layout: GS '(' 'k' pL pH cn fn m <data> -- the 3-byte "GS ( k"
    # prefix comes BEFORE the length bytes, not after.
    assert cmd[:3] == b"\x1d\x28\x6b"
    assert cmd[3] == 0x2F  # pL (low byte)
    assert cmd[4] == 0x01  # pH (high byte)
    assert cmd[5:8] == b"\x31\x50\x30"
    assert cmd[8:] == data


def test_qr_command_contains_model_select_size_ec_and_print():
    out = receipt._qr_command("HELLO")
    assert out.startswith(b"\x1d\x28\x6b\x04\x00\x31\x41\x32\x00")  # select model 2
    assert b"\x1d\x28\x6b\x03\x00\x31\x43\x06" in out               # module size 6
    assert b"\x1d\x28\x6b\x03\x00\x31\x45\x31" in out                # EC level 49 ('1')
    assert receipt._qr_store_command(b"HELLO") in out
    assert out.endswith(b"\x1d\x28\x6b\x03\x00\x31\x51\x30")        # print symbol


def test_render_receipt_includes_qr_when_supplied():
    out = receipt.render_receipt(_jod_sale(), einvoice_qr="ABC123")
    assert receipt._qr_store_command(b"ABC123") in out


def test_render_receipt_omits_qr_block_when_not_supplied():
    out = receipt.render_receipt(_jod_sale(), einvoice_qr=None)
    assert b"\x1d\x28\x6b" not in out  # no GS ( k command at all


# ═════════════════════════════════════════════════════════════════════════
# Non-ASCII text -- degrade to '?', pinned
# ═════════════════════════════════════════════════════════════════════════

def test_non_ascii_name_degrades_to_question_marks():
    sale = _jod_sale()
    sale['lines'][0]['name'] = "Café Crème"
    out = receipt.render_receipt(sale)
    text = out.decode("ascii")
    assert "Caf? Cr?me" in text
    assert "Café" not in text
    assert "é" not in out.decode("latin-1")  # never leaked as a raw non-ASCII byte either


def test_non_ascii_shop_name_degrades_to_question_marks():
    out = receipt.render_receipt(_jod_sale(), shop={'name': "السوق"})
    text = out.decode("ascii")
    assert "?????" in text


# ═════════════════════════════════════════════════════════════════════════
# Header, discount/tax/change conditionality
# ═════════════════════════════════════════════════════════════════════════

def test_no_shop_header_when_shop_omitted():
    out = receipt.render_receipt(_jod_sale())
    # ESC ! 0x10 (double-height) is only ever emitted for the shop-name line
    assert b"\x1b\x21\x10" not in out


def test_shop_header_present_when_name_given():
    out = receipt.render_receipt(_jod_sale(), shop={'name': 'Aura Retail', 'address': '123 St', 'phone': '06-000'})
    assert b"\x1b\x21\x10" in out
    text = out.decode("ascii")
    assert "Aura Retail" in text
    assert "123 St" in text
    assert "06-000" in text


def test_discount_tax_change_lines_omitted_when_zero():
    out = receipt.render_receipt(_jod_sale())  # discount=tax=change=0
    text = out.decode("ascii")
    assert "Discount" not in text
    assert "Tax" not in text
    assert "Change" not in text


def test_discount_tax_change_lines_present_when_nonzero():
    sale = _jod_sale()
    sale['discount_amount'] = 1.0
    sale['tax_amount'] = 2.0
    sale['change'] = 3.0
    out = receipt.render_receipt(sale)
    text = out.decode("ascii")
    assert "Discount" in text
    assert "Tax" in text
    assert "Change" in text


def test_render_receipt_starts_with_initialise_and_ends_with_cut():
    out = receipt.render_receipt(_jod_sale())
    assert out.startswith(b"\x1b\x40")
    assert b"\x1d\x56\x42" in out  # GS V 'B' (partial cut, function 66)


# ── The receipt must identify itself ─────────────────────────────────────────
# Added 2026-09-08 after the module's output was RENDERED AND READ AS PAPER
# rather than only asserted on: it printed the shop header, the items and the
# totals, and no receipt number and no date anywhere. The HTML path this byte
# layer replaces (subsystem-retail.js::_printReceipt) prints both above its
# first rule, so shipping without them would have been a regression against
# what customers already get -- and a sequential number plus a date are named
# requirements for a Jordanian invoice. No existing test could see it: they
# all asserted on what IS present, which is the failure shape ENGINEERING.md
# calls out -- a suite that only checks the lines you remembered to write.

def test_receipt_prints_its_number_and_timestamp():
    sale = _jod_sale()
    sale["sale_number"] = "S-000148"
    sale["created_at"] = "2026-09-08 02:41"
    text = receipt.render_receipt(sale).decode("ascii")
    assert "Receipt #S-000148" in text
    assert "2026-09-08 02:41" in text


def test_receipt_number_and_timestamp_precede_the_items():
    """Order matters: the identifying block belongs above the first rule, the
    way the HTML receipt already prints it, not buried under the totals."""
    sale = _jod_sale()
    sale["sale_number"] = "S-000148"
    sale["created_at"] = "2026-09-08 02:41"
    text = receipt.render_receipt(sale).decode("ascii")
    assert text.index("Receipt #S-000148") < text.index("Subtotal")
    assert text.index("2026-09-08 02:41") < text.index("Subtotal")


def test_a_missing_number_or_date_omits_its_line_rather_than_inventing_one():
    """A pure renderer must not read the clock: a fabricated timestamp would
    make this function untestable AND would put a time on paper that no record
    anywhere agrees with."""
    sale = _jod_sale()
    sale.pop("sale_number", None)
    sale.pop("created_at", None)
    text = receipt.render_receipt(sale).decode("ascii")
    assert "Receipt #" not in text
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text)
