"""
Aura Retail -- cash rounding to real coins (MONEY-5, review #3). Pure functions, no database
access, no Flask -- matching pricing.py / tenders.py / returns_settlement.py in this package.

WHY. Jordan has no 1-fil coin and the smallest coin people actually carry is far bigger than a
fil, so a total of 3.457 cannot be paid in cash. Before this module the cashier either shorted the
drawer or kept 3 fils, and the Z-report variance absorbed it silently. Loyverse, Odoo and Shopify
POS all round the CASH tender by a rule; so does this.

THE RULE (decided, not configurable beyond the two settings):
  * `cash_rounding_interval` -- '0' (default: OFF, nothing changes) | '0.005' | '0.010' | '0.050' |
    '0.100'. The coin the shop rounds to.
  * `cash_rounding_rule` -- 'nearest' (default; a tie rounds UP, away from zero) | 'down' | 'up'.
  * It is applied ONLY to the cash part that settles what is left to pay. A card or mobile amount
    is charged for exactly what it is and is never rounded; a part left on account is a debt, not a
    payment, and is never rounded either. create_sale decides WHEN (the cash handed over must cover
    the rounded amount); this module only does the arithmetic.
  * The sale itself is untouched: `sales.total`, the lines and the tax stay whole (the invoice, and
    what is filed with the tax authority, are the real figures). The difference between what the
    cash part was worth and the coins that changed hands is stored on the sale as the signed
    `sales.rounding_amount` (3.457 settled with 3.450 -> -0.007) and booked to GL 4950.

An interval finer than the currency's own smallest unit changes nothing (every amount is already a
multiple of it), and a bad or unknown setting value means OFF -- never a guess.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, InvalidOperation

SETTING_INTERVAL = 'cash_rounding_interval'
SETTING_RULE = 'cash_rounding_rule'

VALID_INTERVALS = ('0', '0.005', '0.010', '0.050', '0.100')
VALID_RULES = ('nearest', 'down', 'up')
DEFAULT_INTERVAL = '0'
DEFAULT_RULE = 'nearest'

_OFF = Decimal('0')


def parse_interval(raw):
    """The interval as a Decimal; 0 (= off) for anything that is not one of VALID_INTERVALS.
    '0.05' and '0.050' are the same coin, so the comparison is numeric."""
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        return _OFF
    if not value.is_finite():
        return _OFF
    return value if any(value == Decimal(v) for v in VALID_INTERVALS) else _OFF


def parse_rule(raw):
    rule = str(raw or '').strip().lower()
    return rule if rule in VALID_RULES else DEFAULT_RULE


def canonical_interval(raw):
    """The text form a settings write stores ('0.05' -> '0.050'), or None if `raw` is not valid.
    Storing one canonical spelling keeps the settings screen's <select> and the sync of the
    setting (last-writer-wins on the text) from disagreeing about the same coin."""
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        return None
    for v in VALID_INTERVALS:
        if value.is_finite() and value == Decimal(v):
            return v
    return None


def config(settings):
    """(interval, rule) from a settings dict. interval 0 = off."""
    s = settings or {}
    return parse_interval(s.get(SETTING_INTERVAL, DEFAULT_INTERVAL)), parse_rule(s.get(SETTING_RULE))


def round_cash(amount, interval, rule, quantum, never_zero=False):
    """`amount` rounded to a multiple of `interval` by `rule`, quantized to the currency's own
    smallest unit `quantum`. `interval` 0 returns `amount` quantized and otherwise unchanged.

    Never returns a negative number for a non-negative amount. 'down' (and 'nearest' on a tiny
    amount) can take a small positive amount to 0: 0.099 'down' to a 0.100 coin is FREE. A SALE must
    never be charged nothing for goods that cost something, so the sale paths pass `never_zero=True`
    and a positive amount that would round to 0 is charged ONE interval instead (verifier LOW). The
    default stays False for a refund: a return's cash is rounded on the running total of the sale
    (create_return), where an intermediate 0 is correct and the coin arrives with a later part."""
    amount = Decimal(str(amount))
    if interval <= 0:
        return amount.quantize(quantum, rounding=ROUND_HALF_UP)
    units = amount / interval
    mode = {'nearest': ROUND_HALF_UP, 'down': ROUND_FLOOR, 'up': ROUND_CEILING}.get(rule, ROUND_HALF_UP)
    rounded = units.quantize(Decimal(1), rounding=mode) * interval
    rounded = max(rounded, _OFF).quantize(quantum, rounding=ROUND_HALF_UP)
    if never_zero and amount > 0 and rounded == 0:
        return interval.quantize(quantum, rounding=ROUND_HALF_UP)
    return rounded
