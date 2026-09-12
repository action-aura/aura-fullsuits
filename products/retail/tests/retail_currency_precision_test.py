"""
Aura Retail -- currency-aware money precision.

WHY THIS EXISTS. `core/retail/pricing.py` is this backend's single source of
truth for every persisted financial total, and it rounded EVERY currency to two
decimal places. The product's home market is Jordan, and the dinar has THREE
(1000 fils). That was not a display bug: it silently changed money.

Measured against the real 16% Jordanian VAT rate, before the fix:

    2.375 JOD + VAT = 2.755  ->  quantized at 0.01 -> 2.76   (+5 fils)
    3.150 JOD + VAT = 3.654  ->  quantized at 0.01 -> 3.65   (-4 fils)

Up to five fils wrong, in either direction, on every line -- on the receipt the
customer holds and in the totals filed with the tax authority.

WHAT THIS FILE PINS, and why each half matters:

  * JOD keeps three decimals through calculate_line and calculate_invoice --
    the two functions that compute every persisted total.

    HONEST LIMIT: these are unit-level. They prove the ENGINE is right; they do
    NOT prove that every call site remembered to pass the currency through.
    That coverage comes from the money suites -- pricing, returns, promotions,
    modifiers and accounting export -- which boot the real app and read real
    rows, and which were all re-run green against the new JOD default as part
    of this change. Said plainly, because "a helper returns the right number"
    and "the right number reaches the database" are different claims and only
    the second one is what a shopkeeper cares about.
  * USD still rounds to two. This is the half that a careless "just make it
    three everywhere" fix would break, silently, for every non-Gulf market.
  * A malformed currency code falls back to two decimals and NEVER raises,
    matching `normalize_mode`/`clamp_discount_pct`'s posture in the same module:
    a bad stored setting must degrade, never block a sale. A till that refuses
    to ring because someone typoed a currency would be far worse than one that
    rounds imprecisely.
  * A fresh install defaults to JOD, not USD.

Run:
    pytest products/retail/tests/retail_currency_precision_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail import pricing as tax_engine  # noqa: E402


# ═════════════════════════════════════════════════════════════════════════════
# Part A -- the quantum resolver, in isolation
# ═════════════════════════════════════════════════════════════════════════════

def test_jod_resolves_to_three_decimal_places():
    """The whole reason this feature exists."""
    assert str(tax_engine.currency_quantum('JOD')) == '0.001'


def test_the_other_three_decimal_currencies_resolve_too():
    """Kuwait, Bahrain, Oman, Tunisia, Libya and Iraq share the dinar's minor
    unit. Listed because the neighbouring markets are the plausible next sale,
    and finding this out one customer at a time would be the expensive way."""
    for code in ('KWD', 'BHD', 'OMR', 'TND', 'LYD', 'IQD'):
        assert str(tax_engine.currency_quantum(code)) == '0.001', code


def test_usd_still_resolves_to_two_decimal_places():
    assert str(tax_engine.currency_quantum('USD')) == '0.01'


def test_an_unlisted_currency_falls_back_to_two_rather_than_raising():
    """The overwhelming majority of ISO codes are 2-decimal, so absence from the
    table means 2 -- not an error."""
    assert str(tax_engine.currency_quantum('EUR')) == '0.01'
    assert str(tax_engine.currency_quantum('GBP')) == '0.01'


def test_a_malformed_currency_degrades_and_never_raises():
    """A typo in a settings row must not be able to stop a shop selling."""
    for bad in (None, '', '   ', '!!', 'not-a-code', 123, object()):
        assert str(tax_engine.currency_quantum(bad)) == '0.01', repr(bad)


def test_case_and_whitespace_are_tolerated():
    """`jod`, ` JOD ` and `JOD` are the same currency. A stored setting that
    picked up a stray space must not silently lose a decimal place."""
    for variant in ('jod', ' JOD ', 'Jod', '\tjod\n'):
        assert str(tax_engine.currency_quantum(variant)) == '0.001', repr(variant)


# ═════════════════════════════════════════════════════════════════════════════
# Part B -- calculate_line, the function that computes persisted money
# ═════════════════════════════════════════════════════════════════════════════

def test_jod_line_keeps_the_third_decimal():
    """2.375 JOD at 16% VAT is 2.755, not 2.76. The five fils this recovers are
    the entire point."""
    calc = tax_engine.calculate_line(2.375, 1, 0, 16, currency='JOD')
    assert calc['total'] == 2.755


def test_the_same_line_in_usd_still_rounds_to_two():
    """The half a 'make everything three decimals' fix would silently break."""
    calc = tax_engine.calculate_line(2.375, 1, 0, 16, currency='USD')
    assert calc['total'] == 2.76


def test_omitting_the_currency_is_byte_identical_to_before_this_feature():
    """Backward compatibility, stated as a test rather than hoped for: every
    pre-existing caller and every test written against them passes no currency,
    and must behave exactly as it did when the quantum was a hard-coded
    constant."""
    with_none = tax_engine.calculate_line(2.375, 1, 0, 16)
    as_usd = tax_engine.calculate_line(2.375, 1, 0, 16, currency='USD')
    assert with_none == as_usd


def test_invoice_level_arithmetic_is_currency_aware_too():
    """calculate_invoice is the aggregate sibling and shares the same defect if
    it is missed -- it computes persisted totals just as calculate_line does."""
    jod = tax_engine.calculate_invoice(2.375, 0, 16, currency='JOD')
    usd = tax_engine.calculate_invoice(2.375, 0, 16, currency='USD')
    assert jod['total'] == 2.755
    assert usd['total'] == 2.76


def test_a_zero_decimal_currency_rounds_to_whole_units():
    """Yen has no minor unit at all. Included because it is the other direction
    of the same bug: a table that only ever ADDS precision would get this
    wrong."""
    calc = tax_engine.calculate_line(2.375, 1, 0, 16, currency='JPY')
    assert calc['total'] == 3.0


# ═════════════════════════════════════════════════════════════════════════════
# Part C -- the shipped default
# ═════════════════════════════════════════════════════════════════════════════

def test_a_fresh_install_defaults_to_jod_not_usd():
    """This product is sold in Jordan. A Jordanian shop opening a new install
    and finding its money in dollars -- and rounded to the wrong precision --
    is not a default anyone would choose deliberately.

    Read straight from the settings default rather than booting the app: the
    value IS the contract, and a test that needed a running Flask instance to
    check one constant would be slower and no more convincing."""
    from api.retail_api import _DEFAULT_SETTINGS
    assert _DEFAULT_SETTINGS['base_currency'] == 'JOD'
