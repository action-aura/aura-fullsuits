"""UI-3a: the pure pricing half -- cart AMOUNT discounts and "make the total X".

Pure module tests (no Flask, no database): core/retail/cart_discount.py driven through
the REAL core/retail/pricing.calculate_line, at JOD's three decimals and a 16% tax.

What each test would catch (and was mutation-proved against, see ROADMAP "UI-3a CASHED IN"):
  * test_exact_*           -- the discount lands to the fil on the cart the brief names.
  * test_random_carts      -- 200 seeded carts: every request is either EXACT or a clean refusal.
  * test_residue_*         -- the rounding residue goes to the largest line, first on a tie.
  * test_nudge_*           -- a calc whose rounding DRIFTS is pulled back to exact (bounded), else
                              refused. Removing the nudge makes this red.
  * test_target_*          -- "make the total X" solves exactly, picks the smallest discount, and an
                              unreachable total is refused with the nearest reachable ones.
"""
import random
import sys
from decimal import Decimal
from functools import partial
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1] / 'backend'
SUITE_ROOT = BACKEND_DIR.parents[2]
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail import cart_discount as cd  # noqa: E402
from core.retail import pricing  # noqa: E402

QUANT = pricing.currency_quantum('JOD')
D = Decimal


def _calc(mode=pricing.TAX_AFTER_DISCOUNT):
    return lambda p, q, pct, r: pricing.calculate_line(p, q, pct, r, mode=mode, currency='JOD')


def _total(calcs):
    return sum((D(str(c['total'])) for c in calcs), D(0))


def _disc(calcs):
    return sum((D(str(c['discount_amount'])) for c in calcs), D(0))


def _pre_total(lines, calc):
    return _total([calc(p, q, 0, r) for p, q, r in lines])


# 3 lines at 16% tax whose pre-tax subtotal is 9.181 and whose total with tax is 10.650 ... the
# brief's example: "10.650 cart of 3 lines with 16% tax, discount 0.650 ex-tax; target total 10.000".
# 9.181 * 1.16 = 10.65 (to the fil, per-line rounding included -- asserted below, not assumed).
BRIEF_LINES = [(3.5, 1, 16), (2.75, 2, 16), (0.181, 1, 16)]


def test_the_brief_cart_is_the_cart_the_brief_names():
    calc = _calc()
    assert _pre_total(BRIEF_LINES, calc) == D('10.650')


def test_exact_amount_discount_on_the_brief_cart():
    calc = _calc()
    pcts, calcs, shares = cd.apply_cart_discount(BRIEF_LINES, D('0.650'), QUANT, calc)
    assert _disc(calcs) == D('0.650')                      # summed discount is exactly what was asked
    assert sum(shares) == 650                              # in minor units
    assert all(0 <= p <= 100 for p in pcts)


def test_target_total_ten_on_the_brief_cart():
    calc = _calc()
    amount, pcts, calcs, shares = cd.solve_target_total(BRIEF_LINES, D('10.000'), QUANT, calc)
    assert _total(calcs) == D('10.000')
    assert _disc(calcs) == amount > 0


def test_target_equal_to_current_total_is_a_no_op():
    calc = _calc()
    amount, pcts, calcs, shares = cd.solve_target_total(BRIEF_LINES, D('10.650'), QUANT, calc)
    assert amount == 0 and calcs is None


@pytest.mark.parametrize('bad', [0, D('-1'), D('10.651'), D('99')])
def test_target_out_of_range_is_refused(bad):
    with pytest.raises(cd.CartDiscountError) as ei:
        cd.solve_target_total(BRIEF_LINES, bad, QUANT, _calc())
    assert ei.value.code == cd.CODE_BAD_TARGET


@pytest.mark.parametrize('bad', [None, True, 'abc', float('nan'), float('inf'), 0.6501])
def test_amount_must_be_a_real_number_on_the_minor_grid(bad):
    with pytest.raises(cd.CartDiscountError):
        cd.parse_amount(bad, QUANT)


def test_amount_over_subtotal_or_zero_is_refused():
    calc = _calc()
    for amt in (D('0'), D('9.182')):
        with pytest.raises(cd.CartDiscountError) as ei:
            cd.apply_cart_discount(BRIEF_LINES, amt, QUANT, calc)
        assert ei.value.code == cd.CODE_BAD_AMOUNT


def test_the_whole_subtotal_can_be_discounted():
    calc = _calc()
    pcts, calcs, _ = cd.apply_cart_discount(BRIEF_LINES, D('9.181'), QUANT, calc)
    assert _total(calcs) == D('0.000') and pcts == [100.0, 100.0, 100.0]


# ── the residue rule ────────────────────────────────────────────────────────────

def test_residue_lands_on_the_largest_line_first_on_a_tie():
    # three equal lines, 1 fil to give: rounding gives 0/0/0, the residue fil goes to line 0.
    shares, residue_line = cd.distribute_minor([1000, 1000, 1000], 1)
    assert shares == [1, 0, 0] and residue_line == 0
    # unequal: the largest line (index 1) takes the extra fil.
    shares, residue_line = cd.distribute_minor([100, 1000, 100], 7)
    assert sum(shares) == 7 and residue_line == 1 and shares[1] >= shares[0]


def test_residue_spills_when_the_largest_line_is_full():
    # D == gross total except one fil on tiny lines: nothing may exceed its own gross.
    shares, _ = cd.distribute_minor([1, 1, 1], 2)
    assert sum(shares) == 2 and all(0 <= s <= 1 for s in shares)


def test_distribute_always_sums_exactly():
    rng = random.Random(7)
    for _ in range(500):
        gross = [rng.randint(0, 50000) for _ in range(rng.randint(1, 6))]
        if sum(gross) == 0:
            continue
        d = rng.randint(0, sum(gross))
        shares, _ = cd.distribute_minor(gross, d)
        assert sum(shares) == d and all(0 <= s <= g for s, g in zip(shares, gross))


# ── the nudge ────────────────────────────────────────────────────────────────────

def _drifting_calc(real):
    """Wraps calculate_line so the FIRST line's discount comes out one fil short whenever a
    discount is applied -- the shape a future rounding change in pricing.py would produce."""
    def f(p, q, pct, r):
        out = dict(real(p, q, pct, r))
        if pct > 0 and p == BRIEF_LINES[0][0]:
            out['discount_amount'] = float(D(str(out['discount_amount'])) - QUANT)
        return out
    return f


def test_nudge_pulls_a_drifting_calc_back_to_exact():
    calc = _drifting_calc(_calc())
    # the drifting line is the LARGEST (3.5), i.e. the residue line, so a +1 fil nudge fixes it
    pcts, calcs, shares = cd.apply_cart_discount(BRIEF_LINES, D('0.650'), QUANT, calc)
    assert _disc(calcs) == D('0.650')


def test_nudge_gives_up_cleanly_when_it_cannot_reach_the_amount():
    def hopeless(p, q, pct, r):
        out = dict(pricing.calculate_line(p, q, pct, r, currency='JOD'))
        out['discount_amount'] = 0.0 if pct > 0 else out['discount_amount']
        return out
    with pytest.raises(cd.CartDiscountError) as ei:
        cd.apply_cart_discount(BRIEF_LINES, D('0.650'), QUANT, hopeless)
    assert ei.value.code == cd.CODE_NOT_REACHABLE


# ── 200 seeded random carts ──────────────────────────────────────────────────────

def _random_cart(rng):
    n = rng.randint(1, 5)
    return [(round(rng.uniform(0.05, 80), 3), rng.choice([1, 2, 3, 0.5, 1.5, 7]),
             rng.choice([0, 0, 16, 16, 8, 4])) for _ in range(n)]


@pytest.mark.parametrize('mode', [pricing.TAX_AFTER_DISCOUNT, pricing.TAX_BEFORE_DISCOUNT])
def test_random_carts_amount_is_exact_or_a_clean_refusal(mode):
    rng = random.Random(20261013)
    calc = _calc(mode)
    exact = 0
    for _ in range(200):
        lines = _random_cart(rng)
        subtotal = sum((D(str(calc(p, q, 0, r)['gross'])) for p, q, r in lines), D(0))
        want = (subtotal * D(str(rng.uniform(0.001, 1)))).quantize(QUANT)
        if want <= 0:
            continue
        try:
            pcts, calcs, shares = cd.apply_cart_discount(lines, want, QUANT, calc)
        except cd.CartDiscountError as e:
            assert e.code in (cd.CODE_NOT_REACHABLE, cd.CODE_BAD_AMOUNT)
            continue
        assert _disc(calcs) == want, (lines, want)
        assert all(c['total'] >= 0 for c in calcs)
        exact += 1
    assert exact >= 190   # clean refusals are the rare exception, not the rule


@pytest.mark.parametrize('mode', [pricing.TAX_AFTER_DISCOUNT, pricing.TAX_BEFORE_DISCOUNT])
def test_random_carts_target_total_is_exact_or_a_clean_refusal(mode):
    rng = random.Random(20261014)
    calc = _calc(mode)
    exact = refused = 0
    for _ in range(200):
        lines = _random_cart(rng)
        current = _pre_total(lines, calc)
        target = (current * D(str(rng.uniform(0.05, 0.999)))).quantize(QUANT)
        if target <= 0:
            continue
        try:
            amount, pcts, calcs, shares = cd.solve_target_total(lines, target, QUANT, calc)
        except cd.CartDiscountError as e:
            assert e.code == cd.CODE_TARGET_NOT_REACHABLE
            below, above = e.data['nearest_below'], e.data['nearest_above']
            assert below is None or D(str(below)) < target
            assert above is None or D(str(above)) > target
            refused += 1
            continue
        if calcs is None:          # target rounded up to the current total
            continue
        assert _total(calcs) == target, (lines, target)
        exact += 1
    assert exact >= 150, (exact, refused)


def test_target_unreachable_names_the_nearest_reachable_totals():
    # One line, price 1.000 no tax: every total from 0.001 to 1.000 is reachable, so build a
    # case rounding CAN skip: 16% tax on one 0.500 line. Totals step by 1.16 fils per fil off.
    calc = _calc()
    lines = [(0.5, 1, 16)]
    skipped = None
    for t in range(1, 580):
        target = D(t) * QUANT
        try:
            cd.solve_target_total(lines, target, QUANT, calc)
        except cd.CartDiscountError as e:
            skipped = (target, e)
            break
    assert skipped is not None, 'a 16% tax line must skip some totals'
    target, err = skipped
    assert err.code == cd.CODE_TARGET_NOT_REACHABLE
    assert D(str(err.data['nearest_below'])) < target < D(str(err.data['nearest_above']))
    # and the two named totals are really reachable
    for nearest in (err.data['nearest_below'], err.data['nearest_above']):
        _, _, calcs, _ = cd.solve_target_total(lines, D(str(nearest)), QUANT, calc)
        assert _total(calcs) == D(str(nearest))


def test_target_picks_the_smallest_discount_that_lands_exactly():
    calc = _calc()
    amount, _, calcs, _ = cd.solve_target_total(BRIEF_LINES, D('10.000'), QUANT, calc)
    # one fil LESS discount must not also land on 10.000
    _, calcs_less, _ = cd.apply_cart_discount(BRIEF_LINES, amount - QUANT, QUANT, calc)
    assert _total(calcs_less) != D('10.000')
