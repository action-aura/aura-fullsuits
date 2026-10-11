"""
Aura Retail -- refunding a split-paid sale through several methods (MONEY-3 M1, D2).
Pure functions, no database access, no Flask -- the same posture as
returns_settlement.py, which this module sits beside and does NOT replace.

THE BUG THIS CLOSES: a sale paid 30 cash + 70 card, refunded in full, used to give all 100 back
through the ONE method the cashier picked. Picking cash took 100 out of a drawer that only ever
took 30; picking card refunded the card with 100 of which only 70 was ever charged to it. Either
way the drawer or the card side ended wrong.

WHAT A LEG IS. `returns_settlement.split_return_settlement` still decides HOW MUCH goes back as
money (`tender_refund`), as debt forgiven (`ar_forgiven`) and as store credit (`store_credit`);
that function, its cap and every test pinning it are untouched. A LEG is only WHERE the
`tender_refund` part is paid out: (method, amount) rows whose amounts sum to `tender_refund`. The
on-account part is not a leg -- it reduces AR through `ar_forgiven_amount` / `store_credit_amount`
and can never reduce cash, which is exactly what the cash drawer's cash-only legs guarantee.

REFUNDABLE REMAINDER. For each method the sale was paid with: what the sale took through that
method, minus what EARLIER returns of the same sale already paid back through it (their legs).
A leg may not exceed its method's remainder (to within the currency's epsilon).

DEFAULT LEGS: the tender part split across the methods in proportion to their remainders; the
rounding residue goes on the largest leg, so the legs always sum to the tender part exactly.

Every figure is Decimal, quantized by the caller's currency.
"""
from decimal import Decimal, ROUND_HALF_UP

from core.retail import pricing as tax_engine

CODE_SUM = 'RETURN_LEGS_SUM'
CODE_METHOD_NOT_ON_SALE = 'RETURN_LEG_METHOD_NOT_ON_SALE'
CODE_EXCEEDS = 'RETURN_LEG_EXCEEDS_REMAINDER'
CODE_BAD_LEG = 'RETURN_LEG_INVALID'


def _quant(currency):
    return tax_engine.currency_quantum(currency)


def remainders(paid_by_method, refunded_by_method):
    """[(method, remainder)] in the order the sale took them, floored at 0 and dropping methods
    with nothing left. `paid_by_method` is an ordered list of (method, amount)."""
    out = []
    for method, paid in paid_by_method:
        left = Decimal(str(paid or 0)) - Decimal(str(refunded_by_method.get(method, 0) or 0))
        if left > 0:
            out.append((method, left))
    return out


def _fix_residue(legs, tender_refund, quant):
    """Put whatever rounding left over on the largest leg so the legs sum EXACTLY to the tender
    part. `legs` is a list of [method, Decimal amount]."""
    if not legs:
        return legs
    target = Decimal(str(tender_refund)).quantize(quant, rounding=ROUND_HALF_UP)
    residue = target - sum((amount for _, amount in legs), Decimal('0'))
    if residue != 0:
        big = max(range(len(legs)), key=lambda i: legs[i][1])
        legs[big][1] = (legs[big][1] + residue).quantize(quant, rounding=ROUND_HALF_UP)
    return legs


def default_legs(tender_refund, rem, currency, eps):
    """The tender part split in proportion to what each method still has refundable. Returns a
    list of {'method', 'amount'} (floats) or None when the methods' remainders cannot cover the
    tender part (a legacy return refunded through a method the sale never used can do that) --
    the caller then falls back to the single-method behaviour rather than inventing a split."""
    quant = _quant(currency)
    tender = Decimal(str(tender_refund or 0)).quantize(quant, rounding=ROUND_HALF_UP)
    if tender <= Decimal(str(eps)):
        return []
    total = sum((left for _, left in rem), Decimal('0'))
    if total + Decimal(str(eps)) < tender:
        return None
    legs = [[method, (tender * left / total).quantize(quant, rounding=ROUND_HALF_UP)]
            for method, left in rem]
    # The rounding residue lands on the largest leg (first one on a tie), so the legs sum exactly.
    _fix_residue(legs, tender, quant)
    legs = [lg for lg in legs if lg[1] > 0]
    return [{'method': m, 'amount': float(a)} for m, a in legs]


def validate_legs(requested, tender_refund, rem, sale_methods, currency, eps):
    """Check the cashier's legs. Returns (legs, None) with legs normalized (merged per method,
    summing exactly to the tender part) or (None, (code, plain message)).

    Three refusals, each in plain words (all 409): the legs do not add up to the money going
    back; a method the sale was never paid with; a method asked for more than it has left."""
    quant = _quant(currency)
    epsd = Decimal(str(eps))
    tender = Decimal(str(tender_refund or 0)).quantize(quant, rounding=ROUND_HALF_UP)
    if not isinstance(requested, (list, tuple)):
        return None, (CODE_BAD_LEG, 'The refund legs must be a list of method and amount.')
    merged = {}
    order = []
    for raw in requested:
        if not isinstance(raw, dict):
            return None, (CODE_BAD_LEG, 'Each refund leg needs a method and an amount.')
        method = str(raw.get('method') or '').strip().lower()
        try:
            amount = Decimal(str(raw.get('amount')))
        except Exception:
            return None, (CODE_BAD_LEG, 'Each refund leg needs a method and an amount.')
        if not method or amount != amount or amount < 0:
            return None, (CODE_BAD_LEG, 'Each refund leg needs a method and an amount of zero or more.')
        amount = amount.quantize(quant, rounding=ROUND_HALF_UP)
        if method not in merged:
            merged[method] = Decimal('0')
            order.append(method)
        merged[method] += amount
    legs = [[m, merged[m]] for m in order if merged[m] > epsd]
    for method, amount in legs:
        if method not in sale_methods:
            return None, (CODE_METHOD_NOT_ON_SALE,
                          f'This sale was not paid by "{method}", so it cannot be refunded that way.')
    total = sum((a for _, a in legs), Decimal('0'))
    if abs(total - tender) > epsd:
        return None, (CODE_SUM,
                      f'The refund parts add up to {total} but {tender} is going back as money. '
                      f'They must be equal.')
    left = {m: l for m, l in rem}
    for method, amount in legs:
        if amount > left.get(method, Decimal('0')) + epsd:
            return None, (CODE_EXCEEDS,
                          f'Only {left.get(method, Decimal("0"))} can still be refunded by "{method}" '
                          f'on this sale; {amount} was asked.')
    _fix_residue(legs, tender, quant)
    return [{'method': m, 'amount': float(a)} for m, a in legs], None
