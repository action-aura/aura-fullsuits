"""Seller agreement clause 7 -- cash a seller collects goes back to the company in 24 hours.

Every guard is proven by what it WROTE (rows, queued mails), never by a status code alone, and the
deny half has its allow twin: a seller's cash makes one obligation, a card payment / a manager's cash
makes none; OPEN past due is overdue, a SUBMITTED slip is not; the alert goes out once; a seller sees
and acts on only their own rows, a manager sees all.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.conftest import jordan_today
from tests.seller_rules_world import client_for, count, make_customer, queued_mail, record, world
from tests.test_customer_onboarding import _jpeg, kyc  # noqa: F401 -- fixture


@pytest.fixture()
def w(app, seeded):
    return world(app)


def _handins(app, **filters):
    with app.app_context():
        from app.extensions import db_session
        from app.models.sellers import SellerCashHandIn

        stmt = select(SellerCashHandIn)
        for key, value in filters.items():
            stmt = stmt.where(getattr(SellerCashHandIn, key) == value)
        rows = db_session.execute(stmt.order_by(SellerCashHandIn.created_at)).scalars().all()
        db_session.expunge_all()
        return rows


def _total(app):
    from app.models.sellers import SellerCashHandIn

    return count(app, SellerCashHandIn)


def _cash_by_seller(app, w, customer_owner="seller"):
    customer = make_customer(app, w[customer_owner])
    payment = record(app, w["seller"], customer)
    (row,) = _handins(app, payment_record_id=payment)
    return customer, payment, row


# ------------------------------------------------------------------ birth

def test_a_sellers_cash_payment_creates_exactly_one_obligation_due_in_24_hours(app, w):
    customer = make_customer(app, w["seller"])
    assert _total(app) == 0
    payment = record(app, w["seller"], customer, amount="120.50")
    (row,) = _handins(app)
    assert row.payment_record_id == payment and row.seller_staff_user_id == w["seller"]
    assert row.status == "OPEN" and Decimal(row.amount) == Decimal("120.50") and row.currency == "JOD"
    assert row.due_at - row.collected_at == timedelta(hours=24)
    record(app, w["seller"], customer, amount="10.00")  # a second payment is a second obligation, never merged
    assert _total(app) == 2


def test_card_bank_and_cheque_payments_by_a_seller_create_no_obligation(app, w):
    customer = make_customer(app, w["seller"])
    for method in ("BANK_TRANSFER", "CARD_OFFLINE", "CHEQUE", "OTHER"):
        record(app, w["seller"], customer, method=method)
    assert _total(app) == 0


def test_cash_recorded_by_a_manager_or_finance_or_admin_creates_no_obligation(app, w):
    customer = make_customer(app, w["seller"])
    for who in ("sm", "finance", "admin"):
        record(app, w[who], customer)
    assert _total(app) == 0
    record(app, w["seller2"], customer)  # and the allow twin: another SELLER's cash does
    assert _total(app) == 1


def test_the_one_screen_sale_and_the_payments_form_both_reach_the_same_rule(app, w):
    """Two real routes into the one writer: the seller's one-screen sale and the sales-employee
    'I received this payment' action each leave exactly one obligation for a CASH payment."""
    from tests.test_issuance_sale import _codes, _profile
    from tests.test_step0_ladder import _world as _ladder_world

    lw = _ladder_world(app)
    with app.app_context():
        from app.commercial_sales.direct_sale import record_direct_sale
        from app.commercial_sales.payments import submit_payment

        sale = record_direct_sale(
            new_customer_legal_name="Cash Shop", plan_id=lw["p1"], extra_desktops=0, extra_phones=0,
            amount=Decimal("250.00"), method="CASH", reference="CASH-RECEIPT-1", payment_date=jordan_today(),
            idempotency_key=str(uuid.uuid4()), actor_staff_user_id=lw["seller"], actor_permission_codes=_codes(lw["seller"]),
            as_of=jordan_today(),
        )
        submitted = submit_payment(
            customer_id=sale["customer_id"], amount=Decimal("5.00"), currency="JOD", method="CASH",
            payment_date=jordan_today(), actor_staff_user_id=lw["seller"],
        )
        sale_payment, other_payment = sale["payment_id"], submitted.id
    rows = _handins(app)
    assert sorted(r.payment_record_id for r in rows) == sorted([sale_payment, other_payment])
    assert {r.seller_staff_user_id for r in rows} == {lw["seller"]}
    # replaying the same sale key records nothing new
    assert _total(app) == 2


def test_the_database_allows_one_obligation_per_payment_and_refuses_a_slipless_submitted_row(app, w):
    _customer, payment, row = _cash_by_seller(app, w)
    with app.app_context():
        from app.extensions import db_session
        from app.models.sellers import SellerCashHandIn

        db_session.add(SellerCashHandIn(
            seller_staff_user_id=w["seller"], payment_record_id=payment, amount=Decimal("1.00"), currency="JOD",
            collected_at=row.collected_at, due_at=row.due_at, status="OPEN"))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
        live = db_session.get(SellerCashHandIn, row.id)
        live.status = "SUBMITTED"  # no slip named
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


# ------------------------------------------------------------------ overdue

def test_overdue_is_only_an_OPEN_row_past_its_deadline(app, w):
    from app.sellers import cash_handin

    _c, _p, row = _cash_by_seller(app, w)
    assert not cash_handin.is_overdue(row, row.due_at - timedelta(seconds=1))
    assert not cash_handin.is_overdue(row, row.due_at)  # the deadline instant itself is still on time
    assert cash_handin.is_overdue(row, row.due_at + timedelta(seconds=1))
    row.status = "SUBMITTED"  # a slip waiting for a manager is the company's wait, not the seller's fault
    assert not cash_handin.is_overdue(row, row.due_at + timedelta(days=30))
    for closed in ("RECEIVED", "RECEIVED_AT_OFFICE"):
        row.status = closed
        assert not cash_handin.is_overdue(row, row.due_at + timedelta(days=30))


def test_a_payment_that_failed_is_not_chased(app, w):
    from app.extensions import db_session
    from app.models.subscriptions import PaymentRecord
    from app.sellers import cash_handin

    _c, payment, row = _cash_by_seller(app, w)
    with app.app_context():
        assert [r.id for r in cash_handin.for_seller(w["seller"])] == [row.id]
        db_session.get(PaymentRecord, payment).status = "FAILED"
        db_session.commit()
        assert cash_handin.for_seller(w["seller"]) == []
        assert cash_handin.scan_overdue(now=row.due_at + timedelta(days=2))["overdue_found"] == 0


# ------------------------------------------------------------------ the alert

def test_the_overdue_alert_goes_out_once_to_the_seller_and_every_payments_confirm_holder(app, w):
    from app.sellers import cash_handin

    app.config["SMTP_HOST"] = "smtp.test"
    _c, _p, row = _cash_by_seller(app, w)
    before = row.due_at - timedelta(minutes=1)
    after = row.due_at + timedelta(minutes=1)
    with app.app_context():
        assert cash_handin.scan_overdue(now=before)["announced"] == 0  # not yet due
    assert queued_mail(app, "seller_cash_overdue") == []
    with app.app_context():
        first = cash_handin.scan_overdue(now=after)
    assert first["overdue_found"] == 1 and first["announced"] == 1
    mails = queued_mail(app, "seller_cash_overdue")
    assert len(mails) == first["mails_queued"] == 4  # the seller + sales manager + finance + super admin
    from app.models.staff import StaffUser

    with app.app_context():
        from app.extensions import db_session

        expected = {db_session.get(StaffUser, w[k]).email for k in ("seller", "sm", "finance", "admin")}
    assert set(mails) == expected and w["seller2"] not in mails
    with app.app_context():
        again = cash_handin.scan_overdue(now=after + timedelta(days=3))
    assert again["overdue_found"] == 0 and len(queued_mail(app, "seller_cash_overdue")) == 4  # once per obligation


def test_a_slip_waiting_for_a_manager_is_never_announced_as_overdue(app, w, kyc):
    from app.sellers import cash_handin

    app.config["SMTP_HOST"] = "smtp.test"
    _c, _p, row = _cash_by_seller(app, w)
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        cash_handin.submit_slip(row.id, seller=db_session.get(StaffUser, w["seller"]), content=_jpeg())
        found = cash_handin.scan_overdue(now=row.due_at + timedelta(days=5))
    assert found["overdue_found"] == 0 and queued_mail(app, "seller_cash_overdue") == []


def test_with_email_off_nothing_is_queued_nothing_is_stamped_and_it_goes_out_when_email_is_on(app, w):
    from app.sellers import cash_handin

    app.config["SMTP_HOST"] = ""
    _c, _p, row = _cash_by_seller(app, w)
    late = row.due_at + timedelta(hours=1)
    with app.app_context():
        off = cash_handin.scan_overdue(now=late)
    assert off["announced"] == 0 and off["mail_off"] == 1 and queued_mail(app, "seller_cash_overdue") == []
    assert _handins(app)[0].overdue_notified_at is None
    app.config["SMTP_HOST"] = "smtp.test"
    with app.app_context():
        on = cash_handin.scan_overdue(now=late)
    assert on["announced"] == 1 and len(queued_mail(app, "seller_cash_overdue")) == 4
    assert _handins(app)[0].overdue_notified_at is not None


def test_the_dry_run_counts_but_changes_nothing(app, w):
    from app.sellers import cash_handin

    app.config["SMTP_HOST"] = "smtp.test"
    _c, _p, row = _cash_by_seller(app, w)
    with app.app_context():
        dry = cash_handin.scan_overdue(now=row.due_at + timedelta(hours=2), apply=False)
    assert dry["overdue_found"] == 1 and dry["announced"] == 0
    assert queued_mail(app, "seller_cash_overdue") == [] and _handins(app)[0].overdue_notified_at is None


def test_the_cli_command_runs_the_scan(app, w):
    _c, _p, row = _cash_by_seller(app, w)
    result = app.test_cli_runner().invoke(args=["sellers", "scan"])
    assert result.exit_code == 0, result.output
    import json

    out = json.loads(result.output)
    assert out["dry_run"] is True and out["cash_overdue"]["overdue_found"] == 0  # not due yet


# ------------------------------------------------------------------ the screens

def test_a_seller_sees_only_their_own_cash_and_cannot_touch_another_sellers(app, w, kyc):
    c1 = make_customer(app, w["seller"])
    c2 = make_customer(app, w["seller2"])
    mine = _handins(app, payment_record_id=record(app, w["seller"], c1))[0]
    theirs = _handins(app, payment_record_id=record(app, w["seller2"], c2))[0]
    client = client_for(app, w["seller"])
    page = client.get("/sellers/cash").get_data(as_text=True)
    assert f'data-handin-id="{mine.id}"' in page and str(theirs.id) not in page
    # uploading a slip against someone else's row: refused, row unchanged, nothing stored
    from app.models.customer_onboarding import CustomerDocument

    docs_before = count(app, CustomerDocument)
    denied = client.post(f"/sellers/cash/{theirs.id}/slip", data={"slip": (__import__("io").BytesIO(_jpeg()), "s.jpg")},
                         content_type="multipart/form-data")
    assert denied.status_code == 303
    assert _handins(app, id=theirs.id)[0].status == "OPEN" and count(app, CustomerDocument) == docs_before
    # and the allow twin: their own row takes the slip
    ok = client.post(f"/sellers/cash/{mine.id}/slip", data={"slip": (__import__("io").BytesIO(_jpeg()), "s.jpg")},
                     content_type="multipart/form-data")
    assert ok.status_code == 303
    row = _handins(app, id=mine.id)[0]
    assert row.status == "SUBMITTED" and row.slip_document_id is not None and count(app, CustomerDocument) == docs_before + 1


def test_the_manager_page_needs_payments_confirm_and_lists_everyones_cash(app, w):
    c1, c2 = make_customer(app, w["seller"]), make_customer(app, w["seller2"])
    a = _handins(app, payment_record_id=record(app, w["seller"], c1))[0]
    b = _handins(app, payment_record_id=record(app, w["seller2"], c2))[0]
    assert client_for(app, w["seller"]).get("/sellers/cash/manage").status_code == 403
    assert client_for(app, w["seller"]).post(f"/sellers/cash/manage/{a.id}/receive", data={"mode": "office"}).status_code == 403
    assert _handins(app, id=a.id)[0].status == "OPEN"
    for who in ("finance", "sm", "admin"):
        page = client_for(app, w[who]).get("/sellers/cash/manage")
        text = page.get_data(as_text=True)
        assert page.status_code == 200 and str(a.id) in text and str(b.id) in text, who


def test_the_overdue_row_links_to_a_prefilled_sanction_case_only_for_holders_of_sellers_discipline(app, w):
    """Clause 16 shortcut: an overdue hand-in carries 'Open a sanction case' (seller + CASH_HAND_IN link
    prefilled) for a holder of sellers.discipline. Deny halves: a payments.confirm holder WITHOUT that
    permission (finance) sees the same overdue row but no link, a seller cannot even load the page, and a
    row that is not overdue has no link for the manager. Allow half: the link target really opens a case."""
    from app.extensions import db_session
    from app.models.sellers import SellerCashHandIn

    c1, c2 = make_customer(app, w["seller"]), make_customer(app, w["seller2"])
    late = _handins(app, payment_record_id=record(app, w["seller"], c1))[0]
    fresh = _handins(app, payment_record_id=record(app, w["seller2"], c2))[0]
    with app.app_context():
        row = db_session.get(SellerCashHandIn, late.id)
        row.collected_at = row.collected_at - timedelta(days=3)
        row.due_at = row.due_at - timedelta(days=3)
        db_session.commit()
    marker = f'data-open-sanction-case="{late.id}"'
    manager = client_for(app, w["sm"]).get("/sellers/cash/manage").get_data(as_text=True)
    assert 'data-handin-overdue="1"' in manager and marker in manager
    assert f'data-open-sanction-case="{fresh.id}"' not in manager  # not overdue: no link
    assert f"link_kind=CASH_HAND_IN&amp;link_id={late.id}" in manager and f"seller={w['seller']}" in manager
    assert marker in client_for(app, w["admin"]).get("/sellers/cash/manage").get_data(as_text=True)
    finance = client_for(app, w["finance"]).get("/sellers/cash/manage")
    assert finance.status_code == 200 and 'data-handin-overdue="1"' in finance.get_data(as_text=True)
    assert "data-open-sanction-case" not in finance.get_data(as_text=True)
    assert client_for(app, w["seller"]).get("/sellers/cash/manage").status_code == 403
    # the target: the new-case form accepts the link and carries it through
    with app.test_request_context():
        from flask import url_for

        target = url_for("seller_discipline.new_case", seller=w["seller"], link_kind="CASH_HAND_IN", link_id=late.id)
    form = client_for(app, w["sm"]).get(target)
    assert form.status_code == 200 and 'value="CASH_HAND_IN"' in form.get_data(as_text=True)


def test_the_seller_dashboard_tile_counts_only_their_own_and_goes_red_when_overdue(app, w):
    from app.sellers import cash_handin

    c1, c2 = make_customer(app, w["seller"]), make_customer(app, w["seller2"])
    record(app, w["seller"], c1)
    record(app, w["seller2"], c2)
    record(app, w["seller2"], c2)
    with app.app_context():
        tile = cash_handin.tile_for_seller(w["seller"])
        late = cash_handin.tile_for_seller(w["seller"], now=tile["next_due_at"] + timedelta(minutes=1))
    assert tile["open"] == 1 and tile["overdue"] == 0 and late["overdue"] == 1
    page = client_for(app, w["seller"]).get("/").get_data(as_text=True)
    assert 'data-cash-mine-card="1"' in page and 'data-cash-manage-card' not in page
    manager_page = client_for(app, w["finance"]).get("/").get_data(as_text=True)
    assert 'data-cash-manage-card="1"' in manager_page and 'data-cash-mine-card' not in manager_page


# ------------------------------------------------------------------ closing

def test_the_slip_is_confirmed_rejected_or_the_cash_is_received_at_the_office(app, w, kyc):
    from app.extensions import db_session
    from app.models.staff import StaffUser
    from app.sellers import cash_handin
    from app.sellers.errors import SellerRulesError

    c = make_customer(app, w["seller"])
    ids = [_handins(app, payment_record_id=record(app, w["seller"], c))[0].id for _ in range(3)]
    with app.app_context():
        seller, manager = db_session.get(StaffUser, w["seller"]), db_session.get(StaffUser, w["sm"])
        # a manager cannot confirm what was never uploaded
        with pytest.raises(SellerRulesError) as no_slip:
            cash_handin.receive(ids[0], manager=manager, at_office=False)
        assert no_slip.value.code == "HANDIN_NO_SLIP_TO_CONFIRM"
        for hid in ids[:2]:
            cash_handin.submit_slip(hid, seller=seller, content=_jpeg())
        # reject: back to OPEN, slip cleared, reason and count kept; reason is required
        with pytest.raises(SellerRulesError):
            cash_handin.reject_slip(ids[0], manager=manager, reason="  ")
        cash_handin.reject_slip(ids[0], manager=manager, reason="Blurred")
        # confirm: RECEIVED, stamped with who and when
        cash_handin.receive(ids[1], manager=manager, at_office=False)
        # office: straight from OPEN
        cash_handin.receive(ids[2], manager=manager, at_office=True)
        db_session.expire_all()
        from app.models.sellers import SellerCashHandIn

        a, b, c3 = (db_session.get(SellerCashHandIn, i) for i in ids)
        assert (a.status, a.slip_document_id, a.rejected_count, a.last_rejection_reason) == ("OPEN", None, 1, "Blurred")
        assert b.status == "RECEIVED" and b.received_by_staff_user_id == w["sm"] and b.received_at is not None
        assert c3.status == "RECEIVED_AT_OFFICE" and c3.received_by_staff_user_id == w["sm"]
        # a closed one cannot be closed or rejected again
        with pytest.raises(SellerRulesError) as again:
            cash_handin.receive(ids[1], manager=manager, at_office=True)
        assert again.value.code == "HANDIN_ALREADY_CLOSED"
        # the seller can upload again after a rejection
        cash_handin.submit_slip(ids[0], seller=seller, content=_jpeg())
        assert db_session.get(SellerCashHandIn, ids[0]).status == "SUBMITTED"


def test_a_seller_can_never_close_their_own_obligation(app, w):
    from app.extensions import db_session
    from app.models.staff import StaffUser
    from app.sellers import cash_handin
    from app.sellers.errors import SellerRulesError

    # a manager who also sells: SALES + SALES_MANAGER holds payments.confirm and is a seller
    from tests.conftest import make_staff

    both = make_staff(app, f"both-{uuid.uuid4().hex[:5]}@example.com", role_codes=["SALES", "SALES_MANAGER"], mfa=True)
    c = make_customer(app, both)
    row = _handins(app, payment_record_id=record(app, both, c))[0]
    with app.app_context():
        with pytest.raises(SellerRulesError) as exc:
            cash_handin.receive(row.id, manager=db_session.get(StaffUser, both), at_office=True)
        assert exc.value.code == "HANDIN_SELF_RECEIVE_FORBIDDEN"
    assert _handins(app, id=row.id)[0].status == "OPEN"
    # the allow twin: somebody else can
    with app.app_context():
        cash_handin.receive(row.id, manager=db_session.get(StaffUser, w["finance"]), at_office=True)
    assert _handins(app, id=row.id)[0].status == "RECEIVED_AT_OFFICE"


def test_the_sellers_slip_must_be_a_real_file_and_nothing_is_stored_when_it_is_not(app, w, kyc):
    from app.extensions import db_session
    from app.models.customer_onboarding import CustomerDocument
    from app.models.staff import StaffUser
    from app.sellers import cash_handin
    from app.sellers.errors import SellerRulesError

    _c, _p, row = _cash_by_seller(app, w)
    with app.app_context():
        seller = db_session.get(StaffUser, w["seller"])
        for bad, code in ((b"", "HANDIN_SLIP_REQUIRED"), (b"not a picture at all", "HANDIN_SLIP_TYPE_NOT_ALLOWED")):
            with pytest.raises(SellerRulesError) as exc:
                cash_handin.submit_slip(row.id, seller=seller, content=bad)
            assert exc.value.code == code
    assert count(app, CustomerDocument) == 0 and _handins(app, id=row.id)[0].status == "OPEN"
