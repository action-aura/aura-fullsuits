"""
Aura Retail -- split tender (one sale paid by several methods). Pure
functions, no database access, no Flask -- matching returns_settlement.py /
pricing.py / po_split.py in this package. Imported by api/retail_api.py's
create_sale() only.

WHY THIS NEEDS NO SCHEMA: `payments` is already one row per money movement
(retail_api._record_payment), the drawer's cash_sales sums payments rows with
method='cash', and `sales.payment_method` is free TEXT. A sale paid by card
5.000 + cash 6.832 is therefore simply TWO payments rows against one sale and
`sales.payment_method = 'split'`. This module only decides what each of those
rows is worth (`settle_tenders`) and refuses nonsense before anything is
written (`normalize_tenders`).

WHAT COUNTS AS WHAT:
  * a tender's `amount` is the money HANDED OVER with that method. Only cash
    can be handed over in excess (it comes back as change); a card or mobile
    payment is charged for exactly what it is.
  * 'credit' is "on account": the amount that stays owing. It is only ever the
    remainder, so total_in == due exactly when credit is present -- which keeps
    balance_due == the credit part and every downstream gate (customer
    required, credit limit, AR, statement, GL) reading the same number it
    reads for an ordinary credit sale.
  * the rows returned for `payments` are what was RETAINED: cash minus change,
    other methods as given, nothing for credit (that is AR, not a payment).
    Invariant: sum(rows) == min(paid, due) == the sale's net received.
"""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import re

from core.retail import pricing as tax_engine

MAX_TENDERS = 6
_METHOD_RE = re.compile(r'^[a-z][a-z0-9 _-]{0,31}$')
# 'split' is the sale-level label, never a tender; 'store_credit' is a RETURN
# payout channel and has no meaning on the way in.
_RESERVED = ('split', 'store_credit')
_FALLBACK_QUANTUM = Decimal('0.01')


def _fail(code, message):
    return None, code, message


def normalize_tenders(raw):
    """Validate the shape of a `tenders` list.

    Returns (tenders, None, None) on success -- `tenders` a list of
    {'method': str, 'amount': Decimal} -- or (None, code, message).
    """
    if not isinstance(raw, (list, tuple)) or not 1 <= len(raw) <= MAX_TENDERS:
        return _fail('TENDERS_INVALID',
                     f'Send between 1 and {MAX_TENDERS} payment parts.')
    out = []
    seen = set()
    for entry in raw:
        if not isinstance(entry, dict):
            return _fail('TENDERS_INVALID', 'Each payment part must have a method and an amount.')
        method = str(entry.get('method') or '').strip().lower()
        if not _METHOD_RE.match(method) or method in _RESERVED:
            return _fail('TENDER_METHOD_INVALID',
                         'One of the payment methods is not valid.')
        try:
            amount = Decimal(str(entry.get('amount')))
        except (InvalidOperation, ValueError, TypeError):
            return _fail('TENDERS_INVALID', 'Each payment part needs a valid amount.')
        if not amount.is_finite() or amount <= 0:
            return _fail('TENDERS_INVALID', 'Each payment part must be more than zero.')
        if method in seen:
            return _fail('TENDER_DUPLICATE_METHOD',
                         'Each payment method can be used once. Combine the amounts.')
        seen.add(method)
        out.append({'method': method, 'amount': amount})
    return out, None, None


def settle_tenders(tenders, amount_due, currency=None):
    """Work out what a normalised `tenders` list is worth against `amount_due`.

    Returns a dict {'ok': True, 'paid', 'change', 'balance_due', 'rows'} (all
    Decimal; rows = [(method, retained)]) or {'ok': False, 'code', 'message'}.
    """
    quant = tax_engine.currency_quantum(currency) if currency else _FALLBACK_QUANTUM
    eps = quant / 2
    due = Decimal(str(amount_due)).quantize(quant, rounding=ROUND_HALF_UP)

    cash = Decimal(0)
    credit = Decimal(0)
    others = []
    for t in tenders:
        amt = t['amount'].quantize(quant, rounding=ROUND_HALF_UP)
        if amt < quant:
            return {'ok': False, 'code': 'TENDERS_INVALID',
                    'message': 'A payment part is smaller than the smallest coin.'}
        if t['method'] == 'cash':
            cash += amt
        elif t['method'] == 'credit':
            credit += amt
        else:
            others.append((t['method'], amt))
    other_sum = sum((a for _, a in others), Decimal(0))
    paid = cash + other_sum
    total_in = paid + credit

    if total_in < due - eps:
        short = due - total_in
        return {'ok': False, 'code': 'TENDERS_SHORT',
                'message': f'The parts add up to {total_in} but {due} is due. {short} is still missing.'}
    over = total_in - due
    if credit > 0 and over > eps:
        return {'ok': False, 'code': 'TENDER_CREDIT_OVERPAID',
                'message': 'The part on account is more than what is left to pay.'}
    if over > eps and over > cash + eps:
        return {'ok': False, 'code': 'TENDER_OVERPAID_NON_CASH',
                'message': 'Only cash can be more than what is due. Lower the card or mobile amount.'}
    change = over if over > eps else Decimal(0)
    if cash > 0 and cash - change <= eps:
        return {'ok': False, 'code': 'TENDER_CASH_NOT_NEEDED',
                'message': 'The cash part would all come back as change. Remove it.'}

    # Input order is kept so the payments rows read in the order the cashier
    # took them. Methods are unique here (normalize_tenders refuses dups).
    rows = []
    for t in tenders:
        m = t['method']
        if m == 'credit':
            continue
        if m == 'cash':
            rows.append(('cash', cash - change))
        else:
            rows.append((m, t['amount'].quantize(quant, rounding=ROUND_HALF_UP)))
    balance_due = max(Decimal(0), due - paid)
    return {'ok': True, 'paid': paid, 'change': change,
            'balance_due': balance_due, 'rows': rows}
