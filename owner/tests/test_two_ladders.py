"""2026-10-26 -- the owner's SECOND commission ladder (monthly packages).

Package 1 keeps its ladder (20/50/100/200/350, +30 after the 5th) and counts
only itself. Packages 2 and 3 count TOGETHER on a new ladder whose month
totals are 5/10/20/30/55/65/90/100/115/130, +10 after the 10th, and a monthly
sale counts when its FIRST payment is confirmed (replacing "about 100 JOD
paid"). A seller holds both ladders at once; neither moves the other.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from tests.test_step0_ladder import (
    JOD,
    LADDER,
    MONTHLY_LADDER,
    _entries,
    _ladder_sales,
    _net_by_order,
    _refund,
    _sell,
    _summary,
    _world,
)

D = Decimal

P1_TOTALS = ["0", "20", "50", "100", "200", "350", "380", "410"]
MONTHLY_TOTALS = ["0", "5", "10", "20", "30", "55", "65", "90", "100", "115", "130", "140", "150"]


@pytest.mark.parametrize("sales", [1, 5, 6])
def test_package_1_ladder_totals_at_the_boundaries(app, sales):
    with app.app_context():
        from app.commissions.ladder import LADDER_RULE_TYPE, ladder_total

        assert ladder_total(sales, LADDER_RULE_TYPE) == D(P1_TOTALS[sales])
        assert ladder_total(sales) == D(P1_TOTALS[sales])  # the default is Package 1's ladder


@pytest.mark.parametrize("sales", [1, 2, 5, 9, 10, 11, 12])
def test_monthly_ladder_totals_at_the_boundaries(app, sales):
    with app.app_context():
        from app.commissions.ladder import MONTHLY_PACKAGES_RULE_TYPE, ladder_share, ladder_total

        assert ladder_total(sales, MONTHLY_PACKAGES_RULE_TYPE) == D(MONTHLY_TOTALS[sales])
        assert ladder_share(sales, MONTHLY_PACKAGES_RULE_TYPE) == D(MONTHLY_TOTALS[sales]) - D(MONTHLY_TOTALS[sales - 1])


def test_eleven_monthly_sales_through_the_real_flow_add_up_to_140_across_p2_and_p3(app, seeded):
    """Past the 10th sale the ladder adds 10 each: 130 -> 140 at the 11th.
    Alternating P2 and P3 proves they share one count."""
    w = _world(app)
    for i in range(11):
        _sell(app, w, "p2" if i % 2 == 0 else "p3")
    summary = _summary(app, w["seller_profile"])
    assert (summary["monthly_count"], summary["monthly_total"]) == (11, D("140.00"))
    assert (summary["yearly_count"], summary["yearly_total"]) == (0, D("0.00"))
    net = _net_by_order(app, w["seller_profile"])
    assert sum(net.values()) == D("140.00") and len(net) == 11


def test_a_mixed_month_two_package_1_sales_and_four_monthly_sales_pay_50_plus_30(app, seeded):
    w = _world(app)
    for plan in ("p1", "p2", "p1", "p3", "p2", "p3"):
        _sell(app, w, plan)
    summary = _summary(app, w["seller_profile"])
    assert (summary["yearly_count"], summary["yearly_total"]) == (2, D("50.00"))
    assert (summary["monthly_count"], summary["monthly_total"]) == (4, D("30.00"))
    assert (summary["count"], summary["total"]) == (6, D("80.00"))
    # the books agree: each ladder row sits at its own ladder's position
    positions = {}
    for _order, amount, status, position, _month in _entries(app, w["seller_profile"]):
        if status != "CANCELLED":
            positions.setdefault(position, []).append(amount)
    # Each ladder numbers its own sales from 1: Package 1 sits at 1 (20) and 2 (30),
    # the monthly ladder at 1 (5), 2 (5), 3 (10), 4 (10). Compared as sorted lists
    # because the order of rows within a position is not defined. (Verifier F7a:
    # this used to assert only the sum, which a running index across BOTH ladders
    # would also satisfy.)
    assert {p: sorted(v) for p, v in positions.items()} == {
        1: [D("5.00"), D("20.00")], 2: [D("5.00"), D("30.00")], 3: [D("10.00")], 4: [D("10.00")],
    }


def test_a_package_1_sale_never_moves_the_monthly_ladder_and_vice_versa(app, seeded):
    w = _world(app)
    _sell(app, w, "p1")
    first = _summary(app, w["seller_profile"])
    assert (first["yearly_total"], first["monthly_total"], first["monthly_count"]) == (D("20.00"), D("0.00"), 0)
    _sell(app, w, "p2")
    second = _summary(app, w["seller_profile"])
    assert (second["yearly_total"], second["yearly_count"]) == (D("20.00"), 1)  # unchanged by the monthly sale
    assert (second["monthly_total"], second["monthly_count"]) == (D("5.00"), 1)  # the first monthly sale is #1, not #2
    _sell(app, w, "p1")
    third = _summary(app, w["seller_profile"])
    assert (third["yearly_total"], third["monthly_total"]) == (D("50.00"), D("5.00"))


def test_the_extra_pair_never_counts_on_either_ladder(app, seeded):
    w = _world(app)
    _sell(app, w, "p1", pairs=1)
    _sell(app, w, "p2", pairs=2)
    summary = _summary(app, w["seller_profile"])
    assert (summary["yearly_count"], summary["yearly_total"]) == (1, D("20.00"))
    assert (summary["monthly_count"], summary["monthly_total"]) == (1, D("5.00"))


def test_a_refund_inside_the_hold_uncounts_in_the_right_ladder_only(app, seeded):
    w = _world(app)
    p1a = _sell(app, w, "p1")["order_id"]
    _sell(app, w, "p1")
    m1 = _sell(app, w, "p2")["order_id"]
    m2 = _sell(app, w, "p3")["order_id"]
    before = _summary(app, w["seller_profile"])
    assert (before["yearly_total"], before["monthly_total"]) == (D("50.00"), D("10.00"))
    _refund(app, w, m1, "35.00")  # the first monthly sale, in full, inside its hold
    after = _summary(app, w["seller_profile"])
    assert (after["monthly_count"], after["monthly_total"]) == (1, D("5.00"))  # the remaining sale is now #1
    assert (after["yearly_count"], after["yearly_total"]) == (2, D("50.00"))  # Package 1 untouched
    net = _net_by_order(app, w["seller_profile"])
    assert (net.get(m1, D("0.00")), net[m2]) == (D("0.00"), D("5.00"))
    # allow-half: a Package 1 refund leaves the monthly ladder alone
    _refund(app, w, p1a, "250.00")
    last = _summary(app, w["seller_profile"])
    assert (last["yearly_count"], last["yearly_total"], last["monthly_total"]) == (1, D("20.00"), D("5.00"))


def test_a_seller_on_only_the_package_1_ladder_earns_nothing_for_a_monthly_sale(app, seeded):
    """The other direction of the family filter: no monthly-ladder rule, no
    monthly-ladder row -- and never a Package 1 ladder position."""
    w = _world(app)
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.commissions import CommissionPlan, EmployeeCommissionPlanAssignment as A

        plan_ids = [p.id for p in db_session.execute(select(CommissionPlan).where(
            CommissionPlan.plan_code.like("M\\_%"))).scalars()]
        for row in db_session.execute(select(A).where(A.employee_profile_id == w["seller_profile"],
                                                      A.commission_plan_id.in_(plan_ids))).scalars():
            db_session.delete(row)
        db_session.commit()
    _sell(app, w, "p2")
    assert _ladder_sales(app, w["seller_profile"]) == []
    assert _entries(app, w["seller_profile"]) == []
    _sell(app, w, "p1")
    assert _summary(app, w["seller_profile"])["yearly_total"] == D("20.00")


# ------------------------------------------------------ setting sellers up --

def test_one_action_seeds_and_assigns_both_ladders_without_closing_the_first(app, seeded):
    w = _world(app, rule="PERCENT")
    with app.app_context():
        from sqlalchemy import select

        from app.commissions.ladder_setup import (
            LADDER_PLAN_CODES,
            assign_profile_to_ladder,
            current_plan_codes,
            seed_ladder_plans,
        )
        from app.extensions import db_session
        from app.models.commissions import CommissionPlan, CommissionRuleVersion
        from app.models.employees import EmployeeProfile

        results = seed_ladder_plans(actor_staff_user_id=w["admin"])
        assert [(p.plan_code, created) for p, created in results] == [(c, True) for c in LADDER_PLAN_CODES]
        assert [c for _p, c in seed_ladder_plans(actor_staff_user_id=w["admin"])] == [False, False]  # idempotent
        rule_types = {r.rule_type for r in db_session.execute(select(CommissionRuleVersion).join(
            CommissionPlan, CommissionPlan.id == CommissionRuleVersion.commission_plan_id
        ).where(CommissionPlan.plan_code.in_(LADDER_PLAN_CODES))).scalars()}
        assert rule_types == {LADDER, MONTHLY_LADDER}
        profile = db_session.get(EmployeeProfile, w["seller_profile"])
        today = date.today()
        assert len(current_plan_codes(profile.id, today)) == 1  # the percentage plan
        _a, created = assign_profile_to_ladder(profile, effective_from=today, actor_staff_user_id=w["admin"])
        assert created is True
        assert sorted(current_plan_codes(profile.id, today)) == sorted(LADDER_PLAN_CODES)  # percentage closed
        _a, created = assign_profile_to_ladder(profile, effective_from=today, actor_staff_user_id=w["admin"])
        assert created is False
        assert sorted(current_plan_codes(profile.id, today)) == sorted(LADDER_PLAN_CODES)


def test_a_seller_already_on_package_1_gets_only_the_missing_ladder(app, seeded):
    w = _world(app, rule="PERCENT")
    with app.app_context():
        from app.commissions.ladder_setup import (
            LADDER_PLAN_CODE,
            LADDER_PLAN_CODES,
            assign_seller_to_ladder,
            current_plan_codes,
            seed_ladder_plans,
        )
        from app.commissions.management import assign_employee_commission_plan
        from app.extensions import db_session
        from app.models.employees import EmployeeProfile
        from app.models.commissions import CommissionPlan
        from sqlalchemy import select

        seed_ladder_plans(actor_staff_user_id=w["admin"])
        profile = db_session.get(EmployeeProfile, w["seller_profile"])
        today = date.today()
        p1 = db_session.execute(select(CommissionPlan).where(CommissionPlan.plan_code == LADDER_PLAN_CODE)).scalars().one()
        assign_employee_commission_plan(profile.id, p1, effective_from=today, actor_staff_user_id=w["admin"])
        assert current_plan_codes(profile.id, today) == [LADDER_PLAN_CODE]
        assign_seller_to_ladder(profile.staff_user_id, effective_from=today, actor_staff_user_id=w["admin"])
        assert sorted(current_plan_codes(profile.id, today)) == sorted(LADDER_PLAN_CODES)


# ------------------------------------------- the rule-in-force check (F6) --

def _ladder_versions(app, w, spec):
    """Re-shapes the SALES_LADDER plan's rule versions to `spec`, a list of
    (effective_from, effective_until): the seeded version is rewritten to the
    first entry and the rest are added, so gaps the append-only writer would
    never create can be tested directly."""
    with app.app_context():
        from sqlalchemy import select

        from app.commissions.ladder_setup import LADDER_PLAN_CODE, seed_ladder_plans
        from app.extensions import db_session
        from app.models.commissions import CommissionPlan, CommissionRuleVersion

        seed_ladder_plans(actor_staff_user_id=w["admin"], effective_from=date(2026, 1, 1))
        plan = db_session.execute(select(CommissionPlan).where(CommissionPlan.plan_code == LADDER_PLAN_CODE)).scalars().one()
        first = db_session.execute(
            select(CommissionRuleVersion).where(CommissionRuleVersion.commission_plan_id == plan.id)
        ).scalars().one()
        for i, (start, until) in enumerate(spec):
            row = first if i == 0 else CommissionRuleVersion(
                commission_plan_id=plan.id, rule_type=first.rule_type, currency=first.currency,
                created_by_staff_user_id=w["admin"], effective_from=start)
            row.effective_from, row.effective_until = start, until
            db_session.add(row)
        db_session.commit()


def _in_force(app, on):
    with app.app_context():
        from app.commissions.ladder_setup import LADDER_PLAN_CODE, _plan_with_rule

        try:
            return _plan_with_rule(LADDER_PLAN_CODE, on).plan_code
        except ValueError:
            return None


def test_a_closed_rule_is_not_in_force_after_its_end(app, seeded):
    """RED on the old query: it looked only at effective_from, so a rule closed on
    1 July was accepted for August and the seller then earned nothing."""
    w = _world(app, rule="PERCENT")
    _ladder_versions(app, w, [(date(2026, 1, 1), date(2026, 7, 1))])
    assert _in_force(app, date(2026, 3, 1)) == "SALES_LADDER"       # allow side: inside the window
    assert _in_force(app, date(2026, 8, 1)) is None                   # deny side: after it closed


def test_a_gap_between_versions_is_refused_but_each_version_is_accepted(app, seeded):
    w = _world(app, rule="PERCENT")
    _ladder_versions(app, w, [(date(2026, 1, 1), date(2026, 7, 1)), (date(2026, 12, 1), None)])
    assert _in_force(app, date(2026, 3, 1)) == "SALES_LADDER"       # the OLD version still covers March
    assert _in_force(app, date(2026, 11, 1)) is None                  # nothing covers November
    assert _in_force(app, date(2026, 12, 15)) == "SALES_LADDER"
