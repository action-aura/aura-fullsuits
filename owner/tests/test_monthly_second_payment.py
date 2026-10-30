"""Owner decision 2026-10-30: a MONTHLY package (Package 2 / 3) counts on the
seller's commission ladder when its SECOND confirmed payment arrives, in the
month THAT payment is confirmed -- not at the first (activation) payment, as
from 2026-10-26. It is never counted if the second payment never comes; if the
second payment is refunded inside the hold the sale is un-counted (through the
existing correction path); Package 1 still counts at its single payment.

What each test pins, and what a wrong implementation would do:
  - not counted at payment 1 / pending payment 2   -> "count at the first payment" (the old rule)
  - counted at payment 2, in payment 2's month     -> "count at the sale's own date"
  - hold runs from payment 2                       -> "hold from the sale"
  - refunded payment 2 un-counts                   -> a count that never looks again
  - Package 1 counts at payment 1                  -> "monthly rule applied to everything"
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tests.test_step0_ladder import (
    _entries,
    _ladder_sales,
    _net_by_order,
    _pay_month,
    _rows,
    _sell,
    _summary,
    _world,
)

D = Decimal


def _correct(app, payment_id, status, actor):
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord
        from app.subscriptions.services import correct_payment

        correct_payment(db_session.get(PaymentRecord, payment_id), status, f"test {status}", actor)


def _month_of(app, moment):
    with app.app_context():
        from app.commissions.ladder import ladder_month_of

        return ladder_month_of(moment)


def test_p2_is_not_counted_at_payment_1_and_not_while_payment_2_is_only_pending(app, seeded):
    w = _world(app)
    sale = _sell(app, w, "p2")
    assert _ladder_sales(app, w["seller_profile"]) == []
    assert _entries(app, w["seller_profile"]) == []
    assert _summary(app, w["seller_profile"])["monthly_count"] == 0

    # A second payment that is only RECORDED (not confirmed) counts nothing.
    sub = _rows(app, sale["order_id"])["subscription_id"]
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription
        from app.subscriptions.services import record_payment

        s = db_session.get(Subscription, sub)
        pending = record_payment(
            {"customer_id": s.customer_id, "subscription_id": s.id, "amount": D("35.00"), "currency": "JOD",
             "method": "BANK_TRANSFER", "payment_date": datetime.now(timezone.utc).date(),
             "reference": "PENDING-2", "status": "PENDING"},
            w["seller"],
        )
        pending_id = pending.id
    assert _ladder_sales(app, w["seller_profile"]) == []

    # allow-half: confirming it is what counts the sale
    with app.app_context():
        from app.commercial_sales.payments import confirm_payment
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord

        confirm_payment(db_session.get(PaymentRecord, pending_id), actor_staff_user_id=w["manager"])
    [counted] = _ladder_sales(app, w["seller_profile"])
    assert (counted["status"], counted["payment_id"]) == ("COUNTED", pending_id)


def test_p3_follows_the_same_rule(app, seeded):
    w = _world(app)
    sale = _sell(app, w, "p3")
    assert _ladder_sales(app, w["seller_profile"]) == []
    second = _pay_month(app, w, _rows(app, sale["order_id"])["subscription_id"], "20.00")
    [counted] = _ladder_sales(app, w["seller_profile"])
    assert (counted["status"], counted["payment_id"]) == ("COUNTED", second)


def test_the_sale_lands_in_the_month_of_payment_2_and_its_hold_runs_from_payment_2(app, seeded):
    """Payment 1 in one calendar month, payment 2 in a LATER one (75 and 35
    days ago: always different months): the sale is the later month's, the
    earlier month's ladder never sees it, and the 30-day hold starts at
    payment 2."""
    w = _world(app)
    now = datetime.now(timezone.utc)
    first_at, second_at = now - timedelta(days=75), now - timedelta(days=35)
    first_month, second_month = _month_of(app, first_at), _month_of(app, second_at)
    assert first_month != second_month

    sale = _sell(app, w, "p2", confirmed_at=first_at)
    second = _pay_month(app, w, _rows(app, sale["order_id"])["subscription_id"], "35.00",
                        confirmed_exactly_at=second_at)

    [counted] = _ladder_sales(app, w["seller_profile"])
    assert counted["payment_id"] == second
    assert counted["month"] == second_month
    assert counted["qualified_at"] == second_at
    assert counted["hold_ends_at"] == second_at + timedelta(days=30)
    earlier = _summary(app, w["seller_profile"], month=first_month)
    later = _summary(app, w["seller_profile"], month=second_month)
    assert (earlier["monthly_count"], earlier["monthly_total"]) == (0, D("0.00"))
    assert (later["monthly_count"], later["monthly_total"]) == (1, D("5.00"))


def test_a_refunded_payment_2_uncounts_the_sale_and_the_month_re_derives(app, seeded):
    w = _world(app)
    first = _sell(app, w, "p2", second_payment=True)
    other = _sell(app, w, "p3", second_payment=True)
    assert _summary(app, w["seller_profile"])["monthly_total"] == D("10.00")  # two sales: 5 + 5

    _correct(app, first["second_payment_id"], "REFUNDED", w["admin"])

    rows = {s["order_id"]: s for s in _ladder_sales(app, w["seller_profile"])}
    assert (rows[first["order_id"]]["status"], rows[first["order_id"]]["cause"]) == ("UNCOUNTED", "CORRECTION")
    assert rows[other["order_id"]]["status"] == "COUNTED"
    summary = _summary(app, w["seller_profile"])
    assert (summary["monthly_count"], summary["monthly_total"]) == (1, D("5.00"))  # the other sale is #1 again
    net = _net_by_order(app, w["seller_profile"])
    assert (net.get(first["order_id"], D("0.00")), net[other["order_id"]]) == (D("0.00"), D("5.00"))

    # allow-half: the shop pays again -- that confirmed payment IS now its
    # second confirmed payment, and the sale counts again from it.
    third = _pay_month(app, w, _rows(app, first["order_id"])["subscription_id"], "35.00")
    again = [s for s in _ladder_sales(app, w["seller_profile"]) if s["status"] == "COUNTED"
             and s["order_id"] == first["order_id"]]
    assert [s["payment_id"] for s in again] == [third]


def test_package_1_still_counts_at_its_only_payment(app, seeded):
    w = _world(app)
    sale = _sell(app, w, "p1")
    [counted] = _ladder_sales(app, w["seller_profile"])
    assert (counted["status"], counted["payment_id"]) == ("COUNTED", sale["payment_id"])
    summary = _summary(app, w["seller_profile"])
    assert (summary["yearly_count"], summary["yearly_total"], summary["monthly_count"]) == (1, D("20.00"), 0)


def test_a_third_payment_does_not_count_the_sale_a_second_time(app, seeded):
    w = _world(app)
    sale = _sell(app, w, "p2", second_payment=True)
    sub = _rows(app, sale["order_id"])["subscription_id"]
    _pay_month(app, w, sub, "35.00")
    _pay_month(app, w, sub, "35.00")
    assert len([s for s in _ladder_sales(app, w["seller_profile"]) if s["status"] == "COUNTED"]) == 1
    assert _summary(app, w["seller_profile"])["monthly_total"] == D("5.00")
