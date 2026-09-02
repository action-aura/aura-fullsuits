"""
Aura Retail -- tax/discount calculation policy. SINGLE SOURCE OF TRUTH
for how tax interacts with discount on a sale, AND the only module allowed
to compute a persisted financial total anywhere in this backend.

Two modes, selected per-company via retail_settings.tax_calculation_mode
(see api/retail_api.py's _settings()/_DEFAULT_SETTINGS and the
GET/POST /api/sub/retail/settings/tax routes):

  TAX_AFTER_DISCOUNT (default)
      taxable_amount = subtotal - discount
      tax            = taxable_amount * tax_rate
      total          = taxable_amount + tax

  TAX_BEFORE_DISCOUNT
      taxable_amount = subtotal              (tax ignores the discount)
      tax            = taxable_amount * tax_rate
      total          = subtotal - discount + tax

Neither mode supports tax-inclusive pricing (a price that already contains
tax) -- that is not a feature this product has; do not infer one. There is
also no mixed taxable/non-taxable-line concept beyond per-line tax_rate=0
(a zero-rate line is just a line whose resolved tax_rate happens to be 0 --
calculate_line handles it with no special case needed).

CALCULATION PRECISION (Wave 0 correction, AUDIT-006): all arithmetic here
runs in Decimal, not binary float, and every rounding step uses
ROUND_HALF_UP at 2 decimal places -- the same rounding rule
api/retail_api.py's _money() already uses for header-level figures. Before
this correction, this module used Python's built-in round() (banker's
rounding), which disagreed with _money() at exact half-cent boundaries.
Public functions still accept and return plain floats (unchanged call
contract for existing callers/tests); only the internal math changed.

As of Wave 0 (AUDIT-002/AUDIT-003), api/retail_api.py's create_sale() calls
calculate_line() for every line server-side and ignores any client-submitted
subtotal/discount_amount/tax_amount/total -- this module is no longer merely
documented as the source of truth, it is actually load-bearing for every
persisted sale, on every platform.

This module is the only place these two formulas are allowed to be written
out. Anywhere else in the Python backend that needs a tax/discount/total
figure (currently: database/schema.py::_seed_retail's synthetic demo sales)
must call into this module rather than re-deriving the arithmetic.

The POS cart UI (frontend/subsystem-retail.js, function `_recalc`) cannot
import this Python module directly -- it is a browser-side vanilla-JS cart
that must recompute totals instantly on every keystroke without a network
round trip, for INSTANT DISPLAY ONLY. It is a required, deliberately exact
mirror of the two formulas above for that display purpose, but as of Wave 0
it is no longer financially authoritative -- create_sale() recomputes
everything server-side regardless of what the client displayed or submitted,
so a mismatch between the two now produces a wrong on-screen preview
followed by a correct persisted result, not a wrong persisted result.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

TAX_AFTER_DISCOUNT = "after_discount"
TAX_BEFORE_DISCOUNT = "before_discount"

VALID_MODES = (TAX_AFTER_DISCOUNT, TAX_BEFORE_DISCOUNT)
DEFAULT_MODE = TAX_AFTER_DISCOUNT

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
#: Kept here rather than in api/retail_api.py's `_DEFAULT_SETTINGS` because
#: core/ must not import api/ (api/ already imports core/), and BOTH sides
#: need this exact answer -- `_DEFAULT_SETTINGS` reads it, and so do the
#: helpers that read `base_currency` straight out of `retail_settings`
#: without going through `_settings()`. Two literals here is how the drawer
#: came to round a default install's fils to cents while the sale total kept
#: them: `_settings()` supplied 'JOD' from its defaults dict, a raw SELECT on
#: the same setting supplied None, and only one of those paths was tested.
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
    `normalize_mode`/`clamp_discount_pct`'s posture in this same module: a bad
    stored setting must degrade to a sane default, never block a sale. A till
    that refuses to ring because someone typoed a currency code in settings
    would be a far worse defect than rounding to the wrong precision.
    """
    try:
        digits = CURRENCY_MINOR_UNITS.get((currency or '').strip().upper(), CURRENCY_DECIMALS)
    except (AttributeError, TypeError):
        digits = CURRENCY_DECIMALS
    return Decimal(1).scaleb(-digits)

#: What to PRINT in front of an amount, per ISO 4217 code.
#:
#: Deliberately short, and deliberately falling back to the CODE ITSELF rather
#: than to any symbol. "SEK 120.00" is honest and unambiguous; guessing a symbol
#: for a currency nobody entered is how a Swedish shop ends up displaying
#: dollars. The fallback is the feature, not a gap.
#:
#: JOD renders as "JD" -- the form Jordanian shops actually print in English.
#: The Arabic form is a translation concern and belongs in the locale
#: catalogues, not here: this table is what the SERVER knows about money, and
#: the server has no opinion about which language the till is showing.
CURRENCY_SYMBOLS = {
    'JOD': 'JD',
    'USD': '$',
    'EUR': '€',
    'GBP': '£',
    'SAR': 'SR',
    'AED': 'AED',
    'KWD': 'KD',
    'BHD': 'BD',
    'OMR': 'OMR',
    'QAR': 'QR',
    'EGP': 'EGP',
    'ILS': '₪',
    'TRY': '₺',
    'JPY': '¥',
}


def currency_symbol(currency=None) -> str:
    """The display mark for `currency`, falling back to the uppercased code.

    Same never-raise posture as `currency_quantum`: a malformed setting must
    degrade to something a cashier can still read, never break the screen.
    """
    try:
        code = (currency or '').strip().upper()
    except (AttributeError, TypeError):
        return ''
    return CURRENCY_SYMBOLS.get(code, code)


MAX_DISCOUNT_PCT = 100
MIN_DISCOUNT_PCT = 0

CALCULATION_VERSION = "retail-pricing-v2-wave0"


def _d(x) -> Decimal:
    """Decimal from any numeric-ish input, going through str() to avoid
    binary-float artifacts (Decimal(0.1) != Decimal('0.1'))."""
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x if x is not None else 0))


def _money(x: Decimal, quant: Decimal = CURRENCY_QUANT) -> float:
    """Round one figure to a currency's minor unit, ROUND_HALF_UP.

    `quant` defaults to the 2-decimal quantum, so every pre-existing caller --
    and every test written against them -- behaves EXACTLY as it did before
    currency awareness existed. Only a caller that passes a currency through
    gets different arithmetic, which is what makes this change safe to land on
    a module that computes every persisted total in the product.

    ROUND_HALF_UP is unchanged and deliberate (AUDIT-006): Python's built-in
    round() is banker's rounding and disagreed with api/retail_api.py's own
    _money() at exact half-cent boundaries. Only the QUANTUM is now variable;
    the rounding RULE is not.
    """
    return float(x.quantize(quant, rounding=ROUND_HALF_UP))


def normalize_mode(value) -> str:
    """Any unrecognised/missing value falls back to the default rather than
    raising -- a malformed or legacy stored setting must never break a sale."""
    return value if value in VALID_MODES else DEFAULT_MODE


def clamp_discount_pct(value) -> float:
    """Clamp a percentage discount to [0, 100] (AUDIT-005). A negative
    input becomes 0; anything over 100 becomes 100. Malformed input becomes 0
    rather than raising -- a bad discount value must never block a sale, it
    must just stop discounting."""
    try:
        d = _d(value)
    except Exception:
        return 0.0
    if d < 0:
        d = Decimal(0)
    if d > MAX_DISCOUNT_PCT:
        d = Decimal(MAX_DISCOUNT_PCT)
    return float(d)


def calculate_line(unit_price: float, quantity: float, discount_pct: float = 0,
                    tax_rate: float = 0, mode: str = DEFAULT_MODE,
                    currency: str = None) -> dict:
    """Compute one cart/sale line's figures under the given tax mode.

    `discount_pct` and `tax_rate` are percentages (0-100), matching the
    values already stored on `sale_items.discount_pct` / `.tax_rate` and used
    by the POS cart. `discount_pct` is clamped to [0, 100] before use
    (AUDIT-005) -- callers that need to reject an out-of-range discount
    outright rather than silently clamp it should validate before calling.
    Returns a dict of 2-decimal Decimal-rounded floats (ROUND_HALF_UP);
    callers that need currency-safe accumulation across many lines should
    re-round the final sum rather than trust float addition of many
    already-rounded lines to stay exact.
    """
    mode = normalize_mode(mode)
    discount_pct = clamp_discount_pct(discount_pct)

    price = _d(unit_price)
    qty = _d(quantity)
    disc_pct = _d(discount_pct)
    rate = _d(tax_rate)

    gross = price * qty
    discount_amount = gross * (disc_pct / Decimal(100))

    if mode == TAX_BEFORE_DISCOUNT:
        taxable_amount = gross
        tax = gross * (rate / Decimal(100))
        total = gross - discount_amount + tax
    else:  # TAX_AFTER_DISCOUNT
        taxable_amount = gross - discount_amount
        tax = taxable_amount * (rate / Decimal(100))
        total = taxable_amount + tax

    q = currency_quantum(currency)
    return {
        "gross": _money(gross, q),
        "discount_amount": _money(discount_amount, q),
        "taxable_amount": _money(taxable_amount, q),
        "tax": _money(tax, q),
        "total": _money(total, q),
    }


def calculate_invoice(subtotal: float, discount_amount: float, tax_rate_pct: float,
                       mode: str = DEFAULT_MODE, currency: str = None) -> dict:
    """Invoice/cart-level aggregate variant: `discount_amount` is already a
    currency amount (not a percentage) -- this mirrors the POS cart's
    invoice-level discount model (a single discount % applied to the whole
    cart, entered once, not per line) after it has already been converted to
    an amount, and a single blended tax rate applied to the result. Used
    where a caller has already summed line items into one subtotal rather
    than iterating lines through calculate_line(). `discount_amount` is
    clamped to [0, subtotal] (AUDIT-005).
    """
    mode = normalize_mode(mode)
    sub = _d(subtotal)
    disc = _d(discount_amount)
    if disc < 0:
        disc = Decimal(0)
    if disc > sub:
        disc = sub
    rate = _d(tax_rate_pct)

    if mode == TAX_BEFORE_DISCOUNT:
        taxable_amount = sub
        tax = sub * (rate / Decimal(100))
        total = sub - disc + tax
    else:  # TAX_AFTER_DISCOUNT
        taxable_amount = sub - disc
        tax = taxable_amount * (rate / Decimal(100))
        total = taxable_amount + tax

    q = currency_quantum(currency)
    return {
        "discount_amount": _money(disc, q),
        "taxable_amount": _money(taxable_amount, q),
        "tax": _money(tax, q),
        "total": _money(total, q),
    }
