"""Yearly 100 JOD activation on the monthly packages (owner rule 2026-10-30).

Package 2 (35 a month, 12-month contract) and Package 3 (25 a month, 24-month
contract) charge the 100 JOD activation EVERY contract year -- the first payment
of contract year 2, 3, ... includes it -- not only on the first sale. Package 1
(250 the first year, then 100 a year) is unchanged.

The activation is the plan price's setup_fee (no new column). Contract years run
by DATE (contract start + 12*(k-1) months, Jordan business dates), NOT by how many
payment records exist: verifier finding 2026-10-30 -- split payments, a combined
payment (70 JOD for two months) and a lapse each moved a count-based +100 to the
wrong month. packages.next_payment_due says what a payment dated D should be; the
activation is due ONCE in a contract year, until a counted payment dated in that
year carried it (at least a month + the activation, same currency).

Every guard here is paired with its opposite (ENGINEERING 1): the year's later
payments must NOT carry the 100, a payment too small to hold it must NOT count as
having carried it, and Package 1's renewal must NOT gain it.

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
    _entries, _ladder_sales, _pay_month, _rows, _sell, _sell_counted, _summary, _world,
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


def _quote(app, sub, on=None):
    """next_payment_due for a payment dated `on` (default: Jordan's today)."""
    with app.app_context():
        from app.commercial_sales.packages import next_payment_due
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        return next_payment_due(db_session.get(Subscription, sub), on)


def _months_after(start, n):
    from app.commercial_ops.renewal_dates import add_interval

    return add_interval(start, months=n)


def _contract_start(app, sub):
    with app.app_context():
        from app.commercial_sales.packages import contract_start
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        return contract_start(db_session.get(Subscription, sub))


def rebase_contract(app, sub, months_ago):
    """The contract began `months_ago` months ago: contract_ends_on moves so the
    contract start is that day, and the payments that exist (the sale's own) are
    dated that day too. Tests then add payments with explicit past dates -- a
    payment can never be dated in the future."""
    with app.app_context():
        from sqlalchemy import text

        from app.commercial_ops.renewal_dates import add_interval
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        row = db_session.get(Subscription, sub)
        start = add_interval(jordan_today(), months=-months_ago)
        row.contract_ends_on = add_interval(start, months=row.contract_term_months)
        db_session.execute(text(
            "UPDATE owner_payment_records SET payment_date = :d WHERE id IN ("
            " SELECT p.id FROM owner_payment_records p"
            " LEFT JOIN owner_commercial_invoices i ON i.id = p.commercial_invoice_id"
            " LEFT JOIN owner_payment_allocations a ON a.payment_record_id = p.id"
            " LEFT JOIN owner_commercial_invoices i2 ON i2.id = a.commercial_invoice_id"
            " WHERE p.subscription_id = :s OR i.sales_order_id = :o OR i2.sales_order_id = :o)"),
            {"d": start, "s": sub, "o": row.sales_order_id})
        db_session.commit()
        return start


def _pay(app, w, sub, amount, on):
    return _pay_month(app, w, sub, amount, payment_date=on)


def _set_status(app, w, payment_id, status):
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord
        from app.subscriptions.services import correct_payment

        correct_payment(db_session.get(PaymentRecord, payment_id), status, f"test {status}", w["admin"])


def test_p2_the_activation_is_due_on_the_first_payment_of_each_contract_year_by_date(app, seeded, owner_prices):
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    start = _contract_start(app, sub)
    assert start == jordan_today()                                        # a contract starts the day it was sold
    assert _quote(app, sub, start) == (Decimal("35.00"), JOD)             # the sale itself was paid: just the month
    assert _quote(app, sub, _months_after(start, 12) - timedelta(days=1)) == (Decimal("35.00"), JOD)   # last day of year 1
    assert _quote(app, sub, _months_after(start, 12)) == (Decimal("135.00"), JOD)                     # first day of year 2
    assert _quote(app, sub, _months_after(start, 24) - timedelta(days=1)) == (Decimal("135.00"), JOD)  # nothing paid in year 2 yet
    assert _quote(app, sub, _months_after(start, 24)) == (Decimal("135.00"), JOD)                     # year 3


def test_p3_year_2_starts_at_month_12_inside_the_24_month_contract(app, seeded, owner_prices):
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p3")["order_id"])["subscription_id"]
    start = _contract_start(app, sub)
    assert _quote(app, sub, _months_after(start, 11)) == (Decimal("25.00"), JOD)
    assert _quote(app, sub, _months_after(start, 12)) == (Decimal("125.00"), JOD)
    assert _quote(app, sub, _months_after(start, 24)) == (Decimal("125.00"), JOD)
    assert _quote(app, sub, _months_after(start, 36)) == (Decimal("125.00"), JOD)


def test_p1_renewal_is_unchanged_100_only(app, seeded, owner_prices):
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p1")["order_id"])["subscription_id"]
    start = jordan_today()
    for n in (0, 1, 12, 13, 24, 25, 36):
        assert _quote(app, sub, _months_after(start, n)) == (Decimal("100.00"), JOD), n   # the 150 first-time fee never comes back
    with app.app_context():
        from app.commercial_sales.packages import renewal_price
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        assert renewal_price(db_session.get(Subscription, sub)) == (Decimal("100.00"), JOD)


def test_the_activation_is_due_once_per_contract_year(app, seeded, owner_prices):
    """Year 2: a month's 35 cannot carry the activation, so it is still asked for;
    once a payment of month + activation (135) is in, later payments that year are
    35; year 3 asks for it again."""
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    start = rebase_contract(app, sub, 13)                                  # today is in contract year 2
    today = jordan_today()
    assert _quote(app, sub) == (Decimal("135.00"), JOD)
    _pay(app, w, sub, "35.00", today)                                      # the activation forgotten
    assert _quote(app, sub) == (Decimal("135.00"), JOD)                    # a month's payment did not carry it
    _pay(app, w, sub, "135.00", today)
    assert _quote(app, sub) == (Decimal("35.00"), JOD)                     # carried: not due again this year
    _pay(app, w, sub, "35.00", today)
    assert _quote(app, sub) == (Decimal("35.00"), JOD)
    assert _quote(app, sub, _months_after(start, 24)) == (Decimal("135.00"), JOD)   # year 3 asks again


def test_a_combined_payment_that_includes_the_activation_carries_it(app, seeded, owner_prices):
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    rebase_contract(app, sub, 13)
    _pay(app, w, sub, "170.00", jordan_today())                            # the activation + two months
    assert _quote(app, sub) == (Decimal("35.00"), JOD)


def test_deposit_and_balance_in_the_first_sale_do_not_shift_the_activation(app, seeded, owner_prices):
    """Year 1 holds the sale plus a deposit/balance split: three payment records.
    The count rule quoted payment #4 -> 35 on the first day of year 2; by date it
    is the first payment of year 2 and carries the activation."""
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    start = rebase_contract(app, sub, 13)
    _pay(app, w, sub, "50.00", _months_after(start, 1))                    # deposit
    _pay(app, w, sub, "20.00", _months_after(start, 1))                    # balance of that month's 70 -> split
    assert _quote(app, sub) == (Decimal("135.00"), JOD)


def test_one_70_jod_payment_for_two_months_does_not_shift_the_activation(app, seeded, owner_prices):
    """The sale + one 70 payment (months 2-3) + nine months of 35: eleven records
    in year 1, twelve months paid. The count rule saw record #12 -> 35."""
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    start = rebase_contract(app, sub, 13)
    _pay(app, w, sub, "70.00", _months_after(start, 1))
    for month in range(3, 12):
        _pay(app, w, sub, "35.00", _months_after(start, month))
    assert _quote(app, sub) == (Decimal("135.00"), JOD)


def test_a_three_month_lapse_then_resume_still_asks_for_the_activation_on_the_first_year_2_payment(app, seeded, owner_prices):
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    start = rebase_contract(app, sub, 13)
    for month in (1, 2, 3):                                                # months 2-4 paid, then nothing for 3 months
        _pay(app, w, sub, "35.00", _months_after(start, month))
    for month in (7, 8):                                                   # resumed at month 8
        _pay(app, w, sub, "35.00", _months_after(start, month))
    assert _quote(app, sub) == (Decimal("135.00"), JOD)                    # by the calendar it is year 2: the activation
    _pay(app, w, sub, "135.00", jordan_today())
    assert _quote(app, sub) == (Decimal("35.00"), JOD)                     # and not again "when the 13th record arrives"


def test_a_failed_payment_never_counts_as_the_year_2_activation(app, seeded, owner_prices):
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    rebase_contract(app, sub, 13)
    pid = _pay(app, w, sub, "135.00", jordan_today())
    assert _quote(app, sub) == (Decimal("35.00"), JOD)                     # pending/confirmed 135 carries it ...
    _set_status(app, w, pid, "FAILED")
    assert _quote(app, sub) == (Decimal("135.00"), JOD)                    # ... a bounced one does not


def test_a_payment_in_another_currency_cannot_carry_the_activation(app, seeded, owner_prices):
    """135 USD is not 135 JOD: the amounts are only comparable in the plan's currency."""
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    rebase_contract(app, sub, 13)
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription
        from app.subscriptions.services import record_payment

        row = db_session.get(Subscription, sub)
        record_payment({"customer_id": row.customer_id, "subscription_id": sub, "amount": Decimal("500.00"),
                        "currency": "USD", "payment_date": jordan_today(), "status": "PENDING"}, w["seller"])
    assert _quote(app, sub) == (Decimal("135.00"), JOD)


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
    # 2026-10-30: a monthly sale counts at its SECOND payment, so the sale is
    # sold with that payment in; month 13 is then the later payment under test.
    sub = _rows(app, _sell_counted(app, w, "p2")["order_id"])["subscription_id"]
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


# ---------------------------------------------- only money that came in counts
# Verifier gap (3a): the status filter (PENDING/CONFIRMED only) in counted_payments was
# never tested with a payment that is NOT money in. Each of the three ways a payment
# reaches a subscription (its own subscription_id, an allocation to the sale's invoice,
# the sale invoice id on the payment itself) has its own query and its own filter, so
# each is exercised here with the status that must not count.
NOT_MONEY_IN = ["FAILED", "VOIDED", "REFUNDED"]


@pytest.mark.parametrize("status", NOT_MONEY_IN)
def test_a_year_2_payment_that_is_not_money_in_does_not_carry_the_activation(app, seeded, owner_prices, status):
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    rebase_contract(app, sub, 13)
    pid = _pay(app, w, sub, "135.00", jordan_today())
    assert _quote(app, sub) == (Decimal("35.00"), JOD)                     # control: it counts while pending/confirmed
    _set_status(app, w, pid, status)
    assert _quote(app, sub) == (Decimal("135.00"), JOD)                    # the activation is still owed


@pytest.mark.parametrize("status", NOT_MONEY_IN)
def test_a_sale_payment_that_is_not_money_in_leaves_the_first_sale_price_due(app, seeded, owner_prices, status):
    """The sale's own payment reaches the subscription through the sale's invoice (an
    allocation). If it bounced, nothing has been paid: the next payment is the first-sale
    price (month + activation), not just a month."""
    w = _owner_world(app)
    sold = _sell(app, w, "p2")
    sub = _rows(app, sold["order_id"])["subscription_id"]
    assert _quote(app, sub) == (Decimal("35.00"), JOD)                     # control: paid sale -> just the month
    with app.app_context():
        from sqlalchemy import text

        from app.extensions import db_session

        db_session.execute(text("UPDATE owner_payment_records SET status = :s WHERE id = :p"),
                           {"s": status, "p": sold["payment_id"]})
        db_session.commit()
    assert _quote(app, sub) == (Decimal("135.00"), JOD)


@pytest.mark.parametrize("status", NOT_MONEY_IN)
def test_a_payment_recorded_for_the_sale_invoice_counts_only_while_it_is_money_in(app, seeded, owner_prices, status):
    """A payment that names the sale's invoice but no subscription (the commercial-sales
    payment screen's invoice field) reaches the subscription by its own query."""
    w = _owner_world(app)
    order_id = _sell(app, w, "p2")["order_id"]
    rows = _rows(app, order_id)
    sub = rows["subscription_id"]
    rebase_contract(app, sub, 13)
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord
        from app.subscriptions.services import record_payment

        payment = record_payment({"customer_id": rows["customer_id"], "commercial_invoice_id": rows["invoice_id"],
                                  "amount": Decimal("135.00"), "currency": JOD, "payment_date": jordan_today(),
                                  "status": "PENDING"}, w["seller"])
        pid = payment.id
        assert db_session.get(PaymentRecord, pid).subscription_id is None
    assert _quote(app, sub) == (Decimal("35.00"), JOD)
    _set_status(app, w, pid, status)
    assert _quote(app, sub) == (Decimal("135.00"), JOD)


# --------------------------------- the activation is judged on the year's SUM
# Verifier findings (2026-10-30, MEDIUM): the old test asked whether any ONE payment
# was >= month + activation. (a) 140 paid for four months at the start of year 2
# (4 x 35, no activation in it) looked like it carried the 100; (b) the activation
# paid as 100 then 35 was never recognised, so 135 was quoted all year.
# See packages.activation_carried for the rule and why.
def test_140_for_four_months_at_the_start_of_year_2_does_not_carry_the_activation(app, seeded, owner_prices):
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    start = rebase_contract(app, sub, 13)
    _pay(app, w, sub, "140.00", _months_after(start, 12))                  # 4 x 35 on the first day of year 2
    assert _quote(app, sub) == (Decimal("135.00"), JOD)                    # the 100 is still owed


def test_the_activation_paid_as_100_then_35_is_carried_and_the_next_quote_is_35(app, seeded, owner_prices):
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    rebase_contract(app, sub, 13)
    _pay(app, w, sub, "100.00", jordan_today())
    assert _quote(app, sub) == (Decimal("35.00"), JOD)                     # the activation alone is paid: a month is next
    _pay(app, w, sub, "35.00", jordan_today())
    assert _quote(app, sub) == (Decimal("35.00"), JOD)                     # 135 all year was the bug


def test_a_single_135_is_carried(app, seeded, owner_prices):
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    rebase_contract(app, sub, 13)
    assert _quote(app, sub) == (Decimal("135.00"), JOD)
    _pay(app, w, sub, "135.00", jordan_today())
    assert _quote(app, sub) == (Decimal("35.00"), JOD)


def test_a_part_paid_activation_that_does_not_split_exactly_is_still_quoted_in_full(app, seeded, owner_prices):
    """The safe direction: 100 then 40 leaves 40 over, which is not a whole month."""
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    rebase_contract(app, sub, 13)
    _pay(app, w, sub, "50.00", jordan_today())
    assert _quote(app, sub) == (Decimal("135.00"), JOD)                    # 50 < 100: not carried
    _pay(app, w, sub, "50.00", jordan_today())
    assert _quote(app, sub) == (Decimal("35.00"), JOD)                     # exactly 100 between them: carried


def test_the_year_1_sale_is_unchanged_by_the_sum_rule(app, seeded, owner_prices):
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    start = rebase_contract(app, sub, 5)                                   # still inside year 1
    assert _quote(app, sub) == (Decimal("35.00"), JOD)                     # the sale carried the first activation
    _pay(app, w, sub, "140.00", _months_after(start, 1))
    assert _quote(app, sub) == (Decimal("35.00"), JOD)


# ------------------------------------------------- the year window's two edges
def test_a_payment_on_the_first_day_of_year_2_belongs_to_year_2(app, seeded, owner_prices):
    """Kills `first_day <= date` -> `first_day < date`: a 135 dated exactly on the first
    day of year 2 must carry year 2's activation."""
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    start = rebase_contract(app, sub, 13)
    _pay(app, w, sub, "135.00", _months_after(start, 12))
    assert _quote(app, sub, _months_after(start, 12) + timedelta(days=1)) == (Decimal("35.00"), JOD)


def test_a_payment_on_the_first_day_of_year_3_does_not_count_in_year_2(app, seeded, owner_prices):
    """Kills `date < next_year_first_day` -> `<=`: the 135 dated on year 3's first day is
    year 3's money; year 2 still owes its own activation."""
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    start = rebase_contract(app, sub, 25)
    _pay(app, w, sub, "135.00", _months_after(start, 24))
    assert _quote(app, sub, _months_after(start, 13)) == (Decimal("135.00"), JOD)          # year 2
    assert _quote(app, sub, _months_after(start, 24)) == (Decimal("35.00"), JOD)           # year 3 has it
