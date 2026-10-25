"""Step 0 ladder -- fix round after the adversarial verification of d15d11f0.

Each test is a defect the verifier PROVED on d15d11f0 (its probe named in the
docstring), a boundary it found unguarded, or the allow-half of one of them.
Every assertion is what the OWNER's rules require:
  - "10 JOD per renewal"; "a new contract at the end earns the 10 JOD
    renewal" -- one renewal per renewed period, never one per payment;
  - commission counts in the month the sale is CONFIRMED (Jordan time), is
    payable 30 days after that, and money already approved in a closed month
    is never re-sliced;
  - "a refunded licence keeps working 2 days, then stops";
  - business dates are Jordan's dates (Asia/Amman, UTC+3): at 00:30 in Amman
    it is already the next day there, while UTC (and this machine) still say
    the day before.
"""
from __future__ import annotations

import sys
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text

from tests.conftest import force_login, get_csrf
from tests.test_issuance_sale import _codes
from tests.test_issuance_sale_hardening import _OtherManager
from tests.test_step0_ladder import (
    JOD,
    LADDER,
    _assign,
    _entries,
    _ladder_sales,
    _net_by_order,
    _pay_month,
    _refund,
    _reverse,
    _rows,
    _sell,
    _summary,
    _world,
)

D = Decimal
UTC = timezone.utc
AMMAN = ZoneInfo("Asia/Amman")


def _jordan_today() -> date:
    """The test's OWN reading of Jordan's date -- never the helper under test."""
    return datetime.now(UTC).astimezone(AMMAN).date()


def _after_midnight_in_amman() -> datetime:
    """00:30 tomorrow in Amman = 21:30 UTC today: UTC (and a machine at
    UTC+2) still say today; Jordan already says tomorrow."""
    d = datetime.now(UTC).date()
    return datetime(d.year, d.month, d.day, 21, 30, tzinfo=UTC)


def _before_midnight_in_amman() -> datetime:
    """23:30 in Amman = 20:30 UTC: the same calendar day everywhere."""
    d = datetime.now(UTC).date()
    return datetime(d.year, d.month, d.day, 20, 30, tzinfo=UTC)


def _pin_clock(monkeypatch, moment: datetime, *, everywhere: bool = True) -> None:
    """Pins "now" for the business-date decisions. The fixed code reads
    Jordan's date through app.operational_reports.periods.jordan_today (which
    reads periods.utcnow); the code before the fix read utcnow() in each
    module, so `everywhere` pins that too -- a red on d15d11f0 is then the
    wrong DAY, not a clock the test failed to reach. everywhere=False for a
    request that is checked against the real clock (signed timestamps)."""
    import app.models.base as base

    real = base.utcnow
    fake = lambda: moment  # noqa: E731
    monkeypatch.setattr("app.operational_reports.periods.utcnow", fake, raising=False)
    if everywhere:
        for name, module in list(sys.modules.items()):
            if (name == "app" or name.startswith("app.")) and getattr(module, "utcnow", None) is real:
                monkeypatch.setattr(module, "utcnow", fake)


def _approve_order(app, w, order_id):
    with app.app_context():
        from app.commissions.ledger import approve_commission_entry
        from app.extensions import db_session
        from app.models.commissions import CommissionLadderSale as S
        from app.models.commissions import CommissionLedgerEntry as E

        sale = db_session.execute(select(S).where(S.sales_order_id == order_id, S.status == "COUNTED")).scalars().one()
        rows = db_session.execute(select(E).where(E.ladder_sale_id == sale.id, E.status == "EARNED")).scalars().all()
        assert rows
        for r in rows:
            approve_commission_entry(r, actor_staff_user_id=w["manager"])


def _renewal_rows(app, profile_id):
    """[(payment_id, amount, status)] of every live (not CANCELLED) renewal row."""
    with app.app_context():
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry as E

        return [(e.source_payment_record_id, Decimal(e.commission_amount), e.status) for e in db_session.execute(
            select(E).where(E.employee_profile_id == profile_id, E.entry_kind == "RENEWAL", E.status != "CANCELLED")
            .order_by(E.created_at, E.id)
        ).scalars().all()]


def _set_contract_end(app, subscription_id, ends_on: date):
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        db_session.get(Subscription, subscription_id).contract_ends_on = ends_on
        db_session.commit()


def _correct(app, payment_id, status, actor):
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord
        from app.subscriptions.services import correct_payment

        correct_payment(db_session.get(PaymentRecord, payment_id), status, f"test {status}", actor)


# ============================================== 1. renewals: once per period --

def test_a_contract_package_paid_month_to_month_after_its_contract_earns_one_renewal_per_new_contract_period(app, seeded):
    """Verifier probe v7 (HIGH): on d15d11f0 every payment after the
    contract's end paid 10 JOD -- three monthly payments, 30 JOD -- because
    contract_ends_on never moves. One renewal per new contract period: two
    payments inside the first period after the end earn ONE renewal, a
    payment inside the second period earns the second."""
    w = _world(app)
    sale = _sell(app, w, "p2")["order_id"]
    sub = _rows(app, sale)["subscription_id"]
    ends = _jordan_today() - timedelta(days=400)  # the 12 months ended ~13 months ago
    _set_contract_end(app, sub, ends)
    first_period_a = _pay_month(app, w, sub, "35.00", payment_date=ends + timedelta(days=30))
    _pay_month(app, w, sub, "35.00", payment_date=ends + timedelta(days=60))
    second_period = _pay_month(app, w, sub, "35.00")  # today: > 12 months after the end
    assert _renewal_rows(app, w["seller_profile"]) == [
        (first_period_a, D("10.00"), "EARNED"), (second_period, D("10.00"), "EARNED"),
    ]


def test_when_a_periods_renewal_is_taken_back_the_next_payment_of_that_period_earns_it(app, seeded):
    """Both directions of 'one per period': the second payment of a period
    earns nothing while the first holds it; when the first is taken back (a
    bounced transfer), the period's renewal moves to the second -- the shop
    did renew once."""
    w = _world(app)
    sale = _sell(app, w, "p2")["order_id"]
    sub = _rows(app, sale)["subscription_id"]
    ends = _jordan_today() - timedelta(days=60)
    _set_contract_end(app, sub, ends)
    first = _pay_month(app, w, sub, "35.00", payment_date=ends + timedelta(days=10))
    second = _pay_month(app, w, sub, "35.00", payment_date=ends + timedelta(days=20))
    assert _renewal_rows(app, w["seller_profile"]) == [(first, D("10.00"), "EARNED")]
    _correct(app, first, "FAILED", w["admin"])
    assert _renewal_rows(app, w["seller_profile"]) == [(second, D("10.00"), "EARNED")]


def test_p1_earns_one_renewal_per_year_it_pays_for(app, seeded):
    """Package 1 has no contract: each payment of a whole year's price is one
    renewed year (10 JOD); a payment of less is not a renewal."""
    w = _world(app)
    sale = _sell(app, w, "p1")["order_id"]
    sub = _rows(app, sale)["subscription_id"]
    year_two = _pay_month(app, w, sub, "100.00")
    _pay_month(app, w, sub, "50.00")
    year_three = _pay_month(app, w, sub, "100.00")
    assert _renewal_rows(app, w["seller_profile"]) == [
        (year_two, D("10.00"), "EARNED"), (year_three, D("10.00"), "EARNED"),
    ]


def test_a_payment_dated_in_the_future_is_refused(app, client, seeded):
    """Verifier (item 1): a typed future payment_date could place a payment
    in a renewal period that has not started. Refused by the service (every
    caller) and by the payments form (400, not a 500)."""
    w = _world(app)
    sale = _sell(app, w, "p2")["order_id"]
    sub = _rows(app, sale)["subscription_id"]
    tomorrow = _jordan_today() + timedelta(days=1)
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord, Subscription
        from app.subscriptions.services import record_payment

        s = db_session.get(Subscription, sub)
        customer_id = s.customer_id
        before = db_session.execute(select(PaymentRecord.id)).all()
        with pytest.raises(ValueError):
            record_payment({"customer_id": s.customer_id, "subscription_id": s.id, "amount": D("35.00"), "currency": JOD,
                            "method": "BANK_TRANSFER", "payment_date": tomorrow, "reference": "FUT", "status": "PENDING"},
                           w["seller"])
        db_session.rollback()
        assert db_session.execute(select(PaymentRecord.id)).all() == before
    force_login(client, app, w["seller"])
    csrf = get_csrf(client.get("/licenses/issuance/new").get_data(as_text=True))
    resp = client.post("/subscriptions/payments", data={
        "csrf_token": csrf, "customer_id": str(customer_id), "subscription_id": str(sub), "amount": "35.00",
        "currency": JOD, "method": "BANK_TRANSFER", "payment_date": tomorrow.isoformat(), "reference": "FUT2",
        "status": "PENDING"})
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert resp.get_json()["error"] == "payment_date_in_future"


def test_jordans_today_is_a_valid_payment_date_after_midnight_in_amman(app, seeded, monkeypatch):
    """Allow-half: at 00:30 in Amman (21:30 UTC) Jordan's date is UTC's
    tomorrow -- money that arrived today in Jordan is not 'in the future'."""
    w = _world(app)
    sale = _sell(app, w, "p2")["order_id"]
    sub = _rows(app, sale)["subscription_id"]
    moment = _after_midnight_in_amman()
    _pin_clock(monkeypatch, moment)
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription
        from app.subscriptions.services import record_payment

        s = db_session.get(Subscription, sub)
        payment = record_payment({"customer_id": s.customer_id, "subscription_id": s.id, "amount": D("35.00"),
                                  "currency": JOD, "method": "BANK_TRANSFER",
                                  "payment_date": moment.astimezone(AMMAN).date(), "reference": "NIGHT",
                                  "status": "PENDING"}, w["seller"])
        assert payment.id is not None
        with pytest.raises(ValueError):  # and Jordan's tomorrow is still refused
            record_payment({"customer_id": s.customer_id, "subscription_id": s.id, "amount": D("35.00"),
                            "currency": JOD, "method": "BANK_TRANSFER",
                            "payment_date": moment.astimezone(AMMAN).date() + timedelta(days=1), "reference": "N2",
                            "status": "PENDING"}, w["seller"])


# ======================================= 2. a sale finished late never re-slices --

def _two_months_ago_base() -> datetime:
    first = _jordan_today().replace(day=1)
    prev2 = ((first - timedelta(days=1)).replace(day=1) - timedelta(days=1)).replace(day=1)
    return datetime(prev2.year, prev2.month, 3, 9, 0, tzinfo=UTC)


def test_a_sale_finished_late_counts_when_it_is_finished_and_never_claws_back_a_closed_month(app, seeded):
    """Verifier probe v10 (MEDIUM). A closed month: five sales, all past their
    hold and APPROVED (350). Then a sale whose payment was confirmed early in
    that month is FINISHED today. On d15d11f0 it was placed at position 1 of
    the closed month (qualified_at = the payment's old confirmation) and
    clawed back approved money from the five (REVERSED rows), and its own
    commission was payable at once. It counts when it actually became
    countable -- today -- in today's month, held 30 days from today."""
    w = _world(app)
    base = _two_months_ago_base()
    closed = [_sell(app, w, "p1", confirmed_at=base + timedelta(hours=i))["order_id"] for i in range(5)]
    for order_id in closed:
        _approve_order(app, w, order_id)
    with app.app_context():
        from app.commissions.ladder import ladder_month_of

        closed_month, this_month = ladder_month_of(base), ladder_month_of(datetime.now(UTC))
    assert _summary(app, w["seller_profile"], month=closed_month)["total"] == D("350.00")
    started = datetime.now(UTC)
    late = _sell(app, w, "p1", confirmed_at=base - timedelta(hours=1), finished_late=True)["order_id"]

    reversed_rows = [(o, a) for o, a, st, _p, _m in _entries(app, w["seller_profile"]) if st == "REVERSED"]
    assert reversed_rows == []
    closed_summary = _summary(app, w["seller_profile"], month=closed_month)
    assert (closed_summary["count"], closed_summary["total"]) == (5, D("350.00"))
    assert [st for o, _a, st, _p, _m in _entries(app, w["seller_profile"]) if o in closed] == ["APPROVED"] * 5
    [late_sale] = [s for s in _ladder_sales(app, w["seller_profile"]) if s["order_id"] == late]
    assert late_sale["month"] == this_month
    assert late_sale["qualified_at"] >= started - timedelta(seconds=5)
    assert late_sale["hold_ends_at"] >= started + timedelta(days=30) - timedelta(seconds=5)
    now_summary = _summary(app, w["seller_profile"], month=this_month)
    assert (now_summary["count"], now_summary["held"], now_summary["payable"]) == (1, D("20.00"), D("0.00"))


# ======================================================= 3. no deadlocks --

def _race_against_approval(app, w, monkeypatch, entry_id, operation):
    """Runs `operation` (a refund or an allocation reversal) while another
    manager approves the commission row `entry_id`. The approval is fired at
    the moment the operation first asks for the seller's ladder lock and runs
    as far as it can before the operation continues -- the interleaving that
    deadlocked on d15d11f0 (verifier probe v12): the operation had already
    inserted the reversal row (FK -> FOR KEY SHARE on the approved row) when
    it asked for the seller lock; the approval held the seller lock and asked
    FOR UPDATE on that row."""
    import app.commissions.ladder as ladder_module

    def _load():
        from app.extensions import db_session as s2
        from app.models.commissions import CommissionLedgerEntry as E2

        return s2.get(E2, entry_id)

    def _act(row):
        from app.commissions.ledger import approve_commission_entry

        return approve_commission_entry(row, actor_staff_user_id=w["manager"])

    other = _OtherManager(app, load=_load, act=_act).start()
    real_lock = ladder_module.lock_seller
    seen = {}

    def _lock_then_race(profile_id):
        if threading.current_thread() is threading.main_thread() and "other" not in seen:
            seen["other"] = other.fire()
        return real_lock(profile_id)

    monkeypatch.setattr(ladder_module, "lock_seller", _lock_then_race)
    error = None
    try:
        operation()
    except Exception as exc:  # noqa: BLE001
        error = exc
    monkeypatch.setattr(ladder_module, "lock_seller", real_lock)
    other.finish()
    return error, other.error, seen.get("other")


def _percentage_entry(app, w, order_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry as E

        return db_session.execute(select(E.id).where(E.employee_profile_id == w["seller_profile"])).scalar_one()


def _entry_status(app, entry_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry as E

        return db_session.get(E, entry_id).status


def test_a_refund_and_an_approval_of_its_commission_at_once_never_deadlock(app, seeded, monkeypatch):
    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1")["order_id"]
    entry_id = _percentage_entry(app, w, sale)
    rows = _rows(app, sale)
    with app.app_context():
        from app.commercial_sales.refunds import approve_refund, create_refund
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice

        refund = create_refund(db_session.get(CommercialInvoice, rows["invoice_id"]), amount=D("250.00"), reason="r",
                               payment_record_id=None, actor_employee_profile_id=w["manager_profile"],
                               actor_staff_user_id=w["manager"])
        approve_refund(refund, actor_staff_user_id=w["admin"])
        refund_id = refund.id

    def _confirm():
        with app.app_context():
            from app.commercial_sales.refunds import confirm_refund
            from app.extensions import db_session
            from app.models.commercial_sales import CommercialRefund

            confirm_refund(db_session.get(CommercialRefund, refund_id), actor_staff_user_id=w["admin"])

    refund_error, approval_error, _seen = _race_against_approval(app, w, monkeypatch, entry_id, _confirm)
    assert (refund_error, approval_error) == (None, None)
    assert _entry_status(app, entry_id) == "APPROVED"
    amounts = sorted(r[1] for r in _entries(app, w["seller_profile"], kind="PAYMENT_RULE"))
    assert amounts == [D("-25.00"), D("25.00")]


def test_an_allocation_reversal_and_an_approval_at_once_never_deadlock(app, seeded, monkeypatch):
    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1")["order_id"]
    entry_id = _percentage_entry(app, w, sale)
    error, approval_error, _seen = _race_against_approval(app, w, monkeypatch, entry_id, lambda: _reverse(app, w, sale))
    assert (error, approval_error) == (None, None)
    assert _entry_status(app, entry_id) == "APPROVED"
    amounts = sorted(r[1] for r in _entries(app, w["seller_profile"], kind="PAYMENT_RULE"))
    assert amounts == [D("-25.00"), D("25.00")]


def _ledger_row_locks_held_when_the_seller_lock_is_first_asked(app, monkeypatch, operation) -> list[str]:
    """The lock ORDER itself (the check ran, not just 'no error'): which
    row-level lock modes this transaction already holds on the commission
    ledger table at the moment it first asks for the seller lock."""
    import app.commissions.ladder as ladder_module

    real_lock = ladder_module.lock_seller
    held: dict = {}

    def _spy(profile_id):
        if "modes" not in held:
            from app.extensions import db_session

            held["modes"] = sorted(db_session.execute(text(
                "SELECT mode FROM pg_locks WHERE pid = pg_backend_pid() "
                "AND relation = 'owner_commission_ledger_entries'::regclass "
                "AND mode IN ('RowShareLock', 'RowExclusiveLock')"
            )).scalars().all())
        return real_lock(profile_id)

    monkeypatch.setattr(ladder_module, "lock_seller", _spy)
    operation()
    monkeypatch.setattr(ladder_module, "lock_seller", real_lock)
    assert "modes" in held, "the seller lock was never asked for"
    return held["modes"]


def test_refund_and_reversal_take_the_seller_lock_before_touching_any_commission_row(app, seeded, monkeypatch):
    w = _world(app, rule="PERCENT")
    refunded, reversed_ = _sell(app, w, "p1")["order_id"], _sell(app, w, "p1")["order_id"]
    modes = _ledger_row_locks_held_when_the_seller_lock_is_first_asked(
        app, monkeypatch, lambda: _refund(app, w, refunded, "250.00"))
    assert modes == []
    modes = _ledger_row_locks_held_when_the_seller_lock_is_first_asked(
        app, monkeypatch, lambda: _reverse(app, w, reversed_))
    assert modes == []


def test_a_payment_takes_the_seller_and_the_sales_owner_locks_in_one_fixed_order(app, seeded, monkeypatch):
    """A renewal payment can need TWO seller locks: the sale's seller (the
    ladder) and the subscription's sales owner (the 10 JOD). Taken in the
    order they were needed, two such payments with the roles swapped wait
    for each other. Both are taken up front, in key order."""
    import app.commissions.ladder as ladder_module

    w = _world(app)
    with app.app_context():
        from app.extensions import db_session

        def _key(profile_id):
            import hashlib

            digest = hashlib.sha256(f"commissions.ladder.seller|{profile_id}".encode()).hexdigest()
            return int(digest[:15], 16)

    # the sale's seller is the one whose lock key sorts LAST, so "the order
    # they were needed" (seller, then owner) is the wrong order
    seller_key = "seller" if _key(w["seller_profile"]) > _key(w["seller2_profile"]) else "seller2"
    owner_key = "seller2" if seller_key == "seller" else "seller"
    sale = _sell(app, w, "p1", seller=seller_key)["order_id"]
    sub = _rows(app, sale)["subscription_id"]
    with app.app_context():
        from app.models.subscriptions import Subscription

        db_session.get(Subscription, sub).sales_owner_staff_user_id = w[owner_key]
        db_session.commit()
    real_lock = ladder_module.lock_seller
    order: list = []

    def _spy(profile_id):
        order.append(profile_id)
        return real_lock(profile_id)

    monkeypatch.setattr(ladder_module, "lock_seller", _spy)
    _pay_month(app, w, sub, "100.00")
    monkeypatch.setattr(ladder_module, "lock_seller", real_lock)
    first_two = order[:2]
    assert sorted(set(order)) == sorted({w[f"{seller_key}_profile"], w[f"{owner_key}_profile"]})
    assert first_two == sorted(first_two, key=_key), (first_two, order)


# ========================================================= 4. Jordan's date --

def _record_direct_sale(app, w, *, payment_date, as_of=None):
    with app.app_context():
        from app.commercial_sales.direct_sale import record_direct_sale

        return record_direct_sale(
            new_customer_legal_name=f"Night shop {uuid.uuid4().hex[:6]}", plan_id=w["p1"], extra_device_pairs=0,
            amount=D("250.00"), method="BANK_TRANSFER", reference=f"CLIQ-{uuid.uuid4().hex[:6]}",
            payment_date=payment_date, idempotency_key=str(uuid.uuid4()), actor_staff_user_id=w["seller"],
            actor_permission_codes=_codes(w["seller"]), as_of=as_of,
        )


def test_the_one_screen_sale_takes_jordans_date_after_midnight_in_amman(app, seeded, monkeypatch):
    """Verifier probe v13: at 00:30 on day N+1 in Amman (21:30 UTC on day N)
    the money that arrived 'today' in Jordan was refused as 'in the future'
    (direct_sale.py:499 as_of = utcnow().date())."""
    w = _world(app)
    moment = _after_midnight_in_amman()
    jordan = moment.astimezone(AMMAN).date()
    _pin_clock(monkeypatch, moment)
    assert _record_direct_sale(app, w, payment_date=jordan)["order_id"]
    from app.commercial_sales.direct_sale import DirectSaleError

    with pytest.raises(DirectSaleError) as exc:
        _record_direct_sale(app, w, payment_date=jordan + timedelta(days=1))
    assert exc.value.code == "PAYMENT_DATE_IN_FUTURE"


def test_the_one_screen_sale_refuses_jordans_tomorrow_before_midnight_in_amman(app, seeded, monkeypatch):
    w = _world(app)
    moment = _before_midnight_in_amman()
    _pin_clock(monkeypatch, moment)
    from app.commercial_sales.direct_sale import DirectSaleError

    with pytest.raises(DirectSaleError) as exc:
        _record_direct_sale(app, w, payment_date=moment.astimezone(AMMAN).date() + timedelta(days=1))
    assert exc.value.code == "PAYMENT_DATE_IN_FUTURE"
    assert _record_direct_sale(app, w, payment_date=moment.astimezone(AMMAN).date())["order_id"]


def test_the_sale_form_offers_jordans_date(app, client, seeded, monkeypatch):
    """licensing/issuance_routes.py:134 date.today() -> the form's default and
    max (templates/licensing/sale.html:77): the server's calendar, not
    Jordan's."""
    w = _world(app)
    moment = _after_midnight_in_amman()
    _pin_clock(monkeypatch, moment)
    force_login(client, app, w["seller"])
    html = client.get("/licenses/issuance/new").get_data(as_text=True)
    assert f'max="{moment.astimezone(AMMAN).date().isoformat()}"' in html


@pytest.mark.parametrize("utc_moment, year", [
    (datetime(2026, 12, 31, 21, 30, tzinfo=UTC), "2027"),   # 00:30 on 1 January in Amman
    (datetime(2026, 12, 31, 20, 30, tzinfo=UTC), "2026"),   # 23:30 on 31 December in Amman
])
def test_document_numbers_take_jordans_year(app, seeded, monkeypatch, utc_moment, year):
    _pin_clock(monkeypatch, utc_moment)
    with app.app_context():
        from app.commercial_sales.numbering import allocate_document_number
        from app.expenses.numbering import allocate_expense_number
        from app.extensions import db_session

        number = allocate_document_number("QUOTE")
        expense = allocate_expense_number()
        db_session.commit()
    assert number.split("-")[1] == year
    assert expense.split("-")[1] == year


def test_an_invoice_is_due_thirty_jordan_days_after_it_is_issued(app, seeded, monkeypatch):
    from tests.test_phase9_5d_invoices import _make_confirmed_order, _seed_customer, _seed_plan, _seed_sales_employee

    staff_id, profile_id = _seed_sales_employee(app, f"due-{uuid.uuid4().hex[:6]}@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, f"DUE_{uuid.uuid4().hex[:4]}")
    moment = _after_midnight_in_amman()
    with app.app_context():
        from app.commercial_sales.invoices import DEFAULT_DUE_DAYS, create_invoice_from_order, issue_invoice

        order = _make_confirmed_order(app, staff_id, profile_id, customer_id, plan_id)
        invoice = create_invoice_from_order(order, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id,
                                            idempotency_key=str(uuid.uuid4()))
        _pin_clock(monkeypatch, moment)
        issue_invoice(invoice, actor_staff_user_id=staff_id)
        assert invoice.due_date == moment.astimezone(AMMAN).date() + timedelta(days=DEFAULT_DUE_DAYS)


def test_a_quote_is_valid_in_jordan_days_and_one_expired_in_jordan_cannot_be_accepted(app, seeded, monkeypatch):
    from tests.test_phase9_5d_quotes import _seed_customer, _seed_plan, _seed_sales_employee

    staff_id, profile_id = _seed_sales_employee(app, f"qd-{uuid.uuid4().hex[:6]}@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, plan_code=f"QD_{uuid.uuid4().hex[:4]}")
    moment = _after_midnight_in_amman()
    jordan = moment.astimezone(AMMAN).date()
    _pin_clock(monkeypatch, moment)
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import (
            DEFAULT_QUOTE_VALIDITY_DAYS,
            add_quote_line,
            create_quote,
            record_customer_decision,
            submit_quote,
        )

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id,
                             actor_staff_user_id=staff_id)
        assert quote.valid_until == jordan + timedelta(days=DEFAULT_QUOTE_VALIDITY_DAYS)
        expired = create_quote({"customer_id": customer_id, "currency": "USD", "valid_until": jordan - timedelta(days=1)},
                               actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(expired, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        submit_quote(expired, actor_staff_user_id=staff_id)
        with pytest.raises(CommercialSalesError) as exc:
            record_customer_decision(expired, accepted=True, actor_staff_user_id=staff_id)
        assert exc.value.code == "PRICE_VERSION_EXPIRED"
        # allow-half: valid until Jordan's today is still acceptable today
        live = create_quote({"customer_id": customer_id, "currency": "USD", "valid_until": jordan},
                            actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(live, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        submit_quote(live, actor_staff_user_id=staff_id)
        record_customer_decision(live, accepted=True, actor_staff_user_id=staff_id)
        assert live.status == "ACCEPTED"


def test_cancellation_and_employment_end_dates_are_jordans(app, seeded, monkeypatch):
    w = _world(app)
    sale = _sell(app, w, "p1")["order_id"]
    sub = _rows(app, sale)["subscription_id"]
    moment = _after_midnight_in_amman()
    _pin_clock(monkeypatch, moment)
    with app.app_context():
        from app.employees.services import terminate_employee
        from app.extensions import db_session
        from app.models.employees import EmployeeProfile
        from app.models.subscriptions import Subscription
        from app.subscriptions.services import transition_subscription

        subscription = db_session.get(Subscription, sub)
        transition_subscription(subscription, "CANCELLED", w["admin"], "customer left")
        assert subscription.cancellation_date == moment.astimezone(AMMAN).date()
        profile = db_session.get(EmployeeProfile, w["seller2_profile"])
        terminate_employee(profile, "left", w["admin"])
        assert profile.employment_end_date == moment.astimezone(AMMAN).date()


def test_a_licence_term_starts_on_jordans_day(app, seeded, monkeypatch):
    w = _world(app)
    moment = _after_midnight_in_amman()
    _pin_clock(monkeypatch, moment)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.issuance import resolve_license_term
        from app.models.catalog import Plan

        start, end = resolve_license_term(db_session.get(Plan, w["p1"]))
        assert start == moment.astimezone(AMMAN).date()


def test_activation_judges_licence_validity_by_jordans_day(app, client, seeded, signing_key, monkeypatch):
    """licensing_service/activation.py:166 date.today(): the SERVER's calendar.
    Licence dates are Jordan dates (issued from Jordan's day, above); at 00:30
    in Amman a licence valid until UTC's 'today' -- Jordan's yesterday -- has
    expired. Both directions."""
    import json

    from tests.conftest import build_activation_body, make_device_keypair

    w = _world(app, rule="PERCENT")
    expired, valid = _sell(app, w, "p1"), _sell(app, w, "p1")
    moment = _after_midnight_in_amman()
    jordan = moment.astimezone(AMMAN).date()
    expired_licence, valid_licence = _rows(app, expired["order_id"])["license_id"], _rows(app, valid["order_id"])["license_id"]
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License

        db_session.get(License, expired_licence).valid_until = jordan - timedelta(days=1)
        db_session.get(License, valid_licence).valid_until = jordan
        db_session.commit()
    _pin_clock(monkeypatch, moment, everywhere=False)  # the signed request is checked against the real clock

    def _activate(sale, installation):
        return client.post("/api/licensing/v1/activations", content_type="application/json", data=json.dumps(
            build_activation_body(make_device_keypair(), full_key=sale["full_key"], installation_id=installation,
                                  product_code="AURA_RETAIL", platform="WINDOWS")))

    # The public code is normalized on purpose (reason_codes.py: an expired
    # key must look like an unknown one); the two licences differ ONLY in
    # valid_until, so the refusal of one and the success of the other is the
    # expiry check deciding by Jordan's day.
    refused = _activate(expired, "night-expired")
    assert (refused.status_code, refused.get_json()["reason_code"]) == (400, "ACTIVATION_REJECTED")
    assert _activate(valid, "night-valid").status_code == 200


_BUSINESS_DATE_SITES = (
    "app/attention/service.py", "app/dashboard/services.py", "app/operational_reports/dashboards.py",
    "app/leads/website_intake.py", "app/cli.py", "app/commercial_sales/direct_sale.py",
    "app/commercial_sales/numbering.py", "app/expenses/numbering.py", "app/commercial_sales/invoices.py",
    "app/commercial_sales/quotes.py", "app/subscriptions/services.py", "app/employees/services.py",
    "app/licensing/issuance_routes.py", "app/licensing_service/activation.py", "app/licensing/issuance.py",
)


def test_no_business_date_site_reads_the_servers_or_utcs_calendar():
    """The display/queue sites (Attention, dashboards, the CLI defaults, the
    website intake) decide 'today' the same way as the money sites tested
    above: through the one Jordan helper. A server calendar read
    (date.today()) or a UTC one (utcnow().date()) in any of these files is
    the bug this round fixed."""
    owner = Path(__file__).resolve().parents[1]
    offenders = []
    for rel in _BUSINESS_DATE_SITES:
        for number, line in enumerate((owner / rel).read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if "date.today()" in code or "utcnow().date()" in code:
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert offenders == []


# ================================================ 5. refund -> licence stop --

def _stops(app):
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import LicenseScheduledStop

        return [(s.license_id, s.to_status, getattr(s, "devices_removed", None))
                for s in db_session.execute(select(LicenseScheduledStop)).scalars().all()]


def test_a_partial_refund_of_package_money_schedules_the_licence_stop(app, seeded):
    """Verifier probe v9: refund 250 of a 300 Package 1 + pair sale. The sale
    stops counting (its package money is no longer fully paid) -- and on
    d15d11f0 no stop was scheduled, so the shop kept the package for 50 JOD
    forever."""
    w = _world(app)
    sale = _sell(app, w, "p1", pairs=1)["order_id"]
    _refund(app, w, sale, "250.00")
    assert [s["status"] for s in _ladder_sales(app, w["seller_profile"])] == ["UNCOUNTED"]
    assert _stops(app) == [(_rows(app, sale)["license_id"], "SUSPENDED", None)]


def test_a_refund_of_only_the_pair_removes_its_two_devices_after_two_days_and_keeps_the_licence(app, seeded):
    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1", pairs=1)["order_id"]
    rows = _rows(app, sale)
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        limit_before = db_session.get(License, rows["license_id"]).device_limit
        allowance_before = db_session.get(Subscription, rows["subscription_id"]).device_allowance
    _refund(app, w, sale, "50.00")
    assert _stops(app) == [(rows["license_id"], None, 2)]  # no stop: the devices go
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.scheduled_stops import apply_due_licence_stops
        from app.models.commercial_sales import CommercialRefund
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        paid = db_session.execute(select(CommercialRefund)).scalars().one().paid_at.astimezone(UTC)
        assert apply_due_licence_stops(now=paid + timedelta(days=2) - timedelta(hours=1)) == 0
        db_session.expire_all()
        assert db_session.get(License, rows["license_id"]).device_limit == limit_before
        assert apply_due_licence_stops(now=paid + timedelta(days=2, minutes=1)) == 1
        db_session.expire_all()
        licence = db_session.get(License, rows["license_id"])
        subscription = db_session.get(Subscription, rows["subscription_id"])
        assert (licence.device_limit, licence.status) == (limit_before - 2, "ISSUED")
        assert (subscription.device_allowance, subscription.status) == (allowance_before - 2, "ACTIVE")


def test_half_a_pair_refunded_removes_no_device(app, seeded):
    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1", pairs=1)["order_id"]
    _refund(app, w, sale, "25.00")
    assert _stops(app) == []


def test_a_release_download_applies_a_due_stop_before_it_checks_the_licence(app, client, seeded, signing_key):
    """releases/distribution.py read the licence status without applying a
    stop whose moment had passed: two days after a full refund the shop could
    still download updates until some check-in or the nightly job ran."""
    import json

    from tests.conftest import build_activation_body, make_device_keypair
    from tests.test_phase9r_private_distribution import _authorize, _build_download_body, _draft_release

    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1")
    device = make_device_keypair()
    resp = client.post("/api/licensing/v1/activations", content_type="application/json", data=json.dumps(
        build_activation_body(device, full_key=sale["full_key"], installation_id="dl-shop",
                              product_code="AURA_RETAIL", platform="WINDOWS")))
    assert resp.status_code == 200, resp.get_json()
    installation_id = resp.get_json()["installation_id"]
    release_id, _content = _draft_release(app, product_code="AURA_RETAIL", platform_code="WINDOWS")
    with app.app_context():
        from app.extensions import db_session
        from app.models.catalog import ProductVersion
        from app.releases.authority import publish_release

        publish_release(db_session.get(ProductVersion, release_id), actor_staff_user_id=w["admin"])
    _refund(app, w, sale["order_id"], "250.00")
    with app.app_context():
        from app.extensions import db_session

        db_session.execute(text("UPDATE owner_license_scheduled_stops SET effective_at = now() - interval '1 minute'"))
        db_session.commit()
    denied = _authorize(client, _build_download_body(device, installation_id=installation_id, product_version_id=release_id))
    assert denied.status_code != 201, denied.get_json()
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License

        assert db_session.get(License, _rows(app, sale["order_id"])["license_id"]).status == "SUSPENDED"


def test_a_pending_stop_shows_on_the_subscription_and_licence_screens_in_arabic_too(app, client, seeded):
    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1")["order_id"]
    rows = _rows(app, sale)
    _refund(app, w, sale, "250.00")
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import LicenseScheduledStop

        effective = db_session.execute(select(LicenseScheduledStop)).scalars().one().effective_at
    jordan_day = effective.astimezone(AMMAN).date().isoformat()
    force_login(client, app, w["admin"])
    for url in (f"/subscriptions/{rows['subscription_id']}", f"/licenses/{rows['license_id']}"):
        html = client.get(url).get_data(as_text=True)
        assert f'data-pending-stop="{jordan_day}"' in html, url
        assert "This licence stops on" in html, url
    client.get("/locale/ar?next=/")
    for url in (f"/subscriptions/{rows['subscription_id']}", f"/licenses/{rows['license_id']}"):
        ar = client.get(url).get_data(as_text=True)
        assert f'data-pending-stop="{jordan_day}"' in ar, url
        assert "يتوقف هذا الترخيص في" in ar, url
        assert "This licence stops on" not in ar, url


# ===================================== 6. the rule at the first payment --

def test_a_ladder_to_percentage_switch_mid_p2_counts_the_sale_under_its_first_payments_rule(app, seeded):
    """Verifier probe v8: a ladder seller's Package 2 sale, then the seller
    moves to a percentage plan before the 3rd payment. On d15d11f0 the sale
    earned NOTHING: the ladder used the rule at the 3rd payment (percentage),
    the percentage path never sees a later monthly payment. The rule in force
    at the sale's FIRST package payment governs the whole sale."""
    w = _world(app)
    days_ago = datetime.now(UTC) - timedelta(days=3)  # the plan change takes effect from a later DAY
    sale = _sell(app, w, "p2", confirmed_at=days_ago)
    sub = _rows(app, sale["order_id"])["subscription_id"]
    _pay_month(app, w, sub, "35.00", confirmed_at=days_ago + timedelta(hours=1))
    _assign(app, w, [w["seller_profile"]], rule="PERCENT", since=date.today())
    third = _pay_month(app, w, sub, "35.00")
    [counted] = _ladder_sales(app, w["seller_profile"])
    assert (counted["status"], counted["payment_id"]) == ("COUNTED", third)
    assert _net_by_order(app, w["seller_profile"]) == {sale["order_id"]: D("20.00")}
    assert _entries(app, w["seller_profile"], kind="PAYMENT_RULE") == []


def test_a_ladder_p2_not_yet_counted_gets_no_percentage_row(app, seeded):
    """Kills V8b (the percentage path's 'is this a ladder rule?' check): a
    ladder seller's first P2 payment is allocated while the sale does not
    count yet, so no counted row masks a missing rule check."""
    w = _world(app)
    _sell(app, w, "p2")
    assert _entries(app, w["seller_profile"], kind="PAYMENT_RULE") == []
    assert _ladder_sales(app, w["seller_profile"]) == []


def test_the_percentage_path_never_pays_an_order_already_counted_on_a_ladder(app, seeded, monkeypatch):
    """The never-both guard on the percentage side. No normal flow reaches it
    (one governing rule decides both sides the same way), so the state is
    made by hand: while the sale is being finished, the ladder step is
    replaced by one that records a COUNTED ladder row for the order -- as if
    it had been counted on a ladder -- although its seller is on a
    percentage plan. The allocation must still post no percentage row."""
    import app.commissions.ladder as ladder_module

    w = _world(app, rule="PERCENT")

    def _counted_by_hand(order_id, **_kwargs):
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice, PaymentAllocation, SalesOrderLine
        from app.models.commissions import CommissionLadderSale, CommissionRuleVersion

        db_session.flush()
        invoice = db_session.execute(select(CommercialInvoice).where(
            CommercialInvoice.sales_order_id == order_id)).scalars().one()
        allocation = db_session.execute(select(PaymentAllocation).where(
            PaymentAllocation.commercial_invoice_id == invoice.id)).scalars().one()
        plan_line = db_session.execute(select(SalesOrderLine).where(
            SalesOrderLine.sales_order_id == order_id, SalesOrderLine.plan_id.is_not(None))).scalars().first()
        rule = db_session.execute(select(CommissionRuleVersion)).scalars().first()
        now = datetime.now(UTC)
        db_session.add(CommissionLadderSale(
            employee_profile_id=w["seller_profile"], sales_order_id=order_id, commercial_invoice_id=invoice.id,
            package_plan_id=plan_line.plan_id, commission_rule_version_id=rule.id,
            qualifying_payment_record_id=allocation.payment_record_id, qualifying_allocation_id=allocation.id,
            qualified_at=now, ladder_month=now.astimezone(AMMAN).date().replace(day=1),
            hold_ends_at=now + timedelta(days=30), currency=JOD, status="COUNTED",
        ))
        db_session.flush()

    monkeypatch.setattr(ladder_module, "evaluate_sale", _counted_by_hand)
    sale = _sell(app, w, "p1")["order_id"]
    monkeypatch.undo()
    assert [s["order_id"] for s in _ladder_sales(app, w["seller_profile"])] == [sale]
    assert _entries(app, w["seller_profile"], kind="PAYMENT_RULE") == []


def test_the_percentage_side_pays_every_part_of_a_sale_under_the_rule_of_its_first_payment(app, seeded):
    """The percentage side of 'the rule at the first package payment governs
    the whole sale': an invoice paid in two parts, the seller's rate raised
    from 10% to 20% between them. Both parts earn 10% -- on a per-payment
    rule the second would earn 20%."""
    from tests.test_phase9_5d_commission_ledger import (
        _make_confirmed_payment,
        _make_issued_invoice,
        _seed_customer,
        _seed_plan,
        _seed_sales_employee,
    )
    from tests.test_step0_ladder import _backdate_confirmation

    staff_a, profile_a = _seed_sales_employee(app, f"gov-{uuid.uuid4().hex[:6]}@example.com")
    staff_b, _ = _seed_sales_employee(app, f"govf-{uuid.uuid4().hex[:6]}@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, f"GOV_{uuid.uuid4().hex[:4]}")
    with app.app_context():
        from app.commercial_sales.allocation import allocate_payment
        from app.commissions.management import (
            assign_employee_commission_plan,
            create_commission_plan,
            create_commission_rule_version,
        )
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry as E

        def _grant(rate, since):
            plan = create_commission_plan({"plan_code": f"GOV_{uuid.uuid4().hex[:8]}", "name": "x"}, staff_b)
            create_commission_rule_version(plan, rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=rate,
                                           effective_from=since, actor_staff_user_id=staff_b)
            assign_employee_commission_plan(profile_a, plan, effective_from=since, actor_staff_user_id=staff_b)

        _grant(Decimal("10.0"), date.today() - timedelta(days=10))
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        first = _make_confirmed_payment(app, customer_id, Decimal("300.00"), staff_a, staff_b)
        _backdate_confirmation(first.id, datetime.now(UTC) - timedelta(days=3))
        allocate_payment(payment=db_session.get(type(first), first.id), invoice=invoice, amount=Decimal("300.00"),
                         actor_staff_user_id=staff_b)
        _grant(Decimal("20.0"), date.today())
        second = _make_confirmed_payment(app, customer_id, Decimal("700.00"), staff_a, staff_b)
        allocate_payment(payment=second, invoice=invoice, amount=Decimal("700.00"), actor_staff_user_id=staff_b)
        amounts = sorted(Decimal(e.commission_amount) for e in db_session.execute(
            select(E).where(E.source_commercial_invoice_id == invoice.id)).scalars().all())
    assert amounts == [D("30.00"), D("70.00")]


def test_the_payments_screen_refuses_a_future_payment_date(app, seeded):
    """commercial_sales/payments.py::submit_payment (the payments screen and
    the API): the same refusal, with its own code -- not a 500."""
    w = _world(app)
    customer_id = _rows(app, _sell(app, w, "p1")["order_id"])["customer_id"]
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.payments import submit_payment

        with pytest.raises(CommercialSalesError) as exc:
            submit_payment(customer_id=customer_id, amount=D("10.00"), currency=JOD, method="CASH",
                           payment_date=_jordan_today() + timedelta(days=1), actor_staff_user_id=w["seller"])
        assert exc.value.code == "PAYMENT_DATE_IN_FUTURE"
        assert submit_payment(customer_id=customer_id, amount=D("10.00"), currency=JOD, method="CASH",
                              payment_date=_jordan_today(), actor_staff_user_id=w["seller"]).id


# ================================================ 7. the boundaries --

def test_approval_one_day_before_the_hold_ends_is_refused_and_one_hour_after_is_allowed(app, seeded):
    """Kills V5b (approval allowed up to a day early)."""
    w = _world(app)
    early = _sell(app, w, "p1", confirmed_at=datetime.now(UTC) - timedelta(days=29, hours=12))["order_id"]
    late = _sell(app, w, "p1", confirmed_at=datetime.now(UTC) - timedelta(days=30, hours=1))["order_id"]
    from app.commissions.errors import CommissionError

    with pytest.raises(CommissionError) as exc:
        _approve_order(app, w, early)
    assert exc.value.code == "COMMISSION_ON_HOLD"
    _approve_order(app, w, late)
    assert [st for o, _a, st, _p, _m in _entries(app, w["seller_profile"]) if o == late] == ["APPROVED"]


def test_the_on_hold_refusal_names_jordans_date(app, seeded):
    """ledger.py printed payable_after.date() -- the date in the DB session's
    zone. A hold ending at 21:30 UTC ends at 00:30 the next day in Amman."""
    w = _world(app)
    d = datetime.now(UTC).date() - timedelta(days=29)
    order_id = _sell(app, w, "p1", confirmed_at=datetime(d.year, d.month, d.day, 21, 30, tzinfo=UTC))["order_id"]
    from app.commissions.errors import CommissionError

    with pytest.raises(CommissionError) as exc:
        _approve_order(app, w, order_id)
    [held] = _ladder_sales(app, w["seller_profile"])
    assert exc.value.params["payable_after"] == held["hold_ends_at"].astimezone(AMMAN).date().isoformat()


def test_a_refund_in_the_last_days_of_the_hold_uncounts_and_one_after_it_does_not(app, seeded):
    """Kills V6 (a refund in the last 2 days of the hold kept counting)."""
    w = _world(app)
    inside = _sell(app, w, "p1", confirmed_at=datetime.now(UTC) - timedelta(days=29))["order_id"]
    after = _sell(app, w, "p1", confirmed_at=datetime.now(UTC) - timedelta(days=30, hours=1))["order_id"]
    _refund(app, w, inside, "250.00")
    _refund(app, w, after, "250.00")
    sales = {s["order_id"]: s for s in _ladder_sales(app, w["seller_profile"])}
    assert (sales[inside]["status"], sales[inside]["cause"]) == ("UNCOUNTED", "REFUND_IN_HOLD")
    assert sales[after]["status"] == "COUNTED" and sales[after]["refunded_after_hold_at"] is not None


def test_a_licence_stop_is_not_applied_early(app, seeded):
    """Kills V7b (stops applied 12 hours early)."""
    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1")["order_id"]
    _refund(app, w, sale, "250.00")
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.scheduled_stops import apply_due_licence_stops
        from app.models.commercial_sales import CommercialRefund

        paid = db_session.execute(select(CommercialRefund)).scalars().one().paid_at.astimezone(UTC)
        assert apply_due_licence_stops(now=paid + timedelta(days=2) - timedelta(hours=1)) == 0
        assert apply_due_licence_stops(now=paid + timedelta(days=2) + timedelta(minutes=1)) == 1


def _a_past_winter_month_end() -> date:
    """The last day of a past month in which Asia/Gaza (the database
    session's zone) is UTC+2 while Asia/Amman is UTC+3."""
    month = _jordan_today().replace(day=1)
    while True:
        last_day = month - timedelta(days=1)
        month = last_day.replace(day=1)
        if last_day.month in (11, 12, 1, 2) and last_day < _jordan_today() - timedelta(days=2):
            return last_day


def test_the_month_is_jordan_time_in_winter_too(app, seeded):
    """Kills V2b (the month bucketed in the DB session's zone, Asia/Gaza):
    in summer Gaza and Amman are both UTC+3 and the summer test cannot tell
    them apart. 21:30 UTC on a winter month's last day is 00:30 the next day
    in Amman -- the NEXT month -- and still 23:30 in Gaza."""
    w = _world(app, rule_from_days_ago=400)
    last_day = _a_past_winter_month_end()
    moment = datetime(last_day.year, last_day.month, last_day.day, 21, 30, tzinfo=UTC)
    _sell(app, w, "p1", confirmed_at=moment)
    [s] = _ladder_sales(app, w["seller_profile"])
    assert s["month"] == last_day + timedelta(days=1)


# ======================================================= 8. the screen --

def test_on_hold_until_shows_jordans_date(app, client, seeded):
    """commissions_list.html:81 printed payable_after.date() -- the date in
    whatever zone the value came back in (the DB session's, Asia/Gaza). A
    hold ending 21:30 UTC ends at 00:30 the next day in Amman."""
    w = _world(app)
    d = datetime.now(UTC).date() - timedelta(days=2)
    confirmed = datetime(d.year, d.month, d.day, 21, 30, tzinfo=UTC)
    _sell(app, w, "p1", confirmed_at=confirmed)
    hold_end_in_jordan = (confirmed + timedelta(days=30)).astimezone(AMMAN).date()
    force_login(client, app, w["manager"])
    html = client.get("/commissions").get_data(as_text=True)
    assert f'data-on-hold-until="{hold_end_in_jordan.isoformat()}"' in html
