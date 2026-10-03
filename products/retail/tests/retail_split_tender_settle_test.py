"""Split tender: the pure settlement rules (core/retail/tenders.py), JOD.

Run: pytest products/retail/tests/retail_split_tender_settle_test.py -q
"""
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
for _p in (str(PRODUCT_DIR.parent.parent), str(PRODUCT_DIR / 'backend')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail import tenders  # noqa: E402

CUR = 'JOD'


def _norm(*pairs):
    out, code, msg = tenders.normalize_tenders(
        [{'method': m, 'amount': a} for m, a in pairs])
    assert code is None, (code, msg)
    return out


def _settle(due, *pairs):
    return tenders.settle_tenders(_norm(*pairs), D(due), CUR)


def test_exact_card_plus_cash():
    r = _settle('11.832', ('card', '5'), ('cash', '6.832'))
    assert r['ok'] and r['change'] == 0 and r['paid'] == D('11.832')
    assert r['balance_due'] == 0
    assert dict(r['rows']) == {'card': D('5.000'), 'cash': D('6.832')}


def test_cash_over_hands_back_change_and_row_is_retained_amount():
    r = _settle('11.832', ('card', '5'), ('cash', '10'))
    assert r['ok'] and r['change'] == D('3.168')
    assert dict(r['rows'])['cash'] == D('6.832')


def test_one_fil_short_is_refused_with_the_shortfall_named():
    r = _settle('11.832', ('card', '5'), ('cash', '6.831'))
    assert not r['ok'] and r['code'] == 'TENDERS_SHORT'
    assert '0.001' in r['message']


def test_amount_that_quantizes_exactly_to_due_is_accepted():
    r = _settle('11.832', ('card', '5'), ('cash', '6.8319'))
    assert r['ok'] and r['change'] == 0


def test_non_cash_overpay_refused():
    r = _settle('25', ('card', '30'), ('cash', '5'))
    assert not r['ok'] and r['code'] == 'TENDER_OVERPAID_NON_CASH'


def test_cash_that_would_all_be_change_refused():
    r = _settle('10', ('card', '10'), ('cash', '5'))
    assert not r['ok'] and r['code'] == 'TENDER_CASH_NOT_NEEDED'


def test_credit_exact_remainder_ok_and_not_a_payment_row():
    r = _settle('11.832', ('cash', '5'), ('credit', '6.832'))
    assert r['ok'] and r['balance_due'] == D('6.832')
    assert [m for m, _ in r['rows']] == ['cash']


def test_credit_overpay_refused():
    r = _settle('11.832', ('cash', '8'), ('credit', '6.832'))
    assert not r['ok'] and r['code'] == 'TENDER_CREDIT_OVERPAID'


@pytest.mark.parametrize('pairs,code', [
    ([('cash', '1'), ('cash', '2')], 'TENDER_DUPLICATE_METHOD'),
    ([('split', '1')], 'TENDER_METHOD_INVALID'),
    ([('store_credit', '1')], 'TENDER_METHOD_INVALID'),
    ([('', '1')], 'TENDER_METHOD_INVALID'),
    ([('card', '0')], 'TENDERS_INVALID'),
    ([('card', '-1')], 'TENDERS_INVALID'),
    ([('card', 'nan')], 'TENDERS_INVALID'),
    ([('card', 'abc')], 'TENDERS_INVALID'),
])
def test_normalize_refuses(pairs, code):
    _, got, _msg = tenders.normalize_tenders(
        [{'method': m, 'amount': a} for m, a in pairs])
    assert got == code


def test_more_than_six_entries_refused():
    raw = [{'method': f'm{i}', 'amount': '1'} for i in range(7)]
    assert tenders.normalize_tenders(raw)[1] == 'TENDERS_INVALID'
    assert tenders.normalize_tenders([])[1] == 'TENDERS_INVALID'
    assert tenders.normalize_tenders('x')[1] == 'TENDERS_INVALID'


def test_method_is_lowercased_and_stripped():
    out, code, _ = tenders.normalize_tenders([{'method': ' CARD ', 'amount': 1}])
    assert code is None and out[0]['method'] == 'card'


@pytest.mark.parametrize('cash,card,credit', [
    ('0', '5', '0'), ('3', '5', '0'), ('8', '5', '0'), ('20', '5', '0'),
    ('2', '0', '9'), ('0', '3', '10'), ('13', '0', '0'), ('6', '7', '0'),
])
def test_rows_sum_equals_min_paid_due_grid(cash, card, credit):
    due = D('13')
    pairs = [(m, a) for m, a in (('cash', cash), ('card', card), ('credit', credit)) if D(a) > 0]
    r = tenders.settle_tenders(_norm(*pairs), due, CUR)
    if not r['ok']:
        return
    assert sum((a for _, a in r['rows']), D(0)) == min(r['paid'], due)
