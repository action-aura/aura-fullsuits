"""Aura Retail -- the receipt's Code 128 line, model half (UI-2 item 2).

The raster receipt is drawn from LAYOUT DATA the server builds
(core/retail/receipt_model.py). This pins what the model says about the barcode;
the drawing half (and the encoder, with golden strings and an independent decode
of the painted bars) is retail_ui2_returns_test.js.

  * a legacy long number (SALE-000016-<company8>-<device8>) is encoded as the
    SHORT number the customer sees -- the 30-character form is 365 modules wide,
    which is 1 dot per module on a 58 mm head. The Returns lookup accepts the
    short form and refuses (listing device tags) when two tills minted the same
    one, so a shorter value never refunds the wrong sale.
  * a per-till series number (MAI1-000001) is encoded as is.
  * a number that cannot be encoded (non-ASCII) or is missing prints NO barcode
    line -- never one that decodes to something other than the text beside it.
  * the line is a `barcode` kind with a `value`, placed after the footer, in
    both receipt languages; nothing else in the model moved.

Pure: no app boot, no database. Run:
    pytest products/retail/tests/retail_receipt_barcode_test.py -q
"""
import sys
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
for _p in (str(PRODUCT_DIR.parent.parent), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail import receipt_model  # noqa: E402


def _model(sale_number, lang='en', footer=None):
    return receipt_model.build_receipt_model(
        sale={'sale_number': sale_number, 'subtotal': 7, 'total': 7, 'amount_paid': 7},
        items=[{'name': 'x', 'quantity': 2, 'unit_price': 3.5, 'applied_promotion': None}],
        branding={'business_name': 'S', 'branding_receipt_footer': footer or ''},
        currency='JOD', requested_lang=lang)


def _barcodes(model):
    return [ln for ln in model['lines'] if ln['kind'] == 'barcode']


@pytest.mark.parametrize('stored,expected', [
    ('SALE-000016-a348be85-f57464f8', 'SALE-000016'),      # legacy long form -> what the customer sees
    ('MAI1-000001', 'MAI1-000001'),                         # per-till series, as is
    ('SALE-000016', 'SALE-000016'),                         # already short
    ('  S-7  ', 'S-7'),                                     # trimmed
])
def test_barcode_value_is_the_number_the_customer_sees(stored, expected):
    assert receipt_model.barcode_value_for(stored) == expected
    lines = _barcodes(_model(stored))
    assert [ln['value'] for ln in lines] == [expected]


@pytest.mark.parametrize('stored', [None, '', '   ', 'فاتورة-١٢٣', 'SALE-1é'])
def test_no_barcode_when_the_number_is_missing_or_not_encodable(stored):
    assert receipt_model.barcode_value_for(stored) is None
    assert _barcodes(_model(stored)) == []


def test_the_barcode_comes_after_the_footer_and_in_both_languages():
    for lang in ('en', 'ar'):
        m = _model('MAI1-000001', lang=lang, footer='See you soon')
        kinds = [ln['kind'] for ln in m['lines']]
        i = kinds.index('barcode')
        # the footer text is before it; nothing after it but (optionally) e-invoice lines
        assert any(ln.get('text') == 'See you soon' for ln in m['lines'][:i])
        assert all(k in ('rule', 'qr', 'text') for k in kinds[i + 1:])
        assert m['lines'][i] == {'kind': 'barcode', 'value': 'MAI1-000001'}


def test_the_rest_of_the_model_is_unchanged_by_the_barcode():
    with_bc = _model('MAI1-000001')
    without = _model(None)
    stripped = [ln for ln in with_bc['lines'] if ln['kind'] != 'barcode']
    # the only difference from a sale with no number is the barcode line and the
    # "Receipt #..." header text (which carries the number itself)
    assert len(stripped) == len(without['lines'])
    assert [ln['kind'] for ln in stripped] == [ln['kind'] for ln in without['lines']]
    assert with_bc['width_dots'] == without['width_dots'] and with_bc['rtl'] == without['rtl']
