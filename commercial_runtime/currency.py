"""Currency minor units -- THE ONE TABLE, shared by every product.

This lived in products/retail/backend/core/retail/pricing.py, which is the
right home for Retail's tax/discount policy but the wrong home for a fact
about ISO 4217 that Clinic and the e-invoicing serializer need just as much.
`commercial_runtime` cannot import `products/` (nothing in here may depend on
either product), so the only way for Clinic and
commercial_runtime/einvoicing/ubl.py to round money correctly was either a
SECOND copy of the table -- exactly what pricing.py's own comment warns
against -- or this module. pricing.py now imports from here, so there is
still exactly one table and one `currency_quantum`; every existing
`tax_engine.CURRENCY_MINOR_UNITS` / `tax_engine.currency_quantum` caller
keeps working unchanged through that re-export.

The three modules that were rounding money to a hardcoded 2 decimals while
the shop's currency said JOD, all fixed by importing from here:

  * products/clinic/backend/api/clinic_api.py::create_invoice  (tax/total)
  * products/clinic/backend/api/clinic_api.py::record_payment  (amount)
  * commercial_runtime/einvoicing/ubl.py::_money               (every amount
    in the document filed with the tax authority)
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

#: The 2-decimal default, kept under its original names because other modules
#: import them. NOTHING about their meaning changes: they remain "what a
#: currency with no entry in the table below rounds to", which is what every
#: caller that never passes a currency still gets.
CURRENCY_DECIMALS = 2
CURRENCY_QUANT = Decimal('0.01')

#: The currency a shop has when it has never chosen one.
#:
#: JOD, not USD, and NOT the same thing as `CURRENCY_DECIMALS` above: that is
#: the fallback for an UNKNOWN code (a shop that stored something this module
#: has never heard of), whereas this is the fallback for NO code at all. The
#: two are different questions and conflating them is a money bug: an unknown
#: code should degrade to the safest generic precision, but a shop that simply
#: has not opened settings yet is a Jordanian shop, and the dinar has three
#: decimal places.
#:
#: Kept here rather than in either product's settings defaults because core/
#: must not import api/ (api/ already imports core/), and BOTH sides need this
#: exact answer -- Retail's `_DEFAULT_SETTINGS` reads it, and so do the helpers
#: that read `base_currency` straight out of `retail_settings` without going
#: through `_settings()`. Two literals is how the drawer came to round a
#: default install's fils to cents while the sale total kept them:
#: `_settings()` supplied 'JOD' from its defaults dict, a raw SELECT on the
#: same setting supplied None, and only one of those paths was tested.
DEFAULT_BASE_CURRENCY = 'JOD'

#: Minor-unit digits per ISO 4217 code, for the currencies that are NOT 2.
#:
#: This exists because the product's home market is Jordan, and the dinar has
#: THREE decimal places (1000 fils). Rounding JOD to 2 was not a display bug --
#: it silently changed persisted money. Measured, with the real 16% VAT rate:
#:
#:     2.375 JOD + VAT = 2.755  ->  quantized at 0.01 -> 2.76   (+5 fils)
#:     3.150 JOD + VAT = 3.654  ->  quantized at 0.01 -> 3.65   (-4 fils)
#:
#: Every line item could be wrong by up to 5 fils in EITHER direction, on the
#: receipt the customer holds and in the totals submitted to the tax authority.
#:
#: A TABLE, not a currency library: this codebase has no such dependency and
#: CLAUDE.md forbids adding one casually. Only the exceptions are listed --
#: the Gulf 3-decimal currencies and the 0-decimal yen -- because enumerating
#: all ~180 ISO codes would be a maintenance burden that buys nothing. Anything
#: absent means 2, which is correct for the overwhelming majority.
CURRENCY_MINOR_UNITS = {
    'JOD': 3,   # Jordanian dinar -- the home market
    'KWD': 3,   # Kuwaiti dinar
    'BHD': 3,   # Bahraini dinar
    'OMR': 3,   # Omani rial
    'TND': 3,   # Tunisian dinar
    'LYD': 3,   # Libyan dinar
    'IQD': 3,   # Iraqi dinar
    'JPY': 0,   # yen -- no minor unit at all
    'KRW': 0,
}


def currency_quantum(currency=None) -> Decimal:
    """The Decimal quantum to round to for `currency` (an ISO 4217 code).

    An unknown, missing, malformed or non-string code falls back to the
    2-decimal default and NEVER raises. That is deliberate and matches
    `normalize_mode`/`clamp_discount_pct`'s posture in pricing.py: a bad
    stored setting must degrade to a sane default, never block a sale. A till
    that refuses to ring because someone typoed a currency code in settings
    would be a far worse defect than rounding to the wrong precision.
    """
    try:
        digits = CURRENCY_MINOR_UNITS.get((currency or '').strip().upper(), CURRENCY_DECIMALS)
    except (AttributeError, TypeError):
        digits = CURRENCY_DECIMALS
    return Decimal(1).scaleb(-digits)


def currency_decimals(currency=None) -> int:
    """How many digits after the point `currency` has. Same never-raise
    posture as `currency_quantum`, which it is derived from so the two can
    never disagree -- a format string built from one and a quantize() call
    built from the other is exactly how '12.35' ends up printed next to a
    stored 12.345."""
    return -currency_quantum(currency).as_tuple().exponent


def quantize_money(value, currency=None) -> Decimal:
    """Round `value` to `currency`'s minor unit, ROUND_HALF_UP.

    ROUND_HALF_UP, not Python's built-in round(): round() is banker's
    rounding and disagreed with the rest of this codebase's money at exact
    half-unit boundaries (AUDIT-006). Only the QUANTUM varies by currency;
    the rounding RULE never does.

    Goes through str() for non-Decimal input to avoid binary-float artifacts
    (Decimal(0.1) != Decimal('0.1')).
    """
    d = value if isinstance(value, Decimal) else Decimal(str(value if value is not None else 0))
    return d.quantize(currency_quantum(currency), rounding=ROUND_HALF_UP)


def format_money_plain(value, currency=None) -> str:
    """`value` rendered with exactly `currency`'s minor-unit digits and no
    symbol, grouping or sign decoration -- the form a machine-readable
    document or an error message wants.

    'f' rather than str(): str() of a Decimal can come back in exponent form
    for some magnitudes, and a tax authority parsing '2.3E+1' where it
    expected '23.000' is not a failure anyone would enjoy debugging.
    """
    return format(quantize_money(value, currency), 'f')
