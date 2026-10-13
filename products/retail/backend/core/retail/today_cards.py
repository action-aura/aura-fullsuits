"""Aura Retail -- Today: the pure card-shaping behind GET /reports/today.

NO DATABASE, NO CLOCK, NO FLASK in here. `api/retail_api.py` runs the queries
(the `_today_*` helpers) and hands the figures to these functions, which only
decide SHAPE and ORDER: which cards are open, which resolved into "all clear"
rows, in what order the open ones are shown, how a percentage is stated.
Keeping that apart is what lets the ordering and the "is it really zero?"
rules be tested on bare numbers, and it keeps the route the only place that
can disclose money (so the capability ratchet has one thing to watch).

WHAT THE SERVER DOES NOT DO: write a sentence. Every card carries kinds,
counts, amounts and short rows; the client builds the wording with t() so the
Arabic is not a translation of an English string with the number baked in.

THE RULES THAT MATTER
  * A percentage is a number with ONE decimal, or None when it has no base.
    "No sales yesterday by this hour" must read as "no comparison", never as
    "+0%" or "+infinity" -- hence None, not 0.
  * A card exists only while something needs the owner. When it resolves it
    leaves `cards` and becomes an `all_clear` row with an honest detail
    ("owe 450.000, none over 30 days") -- a calm screen is only trustworthy if
    it can say WHY it is calm.
  * Money cards are ordered by what is at stake, biggest first; a tie goes to
    the fixed order collect > cheques > restock > drawers so the screen does
    not reshuffle on equal figures. Cards with no stake (tax, branches) follow.
"""
from decimal import ROUND_HALF_UP, Decimal

#: How many rows a card shows; the rest is `more`.
MAX_CARD_ROWS = 3

#: Tie-break for equal stakes, and the order of the all-clear rows.
MONEY_KIND_ORDER = ('collect', 'cheques', 'restock', 'drawers')
ALL_CLEAR_ORDER = MONEY_KIND_ORDER + ('tax', 'branches', 'pin_locked', 'backup')

#: Cards that belong to THIS computer only (cash sessions and the e-invoice
#: outbox do not sync). Everything else is shop-wide.
THIS_COMPUTER_KINDS = frozenset({'drawers', 'tax', 'pin_locked', 'backup'})


def scope_of(kind):
    return 'this_computer' if kind in THIS_COMPUTER_KINDS else 'shop'


def one_decimal(value):
    """A percentage to ONE decimal place, half-up (Decimal, not round())."""
    return float(Decimal(str(value)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))


def pct_change(now_value, before_value):
    """Percent change of `now_value` against `before_value`; None when there is
    no base to compare with (before <= 0)."""
    if before_value is None or float(before_value) <= 0:
        return None
    return one_decimal((float(now_value) - float(before_value)) * 100.0 / float(before_value))


def share_pct(part, total):
    """`part` as a percent of `total`; None when the total is not positive."""
    if total is None or float(total) <= 0:
        return None
    return one_decimal(float(part) * 100.0 / float(total))


def margin_pct(profit, revenue):
    """Gross margin as a percent of revenue; None when revenue is not positive."""
    if revenue is None or float(revenue) <= 0:
        return None
    return one_decimal(float(profit) * 100.0 / float(revenue))


def build_card(kind, count, rows, stake=None, amount=None, extra=None):
    """One OPEN card. `rows` is the full ordered list; at most MAX_CARD_ROWS
    are shown and `more` says how many were cut."""
    shown = list(rows)[:MAX_CARD_ROWS]
    return {
        'kind': kind,
        'state': 'open',
        'scope': scope_of(kind),
        'stake': stake,
        'count': count,
        'amount': amount,
        'rows': shown,
        'more': max(int(count) - len(shown), 0),
        'extra': dict(extra or {}),
    }


def order_cards(cards):
    """Money cards by stake, largest first, ties by MONEY_KIND_ORDER; then the
    stake-less cards (tax, branches) in ALL_CLEAR_ORDER."""
    def money_key(card):
        return (-float(card['stake']), MONEY_KIND_ORDER.index(card['kind']))
    money = [c for c in cards if c['stake'] is not None]
    other = [c for c in cards if c['stake'] is None]
    money.sort(key=money_key)
    other.sort(key=lambda c: ALL_CLEAR_ORDER.index(c['kind']))
    return money + other


def build_all_clear(resolved):
    """`resolved` maps kind -> detail dict for every kind that is NOT open;
    returns the rows in the fixed order. A kind with detail None is left out
    (nothing honest to say about it, e.g. e-invoicing absent from the build)."""
    return [{'kind': kind, 'detail': dict(resolved[kind])}
            for kind in ALL_CLEAR_ORDER
            if kind in resolved and resolved[kind] is not None]
