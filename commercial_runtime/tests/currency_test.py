"""commercial_runtime/currency.py -- the ONE minor-unit table.

This module exists because the table used to live in
products/retail/backend/core/retail/pricing.py, which commercial_runtime is
not allowed to import. While it lived there, Clinic's billing and the UBL
serializer that renders the document filed with the tax authority both
hardcoded 2 decimals, and both were measurably wrong for JOD -- the home
market's currency, which has three.

The tests below pin the two things that can silently rot:

  1. the arithmetic itself, including the never-raise posture a money helper
     in a till has to have;
  2. that pricing.py still SHARES this table rather than having grown a
     second copy of it. That is the failure this move exists to prevent, and
     a second copy would not break anything until the two drifted.

Test #2 imports from products/, which production code in this package must
never do. That rule constrains the DEPENDENCY DIRECTION of shipped modules;
a test whose whole job is to verify both sides hold the same object has to
be able to see both sides. It skips rather than fails if the retail backend
is not importable, so this file stays runnable on its own.

Run:
    pytest commercial_runtime/tests/currency_test.py -v
"""
import sys
from decimal import Decimal
from pathlib import Path

import pytest

SUITE_ROOT = Path(__file__).resolve().parents[2]
if str(SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(SUITE_ROOT))

from commercial_runtime import currency  # noqa: E402


# ── The table ────────────────────────────────────────────────────────────────

def test_the_home_market_currency_has_three_decimals():
    """JOD is the reason this module exists. 1 dinar = 1000 fils."""
    assert currency.CURRENCY_MINOR_UNITS['JOD'] == 3
    assert str(currency.currency_quantum('JOD')) == '0.001'
    assert currency.currency_decimals('JOD') == 3


def test_the_default_currency_is_the_home_market_not_the_generic_fallback():
    """DEFAULT_BASE_CURRENCY and CURRENCY_DECIMALS answer DIFFERENT
    questions, and conflating them is a money bug: an unknown CODE degrades
    to the safest generic precision (2), but NO code at all means a
    Jordanian shop that has not opened settings yet (3)."""
    assert currency.DEFAULT_BASE_CURRENCY == 'JOD'
    assert currency.CURRENCY_DECIMALS == 2
    assert currency.currency_decimals(currency.DEFAULT_BASE_CURRENCY) == 3


@pytest.mark.parametrize('code', ['KWD', 'BHD', 'OMR', 'TND', 'LYD', 'IQD'])
def test_the_other_three_decimal_currencies(code):
    assert str(currency.currency_quantum(code)) == '0.001'


@pytest.mark.parametrize('code', ['USD', 'EUR', 'GBP', 'SAR', 'AED'])
def test_two_decimal_currencies_are_unaffected(code):
    assert str(currency.currency_quantum(code)) == '0.01'


def test_the_zero_decimal_currencies():
    assert str(currency.currency_quantum('JPY')) == '1'
    assert currency.format_money_plain(23.4, 'JPY') == '23'


@pytest.mark.parametrize('bad', [None, '', '   ', 'ZZZ', 123, object()])
def test_a_malformed_code_degrades_and_never_raises(bad):
    """A till that refuses to ring because someone typoed a currency code in
    settings would be a far worse defect than rounding to the wrong
    precision."""
    assert str(currency.currency_quantum(bad)) == '0.01'


@pytest.mark.parametrize('variant', ['jod', ' JOD ', 'Jod'])
def test_codes_are_normalized_before_lookup(variant):
    assert str(currency.currency_quantum(variant)) == '0.001'


# ── The arithmetic ───────────────────────────────────────────────────────────

def test_rounding_is_half_up_not_bankers():
    """Python's built-in round() is banker's rounding and disagreed with the
    rest of this codebase's money at exact half-unit boundaries (AUDIT-006).
    round(0.125, 2) is 0.12; ROUND_HALF_UP is 0.13."""
    assert currency.quantize_money('0.125', 'USD') == Decimal('0.13')
    assert currency.quantize_money('0.1235', 'JOD') == Decimal('0.124')
    assert round(0.125, 2) == 0.12, 'the behaviour this deliberately does NOT use'


def test_the_measured_jod_cases_from_the_correction():
    """The two figures the table's own comment records, with the real 16%
    VAT rate. At 2 decimals these move by 5 fils in either direction."""
    assert currency.format_money_plain('2.755', 'JOD') == '2.755'
    assert currency.format_money_plain('2.755', 'USD') == '2.76'
    assert currency.format_money_plain('3.654', 'JOD') == '3.654'
    assert currency.format_money_plain('3.654', 'USD') == '3.65'


def test_float_input_goes_through_str_not_binary():
    """Decimal(0.1) != Decimal('0.1'); a money helper that skipped the str()
    hop would carry binary-float noise into a persisted total."""
    assert currency.quantize_money(0.1, 'JOD') == Decimal('0.100')


def test_format_never_returns_exponent_notation():
    """A tax authority parsing '2.3E+1' where it expected '23.000' is not a
    failure anyone would enjoy debugging."""
    for value in ('23', '2.3E+1', '0.0000001', '1234567890.123'):
        assert 'E' not in currency.format_money_plain(value, 'JOD').upper()


def test_format_pads_to_the_full_minor_unit():
    """'23' must file as '23.000' under JOD -- a document that drops the
    trailing digits is not the same document."""
    assert currency.format_money_plain(23, 'JOD') == '23.000'
    assert currency.format_money_plain(23, 'USD') == '23.00'


# ── One table, not two ───────────────────────────────────────────────────────

def test_retail_pricing_shares_this_table_rather_than_copying_it():
    """The whole point of the move. A second copy would pass every other
    test in this file and every test in the retail suite, right up until the
    day one of them was edited and the other was not -- at which point a
    till and the document it files would round the same sale differently.

    Identity (`is`), not equality: two equal dicts today are exactly the
    thing that drifts tomorrow.
    """
    backend = SUITE_ROOT / 'products' / 'retail' / 'backend'
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    try:
        from core.retail import pricing
    except Exception as exc:  # pragma: no cover - environment, not behaviour
        pytest.skip(f'retail backend not importable here: {exc!r}')

    assert pricing.CURRENCY_MINOR_UNITS is currency.CURRENCY_MINOR_UNITS
    assert pricing.currency_quantum is currency.currency_quantum
    assert pricing.DEFAULT_BASE_CURRENCY == currency.DEFAULT_BASE_CURRENCY
    assert pricing.CURRENCY_DECIMALS == currency.CURRENCY_DECIMALS
    assert pricing.CURRENCY_QUANT == currency.CURRENCY_QUANT
