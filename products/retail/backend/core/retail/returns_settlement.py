"""
Aura Retail -- return settlement split (DEFECT 1 fix, launch-readiness
money-reconciliation pass). Pure function, no database access, no Flask --
matching pricing.py/cheques.py/po_split.py/promotions.py in this package.
Imported by api/retail_api.py's create_return() only.

THE BUG THIS MODULE CLOSES: `returns.refund_amount` is (correctly) the
tax-inclusive VALUE of the goods that came back -- core/retail/metrics.py's
own canonical rule is `revenue = SUM(sales.total) - SUM(returns.refund_amount)`,
and this module never touches that number or its meaning. The bug was that
the SAME number was also trusted, unmodified, as "cash that physically left
the drawer" by api/retail_api.py's `_cash_session_report`. Those are two
different facts -- "what was the customer made whole for" versus "what
physically left this till" -- and conflating them let a walk-in's unpaid
credit balance (correctly forgiven via `_adjust_credit`) ALSO get paid out
a second time as phantom cash, because nothing tied the two together.

THE FIX: split a return's recomputed value into three parts that sum back
to it whenever the sale ever recorded enough consideration to cover the
refund -- see the DEFECT-1B note below for the one case where they don't:

  1. tender_refund -- real money paid back, hard-capped by the caller at
     what THIS sale actually collected in tender (its own `payments` rows,
     net of change, minus tender already paid out on earlier partial
     returns of the same sale), regardless of which method the cashier
     routes the payout through.
  2. ar_forgiven -- whatever value is left after (1) is applied to erase
     this sale's own still-outstanding debt, capped at the customer's live
     `credit_balance` exactly as the pre-fix code already did.
  3. store_credit -- anything still left over is issued as credit on the
     same `credit_balance` column, negative, rather than vanishing or
     being paid out as phantom cash. Legitimate only when it traces back
     to consideration THIS SALE actually recorded, from either of two
     sources: (a) debt the sale carried that's since been paid down some
     other way (the pre-existing case: `current_balance <
     balance_due_remaining`), or (b) tender the sale actually collected
     that the cashier explicitly declined to hand back via
     `requested_method='store_credit'` (real money the shop holds, just
     routed to credit instead of cash -- see the paragraph below on that
     method). Capped so the SUM of all three parts never exceeds this
     sale's total recorded consideration -- see DEFECT-1B below.

DEFECT-1B (2026-09-16): store_credit used to absorb the ENTIRE remainder
after (1) and (2) with nothing tying it to consideration the shop ever
actually recorded. A sale create_sale itself never treated as AR --
because create_sale's own `balance_due > 0.005` gate didn't fire, which
happens for a sub-quantum JOD sale (total 0.006, amount_paid 0.001 leaves
balance_due exactly 0.005, not > 0.005) -- collected no `payments` row and
no `credit_balance` entry, i.e. `tender_available == 0` and
`balance_due_remaining == 0.005` (the RAW total-minus-paid difference,
which create_return computes independently of what create_sale actually
granted -- see create_return's own `original_balance_due` comment). With
the old code, `store_credit = remainder - ar_forgiven` still minted 0.006
of NEGATIVE credit_balance out of a sale the shop recorded as a complete
no-op.

The fix caps the SUM of all three parts at `settleable = min(refund,
tender_available + balance_due_remaining)` -- this sale's total recorded
consideration -- rather than capping store_credit on
`balance_due_remaining` alone. That distinction matters: an earlier draft
of this fix capped store_credit at `balance_due_remaining - ar_forgiven`
only, which is correct for source (a) above but WRONG for source (b) --
it broke the pre-existing, deliberately-pinned
test_store_credit_refund_method_takes_the_whole_value_off_tender (refund
100, tender_available 20, requested_method='store_credit',
balance_due_remaining 80 == current_balance 80): ar_forgiven consumes all
80 of balance_due_remaining, so a `balance_due_remaining - ar_forgiven`
cap forces store_credit to 0 -- silently destroying the 20 of tender the
sale genuinely collected and the cashier chose to convert to credit,
which is worse than the defect being fixed (money vanishes instead of
merely being misrouted). Capping the TOTAL at `settleable` instead lets
`tender_refund` and `ar_forgiven` claim their shares first;
`store_credit` only ever gets what's left of `settleable`, from whichever
source, so both (a) and (b) stay correct while a sale with
`tender_available == 0 and balance_due_remaining == 0` (settleable == 0)
can still never produce store_credit, no matter how large the leftover
arithmetic remainder is.

THE INVARIANT THIS WEAKENS: the three parts no longer always sum to
`refund` -- only to `min(refund, tender_available + balance_due_remaining)`,
i.e. never more than the consideration this sale actually recorded (tender
collected plus debt it was ever charged with). See
retail_returns_settlement_test.py's test_three_parts_always_sum_to_refund
for the restated property and exactly what it can no longer catch.

`requested_method='store_credit'` is the one case where the CALLER's choice
overrides the tender pool rather than just routing it: an operator who
explicitly asks for store credit gets zero tender payout even if the pool
has room, so a refund can never be paid out in cash against the customer's
own wishes.

All arithmetic is Decimal, quantized by the caller's `currency` (matching
create_return's own `refund_total` handling) -- see the module-level
`_Q` fallback below, used only when no currency is supplied, for the same
"historical 2dp behaviour byte for byte" contract `_money`/`_adjust_credit`
already carry.
"""
from decimal import Decimal, ROUND_HALF_UP

from core.retail import pricing as tax_engine

_FALLBACK_QUANTUM = Decimal('0.01')


def split_return_settlement(refund, tender_available, requested_method,
                             balance_due_remaining, current_balance,
                             has_customer, currency=None):
    """Split a return's `refund` value into (tender_refund, ar_forgiven,
    store_credit); the three sum to `min(refund, tender_available +
    balance_due_remaining)` (to within the currency's own quantum) --
    NOT unconditionally to `refund` -- see the module docstring's DEFECT-1B
    note for why store_credit is capped at this sale's own recorded,
    unforgiven debt rather than absorbing whatever is left over.

    `tender_available` -- how much of THIS sale's own tender pool remains
    unclaimed by earlier returns against the same sale; may be 0 (a fully
    credit sale, or a pool already drained by prior partial returns).
    `requested_method` -- the cashier's chosen payout channel; 'store_credit'
    is the one value that changes the split (see module docstring), every
    other value is purely a routing label for the caller's own
    `refund_method` column and does not affect the amounts computed here.
    `balance_due_remaining` -- this sale's own still-outstanding debt (its
    original balance_due minus AR already forgiven by earlier partial
    returns of this same sale).
    `current_balance` -- the customer's LIVE credit_balance right now
    (may be lower than `balance_due_remaining` if they paid some of it off
    another way since the sale -- see module docstring's `store_credit`
    case).
    `has_customer` -- False for a walk-in; forces ar_forgiven to 0 (a
    walk-in cannot carry AR at all, so any leftover after the tender cap
    is store_credit, handled defensively by the caller -- see
    create_return's own 409 guard for why this is provably unreachable
    today).
    """
    quant = tax_engine.currency_quantum(currency) if currency else _FALLBACK_QUANTUM
    refund_d = Decimal(str(refund or 0))
    tender_available_d = max(Decimal('0'), Decimal(str(tender_available or 0)))
    balance_due_remaining_d = max(Decimal('0'), Decimal(str(balance_due_remaining or 0)))
    current_balance_d = max(Decimal('0'), Decimal(str(current_balance or 0)))

    if requested_method == 'store_credit':
        tender_refund_d = Decimal('0')
    else:
        tender_refund_d = min(refund_d, tender_available_d)

    remainder_d = refund_d - tender_refund_d

    if has_customer:
        ar_forgiven_d = min(remainder_d, balance_due_remaining_d, current_balance_d)
    else:
        ar_forgiven_d = Decimal('0')

    # DEFECT-1B cap (see module docstring): store_credit's only legitimate
    # sources are (a) debt THIS SALE actually recorded (`balance_due_
    # remaining`) that (2) above hasn't already forgiven -- e.g. the
    # customer paid it off some other way -- and (b) tender THIS SALE
    # actually collected (`tender_available`) that the cashier explicitly
    # declined to hand back via `requested_method='store_credit'` (still
    # real money the shop holds, just routed to credit instead of cash).
    # Never the bare arithmetic remainder. `settleable` is the sale's total
    # recorded consideration across BOTH sources; capping the SUM of all
    # three parts at it (rather than capping store_credit on
    # balance_due_remaining alone) is what makes both sources legitimate
    # at once -- a cap using only balance_due_remaining would zero out
    # case (b) above whenever ar_forgiven already exhausted
    # balance_due_remaining, silently destroying real collected tender the
    # cashier chose to convert to credit (see
    # test_store_credit_refund_method_takes_the_whole_value_off_tender).
    # Without this cap, a sale with zero recorded tender AND zero recorded
    # debt (e.g. create_sale's own sub-quantum-balance_due gate deciding a
    # sale was a complete no-op) could still mint negative store_credit
    # purely because `refund` (recomputed independently from the sale's
    # line items) came out larger than anything the shop ever recorded
    # collecting or owing. `tender_refund_d + ar_forgiven_d` never exceeds
    # `settleable_d` by construction (each is independently capped at its
    # own pool and at `refund`), so `max(Decimal('0'), ...)` is a
    # defensive floor, not a case this function is expected to hit.
    settleable_d = min(refund_d, tender_available_d + balance_due_remaining_d)
    store_credit_d = max(Decimal('0'), settleable_d - tender_refund_d - ar_forgiven_d)

    tender_refund = float(tender_refund_d.quantize(quant, rounding=ROUND_HALF_UP))
    ar_forgiven = float(ar_forgiven_d.quantize(quant, rounding=ROUND_HALF_UP))
    store_credit = float(store_credit_d.quantize(quant, rounding=ROUND_HALF_UP))
    return tender_refund, ar_forgiven, store_credit
