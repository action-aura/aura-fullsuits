"""UI-3a: what the printed / ESC-POS receipt says about a discount and an overridden price.

Pure (core/retail/receipt_model.py, no Flask). The model is the ONE description of the paper receipt
that the ESC/POS printer, the raster printer and the on-screen preview all draw, so what it says here
is what the customer is handed.

  * "Discount (Damaged)" -- the reason the cashier gave travels to the paper; with no reason the row
    is the plain "Discount" it always was (an old sale has none -- NULL is unknown, not "none").
  * a line sold below (or above) its LIST price shows the list price struck through; a line sold AT
    list shows nothing; a sale from before v54 (list_price None) shows nothing.
  * the Arabic receipt carries the Arabic label and the reason verbatim.
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / 'backend'
SUITE_ROOT = BACKEND_DIR.parents[2]
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail import receipt_model  # noqa: E402


def _model(*, reason=None, list_price=3.5, unit_price=3.0, lang='en', discount=0.56):
    sale = {'sale_number': 'SALE-000001', 'subtotal': 9.181, 'discount_amount': discount, 'tax_amount': 1.379,
            'total': 10.0, 'amount_paid': 10.0, 'change_amount': 0, 'created_at': '2026-10-13 10:00:00'}
    if reason is not None:
        sale['discount_reason'] = reason
    items = [{'name': 'Olive Oil', 'quantity': 1, 'unit_price': unit_price, 'list_price': list_price,
              'modifiers': [], 'applied_promotion': None}]
    return receipt_model.build_receipt_model(sale=sale, items=items, branding={}, currency='JOD',
                                             requested_lang=lang)


def _rows(model):
    return [ln for ln in model['lines'] if ln.get('kind') == 'row']


def _discount_row(model):
    return next((r for r in _rows(model) if r['start'].startswith(('Discount', 'الخصم'))), None)


def test_the_discount_row_carries_the_reason():
    row = _discount_row(_model(reason='Damaged'))
    assert row['start'] == 'Discount (Damaged)' and row['end'].endswith('0.560'), row


def test_no_reason_is_the_plain_label_and_a_blank_reason_too():
    assert _discount_row(_model())['start'] == 'Discount'
    assert _discount_row(_model(reason='   '))['start'] == 'Discount'


def test_the_arabic_receipt_has_the_arabic_label_and_the_reason_verbatim():
    row = _discount_row(_model(reason='تالف', lang='ar'))
    assert row['start'] == 'الخصم (تالف)', row


def test_an_overridden_line_strikes_its_list_price():
    row = next(r for r in _rows(_model()) if r.get('qty'))
    assert row['end'].endswith('3.000') and row['strike'].endswith('3.500'), row


def test_a_line_at_list_price_or_a_pre_v54_sale_shows_no_strike():
    at_list = next(r for r in _rows(_model(list_price=3.0)) if r.get('qty'))
    unknown = next(r for r in _rows(_model(list_price=None)) if r.get('qty'))
    assert 'strike' not in at_list and 'strike' not in unknown, (at_list, unknown)
