"""
Aura Retail -- return settlement split unit tests (DEFECT 1).

Pure unit tests of core/retail/returns_settlement.py's
split_return_settlement -- no DB, no Flask boot needed, matching this
directory's own retail_pricing_test.py Part A convention (a pure-function
module gets pure-function tests).

THE BUG THIS PINS SHUT: `returns.refund_amount` (the full recomputed value
of the goods returned) used to be paid out AS CASH in full AND the unpaid
portion forgiven AS AR, separately -- double-counting the unpaid portion.
See core/retail/returns_settlement.py's module docstring for the full
accounting reasoning and api/retail_api.py's create_return for the runtime
integration these unit tests do not exercise (see
retail_returns_wave0_test.py for the end-to-end HTTP-level coverage of
that integration, including the live cash-session drawer math).

Run:
    pytest products/retail/tests/retail_returns_settlement_test.py -v
"""
import sys
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]        # products/retail
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent                    # aura-fullsuits
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail.returns_settlement import split_return_settlement  # noqa: E402


def test_tender_capped_at_collected_not_full_refund():
    """DEFECT 1's exact shape: a full refund of a partially-paid sale must
    never pay out more tender than the sale actually collected.

    Mutation that must turn this red: replace the tender_refund computation
    with the raw `refund` value (i.e. reintroduce the pre-fix "pay out the
    whole refund_amount as cash" behaviour) -- tender_refund would read
    100.0 instead of 20.0.
    """
    tender_refund, ar_forgiven, store_credit = split_return_settlement(
        refund=100.0, tender_available=20.0, requested_method='cash',
        balance_due_remaining=80.0, current_balance=80.0, has_customer=True,
    )
    assert tender_refund == 20.0, \
        f'tender_refund must be capped at what was collected (20), got {tender_refund}'


def test_remainder_forgiven_as_ar_up_to_balance_due():
    """The leftover after the tender cap is forgiven as AR, capped at the
    outstanding balance and the customer's live credit_balance -- exactly
    the pre-fix logic's own two caps, now fed the REMAINDER instead of the
    raw refund.

    NOTE on what this specific case can and cannot distinguish: feeding
    `ar_forgiven` the raw `refund` (100) instead of the remainder (80)
    would read 80 here too by coincidence (min(100,80,80) ==
    min(80,80,80) == 80, because `current_balance`/`balance_due_remaining`
    are the binding constraint either way) -- so THIS test alone cannot
    catch that particular mutation. See
    test_ar_forgiven_never_exceeds_what_the_tender_cap_left_behind below,
    which uses numbers where the remainder itself is the binding
    constraint, for the case that actually tells the two apart.
    """
    tender_refund, ar_forgiven, store_credit = split_return_settlement(
        refund=100.0, tender_available=20.0, requested_method='cash',
        balance_due_remaining=80.0, current_balance=80.0, has_customer=True,
    )
    assert ar_forgiven == 80.0, f'expected ar_forgiven=80.0, got {ar_forgiven}'
    assert store_credit == 0.0, f'expected store_credit=0.0, got {store_credit}'


def test_ar_forgiven_never_exceeds_what_the_tender_cap_left_behind():
    """The case that actually distinguishes "ar_forgiven fed the post-
    tender-cap remainder" from "ar_forgiven fed the raw refund": most of
    the refund is already covered by tender (remainder is small), so the
    remainder itself -- not balance_due_remaining or current_balance -- is
    the binding constraint.

    Mutation that must turn this red: change `ar_forgiven_d = min(
    remainder_d, balance_due_remaining_d, current_balance_d)` to use
    `refund_d` instead of `remainder_d` -- ar_forgiven would read 80.0
    (min(100,80,80)) instead of 10.0, and store_credit would go NEGATIVE
    (10 - 80 = -70) instead of 0.0, double-counting the 80 already refunded
    as tender.
    """
    tender_refund, ar_forgiven, store_credit = split_return_settlement(
        refund=100.0, tender_available=90.0, requested_method='cash',
        balance_due_remaining=80.0, current_balance=80.0, has_customer=True,
    )
    assert tender_refund == 90.0
    assert ar_forgiven == 10.0, f'expected ar_forgiven capped at the 10 remainder, got {ar_forgiven}'
    assert store_credit == 0.0, f'store_credit must never go negative, got {store_credit}'


def test_leftover_beyond_ar_becomes_store_credit_not_dropped():
    """When the customer already paid the debt down some other way before
    the return (current_balance < balance_due_remaining), the leftover
    after AR forgiveness must become store_credit, not vanish.

    Mutation that must turn this red: drop the `store_credit = remainder -
    ar_forgiven` computation (or clamp it to 0) -- the 50 would vanish with
    nothing asserting where it went. This is also the case that actually
    distinguishes "ar_forgiven fed the remainder" from "ar_forgiven fed the
    raw refund": if a mutant used the raw refund (100) here,
    min(100, 80, 30) would still read 30 -- SAME as the correct answer --
    but store_credit would then be computed as `refund - ar_forgiven` = 70
    instead of `remainder - ar_forgiven` = 50, going red here specifically.
    """
    tender_refund, ar_forgiven, store_credit = split_return_settlement(
        refund=100.0, tender_available=20.0, requested_method='cash',
        balance_due_remaining=80.0, current_balance=30.0, has_customer=True,
    )
    assert tender_refund == 20.0
    assert ar_forgiven == 30.0, f'expected ar_forgiven capped at current_balance=30, got {ar_forgiven}'
    assert store_credit == 50.0, f'expected the leftover 50 as store_credit, got {store_credit}'


def test_three_parts_always_sum_to_refund():
    """Property check across a small matrix: the three parts sum to
    `min(refund, tender_available + balance_due_remaining)` -- the
    SETTLEABLE amount -- not unconditionally to `refund`, to within the
    currency's own quantum.

    RESTATED 2026-09-16 (DEFECT-1B): this used to assert the three parts
    always sum to the bare `refund`. That is no longer true by design --
    see returns_settlement.py's module docstring's DEFECT-1B note. The old
    assertion was WRONG whenever a sale's `refund` (recomputed independently
    from line items) exceeds everything the shop ever recorded collecting
    or owing for that sale (`tender_available + balance_due_remaining`):
    the old code manufactured store_credit to make up the difference out of
    nothing, and the sum-to-refund assertion rewarded exactly that bug by
    treating it as the invariant being upheld. Case 6 below (walk-in,
    balance_due_remaining=0, tender_available=0, refund=115) is that exact
    shape: settleable=0, so all three parts must now be 0, not sum to 115.

    WHAT THIS RESTATED ASSERTION CAN NO LONGER CATCH: a mutant that removes
    the DEFECT-1B cap entirely (reverting to `store_credit = remainder -
    ar_forgiven`) would, on every case below EXCEPT case 6, still sum to the
    settleable amount too, because `remainder - ar_forgiven` and the capped
    value happen to coincide whenever `balance_due_remaining` fully covers
    the leftover (cases 1-5 were deliberately chosen, pre-fix, so the cap is
    non-binding). Only case 6 forces the two formulas apart within this
    property check -- see test_store_credit_capped_at_recorded_debt_not_
    manufactured_from_nothing below for a dedicated, explicit mutation proof
    of the cap itself (breaking it there is what actually pins the defect
    shut; this test alone, minus case 6, would not).

    Mutation that must turn this red: any arithmetic slip in the helper
    (e.g. capping ar_forgiven on `refund` instead of `remainder`, or
    `store_credit = refund - ar_forgiven` instead of `remainder -
    ar_forgiven`) breaks the identity on at least one row below; so does
    dropping the DEFECT-1B cap (case 6 goes from summing to 0 to summing to
    115).
    """
    cases = [
        (100.0, 20.0, 80.0, 80.0, True),
        (100.0, 0.0, 100.0, 100.0, True),     # fully credit sale
        (115.0, 115.0, 0.0, 0.0, True),       # fully paid, nothing owed
        (50.0, 20.0, 80.0, 30.0, True),       # partial return, partial pool
        (100.0, 20.0, 80.0, 30.0, True),      # the store-credit case above
        (115.0, 0.0, 0.0, 0.0, False),        # DEFECT-1B: nothing recorded at all -> settleable=0
    ]
    for refund, tender_available, balance_due_remaining, current_balance, has_customer in cases:
        tender_refund, ar_forgiven, store_credit = split_return_settlement(
            refund=refund, tender_available=tender_available, requested_method='cash',
            balance_due_remaining=balance_due_remaining, current_balance=current_balance,
            has_customer=has_customer,
        )
        settleable = min(refund, tender_available + balance_due_remaining)
        total = round(tender_refund + ar_forgiven + store_credit, 6)
        assert total == round(settleable, 6), (
            f'parts do not sum to the settleable amount for case {(refund, tender_available, balance_due_remaining, current_balance, has_customer)}: '
            f'tender_refund={tender_refund} ar_forgiven={ar_forgiven} store_credit={store_credit} sum={total} settleable={settleable}'
        )


def test_store_credit_capped_at_recorded_debt_not_manufactured_from_nothing():
    """DEFECT-1B's exact shape, the regression this whole pass exists to
    fix: a sale with ZERO tender collected and ZERO recorded debt (a
    sub-quantum balance_due that create_sale's own gate never treated as
    AR -- see create_return's `original_balance_due` comment and
    retail_sale_money_precision_test.py's
    test_create_return_original_balance_due_uses_currency_precision_not_
    hardcoded_2dp) must not manufacture store_credit out of the leftover
    `refund` value. `balance_due_remaining=0.005` deliberately mirrors the
    live regression's exact JOD figures (0.006 total, 0.001 paid) -- and
    `currency='JOD'` is passed deliberately: JOD's real 3dp quantum is
    exactly what makes 0.005 a distinct, representable amount (falling back
    to this module's own 2dp default would round 0.005 up to 0.01 BEFORE
    the cap even gets a chance to matter, silently passing regardless of
    whether the cap works -- see retail_sale_money_precision_test.py's own
    test for the same hardcoded-2dp failure shape one level up the stack).

    Mutation that must turn this red: remove the DEFECT-1B cap (revert to
    `store_credit = remainder - ar_forgiven`) -- store_credit would read
    0.006 instead of 0.005, and (via retail_api.py's `store_credit > 0.005`
    gate) the live regression's `-0.006 == 0.0` failure reappears end to
    end.
    """
    tender_refund, ar_forgiven, store_credit = split_return_settlement(
        refund=0.006, tender_available=0.0, requested_method='cash',
        balance_due_remaining=0.005, current_balance=0.0, has_customer=True,
        currency='JOD',
    )
    assert tender_refund == 0.0
    assert ar_forgiven == 0.0
    assert store_credit == 0.005, (
        f'store_credit must be capped at settleable (0 tender + 0.005 balance_due_remaining), '
        f'got {store_credit} -- this is DEFECT-1B: manufacturing store_credit from a refund '
        'value the shop never recorded collecting or owing'
    )


def test_store_credit_cap_non_binding_on_normal_fully_paid_cash_sale():
    """DEFECT-1B cap sanity check: an ordinary fully-paid cash sale (no AR
    ever involved) must be completely unaffected by the new cap --
    settleable = min(100, 100+0) = 100 = refund, so the cap on the total
    (100) matches what tender_refund alone already claims.
    """
    tender_refund, ar_forgiven, store_credit = split_return_settlement(
        refund=100.0, tender_available=100.0, requested_method='cash',
        balance_due_remaining=0.0, current_balance=0.0, has_customer=True,
    )
    assert (tender_refund, ar_forgiven, store_credit) == (100.0, 0.0, 0.0)


def test_store_credit_cap_non_binding_on_normal_partial_ar_sale():
    """DEFECT-1B cap sanity check: sale of 100, paid 40 in cash, 60 left on
    AR, returned in full -- the cap must not disturb the pre-existing
    tender/AR split (there is no leftover remainder for store_credit to
    absorb once AR forgiveness covers it, so the cap is non-binding).
    """
    tender_refund, ar_forgiven, store_credit = split_return_settlement(
        refund=100.0, tender_available=40.0, requested_method='cash',
        balance_due_remaining=60.0, current_balance=60.0, has_customer=True,
    )
    assert (tender_refund, ar_forgiven, store_credit) == (40.0, 60.0, 0.0)


def test_store_credit_cap_is_non_binding_when_debt_was_genuinely_paid_off_elsewhere():
    """The cap must NOT bite the legitimate store_credit case: a sale fully
    on AR (balance_due_remaining == refund) whose customer has since paid
    it off some other way (current_balance == 0) still converts the whole
    remainder to store_credit -- settleable = min(100, 0+100) = 100 = refund,
    so the total-based cap is non-binding and store_credit gets the full 100.

    Mutation that must turn this red: cap store_credit at `current_balance
    - ar_forgiven` instead of deriving it from `settleable - tender_refund -
    ar_forgiven` (an easy-to-write, WRONG cap that "cap everything to zero"
    style mutants would also pass) -- store_credit would incorrectly read
    0.0 instead of 100.0, since current_balance is 0 here precisely because
    the debt was already paid off elsewhere.
    """
    tender_refund, ar_forgiven, store_credit = split_return_settlement(
        refund=100.0, tender_available=0.0, requested_method='cash',
        balance_due_remaining=100.0, current_balance=0.0, has_customer=True,
    )
    assert tender_refund == 0.0
    assert ar_forgiven == 0.0
    assert store_credit == 100.0, (
        f'cap must not bite legitimate store_credit (debt paid off elsewhere), got {store_credit}'
    )


def test_store_credit_refund_method_takes_the_whole_value_off_tender():
    """An operator who explicitly asks for store_credit gets zero tender
    payout even though the tender pool has room -- the cashier's choice to
    NOT hand back cash must be honoured, not silently overridden by "well
    there was cash available".

    Mutation that must turn this red: remove the `requested_method ==
    'store_credit'` branch (i.e. always compute tender_refund =
    min(refund, tender_available)) -- tender_refund would incorrectly read
    20.0 instead of 0.0.
    """
    tender_refund, ar_forgiven, store_credit = split_return_settlement(
        refund=100.0, tender_available=20.0, requested_method='store_credit',
        balance_due_remaining=80.0, current_balance=80.0, has_customer=True,
    )
    assert tender_refund == 0.0, f'store_credit request must draw nothing from tender, got {tender_refund}'
    assert ar_forgiven == 80.0
    assert store_credit == 20.0


def test_walk_in_never_forgives_ar_even_if_caller_passes_a_balance():
    """`has_customer=False` must force ar_forgiven to 0 regardless of what
    balance_due_remaining/current_balance the caller passes -- a walk-in
    cannot carry AR at all. Defensive: create_return's own 409 guard is the
    belt-and-braces for the case this produces a nonzero store_credit with
    no customer to attach it to.

    Mutation that must turn this red: drop the `has_customer` check (i.e.
    always compute ar_forgiven the same way for a walk-in) -- ar_forgiven
    would incorrectly read 80.0 instead of 0.0.
    """
    tender_refund, ar_forgiven, store_credit = split_return_settlement(
        refund=100.0, tender_available=20.0, requested_method='cash',
        balance_due_remaining=80.0, current_balance=80.0, has_customer=False,
    )
    assert ar_forgiven == 0.0, f'a walk-in must never receive AR forgiveness, got {ar_forgiven}'
    assert store_credit == 80.0
