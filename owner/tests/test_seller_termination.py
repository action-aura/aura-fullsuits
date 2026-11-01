"""Seller agreement clauses 17 + 18 -- ending the agreement, and what happens to the customers.

What is proven (each guard with its allow twin), by what was written:

  notice      either side ends it with 7 days' notice: the effective moment is exactly 7 days later; the
              seller keeps working (sign-in works) until then; a second notice is refused (and the DATABASE
              refuses a second running notice too); the seller cannot choose who gets their customers
  effect      one second before: nothing changes; at the moment: the account is closed, every session is
              dead, the profile is TERMINATED; running it again does nothing
  customers   a customer whose TRIAL had started keeps the seller (a credit row) and every other customer
              of the seller returns to the company (sales owner cleared) -- or goes to the seller the
              manager named; other sellers' customers are not touched
  clause 18   the trial customer's FIRST payment still credits the former seller (the order is theirs, the
              commission lands on their profile), even though their account is closed; no other seller can
              be named for it, and the former seller cannot be named for a trial that had not started;
              once the trial is paid the credit is released and the customer returns to the company
  earned      commissions already on the ledger are exactly as they were
  sign-in     the person signing in settles their own due notice, with no scan having run
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import force_login, get_csrf, jordan_today, make_staff
from tests.test_customer_onboarding import _codes, _jpeg, kyc, onboard  # noqa: F401 -- kyc is a fixture
from tests.test_issuance_sale import PEPPER, _confirm, _profile, _record, _world as sale_world
from tests.test_trial_conversion import _activate

PASSWORD = "Sup3r-Str0ng-Pass!"
SLIP = _jpeg(b"bank-slip-gps")


# ------------------------------------------------------------------ helpers

@pytest.fixture()
def w(app, seeded, kyc):
    world = sale_world(app)
    world["mgr"] = make_staff(app, f"term-mgr-{uuid.uuid4().hex[:5]}@example.com", role_codes=["SALES_MANAGER"], mfa=True)
    world["other"] = make_staff(app, f"term-other-{uuid.uuid4().hex[:5]}@example.com", role_codes=["SALES"], mfa=True)
    world["mgr_profile"] = _profile(app, world["mgr"], f"EMP-G-{uuid.uuid4().hex[:4]}", "Gina Manager")
    world["other_profile"] = _profile(app, world["other"], f"EMP-O-{uuid.uuid4().hex[:4]}", "Omar Other")
    return world


def _now():
    return datetime.now(timezone.utc)


def _staff(staff_id):
    from app.extensions import db_session
    from app.models.staff import StaffUser

    return db_session.get(StaffUser, staff_id)


def _notice(app, w, *, by="seller", seller="seller", now=None, reassign=None, reason="", **kw):
    from app.seller_discipline import terminations

    with app.app_context():
        row = terminations.give_notice(
            w[seller], actor=_staff(w[by]), by_seller=(by == seller), reason=reason,
            reassign_to_staff_user_id=w[reassign] if reassign else None, now=now or _now(), **kw)
        return row.id, row.effective_at


def _take_effect(app, termination_id, now):
    from app.seller_discipline import terminations

    with app.app_context():
        return terminations.apply_termination(termination_id, now=now)


def _refused(call, code):
    from app.seller_discipline.errors import DisciplineError

    with pytest.raises(DisciplineError) as info:
        call()
    assert info.value.code == code, info.value.code


def _termination(app, termination_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.seller_discipline import SellerTermination

        db_session.expire_all()
        row = db_session.get(SellerTermination, termination_id)
        db_session.expunge(row)
        return row


def _count(app, model, *where):
    with app.app_context():
        from app.extensions import db_session

        return db_session.execute(select(func.count()).select_from(model).where(*where)).scalar_one()


def _owner(app, customer_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer

        db_session.expire_all()
        return db_session.get(Customer, customer_id).assigned_sales_staff_id


def _account(app, staff_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.employees import EmployeeProfile

        db_session.expire_all()
        staff = _staff(staff_id)
        profile = db_session.execute(select(EmployeeProfile).where(EmployeeProfile.staff_user_id == staff_id)).scalars().one()
        return staff.is_active, profile.employment_status


def _sign_in(app, staff_id):
    with app.app_context():
        from app.auth.services import authenticate

        got, reason = authenticate(_staff(staff_id).email, PASSWORD, "127.0.0.1", 50, 900)
        return reason


def _ledger(app, profile_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry

        db_session.expire_all()
        rows = db_session.execute(
            select(CommissionLedgerEntry).where(CommissionLedgerEntry.employee_profile_id == profile_id)
            .order_by(CommissionLedgerEntry.created_at, CommissionLedgerEntry.id)).scalars().all()
        return [(r.id, r.status, r.commission_amount, r.entry_kind) for r in rows]


def _trial(app, w, client, *, started, who="seller"):
    """A seller's new customer with a 7-day trial; `started` activates a device (the clock starts)."""
    result = onboard(app, w, who=who)
    if started:
        _activate(client, result.license_key, installation=f"inst-{uuid.uuid4().hex[:6]}")
    return result


def _sold_customer(app, w):
    """A customer the seller sold to and whose payment a manager confirmed: a real commission on the ledger."""
    sale, key = _record(app, w, new_customer_legal_name=f"Corner Shop {uuid.uuid4().hex[:5]}", payment_date=jordan_today())
    _confirm(app, sale["order_id"], w["manager"])
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_sales import SalesOrder

        return db_session.get(SalesOrder, sale["order_id"]).customer_id


def _convert(app, w, trial, *, actor="admin", **over):
    from app.commercial_sales.direct_sale import record_direct_sale

    args = dict(
        converts_subscription_id=trial.subscription_id, plan_id=w["plan"], amount=Decimal("100.00"), method="BANK_TRANSFER",
        reference="CLIQ-" + uuid.uuid4().hex[:8], payment_date=jordan_today(), idempotency_key=str(uuid.uuid4()),
        actor_staff_user_id=w[actor], actor_permission_codes=_codes(app, w[actor]), slip_photo=SLIP,
    )
    args.update(over)
    with app.app_context():
        return record_direct_sale(**args)


def _orders(app):
    from app.models.commercial_sales import SalesOrder

    return _count(app, SalesOrder)


# ---------------------------------------------------------------- the notice

def test_the_notice_runs_exactly_seven_days_and_the_seller_keeps_working(app, w):
    start = _now()
    termination_id, effective = _notice(app, w, now=start, reason="Moving abroad.")
    assert effective == start + timedelta(days=7)
    row = _termination(app, termination_id)
    assert (row.status, row.initiated_by_seller, row.applied_at, row.reason) == ("NOTICE", True, None, "Moving abroad.")
    assert _account(app, w["seller"]) == (True, "ACTIVE")
    assert _sign_in(app, w["seller"]) == "ok"     # a seller whose notice is running works as usual
    from app.models.audit import AuditLog

    assert _count(app, AuditLog, AuditLog.action_code == "SELLER_AGREEMENT_ENDING") == 1


def test_a_second_notice_is_refused_and_the_database_agrees(app, w):
    from app.models.seller_discipline import SellerTermination

    _notice(app, w)
    _refused(lambda: _notice(app, w), "ALREADY_ENDING")
    _refused(lambda: _notice(app, w, by="mgr"), "ALREADY_ENDING")
    assert _count(app, SellerTermination) == 1
    with app.app_context():
        from app.extensions import db_session

        with pytest.raises(IntegrityError):
            db_session.execute(text(
                "INSERT INTO owner_seller_terminations (id, seller_staff_user_id, initiated_by_staff_user_id, "
                "initiated_by_seller, notice_given_at, effective_at, status) VALUES (:i, :s, :s, true, now(), now(), 'NOTICE')"),
                {"i": str(uuid.uuid4()), "s": str(w["seller"])})
            db_session.flush()
        db_session.rollback()


def test_who_may_end_what(app, w):
    from app.models.seller_discipline import SellerTermination

    # a seller cannot end someone else's agreement, nor choose who gets their own customers
    from app.seller_discipline import terminations

    with app.app_context():
        _refused(lambda: terminations.give_notice(w["other"], actor=_staff(w["seller"]), by_seller=True), "ONLY_THE_SELLER_ENDS_THEIR_OWN")
        _refused(lambda: terminations.give_notice(w["seller"], actor=_staff(w["seller"]), by_seller=True,
                                                  reassign_to_staff_user_id=w["other"]), "ONLY_A_MANAGER_CHOOSES_WHO_GETS_CUSTOMERS")
        # a manager cannot end their own; a non-seller cannot be ended
        _refused(lambda: terminations.give_notice(w["mgr"], actor=_staff(w["mgr"]), by_seller=False), "NOT_A_SELLER")
        _refused(lambda: terminations.give_notice(w["admin"], actor=_staff(w["mgr"]), by_seller=False), "NOT_A_SELLER")
        # the destination must be another active seller
        _refused(lambda: terminations.give_notice(w["seller"], actor=_staff(w["mgr"]), by_seller=False,
                                                  reassign_to_staff_user_id=w["seller"]), "REASSIGN_TARGET_INVALID")
        _refused(lambda: terminations.give_notice(w["seller"], actor=_staff(w["mgr"]), by_seller=False,
                                                  reassign_to_staff_user_id=w["manager"]), "NOT_A_SELLER")
    assert _count(app, SellerTermination) == 0
    # the allow twin: a manager ends a seller's, naming another seller
    termination_id, _effective = _notice(app, w, by="mgr", reassign="other")
    row = _termination(app, termination_id)
    assert (row.initiated_by_seller, row.initiated_by_staff_user_id, row.reassign_to_staff_user_id) == (False, w["mgr"], w["other"])


def test_a_notice_mails_the_seller_once(app, w):
    app.config["SMTP_HOST"] = "smtp.test"
    from app.models.email_outbox import EmailOutbox

    _notice(app, w)
    assert _count(app, EmailOutbox, EmailOutbox.kind == "seller_agreement_ending") == 1
    _refused(lambda: _notice(app, w), "ALREADY_ENDING")
    assert _count(app, EmailOutbox, EmailOutbox.kind == "seller_agreement_ending") == 1


# ---------------------------------------------------------------- the effect

def test_nothing_changes_before_the_day_and_the_account_closes_on_it(app, w):
    client = app.test_client()
    force_login(client, app, w["seller"])
    assert client.get("/").status_code == 200
    start = _now()
    termination_id, effective = _notice(app, w, now=start)
    assert _take_effect(app, termination_id, effective - timedelta(seconds=1)) is False     # one second early
    assert _termination(app, termination_id).status == "NOTICE" and _account(app, w["seller"]) == (True, "ACTIVE")
    assert client.get("/").status_code == 200
    assert _take_effect(app, termination_id, effective) is True                             # the moment itself
    row = _termination(app, termination_id)
    assert (row.status, row.applied_at) == ("APPLIED", effective)
    assert _account(app, w["seller"]) == (False, "TERMINATED")
    assert _sign_in(app, w["seller"]) == "account_disabled"
    assert client.get("/").status_code == 302                                                # the old session is dead
    assert _take_effect(app, termination_id, effective + timedelta(days=1)) is False        # and it is not run twice
    from app.models.audit import AuditLog

    assert _count(app, AuditLog, AuditLog.action_code == "SELLER_AGREEMENT_ENDED") == 1


def test_the_scan_applies_only_the_notices_that_are_due(app, w):
    from app.seller_discipline import scan

    due_id, effective = _notice(app, w, now=_now() - timedelta(days=8))      # started 8 days ago: due
    later_id, _later = _notice(app, w, seller="other", by="mgr", now=_now())  # started now: not due
    with app.app_context():
        from app.seller_discipline import terminations

        # the query itself must only name the due notice (apply_termination also refuses an early one;
        # this pins the first line of defence so it cannot drift unnoticed)
        assert terminations.due_termination_ids(_now()) == [due_id]
        assert scan.run_due()["terminations_applied"] == 1
        assert scan.run_due()["terminations_applied"] == 0
    assert _termination(app, due_id).status == "APPLIED" and _termination(app, later_id).status == "NOTICE"
    assert _account(app, w["seller"]) == (False, "TERMINATED") and _account(app, w["other"]) == (True, "ACTIVE")


def test_signing_in_settles_a_due_notice_without_any_scan(app, w, monkeypatch):
    from app.seller_discipline import scan

    termination_id, effective = _notice(app, w, now=_now())
    assert _sign_in(app, w["seller"]) == "ok"                                 # not due: they work
    monkeypatch.setattr(scan, "utcnow", lambda: effective + timedelta(minutes=1))
    assert _sign_in(app, w["seller"]) == "account_disabled"                  # due: refused, no scan ran
    assert _termination(app, termination_id).status == "APPLIED"


# ------------------------------------------------------------- the customers

def test_trial_customers_stay_with_the_seller_and_every_other_customer_returns(app, w, client, signing_key):
    from app.models.leads import CustomerAssignment
    from app.models.seller_discipline import SellerTrialCredit

    sold = _sold_customer(app, w)                              # a customer + a confirmed commission
    started = _trial(app, w, client, started=True)             # clause 18's case
    unstarted = _trial(app, w, client, started=False)          # a trial that never started
    others = _trial(app, w, client, started=True, who="other")  # another seller's customer: untouched
    ledger_before = _ledger(app, w["seller_profile"])
    assert len(ledger_before) == 1 and ledger_before[0][2] == Decimal("10.00")
    start = _now()
    termination_id, effective = _notice(app, w, now=start)
    # during the notice nothing moves
    assert _owner(app, sold) == w["seller"] and _owner(app, started.customer_id) == w["seller"]
    assert _take_effect(app, termination_id, effective) is True
    assert _owner(app, sold) is None                          # returned to the company (open)
    assert _owner(app, unstarted.customer_id) is None         # the trial had not started: returned too
    assert _owner(app, started.customer_id) == w["seller"]    # the started trial keeps its seller
    assert _owner(app, others.customer_id) == w["other"]      # somebody else's customer: not touched
    with app.app_context():
        from app.extensions import db_session

        credits = db_session.execute(select(SellerTrialCredit)).scalars().all()
        assert [(c.customer_id, c.subscription_id, c.seller_staff_user_id, c.released_at) for c in credits] == [
            (started.customer_id, started.subscription_id, w["seller"], None)]
    assert _count(app, CustomerAssignment, CustomerAssignment.unassigned_at.is_(None),
                  CustomerAssignment.customer_id.in_([sold, unstarted.customer_id])) == 0   # no dangling open assignment
    # clause 5: what was already earned is exactly as it was
    assert _ledger(app, w["seller_profile"]) == ledger_before


def test_the_manager_can_hand_the_returning_customers_to_another_seller(app, w, client, signing_key):
    sold = _sold_customer(app, w)
    started = _trial(app, w, client, started=True)
    termination_id, effective = _notice(app, w, by="mgr", reassign="other")
    _take_effect(app, termination_id, effective)
    assert _owner(app, sold) == w["other"]                    # handed over
    assert _owner(app, started.customer_id) == w["seller"]    # the credit still holds the started trial
    from app.models.leads import CustomerAssignment

    assert _count(app, CustomerAssignment, CustomerAssignment.customer_id == sold,
                  CustomerAssignment.assigned_to_staff_user_id == w["other"], CustomerAssignment.unassigned_at.is_(None)) == 1


def test_the_destination_is_checked_again_when_the_day_comes(app, w):
    sold = _sold_customer(app, w)
    termination_id, effective = _notice(app, w, by="mgr", reassign="other")
    with app.app_context():
        from app.extensions import db_session

        other = _staff(w["other"])
        other.is_active = False                              # the chosen seller left meanwhile
        db_session.commit()
    _take_effect(app, termination_id, effective)
    assert _owner(app, sold) is None                          # not handed to a closed account: back to the company


# ------------------------------------------------------------------ clause 18

def test_the_first_payment_of_a_started_trial_still_credits_the_former_seller(app, w, client, signing_key):
    from app.commercial_sales.direct_sale import DirectSaleError
    from app.models.commercial_sales import SalesOrder
    from app.models.seller_discipline import SellerTrialCredit
    from app.seller_discipline import terminations

    started = _trial(app, w, client, started=True)
    unstarted = _trial(app, w, client, started=False)
    termination_id, effective = _notice(app, w, now=_now())
    _take_effect(app, termination_id, effective)
    assert _account(app, w["seller"]) == (False, "TERMINATED")
    before = _ledger(app, w["seller_profile"])
    orders = _orders(app)
    # deny: the former seller cannot be named for a trial that had not started ...
    with pytest.raises(DirectSaleError) as info:
        _convert(app, w, unstarted, sales_owner_employee_profile_id=w["seller_profile"])
    assert info.value.code == "SELLER_NOT_ACTIVE"
    # ... and nobody else can be named for the trial that had
    with pytest.raises(DirectSaleError) as info:
        _convert(app, w, started, sales_owner_employee_profile_id=w["other_profile"])
    assert info.value.code == "CONVERSION_CREDITS_FORMER_SELLER"
    assert _orders(app) == orders and _ledger(app, w["seller_profile"]) == before        # nothing was written
    # allow: the sale is recorded for the former seller, whoever types it in
    sale = _convert(app, w, started)
    with app.app_context():
        from app.extensions import db_session

        assert db_session.get(SalesOrder, sale["order_id"]).created_by_employee_profile_id == w["seller_profile"]
    assert _ledger(app, w["seller_profile"]) == before                                   # nothing is earned before the money is confirmed
    _confirm(app, sale["order_id"], w["manager"])
    after = _ledger(app, w["seller_profile"])
    assert len(after) == len(before) + 1 and after[-1][2] == Decimal("10.00")          # 10% of 100.00, credited to a closed account
    assert after[:-1] == before                                                          # and the earlier rows untouched
    # the trial is paid: the credit is released and the customer returns to the company
    with app.app_context():
        assert terminations.release_finished_trial_credits(now=_now()) == 1
        assert terminations.release_finished_trial_credits(now=_now()) == 0
        from app.extensions import db_session

        credit = db_session.execute(select(SellerTrialCredit)).scalars().one()
        assert (credit.release_reason, credit.released_at is not None) == ("CONVERTED", True)
    assert _owner(app, started.customer_id) is None


def test_a_credit_is_kept_while_the_trial_runs_and_released_when_it_ends_unpaid(app, w, client, signing_key):
    from app.models.seller_discipline import SellerTrialCredit
    from app.seller_discipline import terminations

    started = _trial(app, w, client, started=True)
    termination_id, effective = _notice(app, w, now=_now())
    _take_effect(app, termination_id, effective)
    with app.app_context():
        assert terminations.release_finished_trial_credits(now=_now()) == 0           # still running: kept
    assert _owner(app, started.customer_id) == w["seller"]
    with app.app_context():
        from app.extensions import db_session

        # the trial is cancelled unpaid (set directly: the lifecycle that does it is not what is under test)
        db_session.execute(text("UPDATE owner_pilot_records SET status = 'CANCELLED' WHERE subscription_id = :s"),
                           {"s": started.subscription_id})
        db_session.commit()
        assert terminations.release_finished_trial_credits(now=_now()) == 1
        credit = db_session.execute(select(SellerTrialCredit)).scalars().one()
        assert credit.release_reason == "TRIAL_ENDED"
    assert _owner(app, started.customer_id) is None
    # and the former seller can no longer be credited for it
    from app.commercial_sales.direct_sale import DirectSaleError

    with pytest.raises(DirectSaleError):
        _convert(app, w, started, sales_owner_employee_profile_id=w["seller_profile"])


def test_a_credit_exists_only_once_the_termination_has_taken_effect(app, w, client, signing_key):
    from app.models.seller_discipline import SellerTrialCredit
    from app.seller_discipline import terminations

    started = _trial(app, w, client, started=True)
    with app.app_context():
        assert terminations.credited_staff_for_subscription(started.subscription_id) is None   # nobody has left
    assert _count(app, SellerTrialCredit) == 0
    termination_id, effective = _notice(app, w, now=_now())
    assert _count(app, SellerTrialCredit) == 0                      # a running notice credits nothing yet
    _take_effect(app, termination_id, effective)
    with app.app_context():
        assert terminations.credited_staff_for_subscription(started.subscription_id) == w["seller"]


# ----------------------------------------------------------------- the screens

def test_the_profile_page_offers_the_end_button_then_shows_the_banner(app, w):
    client = app.test_client()
    force_login(client, app, w["seller"])
    page = client.get("/profile").get_data(as_text=True)
    assert "data-end-agreement" in page and "data-agreement-ending" not in page
    token = get_csrf(page)
    # unticked: nothing is written
    client.post("/seller-discipline/end-my-agreement", data={"csrf_token": token, "reason": "x"})
    from app.models.seller_discipline import SellerTermination

    assert _count(app, SellerTermination) == 0
    posted = client.post("/seller-discipline/end-my-agreement", data={"csrf_token": token, "confirm": "yes", "reason": "Moving."})
    assert posted.status_code == 302 and _count(app, SellerTermination) == 1
    page = client.get("/profile").get_data(as_text=True)
    assert "data-agreement-ending" in page and "data-end-agreement" not in page
    assert "data-agreement-ending" in client.get("/").get_data(as_text=True)
    # a non-seller sees no such card at all
    manager = app.test_client()
    force_login(manager, app, w["mgr"])
    assert "data-seller-agreement" not in manager.get("/profile").get_data(as_text=True)


def test_a_manager_ends_an_agreement_through_the_screen_and_a_seller_cannot(app, w):
    from app.models.seller_discipline import SellerTermination

    seller_client = app.test_client()
    force_login(seller_client, app, w["seller"])
    assert seller_client.get("/seller-discipline/end-agreement").status_code == 403
    token = get_csrf(seller_client.get("/profile").get_data(as_text=True))
    assert seller_client.post("/seller-discipline/end-agreement", data={
        "csrf_token": token, "seller": str(w["other"]), "confirm": "yes"}).status_code == 403
    assert _count(app, SellerTermination) == 0
    manager = app.test_client()
    force_login(manager, app, w["mgr"])
    page = manager.get("/seller-discipline/end-agreement")
    assert page.status_code == 200
    token = get_csrf(page.get_data(as_text=True))
    unticked = manager.post("/seller-discipline/end-agreement", data={"csrf_token": token, "seller": str(w["other"])})
    assert unticked.status_code == 400 and _count(app, SellerTermination) == 0
    done = manager.post("/seller-discipline/end-agreement", data={
        "csrf_token": token, "seller": str(w["other"]), "reassign_to": str(w["seller"]), "confirm": "yes", "reason": "Poor results."})
    assert done.status_code == 303
    with app.app_context():
        from app.extensions import db_session

        row = db_session.execute(select(SellerTermination)).scalars().one()
        assert (row.seller_staff_user_id, row.initiated_by_seller, row.reassign_to_staff_user_id) == (w["other"], False, w["seller"])
    assert f'data-termination-id="{row.id}"' in manager.get("/seller-discipline").get_data(as_text=True)


# ---------------------------------------------------------------- the words

def test_the_new_mail_kinds_and_the_permission_are_translated(app):
    from app.mail import templates

    for kind in ("seller_sanction_notice", "seller_sanction_decision", "seller_agreement_ending"):
        subject_en, body_en, _html = templates.render(kind, lang="en", lines=["Case: 1"])
        subject_ar, body_ar, _html = templates.render(kind, lang="ar", lines=["Case: 1"])
        assert subject_en != subject_ar and "Case: 1" in body_en and "--" not in body_ar.split("-" * 24)[0]
