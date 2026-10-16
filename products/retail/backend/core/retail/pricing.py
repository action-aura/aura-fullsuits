"""
Aura Retail -- tax/discount calculation policy. SINGLE SOURCE OF TRUTH
for how tax interacts with discount on a sale, AND the only module allowed
to compute a persisted financial total anywhere in this backend.

Three modes, selected per-company via retail_settings.tax_calculation_mode
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

  TAX_INCLUSIVE (TAXINC -- Jordan shelf prices include tax)
      gross_incl     = round(unit_price * qty)       (the shelf figure, tax inside)
      discount       = round(gross_incl * pct)       (taken off the INCLUSIVE figure)
      net_incl       = gross_incl - discount
      taxable_amount = round(net_incl / (1 + rate))
      tax            = net_incl - taxable_amount     (EXACT remainder, never re-rounded)
      total          = net_incl

  WHY THIS MODE EXISTS: before it, `sell_price` was always ex-tax, so a shop
  whose shelf says 0.250 had to type 0.216 (0.250/1.16 rounded) and the receipt
  came out 0.251 -- every third price a fil off, on the receipt AND on the
  e-invoice. Here the shelf price is the stored price and the customer pays
  exactly round(price*qty); tax is carved OUT of it, and because tax is the
  remainder, taxable + tax == total for every line by construction.

  In this mode `gross` and `discount_amount` are INCLUSIVE figures, so
  gross - discount_amount == total (tax is inside it), NOT gross - discount +
  tax. `sale_items.line_total` keeps meaning TAXABLE (ex-tax) in every mode.

There is no mixed taxable/non-taxable-line concept beyond per-line tax_rate=0
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

# Re-exported below under their original names -- see the comment block where
# the table used to be defined, further down, for why the HOME moved and
# nothing else did.
from commercial_runtime.currency import (
    CURRENCY_DECIMALS,
    CURRENCY_MINOR_UNITS,
    CURRENCY_QUANT,
    DEFAULT_BASE_CURRENCY,
    currency_quantum,
)

TAX_AFTER_DISCOUNT = "after_discount"
TAX_BEFORE_DISCOUNT = "before_discount"
TAX_INCLUSIVE = "inclusive"

VALID_MODES = (TAX_AFTER_DISCOUNT, TAX_BEFORE_DISCOUNT, TAX_INCLUSIVE)
DEFAULT_MODE = TAX_AFTER_DISCOUNT

#: THE MINOR-UNIT TABLE MOVED HOME; IT DID NOT CHANGE.
#:
#: `CURRENCY_DECIMALS`, `CURRENCY_QUANT`, `DEFAULT_BASE_CURRENCY`,
#: `CURRENCY_MINOR_UNITS` and `currency_quantum` are now defined in
#: commercial_runtime/currency.py and imported at the top of this file. Every
#: measurement and every word of reasoning that used to sit here moved with
#: them, unedited; read that module for it. They keep their original names
#: here because ~30 call sites reach them as `tax_engine.CURRENCY_MINOR_UNITS`
#: / `tax_engine.currency_quantum` / `tax_engine.DEFAULT_BASE_CURRENCY`, and a
#: Retail caller asking this module is still the right shape.
#:
#: WHY it moved: Clinic's billing (products/clinic/backend/api/clinic_api.py)
#: and the UBL serializer that renders the document filed with the tax
#: authority (commercial_runtime/einvoicing/ubl.py) need this same table, and
#: commercial_runtime must not import products/. So while the table lived
#: here, both of them hardcoded 2 decimals -- and both were measurably wrong
#: for JOD, which is the home market. The only other way out was a SECOND copy
#: of the table, which is exactly what the comment that used to occupy these
#: lines warned against.

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

#: Bumped from "retail-pricing-v2-wave0" -- the money-arithmetic correction
#: (calculate_line/calculate_invoice now round gross/discount_amount/tax
#: EXACTLY ONCE and derive taxable_amount/total by exact Decimal arithmetic
#: on those rounded primitives, instead of independently re-rounding total
#: from a separate full-precision path) is an OUTPUT-CHANGING behavior
#: change for any discounted line, which is precisely what this constant
#: exists to let a differential-test harness (mobile/aura-retail-unified/
#: differential/generate_python_reference.py stamps it into the golden
#: output) notice rather than silently compare a stale ported formula
#: against numbers this module no longer produces.
#:
#: Bumped again to v4 for TAXINC: a new mode (TAX_INCLUSIVE) joined VALID_MODES.
#: The two existing modes' output is byte-identical (pinned by
#: retail_taxinc_test.py's exclusive-table test); the bump exists so the
#: differential harness notices a ported formula that lacks the third mode.
CALCULATION_VERSION = "retail-pricing-v4-tax-inclusive"


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

    RECONCILIATION BY CONSTRUCTION (AUDIT-XXX, Wave 0 money-arithmetic
    correction): `gross`, `discount_amount` and `tax` are each rounded
    EXACTLY ONCE, from full precision. `taxable_amount` and `total` are never
    independently rounded from their own full-precision source -- they are
    EXACT Decimal arithmetic on those three already-rounded primitives. This
    is what guarantees `gross - discount_amount + tax == total` ALWAYS, not
    just when two independent roundings happen to agree (they didn't: a 7.5%
    discount on a 2.50 JOD item persisted total=8.048 while
    subtotal-discount+tax=8.047, measured on a real till). The previous
    version rounded `total` from `taxable_amount_full_precision + tax_full_
    precision` -- a SEPARATE full-precision path from the one that produced
    the already-rounded `discount_amount` callers actually see and sum, so
    the two could disagree by a fils. Returns a dict of currency-quantized
    floats; callers that sum many lines should re-round the final sum with
    the same currency quantum rather than trust float addition to stay
    exact, but the per-line identity above now holds exactly, so a sum of
    per-line identities is itself an identity (Sigma(gross_i - discount_i +
    tax_i) == Sigma(gross_i) - Sigma(discount_i) + Sigma(tax_i)).
    """
    mode = normalize_mode(mode)
    discount_pct = clamp_discount_pct(discount_pct)

    price = _d(unit_price)
    qty = _d(quantity)
    disc_pct = _d(discount_pct)
    rate = _d(tax_rate)
    q = currency_quantum(currency)

    # Each of these three is rounded EXACTLY ONCE, from full precision.
    gross = (price * qty).quantize(q, rounding=ROUND_HALF_UP)
    discount_amount = (gross * (disc_pct / Decimal(100))).quantize(q, rounding=ROUND_HALF_UP)

    if mode == TAX_INCLUSIVE:
        # `gross` and `discount_amount` above are INCLUSIVE figures here. Tax is
        # carved out of the net: taxable is the ONE rounded step, tax is the exact
        # remainder, so taxable + tax == total with no second rounding to drift.
        total = gross - discount_amount
        taxable_amount = (total / (Decimal(1) + rate / Decimal(100))).quantize(q, rounding=ROUND_HALF_UP)
        tax = total - taxable_amount
    elif mode == TAX_BEFORE_DISCOUNT:
        taxable_amount = gross
        tax = (gross * (rate / Decimal(100))).quantize(q, rounding=ROUND_HALF_UP)
        total = gross - discount_amount + tax
    else:  # TAX_AFTER_DISCOUNT
        taxable_amount = gross - discount_amount
        tax = (taxable_amount * (rate / Decimal(100))).quantize(q, rounding=ROUND_HALF_UP)
        total = taxable_amount + tax

    return {
        "gross": float(gross),
        "discount_amount": float(discount_amount),
        "taxable_amount": float(taxable_amount),
        "tax": float(tax),
        "total": float(total),
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

    RECONCILIATION BY CONSTRUCTION -- same correction as calculate_line
    above, applied to this function's own formula (currently unused by any
    Python route, but blessed by this module's own docstring as one of only
    two formula-writers, so left broken it is a landmine for the next
    caller): `sub`/`disc`/`tax` are each rounded exactly once; `taxable_
    amount`/`total` are exact Decimal arithmetic on the rounded pieces, never
    independently re-rounded.
    """
    mode = normalize_mode(mode)
    q = currency_quantum(currency)
    sub = _d(subtotal).quantize(q, rounding=ROUND_HALF_UP)
    disc = _d(discount_amount)
    if disc < 0:
        disc = Decimal(0)
    if disc > sub:
        disc = sub
    disc = disc.quantize(q, rounding=ROUND_HALF_UP)
    rate = _d(tax_rate_pct)

    if mode == TAX_INCLUSIVE:
        # `sub` and `disc` are inclusive figures (see calculate_line).
        total = sub - disc
        taxable_amount = (total / (Decimal(1) + rate / Decimal(100))).quantize(q, rounding=ROUND_HALF_UP)
        tax = total - taxable_amount
    elif mode == TAX_BEFORE_DISCOUNT:
        taxable_amount = sub
        tax = (sub * (rate / Decimal(100))).quantize(q, rounding=ROUND_HALF_UP)
        total = sub - disc + tax
    else:  # TAX_AFTER_DISCOUNT
        taxable_amount = sub - disc
        tax = (taxable_amount * (rate / Decimal(100))).quantize(q, rounding=ROUND_HALF_UP)
        total = taxable_amount + tax

    return {
        "discount_amount": float(disc),
        "taxable_amount": float(taxable_amount),
        "tax": float(tax),
        "total": float(total),
    }


def header_is_tax_inclusive(subtotal, discount_amount, tax_amount, total, currency=None) -> bool:
    """Whether a stored sale HEADER was priced TAX_INCLUSIVE.

    No column records the mode (and none may be added by TAXINC), but the header
    tells on itself: inclusive stores shelf-price terms, so subtotal - discount ==
    total with the tax already inside, while both exclusive modes have total =
    subtotal - discount + tax. They only coincide when the tax is zero -- and then
    nothing about the document depends on the answer. Used by receipts to say
    "includes tax" instead of adding the tax a second time."""
    try:
        half = currency_quantum(currency) / 2
        tax = _d(tax_amount)
        return tax > half and abs(_d(subtotal) - _d(discount_amount) - _d(total)) <= half
    except Exception:
        return False


def historical_line_mode(current_mode, unit_price, quantity, discount_pct, tax_rate,
                         stored_taxable, currency=None) -> str:
    """The mode a STORED sale line was actually priced under, for re-pricing it
    now (a return, a credit note, a product report).

    WHY THIS EXISTS (TAXINC): `sale_items` records unit_price, discount_pct,
    tax_rate and line_total (the TAXABLE amount) but not the tax mode, and the
    mode is a company setting the owner can flip. Every reader that re-prices a
    sold line with the CURRENT mode therefore refunds a different amount than
    was paid the moment the owner switches -- harmless-ish between the two
    exclusive modes (they differ only by a discount's tax), wrong by up to
    ~14% of the line the moment inclusive is involved. No schema column may be
    added by this work, so the mode is RECOVERED from what is stored: price the
    line under the candidate modes and keep the one whose taxable amount
    equals the stored `line_total`.

    SCOPE, ON PURPOSE: only the inclusive/exclusive boundary is inferred. Two
    exclusive modes are never told apart (as before, the current one wins), so
    pre-TAXINC behaviour is unchanged for every install that never uses
    inclusive. Candidates are tried current-mode first; if nothing matches
    (a line from a divergent history) the current mode is returned, i.e.
    exactly what the old code did.

    KNOWN LIMIT: a line so small that its taxable amount comes out identical
    under both formulas (a few fils at 10%) cannot be told apart; the current
    mode wins and the re-priced total can be 1 fil off on such a line.
    """
    cur = normalize_mode(current_mode)
    if stored_taxable is None:
        return cur
    try:
        rate = _d(tax_rate)
        if rate == 0:
            return cur  # no tax: every mode prices the line identically
        stored = _d(stored_taxable)
        half = currency_quantum(currency) / 2
    except Exception:
        return cur
    others = (TAX_AFTER_DISCOUNT, TAX_BEFORE_DISCOUNT) if cur == TAX_INCLUSIVE else (TAX_INCLUSIVE,)
    for m in (cur,) + others:
        calc = calculate_line(unit_price, quantity, discount_pct, tax_rate, mode=m, currency=currency)
        if abs(_d(calc["taxable_amount"]) - stored) < half:
            return m
    return cur
