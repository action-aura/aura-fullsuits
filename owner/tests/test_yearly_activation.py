"""Yearly 100 JOD activation on the monthly packages (owner rule 2026-10-30).

Package 2 (35 a month, 12-month contract) and Package 3 (25 a month, 24-month
contract) charge the 100 JOD activation EVERY contract year -- the payment for
month 13, 25, ... includes it -- not only on the first sale. Package 1 (250 the
first year, then 100 a year) is unchanged.

The activation is the plan price's setup_fee (no new column); packages.payment_due
is the one place that says what the Nth payment is. Every guard here is paired
with its opposite (ENGINEERING 1): months 14-24 must NOT carry the 100, and
Package 1's renewal must NOT gain it.

Commission: the activation is part of a renewal, never a new ladder sale. The
ladder counts sales from sales orders; a later payment can only ever reach the
existing renewal rule (10 JOD per renewed period), asserted below.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tests.conftest import jordan_today
from tests.test_step0_ladder import (
    _entries, _ladder_sales, _pay_month, _rows, _sell, _summary, _world,
)
import tests.test_step0_ladder as ladder_tests

JOD = "JOD"


@pytest.fixture()
def owner_prices(monkeypatch):
    """The owner's real numbers: P2 35 + 100 activation, P3 25 + 100 activation
    (the shared ladder world seeds both with a 0 setup fee and P3 at 20)."""
    monkeypatch.setitem(ladder_tests._PRICES, "p2", Decimal("135.00"))
    monkeypatch.setitem(ladder_tests._PRICES, "p3", Decimal("125.00"))


def _owner_world(app):
    w = _world(app)
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.catalog import PlanPrice

        for key, base in (("p2", Decimal("35.00")), ("p3", Decimal("25.00"))):
            price = db_session.execute(
                select(PlanPrice).where(PlanPrice.plan_id == w[key], PlanPrice.effective_until.is_(None))
            ).scalars().one()
            price.base_price, price.setup_fee = base, Decimal("100.00")
        db_session.commit()
    return w


def _due(app, subscription_id, n):
    with app.app_context():
        from app.commercial_sales.packages import payment_due
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        return payment_due(db_session.get(Subscription, subscription_id), n)


def test_p2_month_13_carries_the_activation_months_14_to_24_do_not(app, seeded, owner_prices):
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    assert _due(app, sub, 1) == (Decimal("135.00"), JOD)            # the sale itself
    for n in range(2, 13):
        assert _due(app, sub, n) == (Decimal("35.00"), JOD), n       # months 2..12
    assert _due(app, sub, 13) == (Decimal("135.00"), JOD)            # year 2 starts
    for n in range(14, 25):
        assert _due(app, sub, n) == (Decimal("35.00"), JOD), n       # months 14..24
    assert _due(app, sub, 25) == (Decimal("135.00"), JOD)            # year 3 starts


def test_p3_month_13_and_25_carry_the_activation(app, seeded, owner_prices):
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p3")["order_id"])["subscription_id"]
    assert _due(app, sub, 1) == (Decimal("125.00"), JOD)
    assert _due(app, sub, 12) == (Decimal("25.00"), JOD)
    assert _due(app, sub, 13) == (Decimal("125.00"), JOD)
    assert _due(app, sub, 24) == (Decimal("25.00"), JOD)
    assert _due(app, sub, 25) == (Decimal("125.00"), JOD)
    assert _due(app, sub, 26) == (Decimal("25.00"), JOD)


def test_p1_renewal_is_unchanged_100_only(app, seeded, owner_prices):
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p1")["order_id"])["subscription_id"]
    assert _due(app, sub, 1) == (Decimal("250.00"), JOD)
    for n in (2, 3, 13, 25):
        assert _due(app, sub, n) == (Decimal("100.00"), JOD), n   # the 150 first-time fee never comes back
    with app.app_context():
        from app.commercial_sales.packages import renewal_price
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        assert renewal_price(db_session.get(Subscription, sub)) == (Decimal("100.00"), JOD)


def test_next_payment_due_counts_payments_so_far(app, seeded, owner_prices):
    """The sale's own payment is #1, so the next one is 2 (35); after 11 more
    confirmed payments the next is #13 (135). A FAILED payment is not a month."""
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]

    def nxt():
        with app.app_context():
            from app.commercial_sales.packages import next_payment_due
            from app.extensions import db_session
            from app.models.subscriptions import Subscription

            return next_payment_due(db_session.get(Subscription, sub))

    assert nxt() == (Decimal("35.00"), JOD)
    for _ in range(11):
        _pay_month(app, w, sub, "35.00")
    assert nxt() == (Decimal("135.00"), JOD)
    _pay_month(app, w, sub, "135.00")
    assert nxt() == (Decimal("35.00"), JOD)


def test_catalog_states_the_yearly_activation_only_for_monthly_packages(app, seeded, owner_prices):
    w = _owner_world(app)
    with app.app_context():
        from app.commercial_sales.catalog_for_sales import describe_plan_for_sale

        assert describe_plan_for_sale(w["p1"])["yearly_activation_fee"] is None
        assert describe_plan_for_sale(w["p2"])["yearly_activation_fee"] == Decimal("100.00")
        assert describe_plan_for_sale(w["p3"])["yearly_activation_fee"] == Decimal("100.00")


def test_the_yearly_activation_is_not_a_new_ladder_sale(app, seeded, owner_prices):
    """Month 13 (135 JOD, after the contract ended) is a renewal: the ladder still
    counts exactly the one sale and its LADDER rows do not change; the only new
    commission row is the existing 10 JOD renewal for the new contract period."""
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    ladder_before = _entries(app, w["seller_profile"], kind="LADDER")
    sales_before = _ladder_sales(app, w["seller_profile"])
    assert len(sales_before) == 1 and _entries(app, w["seller_profile"], kind="RENEWAL") == []
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        db_session.get(Subscription, sub).contract_ends_on = jordan_today() - timedelta(days=1)
        db_session.commit()
    _pay_month(app, w, sub, "135.00")
    assert _entries(app, w["seller_profile"], kind="LADDER") == ladder_before
    assert _ladder_sales(app, w["seller_profile"]) == sales_before
    assert [(r[1], r[2]) for r in _entries(app, w["seller_profile"], kind="RENEWAL")] == [(Decimal("10.00"), "EARNED")]
    assert _summary(app, w["seller_profile"])["count"] == 1
