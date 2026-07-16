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

CURRENCY_DECIMALS = 2
CURRENCY_QUANT = Decimal('0.01')

MAX_DISCOUNT_PCT = 100
MIN_DISCOUNT_PCT = 0

CALCULATION_VERSION = "retail-pricing-v2-wave0"


def _d(x) -> Decimal:
    """Decimal from any numeric-ish input, going through str() to avoid
    binary-float artifacts (Decimal(0.1) != Decimal('0.1'))."""
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x if x is not None else 0))


def _money(x: Decimal) -> float:
    return float(x.quantize(CURRENCY_QUANT, rounding=ROUND_HALF_UP))


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
                    tax_rate: float = 0, mode: str = DEFAULT_MODE) -> dict:
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

    return {
        "gross": _money(gross),
        "discount_amount": _money(discount_amount),
        "taxable_amount": _money(taxable_amount),
        "tax": _money(tax),
        "total": _money(total),
    }


def calculate_invoice(subtotal: float, discount_amount: float, tax_rate_pct: float,
                       mode: str = DEFAULT_MODE) -> dict:
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

    return {
        "discount_amount": _money(disc),
        "taxable_amount": _money(taxable_amount),
        "tax": _money(tax),
        "total": _money(total),
    }
