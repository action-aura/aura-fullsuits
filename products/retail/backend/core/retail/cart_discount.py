"""
Aura Retail -- cart-level AMOUNT discounts and "make the total X" (UI-3a).

PURE module: no database, no Flask, no clock. `create_sale` hands it the lines it
has already priced and a `calc_line` callable (a thin wrapper over
`core.retail.pricing.calculate_line` with the shop's tax mode and currency bound),
and gets back, per line, the `discount_pct` to charge and the resulting
`calculate_line` figures.

WHY THE MONEY IS CARRIED AS A PERCENTAGE PER LINE (design decision, not drift):
the sale already stores one truthful number per line -- `sale_items.discount_pct`
-- and returns, receipts, reports and the sync payload all derive from it. An
amount discount "JD 0.650 off" is therefore SPREAD over the lines in proportion to
what each costs, and each line's share is turned back into the percentage that
makes `calculate_line` take exactly that share off. No new money column exists,
so no reader can disagree with a second source.

WHY EXACT, AND HOW:
  * Everything here is in MINOR UNITS (integers: fils for JOD). The requested
    amount must be representable in them (a client asking for 0.6501 JD of a
    3-decimal currency is refused, not rounded -- a rounded discount is money the
    shop gave away without being asked).
  * Shares are proportional to each line's PRE-discount net (`gross`), rounded
    half-up, and the rounding residue lands on the largest line (first on a tie) --
    the same rule `core.retail.return_legs.default_legs` uses for refund legs, so
    the two features split money the same way. A residue that would push a line
    past its own gross (tiny lines, huge discount) spills to the next largest.
  * Each line's pct is the exact Decimal fraction share/gross*100 carried at full
    float precision, then PUSHED THROUGH THE REAL calculate_line, never
    re-derived: what is stored is what the pricing module computed.
  * The sum of the lines' real `discount_amount` is then ASSERTED equal to the
    requested amount. If a line's rounding ever drifts, the residue line is nudged
    by +-1 minor unit (bounded) until exact, else the request is refused with
    DISCOUNT_NOT_REACHABLE. Today the exact fraction never drifts; the loop is the
    guarantee that a future change to calculate_line's rounding cannot silently
    make the till charge a different figure than the one the cashier agreed.

"MAKE THE TOTAL X": the total (incl. tax) is a step function of the discount, so
it is SOLVED rather than inverted: bisect to where the total first reaches X, scan a
bounded window of minor units around it using the real pricing at every step, and take the
SMALLEST discount that lands exactly on X (least given away). If no discount in the
window lands on X exactly (rounding can skip a total), the request is refused with
TARGET_NOT_REACHABLE and the nearest reachable totals on each side.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

#: How many minor units either side of the bisected point the target solver scans.
#: Rounding makes the total wobble by a fil or two against the discount, so the exact
#: hit (if any) is within a few units of where the total first drops to the target; 32
#: is a wide margin and the scan costs 65 pricing passes at most.
TARGET_SCAN_WINDOW = 32

#: Bound on the +-1 nudge loop. Drift larger than a handful of minor units would
#: mean calculate_line changed shape, not that rounding wobbled -- refuse instead.
MAX_NUDGES = 8

CODE_NOT_REACHABLE = 'DISCOUNT_NOT_REACHABLE'
CODE_BAD_AMOUNT = 'DISCOUNT_INVALID_AMOUNT'
CODE_TARGET_NOT_REACHABLE = 'TARGET_NOT_REACHABLE'
CODE_BAD_TARGET = 'TARGET_INVALID'


class CartDiscountError(Exception):
    """A refusal with a stable `code`, a plain-words `message` and optional `data`."""

    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}


def _dec(x):
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _minor(amount: Decimal, quant: Decimal) -> int:
    return int((amount / quant).to_integral_value(rounding=ROUND_HALF_UP))


def parse_amount(value, quant: Decimal, code=CODE_BAD_AMOUNT, what='discount amount'):
    """`value` as an exact Decimal on the currency's minor-unit grid, or raise.

    A bool, NaN, infinity, non-number or a figure finer than the minor unit is
    refused. Never rounded: see the module docstring."""
    if isinstance(value, bool) or value is None:
        raise CartDiscountError(code, f'The {what} must be a number.')
    try:
        d = Decimal(str(value))
    except Exception:
        raise CartDiscountError(code, f'The {what} must be a number.')
    if not d.is_finite():
        raise CartDiscountError(code, f'The {what} must be a number.')
    if d != d.quantize(quant, rounding=ROUND_HALF_UP):
        raise CartDiscountError(code, f'The {what} has more decimals than this currency uses.')
    return d.quantize(quant, rounding=ROUND_HALF_UP)


def distribute_minor(gross_minor, discount_minor):
    """Split `discount_minor` over lines of `gross_minor` (ints) in proportion to
    each, residue to the largest line (first on a tie), spilling when a line is
    full. Returns the per-line shares; they sum to `discount_minor` exactly."""
    n = len(gross_minor)
    total = sum(gross_minor)
    if n == 0 or total <= 0 or discount_minor < 0 or discount_minor > total:
        raise CartDiscountError(CODE_NOT_REACHABLE, 'That discount does not fit this cart.')
    shares = [(2 * discount_minor * g + total) // (2 * total) for g in gross_minor]
    residue = discount_minor - sum(shares)
    order = sorted(range(n), key=lambda i: (-gross_minor[i], i))
    for i in order:
        if residue == 0:
            break
        if residue > 0:
            take = min(residue, gross_minor[i] - shares[i])
        else:
            take = -min(-residue, shares[i])
        shares[i] += take
        residue -= take
    if residue != 0 or sum(shares) != discount_minor:
        raise CartDiscountError(CODE_NOT_REACHABLE, 'That discount does not fit this cart.')
    return shares, order[0]


def _price_with_shares(lines, shares, gross_minor, quant, calc_line):
    pcts, calcs = [], []
    for (price, qty, rate), share, g in zip(lines, shares, gross_minor):
        if g <= 0 or share <= 0:
            pct = 0.0
        else:
            pct = float(Decimal(share) / Decimal(g) * Decimal(100))
        pcts.append(pct)
        calcs.append(calc_line(price, qty, pct, rate))
    return pcts, calcs


def apply_cart_discount(lines, discount: Decimal, quant: Decimal, calc_line, zero_calcs=None):
    """Spread `discount` (Decimal on the minor grid, ex-tax) over `lines`.

    lines     -- [(unit_price, quantity, tax_rate)] already priced by create_sale.
    calc_line -- f(unit_price, quantity, discount_pct, tax_rate) -> calculate_line dict.
    Returns (pcts, calcs, shares_minor): per-line pct to charge, the real
    calculate_line result at that pct, and the minor-unit share each line carries.
    Raises CartDiscountError. The returned calcs' discount_amounts sum EXACTLY to
    `discount` (asserted, nudged if a line drifted, else refused)."""
    zero_calcs = zero_calcs or [calc_line(p, q, 0, r) for p, q, r in lines]
    gross_minor = [_minor(_dec(c['gross']), quant) for c in zero_calcs]
    want = _minor(discount, quant)
    if want <= 0:
        raise CartDiscountError(CODE_BAD_AMOUNT, 'The discount must be more than zero.')
    if want > sum(gross_minor):
        raise CartDiscountError(
            CODE_BAD_AMOUNT, 'The discount cannot be more than the cart total before tax.',
            {'max_discount': float(Decimal(sum(gross_minor)) * quant)})
    shares, residue_line = distribute_minor(gross_minor, want)
    pcts, calcs = _price_with_shares(lines, shares, gross_minor, quant, calc_line)
    for _ in range(MAX_NUDGES + 1):
        got = sum(_minor(_dec(c['discount_amount']), quant) for c in calcs)
        if got == want:
            return pcts, calcs, shares
        # Drift: move the residue line by one minor unit toward the target.
        step = 1 if got < want else -1
        i = residue_line
        if not (0 <= shares[i] + step <= gross_minor[i]):
            break
        shares[i] += step
        pcts, calcs = _price_with_shares(lines, shares, gross_minor, quant, calc_line)
    raise CartDiscountError(
        CODE_NOT_REACHABLE,
        'That exact discount cannot be given on this cart. Try a slightly different amount.')


def _cart_total(calcs):
    return sum((_dec(c['total']) for c in calcs), Decimal('0'))


def solve_target_total(lines, target: Decimal, quant: Decimal, calc_line,
                       window=TARGET_SCAN_WINDOW):
    """The discount that makes the cart's total (incl. tax) exactly `target`.

    Returns (discount Decimal, pcts, calcs, shares). A target equal to the current
    total needs no discount: returns (Decimal(0), None, None, None) and the caller
    leaves the cart alone. Raises CartDiscountError(TARGET_NOT_REACHABLE) with
    `data={'nearest_below': .., 'nearest_above': ..}` when no discount in the
    scanned window lands exactly on `target`."""
    zero_calcs = [calc_line(p, q, 0, r) for p, q, r in lines]
    current = _cart_total(zero_calcs)
    if target <= 0:
        raise CartDiscountError(CODE_BAD_TARGET, 'The new total must be more than zero.')
    if target > current:
        raise CartDiscountError(CODE_BAD_TARGET, 'The new total cannot be higher than the current total.',
                                {'current_total': float(current)})
    if target == current:
        return Decimal('0'), None, None, None
    gross_minor = [_minor(_dec(c['gross']), quant) for c in zero_calcs]
    sub_minor = sum(gross_minor)
    # The total is (very nearly) non-increasing in the discount, so BISECT for the first
    # discount whose real total is <= target, then scan a small window around it. (An
    # ex-tax proportional estimate is only right when tax follows the discount; in
    # tax-before-discount mode the discount moves the total 1:1 and the estimate misses.)
    def total_at(d_minor):
        try:
            return _cart_total(apply_cart_discount(
                lines, Decimal(d_minor) * quant, quant, calc_line, zero_calcs)[1])
        except CartDiscountError:
            return None
    lo_b, hi_b = 1, sub_minor
    while lo_b < hi_b:
        mid = (lo_b + hi_b) // 2
        t = total_at(mid)
        if t is not None and t <= target:
            hi_b = mid
        else:
            lo_b = mid + 1
    est = lo_b
    lo, hi = max(1, est - window), min(sub_minor, est + window)
    below = above = None   # nearest reachable totals around `target`
    for d_minor in range(lo, hi + 1):
        try:
            pcts, calcs, shares = apply_cart_discount(
                lines, Decimal(d_minor) * quant, quant, calc_line, zero_calcs)
        except CartDiscountError:
            continue
        total = _cart_total(calcs)
        if total == target:
            return Decimal(d_minor) * quant, pcts, calcs, shares
        if total < target and (below is None or total > below):
            below = total
        if total > target and (above is None or total < above):
            above = total
    raise CartDiscountError(
        CODE_TARGET_NOT_REACHABLE,
        'That exact total cannot be reached. Pick one of the nearby totals.',
        {'nearest_below': float(below) if below is not None else None,
         'nearest_above': float(above) if above is not None else None})
