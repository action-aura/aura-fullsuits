"""Step 0 (2026-10-24) -- the owner's monthly commission LADDER and the REFUND rules.

The owner's decisions (Aura Growth Plan, "Your answers" + the 2026-10-24
additions; the 2-day licence rule from the owner's brief):
  - per seller, per calendar month in Jordan time (Asia/Amman), the count
    restarts each month and the ladder numbers are TOTALS: 1 sale = 20,
    2 = 50, 3 = 100, 4 = 200, 5 = 350 JOD, each sale after the 5th +30;
  - a sale is a new package sale for a branch; extra devices (the pair) never
    count and earn nothing; Package 1 counts when its 250 is confirmed;
    a monthly package counts once about 100 JOD is paid -- Package 2 at its
    3rd payment, Package 3 at its 5th -- in the month THAT payment is
    confirmed, and never if the customer stops before;
  - 10 JOD for each renewal; commission is payable 30 days after the
    confirming date; a refund inside that hold means the sale does not count;
  - "Commission follows the rule exactly: no commission on the extra-device
    part of any payment" (also for sellers still on a percentage plan);
  - a refunded licence keeps working 2 days, then stops.

Every test asserts ledger AMOUNTS and ROWS, not just status codes.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import force_login, make_staff
from tests.test_issuance_sale import _codes, _profile
from tests.test_step0_packages import _make_plan

JOD = "JOD"
LADDER = "MONTHLY_LADDER"


# ------------------------------------------------------------------ setup --

def _world(app, *, rule=LADDER, rate=Decimal("10.0"), rule_from_days_ago=120):
    """A seller (SALES) and a second seller, a manager (FINANCE), a Super
    Admin -- each with an employee profile; the owner's three packages; the
    pair add-on at 50 JOD; the seller (and the second seller) on `rule`."""
    w = {}
    w["seller"] = make_staff(app, f"seller-{uuid.uuid4().hex[:6]}@example.com", role_codes=["SALES"], mfa=True)
    w["seller2"] = make_staff(app, f"seller2-{uuid.uuid4().hex[:6]}@example.com", role_codes=["SALES"], mfa=True)
    w["manager"] = make_staff(app, f"manager-{uuid.uuid4().hex[:6]}@example.com", role_codes=["FINANCE"], mfa=True)
    w["admin"] = make_staff(app, f"admin-{uuid.uuid4().hex[:6]}@example.com", super_admin=True, mfa=True)
    w["seller_profile"] = _profile(app, w["seller"], f"EMP-S-{uuid.uuid4().hex[:4]}", "Sami Seller")
    w["seller2_profile"] = _profile(app, w["seller2"], f"EMP-T-{uuid.uuid4().hex[:4]}", "Tala Seller")
    w["manager_profile"] = _profile(app, w["manager"], f"EMP-M-{uuid.uuid4().hex[:4]}", "Mona Manager")
    w["admin_profile"] = _profile(app, w["admin"], f"EMP-A-{uuid.uuid4().hex[:4]}", "Adam Admin")
    since = date.today() - timedelta(days=rule_from_days_ago)
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.catalog import Addon, Plan, Product

        product = db_session.execute(select(Product).where(Product.product_code == "AURA_RETAIL")).scalars().one()
        product.is_sellable = True
        product.is_active = True
        w["p1"] = _make_plan(db_session, product, code="T_P1", name="Aura Retail Package 1", billing_model="ANNUAL",
                             interval=12, base=Decimal("100.00"), setup=Decimal("150.00"))
        w["p2"] = _make_plan(db_session, product, code="T_P2", name="Aura Retail Package 2", billing_model="MONTHLY",
                             interval=1, base=Decimal("35.00"), setup=Decimal("0"), contract=12)
        w["p3"] = _make_plan(db_session, product, code="T_P3", name="Aura Retail Package 3", billing_model="MONTHLY",
                             interval=1, base=Decimal("20.00"), setup=Decimal("0"), contract=24)
        for key in ("p1", "p2", "p3"):  # sellable since long enough for the back-dated tests
            db_session.get(Plan, w[key]).effective_date = since
        pair = db_session.execute(select(Addon).where(Addon.addon_code == "EXTRA_DEVICE_PAIR")).scalars().one()
        pair.price, pair.currency, pair.availability_status = Decimal("50.00"), JOD, "AVAILABLE"
        db_session.commit()
    w["rule_plan"] = _assign(app, w, [w["seller_profile"], w["seller2_profile"]], rule=rule, rate=rate, since=since)
    return w


def _assign(app, w, profiles, *, rule, rate=Decimal("10.0"), since):
    with app.app_context():
        from app.commissions.management import (
            assign_employee_commission_plan,
            create_commission_plan,
            create_commission_rule_version,
        )

        plan = create_commission_plan({"plan_code": f"C_{uuid.uuid4().hex[:6]}", "name": f"Sellers {rule}"}, w["admin"])
        if rule == LADDER:
            create_commission_rule_version(plan, rule_type=LADDER, currency=JOD, effective_from=since,
                                           actor_staff_user_id=w["admin"])
        else:
            create_commission_rule_version(plan, rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=rate,
                                           effective_from=since, actor_staff_user_id=w["admin"])
        for profile_id in profiles:
            assign_employee_commission_plan(profile_id, plan, effective_from=since, actor_staff_user_id=w["admin"])
        return plan.id


_PRICES = {"p1": Decimal("250.00"), "p2": Decimal("35.00"), "p3": Decimal("20.00")}


def _backdate_confirmation(payment_id, when: datetime):
    """The payment was confirmed at `when` (its CONFIRMED history row -- the
    payment-confirmed time the owner counts commission by)."""
    from app.extensions import db_session

    db_session.execute(
        text("UPDATE owner_payment_correction_history SET created_at = :t "
             "WHERE payment_record_id = :p AND new_status = 'CONFIRMED'"),
        {"t": when, "p": payment_id},
    )
    db_session.commit()


def _sell(app, w, plan_key, *, pairs=0, seller="seller", confirmed_at: datetime | None = None):
    """One sale through the one-screen sale: recorded by the seller, confirmed
    by the manager. With confirmed_at, the manager confirms the payment on
    the payments screen first, at that moment, then finishes the sale."""
    amount = _PRICES[plan_key] + Decimal("50.00") * pairs
    with app.app_context():
        from app.commercial_sales.direct_sale import confirm_direct_sale, record_direct_sale
        from app.commercial_sales.payments import confirm_payment
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord

        result = record_direct_sale(
            new_customer_legal_name=f"Shop {uuid.uuid4().hex[:6]}", plan_id=w[plan_key], extra_device_pairs=pairs,
            amount=amount, method="BANK_TRANSFER", reference=f"CLIQ-{uuid.uuid4().hex[:6]}",
            payment_date=date.today(),
            idempotency_key=str(uuid.uuid4()), actor_staff_user_id=w[seller], actor_permission_codes=_codes(w[seller]),
            as_of=date.today(),
        )
        if confirmed_at is not None:
            confirm_payment(db_session.get(PaymentRecord, result["payment_id"]), actor_staff_user_id=w["manager"])
            _backdate_confirmation(result["payment_id"], confirmed_at)
        done = confirm_direct_sale(result["order_id"], actor_staff_user_id=w["manager"], license_pepper=app.config["LICENSE_PEPPER"])
        return {"order_id": result["order_id"], "payment_id": result["payment_id"], "full_key": done["full_key"]}


def _rows(app, order_id):
    """invoice, subscription, licence ids of a finished sale."""
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        inv = db_session.execute(select(CommercialInvoice).where(CommercialInvoice.sales_order_id == order_id)).scalars().one()
        sub = db_session.execute(select(Subscription).where(Subscription.sales_order_id == order_id)).scalars().one()
        lic = db_session.execute(select(License).where(License.subscription_id == sub.id)).scalars().one()
        return {"invoice_id": inv.id, "subscription_id": sub.id, "license_id": lic.id, "customer_id": sub.customer_id}


def _pay_month(app, w, subscription_id, amount, *, payment_date=None, confirmed_at=None, confirmer="manager"):
    """A later monthly (or renewal) payment: recorded by the seller against
    the subscription (the legacy payments screen), confirmed by a manager."""
    with app.app_context():
        from app.commercial_sales.payments import confirm_payment
        from app.extensions import db_session
        from app.models.subscriptions import Subscription
        from app.subscriptions.services import record_payment

        sub = db_session.get(Subscription, subscription_id)
        payment = record_payment(
            {"customer_id": sub.customer_id, "subscription_id": sub.id, "amount": Decimal(amount), "currency": JOD,
             "method": "BANK_TRANSFER", "payment_date": payment_date or date.today(),
             "reference": f"CLIQ-{uuid.uuid4().hex[:6]}", "status": "PENDING"},
            w["seller"],
        )
        payment_id = payment.id
        confirm_payment(payment, actor_staff_user_id=w[confirmer])
        if confirmed_at is not None:
            _backdate_confirmation(payment_id, confirmed_at)
        return payment_id


def _entries(app, profile_id, kind="LADDER"):
    """[(order_id or None, amount, status, position, month)] -- every ledger row of that kind."""
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models import commissions as models

        Entry = models.CommissionLedgerEntry
        stmt = select(Entry).where(Entry.employee_profile_id == profile_id)
        if hasattr(Entry, "entry_kind"):
            stmt = stmt.where(Entry.entry_kind == kind)
        elif kind != "PAYMENT_RULE":
            return []  # the code before the ladder: every row is a payment-rule row
        rows = db_session.execute(stmt.order_by(Entry.created_at, Entry.id)).scalars().all()
        out = []
        for e in rows:
            ladder_sale_id = getattr(e, "ladder_sale_id", None)
            order_id = db_session.get(models.CommissionLadderSale, ladder_sale_id).sales_order_id if ladder_sale_id else None
            out.append((order_id, Decimal(e.commission_amount), e.status, getattr(e, "ladder_position", None),
                        getattr(e, "ladder_month", None)))
        return out


def _net_by_order(app, profile_id):
    """{order_id: what the books owe the seller for that sale} -- every row but CANCELLED."""
    net: dict = {}
    for order_id, amount, status, _pos, _month in _entries(app, profile_id):
        if status != "CANCELLED":
            net[order_id] = net.get(order_id, Decimal("0.00")) + amount
    return net


def _ladder_sales(app, profile_id):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models import commissions as models

        if not hasattr(models, "CommissionLadderSale"):
            return []  # the code before the ladder had no ladder
        CommissionLadderSale = models.CommissionLadderSale
        return [
            {"order_id": s.sales_order_id, "status": s.status, "month": s.ladder_month, "qualified_at": s.qualified_at,
             "payment_id": s.qualifying_payment_record_id, "hold_ends_at": s.hold_ends_at, "cause": s.uncount_cause,
             "refunded_after_hold_at": s.refunded_after_hold_at}
            for s in db_session.execute(
                select(CommissionLadderSale).where(CommissionLadderSale.employee_profile_id == profile_id)
                .order_by(CommissionLadderSale.qualified_at)
            ).scalars().all()
        ]


def _summary(app, profile_id, month=None, now=None):
    with app.app_context():
        from app.commissions.ladder import ladder_month_of, ladder_month_summary
        from app.models.base import utcnow

        return ladder_month_summary(profile_id, month or ladder_month_of(utcnow()), now=now)


def _this_month():
    from app.commissions.ladder import ladder_month_of
    from app.models.base import utcnow

    return ladder_month_of(utcnow())


def _refund(app, w, order_id, amount):
    """A refund the normal way: requested by the manager, approved and
    confirmed (paid out) by the Super Admin."""
    rows = _rows(app, order_id)
    with app.app_context():
        from app.commercial_sales.refunds import approve_refund, confirm_refund, create_refund
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice

        invoice = db_session.get(CommercialInvoice, rows["invoice_id"])
        refund = create_refund(invoice, amount=Decimal(amount), reason="Customer changed their mind", payment_record_id=None,
                               actor_employee_profile_id=w["manager_profile"], actor_staff_user_id=w["manager"])
        approve_refund(refund, actor_staff_user_id=w["admin"])
        confirm_refund(refund, actor_staff_user_id=w["admin"])
        return refund.id


# ------------------------------------------------------------ the ladder --

def test_ladder_totals_are_the_owners_cumulative_numbers(app):
    with app.app_context():
        from app.commissions.ladder import ladder_total

        assert [ladder_total(n) for n in range(0, 9)] == [
            Decimal(x) for x in ("0", "20", "50", "100", "200", "350", "380", "410", "440")
        ]


@pytest.mark.parametrize("sales, total", [(1, "20.00"), (3, "100.00"), (5, "350.00"), (7, "410.00")])
def test_a_month_of_sales_adds_up_to_the_ladder_total(app, seeded, sales, total):
    w = _world(app)
    orders = [_sell(app, w, "p1")["order_id"] for _ in range(sales)]
    summary = _summary(app, w["seller_profile"])
    assert (summary["count"], summary["total"]) == (sales, Decimal(total))
    # all of it is still inside the 30-day hold
    assert (summary["held"], summary["payable"]) == (Decimal(total), Decimal("0.00"))
    # the books: one live row per sale, its marginal step, in the order sold
    marginal = [Decimal(x) for x in ("20", "30", "50", "100", "150", "30", "30")][:sales]
    net = _net_by_order(app, w["seller_profile"])
    assert [net[o] for o in orders] == marginal
    assert sum(net.values()) == Decimal(total)
    # nothing on the percentage rule for a ladder seller
    assert _entries(app, w["seller_profile"], kind="PAYMENT_RULE") == []


def test_the_second_seller_has_their_own_ladder(app, seeded):
    w = _world(app)
    for _ in range(3):
        _sell(app, w, "p1")
    _sell(app, w, "p1", seller="seller2")
    assert _summary(app, w["seller_profile"])["total"] == Decimal("100.00")
    assert _summary(app, w["seller2_profile"])["total"] == Decimal("20.00")


def test_a_sale_with_an_extra_pair_counts_and_earns_exactly_like_one_without(app, seeded):
    w = _world(app)
    with_pair = _sell(app, w, "p1", pairs=1)["order_id"]
    plain = _sell(app, w, "p1")["order_id"]
    net = _net_by_order(app, w["seller_profile"])
    assert (net[with_pair], net[plain]) == (Decimal("20.00"), Decimal("30.00"))
    assert _summary(app, w["seller_profile"])["count"] == 2


def test_percentage_seller_earns_nothing_on_the_pair_part_and_never_gets_ladder_rows(app, seeded):
    """Doc: "Commission follows the rule exactly: no commission on the
    extra-device part of any payment." RED on 1025d626: 30.00 (10% of 300,
    the pair's 50 included)."""
    w = _world(app, rule="PERCENT")
    _sell(app, w, "p1", pairs=1)
    _sell(app, w, "p1")
    rule_rows = _entries(app, w["seller_profile"], kind="PAYMENT_RULE")
    assert [r[1] for r in rule_rows] == [Decimal("25.00"), Decimal("25.00")]
    assert _ladder_sales(app, w["seller_profile"]) == []
    assert _entries(app, w["seller_profile"]) == []


# ------------------------------------------------------- monthly packages --

def test_p2_counts_only_at_its_third_payment_in_the_month_that_payment_is_confirmed(app, seeded):
    w = _world(app)
    last_month = datetime.now(timezone.utc) - timedelta(days=40)
    sale = _sell(app, w, "p2", confirmed_at=last_month)
    sub = _rows(app, sale["order_id"])["subscription_id"]
    _pay_month(app, w, sub, "35.00", confirmed_at=last_month + timedelta(days=1))
    assert _ladder_sales(app, w["seller_profile"]) == []  # 70 JOD paid: not yet
    third = _pay_month(app, w, sub, "35.00")
    [counted] = _ladder_sales(app, w["seller_profile"])
    assert (counted["status"], counted["payment_id"], counted["month"]) == ("COUNTED", third, _this_month())
    assert _summary(app, w["seller_profile"])["total"] == Decimal("20.00")
    with app.app_context():
        from app.commissions.ladder import ladder_month_of

        assert _summary(app, w["seller_profile"], month=ladder_month_of(last_month))["count"] == 0
    # a 4th payment never counts the same sale again
    _pay_month(app, w, sub, "35.00")
    assert len(_ladder_sales(app, w["seller_profile"])) == 1
    assert _summary(app, w["seller_profile"])["total"] == Decimal("20.00")


def test_p2_sold_with_a_pair_still_counts_at_its_third_payment(app, seeded):
    """The pair's 50 is not package money: 85 + 35 = 120 must NOT count the
    sale at its 2nd payment."""
    w = _world(app)
    sale = _sell(app, w, "p2", pairs=1)
    sub = _rows(app, sale["order_id"])["subscription_id"]
    _pay_month(app, w, sub, "35.00")
    assert _ladder_sales(app, w["seller_profile"]) == []
    third = _pay_month(app, w, sub, "35.00")
    assert [s["payment_id"] for s in _ladder_sales(app, w["seller_profile"])] == [third]


def test_p3_counts_at_its_fifth_payment(app, seeded):
    w = _world(app)
    sale = _sell(app, w, "p3")
    sub = _rows(app, sale["order_id"])["subscription_id"]
    for _ in range(3):
        _pay_month(app, w, sub, "20.00")
    assert _ladder_sales(app, w["seller_profile"]) == []  # 4 payments, 80 JOD
    fifth = _pay_month(app, w, sub, "20.00")
    [counted] = _ladder_sales(app, w["seller_profile"])
    assert counted["payment_id"] == fifth
    assert _summary(app, w["seller_profile"])["total"] == Decimal("20.00")


def test_p2_customer_who_stops_after_two_payments_never_counts(app, seeded):
    w = _world(app)
    sale = _sell(app, w, "p2")
    _pay_month(app, w, _rows(app, sale["order_id"])["subscription_id"], "35.00")
    assert _ladder_sales(app, w["seller_profile"]) == []
    summary = _summary(app, w["seller_profile"])
    assert (summary["count"], summary["total"]) == (0, Decimal("0.00"))
    assert _entries(app, w["seller_profile"]) == []
    assert _entries(app, w["seller_profile"], kind="PAYMENT_RULE") == []


def test_the_seller_cannot_confirm_a_later_payment_of_their_own_sale(app, seeded):
    """"Nobody confirms their own sale" -- the 3rd monthly payment is what
    counts the sale on the seller's ladder. RED on 1025d626: a payment that
    names only the subscription had no seller, so the seller could confirm it."""
    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p2")
    sub = _rows(app, sale["order_id"])["subscription_id"]
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription
        from app.subscriptions.services import PaymentSelfVerificationError, correct_payment, record_payment

        s = db_session.get(Subscription, sub)
        payment = record_payment(
            {"customer_id": s.customer_id, "subscription_id": s.id, "amount": Decimal("35.00"), "currency": JOD,
             "method": "BANK_TRANSFER", "payment_date": date.today(), "reference": "X", "status": "PENDING"},
            w["manager"],
        )
        with pytest.raises(PaymentSelfVerificationError):
            correct_payment(payment, "CONFIRMED", "own sale", w["seller"])
        # allow-half: the manager who did not record it may
        correct_payment(payment, "CONFIRMED", "ok", w["admin"])
        assert payment.status == "CONFIRMED"


# ----------------------------------------------------- refunds and holds --

def test_refund_inside_the_hold_removes_the_sale_and_the_month_re_derives(app, seeded):
    """Three sales (100); the FIRST is refunded inside its hold. The month
    must end exactly as if it never happened: 2 sales = 50, the two remaining
    sales now first (20) and second (30) -- and no live row still claims the
    old, larger steps."""
    w = _world(app)
    first, second, third = (_sell(app, w, "p1")["order_id"] for _ in range(3))
    assert _summary(app, w["seller_profile"])["total"] == Decimal("100.00")
    _refund(app, w, first, "250.00")
    summary = _summary(app, w["seller_profile"])
    assert (summary["count"], summary["total"], summary["held"]) == (2, Decimal("50.00"), Decimal("50.00"))
    net = _net_by_order(app, w["seller_profile"])
    assert (net.get(first, Decimal("0.00")), net[second], net[third]) == (Decimal("0.00"), Decimal("20.00"), Decimal("30.00"))
    sales = {s["order_id"]: s for s in _ladder_sales(app, w["seller_profile"])}
    assert (sales[first]["status"], sales[first]["cause"]) == ("UNCOUNTED", "REFUND_IN_HOLD")
    # at most ONE approvable (EARNED) row per sale, and it is the sale's share
    earned = [(o, a) for o, a, s, _p, _m in _entries(app, w["seller_profile"]) if s == "EARNED"]
    assert sorted(earned, key=lambda r: r[1]) == [(second, Decimal("20.00")), (third, Decimal("30.00"))]
    # the held rows were CANCELLED, never edited: the history still shows them
    statuses = [s for o, _a, s, _p, _m in _entries(app, w["seller_profile"]) if o == first]
    assert statuses == ["CANCELLED"]


def test_refund_of_the_last_sale_inside_the_hold(app, seeded):
    w = _world(app)
    orders = [_sell(app, w, "p1")["order_id"] for _ in range(5)]
    _refund(app, w, orders[-1], "250.00")
    summary = _summary(app, w["seller_profile"])
    assert (summary["count"], summary["total"]) == (4, Decimal("200.00"))


def test_refund_after_the_hold_keeps_the_sale_counted_and_flags_it(app, seeded):
    """The owner ruled only on refunds INSIDE the hold. Default chosen: after
    the hold the sale stays counted (that commission had become final), and
    the refund is flagged on the sale for a manager to see."""
    w = _world(app)
    long_ago = datetime.now(timezone.utc) - timedelta(days=31)
    sale = _sell(app, w, "p1", confirmed_at=long_ago)["order_id"]
    with app.app_context():
        from app.commissions.ladder import ladder_month_of

        month = ladder_month_of(long_ago)
    assert _summary(app, w["seller_profile"], month=month)["payable"] == Decimal("20.00")
    _refund(app, w, sale, "250.00")
    [s] = _ladder_sales(app, w["seller_profile"])
    assert s["status"] == "COUNTED" and s["refunded_after_hold_at"] is not None
    summary = _summary(app, w["seller_profile"], month=month)
    assert (summary["count"], summary["total"], summary["payable"]) == (1, Decimal("20.00"), Decimal("20.00"))
    assert summary["refunded_after_hold"] == 1


def test_a_refund_of_only_the_pair_leaves_the_sale_counted_and_the_licence_running(app, seeded):
    w = _world(app)
    sale = _sell(app, w, "p1", pairs=1)["order_id"]
    _refund(app, w, sale, "50.00")
    [s] = _ladder_sales(app, w["seller_profile"])
    assert s["status"] == "COUNTED"
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.licensing import LicenseScheduledStop

        assert db_session.execute(select(LicenseScheduledStop)).scalars().all() == []


def test_held_ladder_commission_cannot_be_approved_until_the_hold_ends(app, seeded):
    w = _world(app)
    _sell(app, w, "p1")
    _sell(app, w, "p1", confirmed_at=datetime.now(timezone.utc) - timedelta(days=31))
    with app.app_context():
        from sqlalchemy import select

        from app.commissions.errors import CommissionError
        from app.commissions.ledger import approve_commission_entry
        from app.extensions import db_session
        from app.models.base import utcnow
        from app.models.commissions import CommissionLedgerEntry

        rows = db_session.execute(select(CommissionLedgerEntry).where(CommissionLedgerEntry.entry_kind == "LADDER")).scalars().all()
        held = [e for e in rows if e.payable_after > utcnow()]
        over = [e for e in rows if e.payable_after <= utcnow()]
        assert len(held) == 1 and len(over) == 1
        with pytest.raises(CommissionError) as exc:
            approve_commission_entry(held[0], actor_staff_user_id=w["manager"])
        assert exc.value.code == "COMMISSION_ON_HOLD"
        db_session.rollback()
        approve_commission_entry(over[0], actor_staff_user_id=w["manager"])  # allow-half
        assert over[0].status == "APPROVED"


def test_a_refund_and_what_it_takes_back_are_one_write(app, seeded, monkeypatch):
    """A crash while the refund re-derives the seller's month must leave the
    refund unpaid, the sale counted and no licence stop -- never a refunded
    customer whose seller keeps the commission (on 1025d626 the refund
    committed first and every consequence after it committed on its own)."""
    w = _world(app)
    sale = _sell(app, w, "p1")["order_id"]
    import app.commissions.ladder as ladder_module

    def _crash(*args, **kwargs):
        raise RuntimeError("simulated crash while re-deriving the month")

    monkeypatch.setattr(ladder_module, "evaluate_sale", _crash)
    with pytest.raises(RuntimeError):
        _refund(app, w, sale, "250.00")
    monkeypatch.undo()
    invoice_id = _rows(app, sale)["invoice_id"]
    assert [s["status"] for s in _ladder_sales(app, w["seller_profile"])] == ["COUNTED"]
    with app.app_context():
        from sqlalchemy import select

        from app.commercial_sales.refunds import confirm_refund
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice, CommercialRefund
        from app.models.licensing import LicenseScheduledStop

        refund = db_session.execute(select(CommercialRefund)).scalars().one()
        assert refund.status == "APPROVED"
        assert db_session.get(CommercialInvoice, invoice_id).status == "PAID"
        assert db_session.execute(select(LicenseScheduledStop)).scalars().all() == []
        confirm_refund(refund, actor_staff_user_id=w["admin"])  # the retry does all of it, once
        assert len(db_session.execute(select(LicenseScheduledStop)).scalars().all()) == 1
    assert [s["status"] for s in _ladder_sales(app, w["seller_profile"])] == ["UNCOUNTED"]
    assert _summary(app, w["seller_profile"])["total"] == Decimal("0.00")


def test_a_held_row_is_not_counted_as_waiting_for_approval_and_shows_its_hold(app, client, seeded):
    """The Finance figure and the commissions screen agree with the approval
    rule: a held row is not 'awaiting approval'; it shows when its hold ends."""
    w = _world(app)
    _sell(app, w, "p1")
    _sell(app, w, "p1", confirmed_at=datetime.now(timezone.utc) - timedelta(days=31))
    with app.app_context():
        from sqlalchemy import select

        from app.commercial_sales.dashboards import finance_commercial_dashboard
        from app.extensions import db_session
        from app.models.base import utcnow
        from app.models.commissions import CommissionLedgerEntry

        figure = finance_commercial_dashboard()
        pending = figure["commissions_pending_approval"] if isinstance(figure, dict) else figure.commissions_pending_approval
        assert pending == 1
        held_id = next(e.id for e in db_session.execute(select(CommissionLedgerEntry)).scalars().all()
                       if e.payable_after > utcnow())
    force_login(client, app, w["manager"])
    html = client.get("/commissions").get_data(as_text=True)
    assert f'data-on-hold="{held_id}"' in html
    assert html.count('/approve"') == 1  # only the row past its hold offers Approve


# -------------------------------------------------- refund -> the licence --

def test_refunded_licence_keeps_working_two_days_then_stops(app, seeded):
    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1")["order_id"]
    rows = _rows(app, sale)
    _refund(app, w, sale, "250.00")
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        # right after the refund the shop still works (RED on 1025d626: the
        # refund suspended the subscription at once)
        assert db_session.get(Subscription, rows["subscription_id"]).status == "ACTIVE"
        assert db_session.get(License, rows["license_id"]).status == "ISSUED"
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.licensing.scheduled_stops import apply_due_licence_stops
        from app.models.commercial_sales import CommercialRefund
        from app.models.licensing import License, LicenseScheduledStop
        from app.models.subscriptions import Subscription

        refund = db_session.execute(select(CommercialRefund)).scalars().one()
        paid_at = refund.paid_at.astimezone(timezone.utc)  # day arithmetic in UTC, never the session zone
        [stop] = db_session.execute(select(LicenseScheduledStop)).scalars().all()
        assert (stop.license_id, stop.to_status) == (rows["license_id"], "SUSPENDED")
        assert stop.effective_at == paid_at + timedelta(days=2)
        # right after the refund AND one day later: still working
        assert db_session.get(License, rows["license_id"]).status == "ISSUED"
        assert db_session.get(Subscription, rows["subscription_id"]).status == "ACTIVE"
        assert apply_due_licence_stops(now=paid_at + timedelta(days=1)) == 0
        db_session.expire_all()
        assert db_session.get(License, rows["license_id"]).status == "ISSUED"
        # two days and a bit: stopped -- licence and subscription
        assert apply_due_licence_stops(now=paid_at + timedelta(days=2, minutes=1)) == 1
        db_session.expire_all()
        assert db_session.get(License, rows["license_id"]).status == "SUSPENDED"
        assert db_session.get(Subscription, rows["subscription_id"]).status == "SUSPENDED"
        assert db_session.get(LicenseScheduledStop, stop.id).applied_at is not None
        # running it again changes nothing
        assert apply_due_licence_stops(now=paid_at + timedelta(days=3)) == 0


def test_the_shop_is_refused_at_check_in_once_the_two_days_are_over(app, client, seeded, signing_key):
    """End to end over the product's own API: activate the sold licence,
    refund, check in (still fine), let two days pass, check in -> suspended."""
    import json

    from tests.conftest import build_activation_body, build_checkin_body, make_device_keypair

    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1")
    device = make_device_keypair()
    resp = client.post("/api/licensing/v1/activations", content_type="application/json", data=json.dumps(
        build_activation_body(device, full_key=sale["full_key"], installation_id="ladder-shop",
                              product_code="AURA_RETAIL", platform="WINDOWS")))
    assert resp.status_code == 200, resp.get_json()
    installation_id = resp.get_json()["installation_id"]
    _refund(app, w, sale["order_id"], "250.00")

    def _check_in():
        return client.post("/api/licensing/v1/check-ins", content_type="application/json",
                           data=json.dumps(build_checkin_body(device, installation_id=installation_id)))

    first = _check_in()
    assert (first.status_code, first.get_json()["reason_code"]) == (200, "CHECK_IN_ACCEPTED")
    with app.app_context():
        from app.extensions import db_session

        # two days pass: the stop's moment is now in the past
        db_session.execute(text("UPDATE owner_license_scheduled_stops SET effective_at = now() - interval '1 minute'"))
        db_session.commit()
    second = _check_in()
    # the public code is normalized on purpose (test_phase6_checkin_protocol);
    # the decision itself is recorded on the installation's timeline
    assert (second.status_code, second.get_json()["reason_code"]) == (400, "ACTIVATION_REJECTED")
    lic = _rows(app, sale["order_id"])["license_id"]
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.installations import ActivationEvent
        from app.models.licensing import License

        assert db_session.get(License, lic).status == "SUSPENDED"
        last = db_session.execute(
            select(ActivationEvent).where(ActivationEvent.license_id == lic, ActivationEvent.event_type == "CHECK_IN_RECORDED")
            .order_by(ActivationEvent.created_at.desc())
        ).scalars().first()
        assert (last.result, last.reason_code) == ("FAILURE", "LICENSE_SUSPENDED")


# ------------------------------------------------- reversing allocations --

def _reverse(app, w, order_id):
    rows = _rows(app, order_id)
    with app.app_context():
        from sqlalchemy import select

        from app.commercial_sales.allocation import reverse_allocation
        from app.extensions import db_session
        from app.models.commercial_sales import PaymentAllocation

        allocation = db_session.execute(
            select(PaymentAllocation).where(PaymentAllocation.commercial_invoice_id == rows["invoice_id"])
        ).scalars().one()
        reverse_allocation(allocation, reason="Allocated to the wrong sale", actor_staff_user_id=w["manager"])
        return allocation.id


def test_reverse_allocation_takes_the_percentage_commission_back(app, seeded):
    """RED on 1025d626 (the gap 7571c3df reported): the allocation was
    reversed and the commission stayed EARNED 25.00."""
    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1")["order_id"]
    _reverse(app, w, sale)
    rows = _entries(app, w["seller_profile"], kind="PAYMENT_RULE")
    assert sorted((r[1], r[2]) for r in rows) == [(Decimal("-25.00"), "REVERSED"), (Decimal("25.00"), "EARNED")]
    assert sum(r[1] for r in rows) == Decimal("0.00")


def test_reverse_allocation_uncounts_a_ladder_sale(app, seeded):
    w = _world(app)
    first, second = (_sell(app, w, "p1")["order_id"] for _ in range(2))
    _reverse(app, w, first)
    summary = _summary(app, w["seller_profile"])
    assert (summary["count"], summary["total"]) == (1, Decimal("20.00"))
    sales = {s["order_id"]: s for s in _ladder_sales(app, w["seller_profile"])}
    assert (sales[first]["status"], sales[first]["cause"]) == ("UNCOUNTED", "CORRECTION")
    assert _net_by_order(app, w["seller_profile"])[second] == Decimal("20.00")


def test_reverse_allocation_and_its_commission_reversal_are_one_write(app, seeded, monkeypatch):
    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1")["order_id"]
    import app.commercial_sales.allocation as allocation_module

    def _crash(*args, **kwargs):
        raise RuntimeError("simulated crash at the commission reversal")

    monkeypatch.setattr(allocation_module, "reverse_commissions_for_allocation", _crash)
    with pytest.raises(RuntimeError):
        _reverse(app, w, sale)
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.commercial_sales import PaymentAllocation

        allocation = db_session.execute(select(PaymentAllocation)).scalars().one()
        assert allocation.reversed_at is None
    assert [r[2] for r in _entries(app, w["seller_profile"], kind="PAYMENT_RULE")] == ["EARNED"]


# --------------------------------------------------------------- renewals --

def test_a_p1_renewal_payment_earns_the_sales_owner_10(app, seeded):
    w = _world(app)
    sale = _sell(app, w, "p1")["order_id"]
    sub = _rows(app, sale)["subscription_id"]
    renewal = _pay_month(app, w, sub, "100.00")
    rows = _entries(app, w["seller_profile"], kind="RENEWAL")
    assert [(r[1], r[2]) for r in rows] == [(Decimal("10.00"), "EARNED")]
    summary = _summary(app, w["seller_profile"])
    assert (summary["renewals"], summary["renewals_total"]) == (1, Decimal("10.00"))
    # the renewal is not a new sale: the ladder still has the one sale
    assert summary["count"] == 1
    # confirming it again is refused, and posts nothing more
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.payments import confirm_payment
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord

        with pytest.raises(CommercialSalesError):
            confirm_payment(db_session.get(PaymentRecord, renewal), actor_staff_user_id=w["admin"])
    # and the renewal step itself, run again for the same payment (a retry,
    # a re-saved note), posts nothing more
    with app.app_context():
        from app.commissions.ladder import on_payment_status_written
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord

        on_payment_status_written(db_session.get(PaymentRecord, renewal))
        db_session.commit()
    assert len(_entries(app, w["seller_profile"], kind="RENEWAL")) == 1


def test_monthly_payments_inside_the_contract_are_not_renewals_but_a_new_contract_is(app, seeded):
    w = _world(app)
    sale = _sell(app, w, "p2")["order_id"]
    sub = _rows(app, sale)["subscription_id"]
    _pay_month(app, w, sub, "35.00")
    assert _entries(app, w["seller_profile"], kind="RENEWAL") == []
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        ends = db_session.get(Subscription, sub).contract_ends_on
    _pay_month(app, w, sub, "35.00", payment_date=ends + timedelta(days=1))
    assert [r[1] for r in _entries(app, w["seller_profile"], kind="RENEWAL")] == [Decimal("10.00")]


def test_a_renewal_for_a_percentage_seller_posts_no_ladder_renewal(app, seeded):
    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1")["order_id"]
    _pay_month(app, w, _rows(app, sale)["subscription_id"], "100.00")
    assert _entries(app, w["seller_profile"], kind="RENEWAL") == []


# ---------------------------------------------------------- Jordan's month --

@pytest.mark.parametrize("confirmed_utc, amman_month", [
    (datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc), date(2026, 10, 1)),   # 02:30 on 1 Oct in Amman
    (datetime(2026, 9, 30, 20, 30, tzinfo=timezone.utc), date(2026, 9, 1)),    # 23:30 on 30 Sep in Amman
])
def test_the_month_is_jordan_time(app, seeded, confirmed_utc, amman_month):
    w = _world(app, rule_from_days_ago=400)
    _sell(app, w, "p1", confirmed_at=confirmed_utc)
    [s] = _ladder_sales(app, w["seller_profile"])
    assert s["month"] == amman_month
    assert s["hold_ends_at"] == confirmed_utc + timedelta(days=30)


# ------------------------------------------- exactly once, under pressure --

def test_confirming_the_same_sale_twice_counts_it_once(app, seeded):
    w = _world(app)
    sale = _sell(app, w, "p1")
    with app.app_context():
        from app.commercial_sales.direct_sale import confirm_direct_sale

        again = confirm_direct_sale(sale["order_id"], actor_staff_user_id=w["admin"], license_pepper=app.config["LICENSE_PEPPER"])
    assert again["replayed"] is True
    assert len(_ladder_sales(app, w["seller_profile"])) == 1
    assert len(_entries(app, w["seller_profile"])) == 1


def test_a_crash_while_posting_the_ladder_writes_nothing_and_the_retry_counts_once(app, seeded, monkeypatch):
    w = _world(app)
    import app.commissions.ladder as ladder_module

    real = ladder_module._recompute_months
    calls = {"n": 0}

    def _crash_first(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash while posting the ladder")
        return real(*args, **kwargs)

    monkeypatch.setattr(ladder_module, "_recompute_months", _crash_first)
    with pytest.raises(RuntimeError):
        _sell(app, w, "p1")
    with app.app_context():
        from sqlalchemy import func, select

        from app.extensions import db_session
        from app.models.commercial_sales import PaymentAllocation, SalesOrder
        from app.models.commissions import CommissionLadderSale

        assert db_session.execute(select(func.count()).select_from(PaymentAllocation)).scalar_one() == 0
        assert db_session.execute(select(func.count()).select_from(CommissionLadderSale)).scalar_one() == 0
        order_id = db_session.execute(select(SalesOrder.id)).scalar_one()
    with app.app_context():
        from app.commercial_sales.direct_sale import confirm_direct_sale

        confirm_direct_sale(order_id, actor_staff_user_id=w["manager"], license_pepper=app.config["LICENSE_PEPPER"])
    assert len(_ladder_sales(app, w["seller_profile"])) == 1
    assert [r[1] for r in _entries(app, w["seller_profile"])] == [Decimal("20.00")]


def test_two_managers_confirming_qualifying_payments_at_once_count_the_sale_once(app, seeded, monkeypatch):
    """Payments 3 and 4 of one Package 2 sale, confirmed by two managers at
    the same moment: both cross the 100 JOD line. The second waits for the
    first (a per-seller lock), sees the counted sale, and counts nothing."""
    from tests.test_issuance_sale_hardening import _OtherManager

    w = _world(app)
    sale = _sell(app, w, "p2")
    sub = _rows(app, sale["order_id"])["subscription_id"]
    _pay_month(app, w, sub, "35.00")
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription
        from app.subscriptions.services import record_payment

        s = db_session.get(Subscription, sub)
        ids = []
        for _ in range(2):
            ids.append(record_payment(
                {"customer_id": s.customer_id, "subscription_id": s.id, "amount": Decimal("35.00"), "currency": JOD,
                 "method": "BANK_TRANSFER", "payment_date": date.today(), "reference": uuid.uuid4().hex[:8],
                 "status": "PENDING"}, w["seller"]).id)

    def _load():
        from app.extensions import db_session as s2
        from app.models.subscriptions import PaymentRecord

        return s2.get(PaymentRecord, ids[1])

    def _act(row):
        from app.commercial_sales.payments import confirm_payment

        return confirm_payment(row, actor_staff_user_id=w["admin"])

    other = _OtherManager(app, load=_load, act=_act).start()
    import app.commissions.ladder as ladder_module

    real = ladder_module._recompute_months
    seen = {}

    def _while_counting(*args, **kwargs):
        if "other" not in seen:
            seen["other"] = other.fire()  # the other manager presses Confirm now
        return real(*args, **kwargs)

    monkeypatch.setattr(ladder_module, "_recompute_months", _while_counting)
    with app.app_context():
        from app.commercial_sales.payments import confirm_payment
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord

        confirm_payment(db_session.get(PaymentRecord, ids[0]), actor_staff_user_id=w["manager"])
    other.finish()
    assert seen["other"] == "blocked"
    assert other.error is None, other.error
    [counted] = _ladder_sales(app, w["seller_profile"])
    assert counted["payment_id"] == ids[0]
    assert _summary(app, w["seller_profile"])["total"] == Decimal("20.00")


def test_switching_from_percentage_to_ladder_mid_sale_never_pays_both(app, seeded):
    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p2")
    sub = _rows(app, sale["order_id"])["subscription_id"]
    assert [r[1] for r in _entries(app, w["seller_profile"], kind="PAYMENT_RULE")] == [Decimal("3.50")]
    _assign(app, w, [w["seller_profile"]], rule=LADDER, since=date.today())
    _pay_month(app, w, sub, "35.00")
    _pay_month(app, w, sub, "35.00")
    assert _ladder_sales(app, w["seller_profile"]) == []
    # allow-half: a NEW sale under the ladder does count
    _sell(app, w, "p1")
    assert _summary(app, w["seller_profile"])["total"] == Decimal("20.00")


# ----------------------------------------------------------------- rules --

def test_the_ladder_rule_is_jod_only(app, seeded):
    w = _world(app)
    with app.app_context():
        from app.commissions.errors import CommissionError
        from app.commissions.management import create_commission_plan, create_commission_rule_version

        plan = create_commission_plan({"plan_code": f"C_{uuid.uuid4().hex[:6]}", "name": "x"}, w["admin"])
        for currency in (None, "USD"):
            with pytest.raises(CommissionError):
                create_commission_rule_version(plan, rule_type=LADDER, currency=currency,
                                               effective_from=date.today(), actor_staff_user_id=w["admin"])
        rule = create_commission_rule_version(plan, rule_type=LADDER, currency=JOD, effective_from=date.today(),
                                              actor_staff_user_id=w["admin"])
        assert rule.rule_type == LADDER


# ------------------------------------------------------------- the jobs --

def _cli(app, *args):
    return app.test_cli_runner().invoke(args=list(args))


def test_seed_and_assign_put_a_seller_on_the_ladder(app, seeded):
    w = _world(app, rule="PERCENT")
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        admin_email, seller_email = (db_session.get(StaffUser, w[k]).email for k in ("admin", "seller"))
    refused = _cli(app, "commissions", "assign-ladder", seller_email, "--actor-email", admin_email)
    assert refused.exit_code != 0 and "seed-ladder-plan" in refused.output  # no plan yet
    first = _cli(app, "commissions", "seed-ladder-plan", "--actor-email", admin_email)
    again = _cli(app, "commissions", "seed-ladder-plan", "--actor-email", admin_email)
    assert (json.loads(first.output)["created"], json.loads(again.output)["created"]) == (True, False)
    assert _cli(app, "commissions", "seed-ladder-plan", "--actor-email", seller_email).exit_code != 0  # not a Super Admin
    done = _cli(app, "commissions", "assign-ladder", seller_email, "--actor-email", admin_email)
    assert done.exit_code == 0, done.output
    _sell(app, w, "p1")
    assert _summary(app, w["seller_profile"])["total"] == Decimal("20.00")
    assert _entries(app, w["seller_profile"], kind="PAYMENT_RULE") == []


def test_the_stop_job_reports_then_applies_due_stops(app, seeded):
    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1")["order_id"]
    _refund(app, w, sale, "250.00")
    with app.app_context():
        from app.extensions import db_session

        db_session.execute(text("UPDATE owner_license_scheduled_stops SET effective_at = now() - interval '1 minute'"))
        db_session.commit()
    dry = _cli(app, "commercial", "apply-licence-stops")
    assert json.loads(dry.output) == {"dry_run": True, "stops_due": 1}
    assert _rows_status(app, sale) == ("ISSUED", "ACTIVE")
    applied = _cli(app, "commercial", "apply-licence-stops", "--apply")
    assert json.loads(applied.output) == {"dry_run": False, "stops_applied": 1}
    assert _rows_status(app, sale) == ("SUSPENDED", "SUSPENDED")
    assert json.loads(_cli(app, "commercial", "apply-licence-stops", "--apply").output)["stops_applied"] == 0


def _rows_status(app, order_id):
    rows = _rows(app, order_id)
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        return (db_session.get(License, rows["license_id"]).status,
                db_session.get(Subscription, rows["subscription_id"]).status)


# ----------------------------------------------------- the seller's screen --

def test_the_seller_sees_their_own_month_count_total_held_and_payable_in_arabic_too(app, client, seeded):
    w = _world(app)
    for _ in range(3):
        _sell(app, w, "p1")
    _sell(app, w, "p1", seller="seller2")
    force_login(client, app, w["seller"])
    page = client.get("/commissions")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'data-ladder-month="' + _this_month().isoformat() + '"' in html
    assert 'data-ladder-count="3"' in html
    assert 'data-ladder-total="100.00"' in html
    assert 'data-ladder-held="100.00"' in html and 'data-ladder-payable="0.00"' in html
    assert 'data-ladder-total="20.00"' not in html  # the other seller's month is not shown
    client.get("/locale/ar?next=/")
    ar = client.get("/commissions").get_data(as_text=True)
    for arabic in ("سلم العمولة الشهري", "المبيعات المحتسبة", "مجموع السلم", "محجوزة", "مستحقة الدفع"):
        assert arabic in ar, arabic
    for english in ("Monthly commission ladder", "Sales counted"):
        assert english not in ar, english
