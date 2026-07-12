"""
Aura Retail -- tax/discount calculation policy. SINGLE SOURCE OF TRUTH
for how tax interacts with discount on a sale.

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

This module is the only place these two formulas are allowed to be written
out. Anywhere else in the Python backend that needs a tax/discount/total
figure (currently: database/schema.py::_seed_retail's synthetic demo sales)
must call into this module rather than re-deriving the arithmetic.

The POS cart UI (frontend/subsystem-retail.js, function `_recalc`) cannot
import this Python module directly -- it is a browser-side vanilla-JS cart
that must recompute totals instantly on every keystroke without a network
round trip. It is a REQUIRED, deliberately exact mirror of the two formulas
above. If you change a formula here, change it there too, and vice versa --
tests/retail_pricing_test.py only exercises the Python side.
"""
from __future__ import annotations

TAX_AFTER_DISCOUNT = "after_discount"
TAX_BEFORE_DISCOUNT = "before_discount"

VALID_MODES = (TAX_AFTER_DISCOUNT, TAX_BEFORE_DISCOUNT)
DEFAULT_MODE = TAX_AFTER_DISCOUNT


def normalize_mode(value) -> str:
    """Any unrecognised/missing value falls back to the default rather than
    raising -- a malformed or legacy stored setting must never break a sale."""
    return value if value in VALID_MODES else DEFAULT_MODE


def calculate_line(unit_price: float, quantity: float, discount_pct: float = 0,
                    tax_rate: float = 0, mode: str = DEFAULT_MODE) -> dict:
    """Compute one cart/sale line's figures under the given tax mode.

    `discount_pct` and `tax_rate` are percentages (0-100), matching the
    values already stored on `sale_items.discount_pct` / `.tax_rate` and used
    by the POS cart. Returns a dict of 2-decimal-rounded floats; callers that
    need currency-safe accumulation across many lines should re-round the
    final sum (see _money() in retail_api.py) rather than trust float
    addition of many already-rounded lines to stay exact.
    """
    mode = normalize_mode(mode)
    gross = unit_price * quantity
    discount_amount = gross * (discount_pct / 100.0)

    if mode == TAX_BEFORE_DISCOUNT:
        taxable_amount = gross
        tax = gross * (tax_rate / 100.0)
        total = gross - discount_amount + tax
    else:  # TAX_AFTER_DISCOUNT
        taxable_amount = gross - discount_amount
        tax = taxable_amount * (tax_rate / 100.0)
        total = taxable_amount + tax

    return {
        "gross": round(gross, 2),
        "discount_amount": round(discount_amount, 2),
        "taxable_amount": round(taxable_amount, 2),
        "tax": round(tax, 2),
        "total": round(total, 2),
    }


def calculate_invoice(subtotal: float, discount_amount: float, tax_rate_pct: float,
                       mode: str = DEFAULT_MODE) -> dict:
    """Invoice/cart-level aggregate variant: `discount_amount` is already a
    currency amount (not a percentage) -- this mirrors the POS cart's
    invoice-level discount model (a single discount % applied to the whole
    cart, entered once, not per line) after it has already been converted to
    an amount, and a single blended tax rate applied to the result. Used
    where a caller has already summed line items into one subtotal rather
    than iterating lines through calculate_line().
    """
    mode = normalize_mode(mode)
    if mode == TAX_BEFORE_DISCOUNT:
        taxable_amount = subtotal
        tax = subtotal * (tax_rate_pct / 100.0)
        total = subtotal - discount_amount + tax
    else:  # TAX_AFTER_DISCOUNT
        taxable_amount = subtotal - discount_amount
        tax = taxable_amount * (tax_rate_pct / 100.0)
        total = taxable_amount + tax

    return {
        "taxable_amount": round(taxable_amount, 2),
        "tax": round(tax, 2),
        "total": round(total, 2),
    }
