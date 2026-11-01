"""Seller agreement clause 8 -- 15-day customer ownership.

Time is frozen by passing `now` to the rule and by writing the facts the clock reads (registration,
notes, assignments) at chosen instants; nothing sleeps. Both halves of each rule are proven: a customer
lapses AND one that should not lapse does not (a subscription, activity, a manager as owner, 14 days);
a seller can claim an open customer AND cannot claim an owned one (403, nothing written).
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from tests.seller_rules_world import client_for, count, make_customer, queued_mail, world
from tests.test_customer_onboarding import _jpeg, _png, _world as _onboarding_world, counts as onboarding_counts, kyc, onboard  # noqa: F401
from tests.test_issuance_sale import _profile

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def w(app, seeded):
    return world(app)


def _day(n, *, seconds=0):
    return T0 + timedelta(days=n, seconds=seconds)


def _stamp(app, model, row_id, **values):
    with app.app_context():
        from app.extensions import db_session

        db_session.execute(update(model).where(model.id == row_id).values(**values))
        db_session.commit()


def _customer(app, owner, *, registered=T0, name=None):
    from app.models.customers import Customer

    cid = make_customer(app, owner, name)
    _stamp(app, Customer, cid, created_at=registered)
    return cid


def _row(app, customer_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer

        row = db_session.get(Customer, customer_id)
        db_session.expunge(row)
        return row


def _note(app, customer_id, author, at):
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import CustomerNote

        note = CustomerNote(customer_id=customer_id, author_staff_user_id=author, body="Called the owner", visibility="ASSIGNED_RECORD_USERS")
        db_session.add(note)
        db_session.commit()
        db_session.execute(update(CustomerNote).where(CustomerNote.id == note.id).values(created_at=at))
        db_session.commit()


def _scan(app, now, **kw):
    with app.app_context():
        from app.sellers import ownership

        return ownership.scan_lapsed(now=now, **kw)


def _expires(app, customer_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer
        from app.sellers import ownership

        return ownership.expires_at(db_session.get(Customer, customer_id))


def _releases(app, customer_id=None):
    with app.app_context():
        from app.extensions import db_session
        from app.models.sellers import CustomerOwnershipRelease

        stmt = select(CustomerOwnershipRelease)
        if customer_id is not None:
            stmt = stmt.where(CustomerOwnershipRelease.customer_id == customer_id)
        rows = db_session.execute(stmt.order_by(CustomerOwnershipRelease.released_at)).scalars().all()
        db_session.expunge_all()
        return rows


def _audit_count(app, code, entity_id):
    from app.models.audit import AuditLog

    return count(app, AuditLog, AuditLog.action_code == code, AuditLog.entity_public_id == str(entity_id))


# ------------------------------------------------------------------ the 15 days

def test_a_customer_lapses_after_15_days_with_no_subscription_and_no_activity(app, w):
    app.config["SMTP_HOST"] = "smtp.test"
    old = _customer(app, w["seller"], registered=_day(0))
    fresh = _customer(app, w["seller"], registered=_day(2))
    assert _expires(app, old) == _day(15)
    assert _scan(app, _day(15))["released"] == 0  # the 15th day's instant is still inside the right
    assert _row(app, old).assigned_sales_staff_id == w["seller"] and _releases(app) == []
    result = _scan(app, _day(15, seconds=1))
    assert result["released"] == 1 and result["lapsed"] == 1
    assert _row(app, old).assigned_sales_staff_id is None
    assert _row(app, fresh).assigned_sales_staff_id == w["seller"]  # 13 days old: untouched
    (release,) = _releases(app)
    assert release.customer_id == old and release.previous_owner_staff_user_id == w["seller"]
    assert release.reason == "LAPSED_15_DAYS" and release.closed_at is None
    assert _audit_count(app, "CUSTOMER_OWNERSHIP_RELEASED", old) == 1
    from app.models.staff import StaffUser

    with app.app_context():
        from app.extensions import db_session

        seller_email = db_session.get(StaffUser, w["seller"]).email
    assert queued_mail(app, "seller_customer_released") == [seller_email]
    # idempotent: a second pass finds nothing, writes nothing, mails nobody
    again = _scan(app, _day(40))
    assert again["released"] == 1  # the second customer's own 15 days are up by day 40 ...
    assert len(_releases(app)) == 2
    assert _scan(app, _day(60))["released"] == 0 and len(_releases(app)) == 2
    assert len(queued_mail(app, "seller_customer_released")) == 2


def test_activity_the_owner_documents_extends_the_right_by_another_15_days(app, w):
    cid = _customer(app, w["seller"], registered=_day(0))
    _note(app, cid, w["seller"], _day(10))
    assert _expires(app, cid) == _day(25)
    assert _scan(app, _day(24))["released"] == 0
    assert _row(app, cid).assigned_sales_staff_id == w["seller"]
    assert _scan(app, _day(25, seconds=1))["released"] == 1


def test_a_note_by_somebody_else_is_not_the_sellers_activity(app, w):
    cid = _customer(app, w["seller"], registered=_day(0))
    _note(app, cid, w["sm"], _day(14))  # a manager's note
    assert _expires(app, cid) == _day(15)
    assert _scan(app, _day(16))["released"] == 1


def test_a_logged_call_or_visit_by_the_owner_also_counts_as_activity(app, w):
    profile = _profile(app, w["seller"], f"EMP-{uuid.uuid4().hex[:5]}", "Sami Seller")
    cid = _customer(app, w["seller"], registered=_day(0))
    with app.app_context():
        from app.extensions import db_session
        from app.models.leads import CustomerInteraction

        row = CustomerInteraction(customer_id=cid, employee_profile_id=profile, interaction_type="CALL", summary="rang",
                                  occurred_at=_day(1))
        db_session.add(row)
        db_session.commit()
        db_session.execute(update(CustomerInteraction).where(CustomerInteraction.id == row.id).values(created_at=_day(12)))
        db_session.commit()
    assert _expires(app, cid) == _day(27)


def test_any_subscription_a_trial_included_fixes_ownership_for_good(app, w, kyc):
    ow = _onboarding_world(app)
    onboard(app, ow)  # a registration with its trial subscription (PILOT)
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer
        from app.models.subscriptions import Subscription

        trial_customer = db_session.execute(select(Customer)).scalars().one()
        subscription = db_session.execute(select(Subscription)).scalars().one()
        assert subscription.status == "PILOT"
        trial_id = trial_customer.id
        product_id, plan_id = subscription.product_id, subscription.plan_id
    from app.models.customers import Customer

    _stamp(app, Customer, trial_id, created_at=_day(-200))
    assert _expires(app, trial_id) is None
    assert _scan(app, _day(0))["released"] == 0
    assert _row(app, trial_id).assigned_sales_staff_id == ow["seller"]
    # a DRAFT subscription is not a sale: it does not fix anything
    plain = _customer(app, w["seller"], registered=_day(0))
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        db_session.add(Subscription(customer_id=plain, product_id=product_id, plan_id=plan_id, status="DRAFT"))
        db_session.commit()
    assert _expires(app, plain) == _day(15)
    assert _scan(app, _day(16))["released"] == 1


def test_a_customer_whose_owner_is_not_a_seller_is_never_on_a_clock(app, w):
    mine = _customer(app, w["sm"], registered=_day(-300))
    assert _expires(app, mine) is None
    assert _scan(app, _day(0))["released"] == 0 and _row(app, mine).assigned_sales_staff_id == w["sm"]


def test_the_clock_never_starts_before_the_day_the_rule_was_switched_on(app, w):
    from app.models.audit import SystemSetting

    with app.app_context():
        from app.extensions import db_session

        db_session.add(SystemSetting(key="seller_customer_lapse_started_at", value={"started_at": _day(5).isoformat()}))
        db_session.commit()
    cid = _customer(app, w["seller"], registered=_day(0))
    assert _expires(app, cid) == _day(20)  # 15 days from the switch-on, not from the registration
    assert _scan(app, _day(19))["released"] == 0


def test_the_dry_run_changes_nothing(app, w):
    cid = _customer(app, w["seller"], registered=_day(0))
    result = _scan(app, _day(30), apply=False)
    assert result["lapsed"] == 1 and result["released"] == 0
    assert _row(app, cid).assigned_sales_staff_id == w["seller"] and _releases(app) == []


# ------------------------------------------------------------------ claiming

def test_an_open_customer_can_be_claimed_by_any_seller_and_the_15_days_start_again(app, w):
    cid = _customer(app, w["seller"], registered=_day(0))
    _scan(app, _day(16))
    client = client_for(app, w["seller2"])
    page = client.get("/sellers/customers/open").get_data(as_text=True)
    assert str(cid) in page
    from app.models.customers import Customer
    from app.models.leads import CustomerAssignment

    before = count(app, CustomerAssignment)
    claimed_around = datetime.now(timezone.utc)
    response = client.post(f"/sellers/customers/{cid}/claim")
    assert response.status_code == 303
    row = _row(app, cid)
    assert row.assigned_sales_staff_id == w["seller2"] and row.version >= 2
    (release,) = _releases(app, cid)
    assert release.closed_how == "CLAIMED" and release.claimed_by_staff_user_id == w["seller2"] and release.closed_at is not None
    assert count(app, CustomerAssignment) == before + 1 and _audit_count(app, "CUSTOMER_CLAIMED", cid) == 1
    with app.app_context():
        from app.extensions import db_session
        from app.sellers import ownership

        # independent of the clock's own helpers: fifteen days from the moment of the claim (the real clock, +- a minute)
        assert abs(ownership.expires_at(db_session.get(Customer, cid)) - (claimed_around + timedelta(days=15))) < timedelta(minutes=2)
    assert str(cid) not in client.get("/sellers/customers/open").get_data(as_text=True)  # no longer open


def test_a_customer_somebody_still_owns_cannot_be_claimed_and_nothing_changes(app, w):
    from app.models.audit import AuditLog
    from app.models.leads import CustomerAssignment

    cid = _customer(app, w["seller"], registered=datetime.now(timezone.utc))  # registered just now: right is live
    client = client_for(app, w["seller2"])
    audit_before, assignments_before = count(app, AuditLog), count(app, CustomerAssignment)
    denied = client.post(f"/sellers/customers/{cid}/claim")
    assert denied.status_code == 403
    assert _row(app, cid).assigned_sales_staff_id == w["seller"] and _releases(app) == []
    assert count(app, AuditLog) == audit_before and count(app, CustomerAssignment) == assignments_before


def test_a_customer_nobody_released_is_not_open_and_a_manager_cannot_claim(app, w):
    unowned = make_customer(app, None)  # no owner, but no rule released it: a manager's to hand out
    assert client_for(app, w["seller2"]).post(f"/sellers/customers/{unowned}/claim").status_code == 403
    assert _row(app, unowned).assigned_sales_staff_id is None
    cid = _customer(app, w["seller"], registered=_day(0))
    _scan(app, _day(16))
    assert client_for(app, w["sm"]).post(f"/sellers/customers/{cid}/claim").status_code == 403  # no sellers.self_service
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser
        from app.sellers import ownership
        from app.sellers.errors import SellerRulesError

        with pytest.raises(SellerRulesError) as exc:
            ownership.claim(cid, seller=db_session.get(StaffUser, w["sm"]))
        assert exc.value.code == "CLAIM_SELLERS_ONLY"
    assert _row(app, cid).assigned_sales_staff_id is None


def test_a_customer_whose_15_days_are_up_can_be_claimed_before_the_nightly_scan_runs(app, w):
    cid = _customer(app, w["seller"], registered=_day(0))  # long past 15 days against the real clock
    assert _releases(app) == []
    assert client_for(app, w["seller2"]).post(f"/sellers/customers/{cid}/claim").status_code == 303
    assert _row(app, cid).assigned_sales_staff_id == w["seller2"]
    (release,) = _releases(app, cid)
    assert release.reason == "LAPSED_15_DAYS" and release.previous_owner_staff_user_id == w["seller"] and release.closed_how == "CLAIMED"


def test_a_manager_can_reassign_any_customer_and_it_is_audited(app, w):
    from app.customers.services import assign_customer
    from app.extensions import db_session
    from app.models.customers import Customer
    from app.models.leads import CustomerAssignment

    owned = _customer(app, w["seller"], registered=datetime.now(timezone.utc))
    client = client_for(app, w["sm"])
    response = client.post(f"/customers/{owned}/assign", data={"assigned_to_staff_user_id": str(w["seller2"]), "reason": "Written agreement"})
    assert response.status_code in (302, 303), response.status_code
    assert _row(app, owned).assigned_sales_staff_id == w["seller2"]
    assert _audit_count(app, "CUSTOMER_ASSIGNED", owned) + _audit_count(app, "CUSTOMER_REASSIGNED", owned) >= 1
    # the deny twin: a seller cannot reassign
    assert client_for(app, w["seller"]).post(f"/customers/{owned}/assign", data={"assigned_to_staff_user_id": str(w["seller"])}).status_code == 403
    assert _row(app, owned).assigned_sales_staff_id == w["seller2"]
    # handing an OPEN customer to somebody closes its open record
    cid = _customer(app, w["seller"], registered=_day(0))
    _scan(app, _day(16))
    with app.app_context():
        assign_customer(db_session.get(Customer, cid), w["seller2"], w["sm"], reason="Manager decision")
    (release,) = _releases(app, cid)
    assert release.closed_how == "REASSIGNED" and release.closed_at is not None and _row(app, cid).assigned_sales_staff_id == w["seller2"]
    assert count(app, CustomerAssignment, CustomerAssignment.customer_id == cid, CustomerAssignment.unassigned_at.is_(None)) == 1


# ------------------------------------------------------------------ screens

def test_the_customer_page_says_yours_until_and_the_dashboard_warns_three_days_ahead(app, w):
    now = datetime.now(timezone.utc)
    soon = _customer(app, w["seller"], registered=now - timedelta(days=13), name="Soon Shop")  # 2 days left
    later = _customer(app, w["seller"], registered=now - timedelta(days=5), name="Later Shop")  # 10 days left
    client = client_for(app, w["seller"])
    page = client.get(f"/customers/{soon}").get_data(as_text=True)
    assert 'data-ownership-state="RUNNING"' in page and "Yours until" in page
    dash = client.get("/").get_data(as_text=True)
    assert 'data-expiring-card="1"' in dash and "Soon Shop" in dash and "Later Shop" not in dash
    # a manager's dashboard has no such card
    assert 'data-expiring-card' not in client_for(app, w["sm"]).get("/").get_data(as_text=True)
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer
        from app.sellers import ownership

        assert [c.id for c, _e in ownership.expiring_soon_for(w["seller"])] == [soon]
        assert ownership.describe(db_session.get(Customer, later))["state"] == "RUNNING"


def test_the_open_list_shows_only_open_customers_to_sellers(app, w):
    mine = _customer(app, w["seller"], registered=datetime.now(timezone.utc), name="Owned Shop")
    gone = _customer(app, w["seller"], registered=_day(0), name="Released Shop")
    _scan(app, _day(16))
    text = client_for(app, w["seller2"]).get("/sellers/customers/open").get_data(as_text=True)
    assert "Released Shop" in text and "Owned Shop" not in text
    assert client_for(app, w["finance"]).get("/sellers/customers/open").status_code == 403


# ------------------------------------------------------------------ duplicate protection

def test_a_second_seller_cannot_register_the_customer_another_seller_already_owns(app, w, kyc):
    """Reported behaviour: the new-customer form refuses a shop whose commercial-registration number, phone or
    email is already on file -- whoever owns it -- with TRIAL_ALREADY_USED and creates nothing. It does not name
    the other customer or its owner."""
    from app.customer_onboarding.service import OnboardingError

    ow = _onboarding_world(app)
    first = onboard(app, ow, cr_number="CR-DUP-1")
    assert first.customer_id is not None
    after_first = onboarding_counts(app)
    with pytest.raises(OnboardingError) as refused:
        onboard(app, ow, who="other_seller", cr_number="CR-DUP-1")
    assert refused.value.code == "TRIAL_ALREADY_USED"
    assert onboarding_counts(app) == after_first  # nothing written, no file stored
    onboard(app, ow, who="other_seller", cr_number="CR-DUP-2")  # the allow twin: a different shop registers
    assert onboarding_counts(app)["Customer"] == after_first["Customer"] + 1


def test_a_stale_open_record_never_lets_a_seller_take_a_customer_somebody_owns(app, w):
    """Belt and braces: even if an open-for-claiming record is somehow left behind for a customer that HAS an owner,
    the owner check alone refuses the claim (the record is not what decides)."""
    cid = _customer(app, w["seller"], registered=datetime.now(timezone.utc))
    with app.app_context():
        from app.extensions import db_session
        from app.models.sellers import CustomerOwnershipRelease

        db_session.add(CustomerOwnershipRelease(customer_id=cid, previous_owner_staff_user_id=w["seller2"],
                                                reason="LAPSED_15_DAYS", released_at=T0))
        db_session.commit()
    assert client_for(app, w["seller2"]).post(f"/sellers/customers/{cid}/claim").status_code == 403
    assert _row(app, cid).assigned_sales_staff_id == w["seller"]
    (release,) = _releases(app, cid)
    assert release.closed_at is None and release.claimed_by_staff_user_id is None
