"""Seller agreement clause 16 -- sanctions, applied by degree, with 2 working days to reply first.

What is proven, each guard with its allow twin, by what was WRITTEN (rows, audit entries, queued
mails, the account's state), never by a status code alone:

  reply window   a sanction cannot be applied before the seller replied or the deadline passed (the
                 edge: one second before is refused, the deadline itself is allowed); the deadline is
                 the end of the 2nd Jordan WORKING day (Friday + Saturday skipped)
  immediate      the three named violations end the agreement on the spot, with no reply period; any
                 other category waits; an immediate category with a kind that is not "termination" is
                 refused and writes nothing
  suspension     disables the account (a sign-in is refused), the date ending re-opens it (by the scan
                 AND by the person signing in), a separate manual disable is not overridden
  withhold       the commission of the deal is neither approved nor paid out; the seller's statement
                 shows it as "withheld (case N)"; another deal, a dropped case and an undecided case are
                 untouched
  privacy        a seller sees only their own cases (404 for anyone else's); managers need the new
                 permission; nobody decides a case about themself
  reachability   a seller whose ID photo is missing can still read and answer the notice
  mail / audit   exactly one notice and one decision mail per case; one audit row per step
"""
from __future__ import annotations

import re
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from tests.conftest import force_login, get_csrf
from tests.test_commission_statements import (  # noqa: F401 -- w and clock are fixtures
    ISSUED, JUNE, _client, _sale, amman, clock, w,
)
from tests.test_customer_onboarding import kyc  # noqa: F401 -- fixture
from tests.test_seller_agreement_gate import _accept, _publish_seller_version
from tests.test_seller_id_photo import _send_id_gate

PASSWORD = "Sup3r-Str0ng-Pass!"
T0 = amman(2026, 7, 1, 10, 0, 0)  # a Wednesday: the reply is due at the end of Sunday = 00:00 Monday
DUE = amman(2026, 7, 6, 0, 0, 0)
REASON = "Took a deposit in cash and did not hand it in."


# ------------------------------------------------------------------ helpers

def _staff(staff_id):
    from app.extensions import db_session
    from app.models.staff import StaffUser

    return db_session.get(StaffUser, staff_id)


def _open(app, w, *, kind="WARNING", who="seller", actor="manager", category="OTHER", reason=REASON, now=T0, **kw):
    from app.seller_discipline import sanctions

    with app.app_context():
        case = sanctions.open_case(
            seller_staff_user_id=w[who], actor=_staff(w[actor]), kind=kind, category=category, reason_text=reason, now=now, **kw)
        return case.id


def _case(app, case_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.seller_discipline import SanctionCase

        db_session.expire_all()
        case = db_session.get(SanctionCase, case_id)
        db_session.expunge(case)
        return case


def _apply(app, w, case_id, *, actor="manager", now=T0, note=""):
    from app.seller_discipline import sanctions

    with app.app_context():
        return sanctions.apply_case(case_id, actor=_staff(w[actor]), note=note, now=now).status


def _drop(app, w, case_id, *, actor="manager", now=T0):
    from app.seller_discipline import sanctions

    with app.app_context():
        return sanctions.drop_case(case_id, actor=_staff(w[actor]), now=now).status


def _reply(app, w, case_id, *, who="seller", text_="I handed it in on Monday.", now=T0 + timedelta(hours=1)):
    from app.seller_discipline import sanctions

    with app.app_context():
        return sanctions.reply_to_case(case_id, seller=_staff(w[who]), text=text_, now=now).status


def _refused(call, code):
    from app.seller_discipline.errors import DisciplineError

    with pytest.raises(DisciplineError) as info:
        call()
    assert info.value.code == code, info.value.code
    return info.value


def _count(app, model, *where):
    with app.app_context():
        from app.extensions import db_session

        return db_session.execute(select(func.count()).select_from(model).where(*where)).scalar_one()


def _audit(app, code):
    from app.models.audit import AuditLog

    return _count(app, AuditLog, AuditLog.action_code == code)


def _mails(app, kind):
    from app.models.email_outbox import EmailOutbox

    return _count(app, EmailOutbox, EmailOutbox.kind == kind)


def _account(app, staff_id):
    """(is_active, disabled_at is None, employment_status) -- the three facts that say 'can sign in'."""
    with app.app_context():
        from app.extensions import db_session
        from app.models.employees import EmployeeProfile

        db_session.expire_all()
        staff = _staff(staff_id)
        profile = db_session.execute(select(EmployeeProfile).where(EmployeeProfile.staff_user_id == staff_id)).scalars().one()
        return staff.is_active, staff.disabled_at is None, profile.employment_status


def _sign_in(app, staff_id):
    with app.app_context():
        from app.auth.services import authenticate

        staff = _staff(staff_id)
        got, reason = authenticate(staff.email, PASSWORD, "127.0.0.1", 50, 900)
        return reason


def _mail_on(app):
    app.config["SMTP_HOST"] = "smtp.test"


# ------------------------------------------------------- the reply window

def test_a_sanction_cannot_be_applied_before_the_reply_or_the_deadline(app, w):
    case_id = _open(app, w)
    case = _case(app, case_id)
    assert case.response_due_at == DUE and case.status == "NOTICE_SENT" and case.immediate is False
    before = _audit(app, "SELLER_SANCTION_APPLIED")
    # a day later, no reply: refused, and NOTHING was written
    err = _refused(lambda: _apply(app, w, case_id, now=T0 + timedelta(days=1)), "WAIT_FOR_REPLY")
    assert "due_at" in err.params
    after = _case(app, case_id)
    assert (after.status, after.decided_at, after.decided_by_staff_user_id) == ("NOTICE_SENT", None, None)
    assert _audit(app, "SELLER_SANCTION_APPLIED") == before == 0


def test_the_deadline_edge_one_second_before_is_refused_and_the_deadline_itself_is_allowed(app, w):
    first = _open(app, w)
    _refused(lambda: _apply(app, w, first, now=DUE - timedelta(seconds=1)), "WAIT_FOR_REPLY")
    assert _case(app, first).status == "NOTICE_SENT"
    assert _apply(app, w, first, now=DUE) == "DECIDED_APPLIED"


def test_after_the_seller_replies_the_sanction_can_be_applied_at_once(app, w):
    case_id = _open(app, w)
    assert _reply(app, w, case_id) == "RESPONDED"
    case = _case(app, case_id)
    assert case.response_text and case.responded_at == T0 + timedelta(hours=1)
    assert _apply(app, w, case_id, now=T0 + timedelta(hours=2), note="Noted.") == "DECIDED_APPLIED"
    done = _case(app, case_id)
    assert done.decided_by_staff_user_id == w["manager"] and done.decision_note == "Noted."
    assert _audit(app, "SELLER_SANCTION_REPLIED") == 1 and _audit(app, "SELLER_SANCTION_APPLIED") == 1


@pytest.mark.parametrize("opened, due", [
    (amman(2026, 7, 1, 10), amman(2026, 7, 6, 0)),   # Wednesday -> Thu + Sun, due at the end of Sunday
    (amman(2026, 7, 2, 10), amman(2026, 7, 7, 0)),   # Thursday  -> Sun + Mon
    (amman(2026, 7, 3, 10), amman(2026, 7, 7, 0)),   # Friday    -> Sun + Mon
    (amman(2026, 7, 4, 10), amman(2026, 7, 7, 0)),   # Saturday  -> Sun + Mon
])
def test_the_deadline_is_the_end_of_the_second_jordan_working_day(app, w, opened, due):
    case_id = _open(app, w, now=opened)
    assert _case(app, case_id).response_due_at == due
    # not "48 hours": the plain-calendar reading would be earlier for every one of these
    assert due > opened + timedelta(hours=48)


def test_the_drop_is_allowed_at_any_time_and_a_decided_case_cannot_be_decided_again(app, w):
    case_id = _open(app, w)
    assert _drop(app, w, case_id, now=T0 + timedelta(minutes=5)) == "DECIDED_DROPPED"  # no waiting needed to drop
    _refused(lambda: _apply(app, w, case_id, now=DUE + timedelta(days=1)), "CASE_ALREADY_DECIDED")
    _refused(lambda: _drop(app, w, case_id), "CASE_ALREADY_DECIDED")
    assert _case(app, case_id).status == "DECIDED_DROPPED"
    other = _open(app, w)
    assert _apply(app, w, other, now=DUE) == "DECIDED_APPLIED"
    _refused(lambda: _drop(app, w, other), "CASE_ALREADY_DECIDED")


def test_the_seller_replies_once_and_only_to_their_own_case(app, w):
    case_id = _open(app, w)
    _refused(lambda: _reply(app, w, case_id, who="other"), "CASE_NOT_FOUND")  # someone else's: "does not exist"
    assert _case(app, case_id).response_text is None and _audit(app, "SELLER_SANCTION_REPLIED") == 0
    _refused(lambda: _reply(app, w, case_id, text_="   "), "REPLY_REQUIRED")
    assert _reply(app, w, case_id) == "RESPONDED"
    _refused(lambda: _reply(app, w, case_id, text_="a second answer"), "CASE_NOT_AWAITING_REPLY")
    assert _case(app, case_id).response_text == "I handed it in on Monday."


# ---------------------------------------------------------------- immediate

@pytest.mark.parametrize("category", ["MONEY_MANIPULATION", "CONFIDENTIAL_DISCLOSURE", "REVERSE_ENGINEERING"])
def test_the_three_named_violations_end_the_agreement_on_the_spot(app, w, category):
    case_id = _open(app, w, kind="TERMINATION", category=category)
    case = _case(app, case_id)
    assert (case.status, case.immediate, case.response_due_at, case.decided_by_staff_user_id) == (
        "DECIDED_APPLIED", True, None, w["manager"])
    # the agreement really ended: the account is closed and the termination row is applied
    assert _account(app, w["seller"]) == (False, True, "TERMINATED")
    from app.models.seller_discipline import SellerTermination

    assert _count(app, SellerTermination, SellerTermination.status == "APPLIED", SellerTermination.sanction_case_id == case_id) == 1
    assert _sign_in(app, w["seller"]) == "account_disabled"


def test_any_other_violation_waits_for_the_reply_even_when_the_kind_is_termination(app, w):
    case_id = _open(app, w, kind="TERMINATION", category="OTHER")
    case = _case(app, case_id)
    assert (case.status, case.immediate) == ("NOTICE_SENT", False)
    assert _account(app, w["seller"]) == (True, True, "ACTIVE")  # still working
    from app.models.seller_discipline import SellerTermination

    assert _count(app, SellerTermination) == 0
    _refused(lambda: _apply(app, w, case_id, now=T0 + timedelta(hours=1)), "WAIT_FOR_REPLY")
    assert _apply(app, w, case_id, now=DUE) == "DECIDED_APPLIED"   # after the deadline it ends, at once
    assert _account(app, w["seller"]) == (False, True, "TERMINATED")


def test_an_immediate_category_with_a_softer_kind_is_refused_and_writes_nothing(app, w):
    from app.models.seller_discipline import SanctionCase

    _refused(lambda: _open(app, w, kind="WARNING", category="MONEY_MANIPULATION"), "IMMEDIATE_CATEGORY_IS_TERMINATION")
    assert _count(app, SanctionCase) == 0
    assert _account(app, w["seller"]) == (True, True, "ACTIVE")
    assert _audit(app, "SELLER_SANCTION_OPENED") == 0


def test_an_immediate_ending_overtakes_a_running_notice(app, w):
    from app.models.seller_discipline import SellerTermination
    from app.seller_discipline import terminations

    with app.app_context():
        terminations.give_notice(w["seller"], actor=_staff(w["seller"]), by_seller=True, now=T0)
    case_id = _open(app, w, kind="TERMINATION", category="REVERSE_ENGINEERING", now=T0 + timedelta(days=1))
    assert _case(app, case_id).status == "DECIDED_APPLIED"
    assert _account(app, w["seller"]) == (False, True, "TERMINATED")
    assert _count(app, SellerTermination) == 1  # the SAME row took effect early, not a second one


# --------------------------------------------------------------- suspension

def _real_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _suspend(app, w, *, days=14, reply_first=True):
    """A suspension applied on the REAL clock (a sign-in reads the real clock). Returns (case_id, until)."""
    base = _real_now()
    until = base + timedelta(days=days)
    case_id = _open(app, w, kind="SUSPENSION", suspension_until=until, now=base)
    if reply_first:
        _reply(app, w, case_id, now=base + timedelta(minutes=1))
    _apply(app, w, case_id, now=base + timedelta(minutes=2))
    return case_id, until


def test_a_suspension_closes_the_account_and_the_date_re_opens_it(app, w):
    from app.seller_discipline import scan

    case_id, until = _suspend(app, w)
    assert _account(app, w["seller"]) == (False, True, "SUSPENDED")
    assert _sign_in(app, w["seller"]) == "account_disabled"
    # before the date: the scan changes nothing
    with app.app_context():
        assert scan.run_due(now=until - timedelta(seconds=1))["suspensions_ended"] == 0
    assert _account(app, w["seller"]) == (False, True, "SUSPENDED")
    # the date: it re-opens, exactly once
    with app.app_context():
        assert scan.run_due(now=until)["suspensions_ended"] == 1
        assert scan.run_due(now=until + timedelta(days=1))["suspensions_ended"] == 0
    assert _account(app, w["seller"]) == (True, True, "ACTIVE")
    assert _sign_in(app, w["seller"]) == "ok"
    assert _case(app, case_id).suspension_ended_at is not None
    assert _audit(app, "SELLER_SUSPENSION_ENDED") == 1


def test_signing_in_after_the_date_settles_the_suspension_without_any_scan(app, w, monkeypatch):
    from app.seller_discipline import scan

    _case_id, until = _suspend(app, w)
    assert _sign_in(app, w["seller"]) == "account_disabled"
    monkeypatch.setattr(scan, "utcnow", lambda: until + timedelta(days=1))
    assert _sign_in(app, w["seller"]) == "ok"   # the person signing in settles their own due suspension
    assert _account(app, w["seller"]) == (True, True, "ACTIVE")


def test_a_separate_manual_disable_is_not_overridden_when_the_suspension_ends(app, w):
    from app.seller_discipline import scan

    _case_id, until = _suspend(app, w)
    with app.app_context():
        from app.extensions import db_session
        from app.models.base import utcnow

        staff = _staff(w["seller"])
        staff.disabled_at, staff.disabled_reason = utcnow(), "left the company"
        db_session.commit()
        scan.run_due(now=until + timedelta(days=1))
    assert _account(app, w["seller"])[1:] == (False, "ACTIVE")  # profile re-opened, the account stays blocked
    assert _sign_in(app, w["seller"]) == "account_disabled"


def test_a_suspension_needs_a_future_date_and_cannot_be_applied_after_it_passed(app, w):
    _refused(lambda: _open(app, w, kind="SUSPENSION", suspension_until=None), "SUSPENSION_DATE_REQUIRED")
    _refused(lambda: _open(app, w, kind="SUSPENSION", suspension_until=T0), "SUSPENSION_DATE_REQUIRED")
    case_id = _open(app, w, kind="SUSPENSION", suspension_until=amman(2026, 7, 7, 0))
    _refused(lambda: _apply(app, w, case_id, now=amman(2026, 7, 8)), "SUSPENSION_DATE_PASSED")
    assert _case(app, case_id).status == "NOTICE_SENT"
    assert _account(app, w["seller"]) == (True, True, "ACTIVE")


# ----------------------------------------------------------------- withhold

def _deal(app, w, *, position, amount="25.00", status="EARNED", who="seller"):
    """A counted June sale (and its ledger row) whose order was created by `who`'s profile."""
    sale_id, entry_id = _sale(app, w, who, position=position, amount=amount, status=status)
    with app.app_context():
        from app.extensions import db_session
        from app.models.commissions import CommissionLadderSale

        order_id = db_session.get(CommissionLadderSale, sale_id).sales_order_id
        db_session.execute(text("UPDATE owner_sales_orders SET created_by_employee_profile_id = :p WHERE id = :o"),
                           {"p": w[f"{who}_profile"], "o": order_id})
        db_session.commit()
    return order_id, entry_id


def _entry_status(app, entry_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry

        db_session.expire_all()
        return db_session.get(CommissionLedgerEntry, entry_id).status


def _approve(app, w, entry_id):
    from app.commissions.ledger import approve_commission_entry
    from app.models.commissions import CommissionLedgerEntry

    with app.app_context():
        from app.extensions import db_session

        approve_commission_entry(db_session.get(CommissionLedgerEntry, entry_id), actor_staff_user_id=w["manager"])


def _pay(app, w, entry_id):
    from app.commissions.ledger import approve_payout_batch, create_payout_batch, record_payout
    from app.models.commissions import CommissionLedgerEntry

    with app.app_context():
        from app.extensions import db_session

        batch = create_payout_batch({"batch_reference": f"B-{uuid.uuid4().hex[:6]}", "period_start": JUNE,
                                     "period_end": JUNE.replace(day=30)}, w["manager"])
        approve_payout_batch(batch, actor_staff_user_id=w["admin"])
        return record_payout(db_session.get(CommissionLedgerEntry, entry_id), batch, actor_staff_user_id=w["admin"]).amount


def _withhold(app, w, order_id, *, drop=False, reply=True):
    case_id = _open(app, w, kind="COMMISSION_WITHHOLD", withheld_sales_order_id=order_id)
    if reply:
        _reply(app, w, case_id)
    (_drop if drop else _apply)(app, w, case_id, now=T0 + timedelta(hours=2))
    return case_id


def _payout_lines(app):
    from app.models.commissions import CommissionPayoutLine

    return _count(app, CommissionPayoutLine)


def test_a_withhold_case_must_name_a_deal_of_that_seller_and_only_one_case_per_deal(app, w):
    from app.models.seller_discipline import SanctionCase

    order_id, _entry = _deal(app, w, position=1)
    other_order, _e2 = _deal(app, w, position=1, who="other")
    _refused(lambda: _open(app, w, kind="COMMISSION_WITHHOLD", withheld_sales_order_id=None), "WITHHOLD_ORDER_REQUIRED")
    _refused(lambda: _open(app, w, kind="COMMISSION_WITHHOLD", withheld_sales_order_id=other_order), "WITHHOLD_ORDER_NOT_THE_SELLERS")
    assert _count(app, SanctionCase) == 0
    _open(app, w, kind="COMMISSION_WITHHOLD", withheld_sales_order_id=order_id)
    _refused(lambda: _open(app, w, kind="COMMISSION_WITHHOLD", withheld_sales_order_id=order_id), "ALREADY_WITHHELD")
    assert _count(app, SanctionCase) == 1


def test_a_withheld_deal_cannot_be_approved_but_another_deal_can(app, w):
    from app.commissions.errors import CommissionError

    held_order, held_entry = _deal(app, w, position=1)
    free_order, free_entry = _deal(app, w, position=2)
    # an OPEN (undecided) case holds nothing yet: the allow-half before the decision
    case_id = _open(app, w, kind="COMMISSION_WITHHOLD", withheld_sales_order_id=held_order)
    _approve(app, w, free_entry)
    assert _entry_status(app, free_entry) == "APPROVED"
    _reply(app, w, case_id)
    _apply(app, w, case_id, now=T0 + timedelta(hours=2))
    with pytest.raises(CommissionError) as info:
        _approve(app, w, held_entry)
    assert info.value.code == "COMMISSION_WITHHELD" and info.value.params["case_number"] == _case(app, case_id).case_number
    assert _entry_status(app, held_entry) == "EARNED"          # the ledger row is untouched, just held
    assert _entry_status(app, free_entry) == "APPROVED"        # the other deal is untouched


def test_a_deal_approved_before_the_case_is_still_not_paid_out(app, w):
    from app.commissions.errors import CommissionError

    held_order, held_entry = _deal(app, w, position=1)
    free_order, free_entry = _deal(app, w, position=2)
    _approve(app, w, held_entry)
    _approve(app, w, free_entry)
    _withhold(app, w, held_order)
    with pytest.raises(CommissionError) as info:
        _pay(app, w, held_entry)
    assert info.value.code == "COMMISSION_WITHHELD"
    assert _payout_lines(app) == 0 and _entry_status(app, held_entry) == "APPROVED"   # nothing paid, nothing marked paid
    assert _pay(app, w, free_entry) == Decimal("25.00")                                  # the allow twin
    assert _payout_lines(app) == 1 and _entry_status(app, free_entry) == "PAID"


def test_a_dropped_withhold_case_holds_nothing(app, w):
    from app.commissions import statements

    held_order, held_entry = _deal(app, w, position=1)
    _withhold(app, w, held_order, drop=True)
    _approve(app, w, held_entry)
    assert _entry_status(app, held_entry) == "APPROVED"
    with app.app_context():
        statement = statements.build_statement(w["seller_profile"], JUNE, now=ISSUED + timedelta(days=1))
    assert [(l["withheld_case"], l["withheld"], l["payable"]) for l in statement["lines"]] == [(None, "0.00", "25.00")]


def test_an_undecided_withhold_case_holds_nothing_yet(app, w):
    """The clause gives the seller 2 working days before ANY sanction: until the decision the deal is paid as usual."""
    order_id, entry = _deal(app, w, position=1)
    _open(app, w, kind="COMMISSION_WITHHOLD", withheld_sales_order_id=order_id)   # opened, not decided
    _approve(app, w, entry)
    assert _entry_status(app, entry) == "APPROVED"


def test_the_statement_shows_the_withheld_deal_with_its_case_number(app, w, clock):
    from app.commissions import statements

    held_order, held_entry = _deal(app, w, position=1)
    _deal(app, w, position=2)
    case_id = _withhold(app, w, held_order)
    number = _case(app, case_id).case_number
    with app.app_context():
        statement = statements.build_statement(w["seller_profile"], JUNE, now=ISSUED + timedelta(days=1))
    lines = {line["withheld_case"]: line for line in statement["lines"]}
    assert set(lines) == {number, None}
    held, free = lines[number], lines[None]
    assert (held["withheld"], held["payable"], held["held"]) == ("25.00", "0.00", "0.00")
    assert (free["withheld"], free["payable"]) == ("0.00", "25.00")
    assert statement["totals"]["withheld"] == "25.00" and statement["totals"]["payable"] == "25.00"
    assert statement["totals"]["net"] == "50.00"   # held back is still on the ledger: net is the whole ledger
    # and the seller's own page says so in words
    client = _client(app, w["seller"])
    page = client.get("/commissions/statements/2026-06").get_data(as_text=True)
    assert f'data-withheld-case="{number}"' in page and 'data-total-withheld="25.00"' in page


# ------------------------------------------------------------------ privacy

def test_a_seller_sees_only_their_own_cases(app, w):
    mine = _open(app, w, who="seller")
    theirs = _open(app, w, who="other")
    seller, other = _client(app, w["seller"]), _client(app, w["other"])
    listing = seller.get("/seller-discipline/mine").get_data(as_text=True)
    assert str(mine) in listing and str(theirs) not in listing
    assert seller.get(f"/seller-discipline/mine/{mine}").status_code == 200
    assert seller.get(f"/seller-discipline/mine/{theirs}").status_code == 404            # exactly like a missing one
    assert seller.get(f"/seller-discipline/mine/{uuid.uuid4()}").status_code == 404
    page = other.get("/seller-discipline/mine").get_data(as_text=True)
    assert str(theirs) in page and str(mine) not in page
    # a reply posted at someone else's case writes nothing
    token = get_csrf(seller.get(f"/seller-discipline/mine/{mine}").get_data(as_text=True))
    assert seller.post(f"/seller-discipline/mine/{theirs}/reply", data={"csrf_token": token, "reply_text": "x"}).status_code == 404
    assert _case(app, theirs).response_text is None


def test_the_discipline_screens_need_the_new_permission(app, w):
    case_id = _open(app, w)
    urls = ["/seller-discipline", "/seller-discipline/new", f"/seller-discipline/{case_id}", "/seller-discipline/end-agreement"]
    for who in ("seller", "finance"):                       # deny: no sellers.discipline
        client = _client(app, w[who])
        for url in urls:
            assert client.get(url).status_code == 403, (who, url)
    for who in ("manager", "admin"):                        # allow: Sales Manager and Super Admin hold it
        client = _client(app, w[who])
        for url in urls:
            assert client.get(url).status_code == 200, (who, url)
    # a refused POST changes nothing
    seller = _client(app, w["seller"])
    token = get_csrf(seller.get("/seller-discipline/mine").get_data(as_text=True))
    assert seller.post(f"/seller-discipline/{case_id}/apply", data={"csrf_token": token}).status_code == 403
    assert seller.post(f"/seller-discipline/{case_id}/drop", data={"csrf_token": token}).status_code == 403
    assert _case(app, case_id).status == "NOTICE_SENT"


def test_the_seeded_roles_carry_the_permission_exactly_where_the_decision_says(app, w):
    with app.app_context():
        from app.extensions import db_session
        from app.security.rbac import get_staff_permission_codes

        codes = {key: get_staff_permission_codes(_staff(w[key])) for key in ("seller", "manager", "finance", "admin", "both")}
    assert "sellers.discipline" in codes["manager"] and "sellers.discipline" in codes["admin"] and "sellers.discipline" in codes["both"]
    assert "sellers.discipline" not in codes["seller"] and "sellers.discipline" not in codes["finance"]


def test_nobody_decides_or_opens_a_case_about_themself(app, w):
    from app.models.seller_discipline import SanctionCase

    _refused(lambda: _open(app, w, who="both", actor="both"), "SELF_ACTION_FORBIDDEN")
    assert _count(app, SanctionCase) == 0
    case_id = _open(app, w, who="both", actor="manager")     # a manager who also sells
    _refused(lambda: _apply(app, w, case_id, actor="both", now=DUE), "SELF_ACTION_FORBIDDEN")
    _refused(lambda: _drop(app, w, case_id, actor="both"), "SELF_ACTION_FORBIDDEN")
    assert _case(app, case_id).status == "NOTICE_SENT"
    # the screen says so too (and offers no apply button) while the case is open
    own_page = _client(app, w["both"]).get(f"/seller-discipline/{case_id}").get_data(as_text=True)
    assert "data-self-case" in own_page and "data-apply-button" not in own_page
    other_page = _client(app, w["manager"]).get(f"/seller-discipline/{case_id}").get_data(as_text=True)
    assert "data-self-case" not in other_page and "data-apply-button" in other_page
    assert _apply(app, w, case_id, actor="manager2", now=DUE) == "DECIDED_APPLIED"   # another manager can


def test_only_sellers_can_be_sanctioned(app, w):
    from app.models.seller_discipline import SanctionCase

    for who in ("manager2", "finance", "admin"):
        _refused(lambda who=who: _open(app, w, who=who), "NOT_A_SELLER")
    assert _count(app, SanctionCase) == 0


# ------------------------------------------------------------- reachability

def test_a_seller_without_an_id_photo_can_still_read_and_answer_a_notice(app, w, kyc):
    version_id = _publish_seller_version(app, w["admin"])
    client = _client(app, w["seller"])
    assert _accept(client, version_id).status_code == 302
    case_id = _open(app, w)
    # the ID step is real for this seller (deny): an ordinary page sends them to the upload page ...
    assert _send_id_gate(client.get("/customers/new-trial"))
    # ... but the notice can be read and answered (allow)
    assert client.get("/seller-discipline/mine").status_code == 200
    assert client.get(f"/seller-discipline/mine/{case_id}").status_code == 200
    token = get_csrf(client.get(f"/seller-discipline/mine/{case_id}").get_data(as_text=True))
    posted = client.post(f"/seller-discipline/mine/{case_id}/reply", data={"csrf_token": token, "reply_text": "My answer."})
    assert posted.status_code == 302
    assert _case(app, case_id).response_text == "My answer."
    # and it is still behind sign-in: no session, no notice
    assert app.test_client().get(f"/seller-discipline/mine/{case_id}").status_code in (302, 401)


# ------------------------------------------------------------- mail + audit

def test_one_notice_mail_and_one_decision_mail_per_case(app, w):
    _mail_on(app)
    case_id = _open(app, w)
    assert (_mails(app, "seller_sanction_notice"), _mails(app, "seller_sanction_decision")) == (1, 0)
    _reply(app, w, case_id)
    _apply(app, w, case_id, now=T0 + timedelta(hours=2))
    assert (_mails(app, "seller_sanction_notice"), _mails(app, "seller_sanction_decision")) == (1, 1)
    number = _case(app, case_id).case_number
    with app.app_context():
        from app.extensions import db_session
        from app.models.email_outbox import EmailOutbox

        row = db_session.execute(select(EmailOutbox).where(EmailOutbox.kind == "seller_sanction_notice")).scalars().one()
        assert row.to_address == _staff(w["seller"]).email and f"Case: {number}" in row.body_text
    _refused(lambda: _apply(app, w, case_id, now=DUE), "CASE_ALREADY_DECIDED")
    assert _mails(app, "seller_sanction_decision") == 1     # a refused second decision sends nothing


def test_with_email_off_nothing_is_queued_but_the_case_still_exists(app, w):
    case_id = _open(app, w)
    assert _mails(app, "seller_sanction_notice") == 0 and _case(app, case_id).status == "NOTICE_SENT"


def test_every_step_is_audited_once(app, w):
    case_id = _open(app, w)
    _reply(app, w, case_id)
    _apply(app, w, case_id, now=T0 + timedelta(hours=2))
    other = _open(app, w)
    _drop(app, w, other)
    assert (_audit(app, "SELLER_SANCTION_OPENED"), _audit(app, "SELLER_SANCTION_REPLIED"),
            _audit(app, "SELLER_SANCTION_APPLIED"), _audit(app, "SELLER_SANCTION_DROPPED")) == (2, 1, 1, 1)


def test_the_seller_dashboard_says_a_reply_is_waited_for(app, w):
    client = _client(app, w["seller"])
    assert "data-cases-awaiting" not in client.get("/").get_data(as_text=True)
    case_id = _open(app, w)
    assert 'data-cases-awaiting="1"' in client.get("/").get_data(as_text=True)
    _reply(app, w, case_id)
    assert "data-cases-awaiting" not in client.get("/").get_data(as_text=True)
    manager = _client(app, w["manager"]).get("/").get_data(as_text=True)
    assert "data-discipline-card" in manager and "data-discipline-card" not in client.get("/").get_data(as_text=True)


# --------------------------------------------------------------- the screens

def test_the_manager_opens_decides_and_drops_through_the_screens(app, w):
    manager = _client(app, w["manager"])
    assert 'name="reason_text"' not in manager.get("/seller-discipline/new").get_data(as_text=True)   # step one only picks the seller
    step_two = manager.get(f"/seller-discipline/new?seller={w['seller']}")
    assert step_two.status_code == 200 and 'name="reason_text"' in step_two.get_data(as_text=True)
    token = get_csrf(step_two.get_data(as_text=True))
    # a refused form writes nothing and says why
    refused = manager.post("/seller-discipline/new", data={
        "csrf_token": token, "seller": str(w["seller"]), "kind": "WARNING", "category": "MONEY_MANIPULATION", "reason_text": REASON})
    assert refused.status_code == 400 and "data-form-error" in refused.get_data(as_text=True)
    from app.models.seller_discipline import SanctionCase

    assert _count(app, SanctionCase) == 0
    ok = manager.post("/seller-discipline/new", data={
        "csrf_token": token, "seller": str(w["seller"]), "kind": "WARNING", "category": "OTHER", "reason_text": REASON})
    assert ok.status_code == 303 and _count(app, SanctionCase) == 1
    with app.app_context():
        from app.extensions import db_session

        case_id = db_session.execute(select(SanctionCase.id)).scalars().one()
    detail = manager.get(f"/seller-discipline/{case_id}").get_data(as_text=True)
    assert "data-wait-for-reply" in detail and 'data-apply-button="1" disabled' in detail   # cannot apply yet
    # pressing apply anyway is refused by the server, not only hidden by the button
    manager.post(f"/seller-discipline/{case_id}/apply", data={"csrf_token": token})
    assert _case(app, case_id).status == "NOTICE_SENT"
    manager.post(f"/seller-discipline/{case_id}/drop", data={"csrf_token": token})
    assert _case(app, case_id).status == "DECIDED_DROPPED"
    assert str(case_id) in manager.get("/seller-discipline").get_data(as_text=True)


# ----------------------------------------------------------- words (ar + en)

def test_every_discipline_error_code_has_an_arabic_sentence(app):
    from app.i18n_labels import localize_discipline_error

    sources = "".join(Path(__file__).resolve().parent.parent.joinpath("app", "seller_discipline", name).read_text(encoding="utf-8")
                      for name in ("sanctions.py", "terminations.py", "people.py"))
    codes = set(re.findall(r'DisciplineError\(\s*"([A-Z_]+)"', sources))
    assert len(codes) >= 20
    for code in sorted(codes):
        params = {"due_at": "2026-07-06 00:00 UTC"}
        with app.test_request_context("/", headers={"Accept-Language": "ar"}):
            arabic = localize_discipline_error(code, **params)
        with app.test_request_context("/", headers={"Accept-Language": "en"}):
            english = localize_discipline_error(code, **params)
        assert english != code, f"{code} has no English sentence"
        assert re.search(r"[؀-ۿ]", arabic) and arabic != english and "--" not in arabic, code
