"""New customer flow, slice S2: the trial CLOCK (when it starts, who may stretch
it, what can never happen to it) through the real activation endpoint.

Run through HTTP with real Ed25519 device signatures and the real signed lease,
because the hook sits inside the activation transaction and "the trial started"
has to be read from the lease the shop actually receives (term_end, pilot_status),
not from a variable. Each refusal asserts that NOTHING started (dates, marks,
installations), and each has its allow twin.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from tests.conftest import (
    build_activation_body, jordan_today, login_and_verify_mfa, make_device_keypair, make_license, make_staff,
)
from tests.test_customer_onboarding import _world, kyc, onboard  # noqa: F401 -- kyc is a fixture

NEW_ENOUGH = "1.0.0-rc.17"


def _activate(client, key_pair, full_key, *, app_version=NEW_ENOUGH, installation="inst-a", **extra):
    body = build_activation_body(key_pair, full_key=full_key, installation_id=installation, product_code="AURA_RETAIL",
                                 app_version=app_version, **extra)
    return client.post("/api/licensing/v1/activations", data=json.dumps(body), content_type="application/json")


def _trial(app, customer_id):
    """(subscription, pilot, licence) re-read from the database."""
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_ops import PilotRecord
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        db_session.expire_all()
        pilot = db_session.execute(select(PilotRecord).where(PilotRecord.customer_id == customer_id)).scalars().one()
        sub = db_session.get(Subscription, pilot.subscription_id)
        lic = db_session.execute(select(License).where(License.subscription_id == sub.id)).scalars().one()
        return (
            {"status": sub.status, "start": sub.start_date, "end": sub.end_date},
            {"status": pilot.status, "started": pilot.trial_started_at, "start": pilot.pilot_start, "end": pilot.pilot_end,
             "extensions": pilot.extension_count},
            {"from": lic.valid_from, "until": lic.valid_until, "status": lic.status},
        )


def _marks(app):
    with app.app_context():
        from app.extensions import db_session
        from app.models.customer_onboarding import TrialDeviceMark

        return db_session.execute(select(func.count()).select_from(TrialDeviceMark)).scalar_one()


def _installations(app):
    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation

        return db_session.execute(select(func.count()).select_from(Installation)).scalar_one()


def _reason(resp):
    return resp.get_json().get("reason_code")


# -------------------------------------------------------------- the clock starts

def test_the_first_device_starts_the_seven_day_clock_and_the_lease_says_so(app, client, seeded, kyc, signing_key):
    w = _world(app)
    result = onboard(app, w)
    before = _trial(app, result.customer_id)
    assert before[0]["start"] is None and before[1]["started"] is None  # not started by creating the customer
    resp = _activate(client, make_device_keypair(), result.license_key)
    assert resp.status_code == 200, resp.get_json()
    payload = resp.get_json()["signed_assertion"]["payload"]
    today = jordan_today()
    assert payload["subscription_status"] == "PILOT" and payload["pilot_status"] == "ACTIVE"
    assert payload["term_start"] == today.isoformat()
    assert payload["term_end"] == (today + timedelta(days=7)).isoformat()  # signed up on the 1st -> sells through the 8th
    sub, pilot, lic = _trial(app, result.customer_id)
    assert sub == {"status": "PILOT", "start": today, "end": today + timedelta(days=7)}
    assert pilot["started"] is not None and pilot["start"] == today and pilot["end"] == today + timedelta(days=7)
    assert lic["from"] == today and lic["until"] == today + timedelta(days=7)
    assert _marks(app) == 1
    with app.app_context():
        # the ID photos now carry the real retention date: trial end + 90 days
        from app.customer_onboarding import documents

        for row in documents.documents_of(result.customer_id):
            assert row.purge_after.date() == today + timedelta(days=7 + 90)


def test_a_second_device_of_the_same_shop_does_not_move_the_clock(app, client, seeded, kyc, signing_key):
    w = _world(app)
    result = onboard(app, w)
    assert _activate(client, make_device_keypair(), result.license_key, installation="d1").status_code == 200
    first = _trial(app, result.customer_id)
    # pretend the first device activated three days ago
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_ops import PilotRecord
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        three_days_ago = jordan_today() - timedelta(days=3)
        pilot = db_session.execute(select(PilotRecord)).scalars().one()
        sub = db_session.get(Subscription, pilot.subscription_id)
        sub.start_date, sub.end_date = three_days_ago, three_days_ago + timedelta(days=7)
        pilot.pilot_start, pilot.pilot_end = sub.start_date, sub.end_date
        db_session.commit()
    resp = _activate(client, make_device_keypair(), result.license_key, installation="d2")
    assert resp.status_code == 200
    assert resp.get_json()["signed_assertion"]["payload"]["term_end"] == (jordan_today() + timedelta(days=4)).isoformat()
    sub, pilot, _ = _trial(app, result.customer_id)
    assert sub["start"] == jordan_today() - timedelta(days=3)  # untouched by the second device
    assert _marks(app) == 2  # but the second device is marked too


# ------------------------------------------------------- old builds are refused

def test_a_build_older_than_the_trial_lock_cannot_start_a_trial(app, client, seeded, kyc, signing_key):
    w = _world(app)
    result = onboard(app, w)
    for old in ("1.0.0-rc.16", "0.9.0", "garbage", "1.0.0-rc.9"):
        resp = _activate(client, make_device_keypair(), result.license_key, app_version=old)
        assert resp.status_code in (400, 403) and _reason(resp) == "TRIAL_REQUIRES_UPDATE", (old, resp.get_json())
    sub, pilot, lic = _trial(app, result.customer_id)
    assert sub["start"] is None and pilot["started"] is None and lic["until"] is None
    assert _marks(app) == 0 and _installations(app) == 0  # the whole activation rolled back
    # allow: the first build with the lock, and anything newer
    for ok in ("1.0.0-rc.17", "1.0.0-rc.18", "1.0.0", "1.1.0-rc.1"):
        resp = _activate(client, make_device_keypair(), result.license_key, app_version=ok, installation=f"i-{ok}")
        assert resp.status_code in (200, 400), ok
        if resp.status_code == 400:  # the second+ device may hit the 2-device limit, never the version gate
            assert _reason(resp) != "TRIAL_REQUIRES_UPDATE"
    assert _trial(app, result.customer_id)[1]["started"] is not None


def test_a_paid_licence_is_not_subject_to_the_trial_version_gate(app, client, seeded, signing_key):
    admin = make_staff(app, "a@example.com", super_admin=True)
    _, key = make_license(app, admin, device_limit=1, product_code="AURA_RETAIL")
    resp = _activate(client, make_device_keypair(), key, app_version="0.0.1")
    assert resp.status_code == 200  # an ordinary ACTIVE subscription: no trial rules at all
    assert _marks(app) == 0


# -------------------------------------------------- one trial per physical device

def test_the_same_computer_cannot_start_a_second_customers_trial(app, client, seeded, kyc, signing_key):
    w = _world(app)
    a = onboard(app, w)
    b = onboard(app, w)
    computer = {"registry_device_id": str(uuid.uuid4())}
    assert _activate(client, make_device_keypair(), a.license_key, installation="a1", **computer).status_code == 200
    # deny: a reinstall (new key, new installation) of the SAME computer on the other customer's trial
    resp = _activate(client, make_device_keypair(), b.license_key, installation="b1", **computer)
    assert resp.status_code in (400, 403) and _reason(resp) == "TRIAL_DEVICE_ALREADY_USED", resp.get_json()
    sub, pilot, lic = _trial(app, b.customer_id)
    assert sub["start"] is None and pilot["started"] is None and lic["until"] is None
    assert _marks(app) == 1 and _installations(app) == 1
    # allow: a different computer starts B's trial
    other = {"registry_device_id": str(uuid.uuid4())}
    assert _activate(client, make_device_keypair(), b.license_key, installation="b2", **other).status_code == 200
    assert _trial(app, b.customer_id)[1]["started"] is not None and _marks(app) == 2


def test_the_database_allows_one_trial_mark_per_device_key(app, seeded, kyc):
    from sqlalchemy.exc import IntegrityError

    w = _world(app)
    a = onboard(app, w)
    b = onboard(app, w)
    with app.app_context():
        from app.extensions import db_session
        from app.models.customer_onboarding import TrialDeviceMark

        db_session.add(TrialDeviceMark(device_key_fingerprint="f" * 64, customer_id=a.customer_id))
        db_session.commit()
        db_session.add(TrialDeviceMark(device_key_fingerprint="f" * 64, customer_id=b.customer_id))
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()


# ----------------------------------------------------------- the 14-day fallback

def test_a_trial_nobody_opened_starts_by_itself_after_fourteen_days(app, seeded, kyc):
    from app.customer_onboarding.trial import start_overdue_trials

    w = _world(app)
    result = onboard(app, w)
    created = datetime.now(timezone.utc).date()
    with app.app_context():
        assert start_overdue_trials(today=created + timedelta(days=13)) == 0  # deny: day 13
    assert _trial(app, result.customer_id)[1]["started"] is None
    with app.app_context():
        assert start_overdue_trials(today=created + timedelta(days=14)) == 1  # allow: day 14
        assert start_overdue_trials(today=created + timedelta(days=30)) == 0  # idempotent
    sub, pilot, lic = _trial(app, result.customer_id)
    assert sub["start"] == created + timedelta(days=14) and sub["end"] == created + timedelta(days=21)
    assert pilot["started"] is not None and lic["until"] == created + timedelta(days=21)


# ------------------------------------------------------------------- extension

def _started(app, client):
    w = _world(app)
    result = onboard(app, w)
    assert _activate(client, make_device_keypair(), result.license_key).status_code == 200
    return w, result


def test_a_manager_may_extend_a_started_trial_once_by_up_to_seven_days(app, client, seeded, kyc, signing_key):
    from app.customer_onboarding.trial import TrialError, extend_trial

    w, result = _started(app, client)
    end = jordan_today() + timedelta(days=7)
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_ops import PilotExtension, PilotRecord

        pilot = db_session.execute(select(PilotRecord)).scalars().one()
        for days, reason, code in ((0, "a long enough reason", "TRIAL_EXTEND_DAYS_INVALID"),
                                   (8, "a long enough reason", "TRIAL_EXTEND_DAYS_INVALID"),
                                   (3, "short", "TRIAL_EXTEND_REASON_REQUIRED")):
            with pytest.raises(TrialError) as caught:
                extend_trial(pilot, days=days, reason=reason, actor_staff_user_id=w["manager"])
            assert caught.value.code == code
        assert _trial(app, result.customer_id)[1]["extensions"] == 0
        pilot = db_session_pilot()  # _trial()'s own app context removed the shared session: re-load, do not reuse a detached row
        extend_trial(pilot, days=5, reason="Customer's shop was closed for Eid", actor_staff_user_id=w["manager"])
    sub, pilot_row, lic = _trial(app, result.customer_id)
    assert sub["end"] == end + timedelta(days=5) and lic["until"] == end + timedelta(days=5)
    assert pilot_row["extensions"] == 1 and pilot_row["status"] == "EXTENDED", pilot_row
    assert pilot_row["end"] == end + timedelta(days=5), pilot_row
    with app.app_context():
        with pytest.raises(TrialError) as caught:  # ONCE
            extend_trial(db_session_pilot(), days=1, reason="one more please, really", actor_staff_user_id=w["manager"])
        assert caught.value.code == "TRIAL_ALREADY_EXTENDED"
        rows = db_session_count(PilotExtension)
        assert rows == 1
    # the next lease carries the new term_end
    resp = _activate(client, make_device_keypair(), result.license_key, installation="inst-b")
    assert resp.get_json()["signed_assertion"]["payload"]["term_end"] == (end + timedelta(days=5)).isoformat()


def db_session_pilot():
    from app.extensions import db_session
    from app.models.commercial_ops import PilotRecord

    return db_session.execute(select(PilotRecord)).scalars().one()


def db_session_count(model):
    from app.extensions import db_session

    return db_session.execute(select(func.count()).select_from(model)).scalar_one()


def test_a_trial_nobody_has_opened_cannot_be_extended(app, seeded, kyc):
    from app.customer_onboarding.trial import TrialError, extend_trial

    w = _world(app)
    onboard(app, w)
    with app.app_context():
        with pytest.raises(TrialError) as caught:
            extend_trial(db_session_pilot(), days=3, reason="a long enough reason", actor_staff_user_id=w["manager"])
        assert caught.value.code == "TRIAL_NOT_STARTED"


def test_the_extend_route_is_for_a_manager_with_recent_mfa_only(app, client, seeded, kyc, signing_key):
    w, result = _started(app, client)
    url = f"/customers/{result.customer_id}/trial/extend"
    form = {"days": "4", "reason": "Shop closed for the holiday"}
    # deny: the seller (no trials.extend)
    from tests.conftest import force_login, get_csrf

    seller = app.test_client()
    force_login(seller, app, w["seller"])
    token = get_csrf(seller.get("/customers/new-trial").get_data(as_text=True))
    assert seller.post(url, data={**form, "csrf_token": token}).status_code == 403
    assert _trial(app, result.customer_id)[1]["extensions"] == 0
    # allow: the manager, signed in with MFA
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        email = db_session.get(StaffUser, w["manager"]).email
    manager = app.test_client()
    login_and_verify_mfa(manager, email)
    token = get_csrf(manager.get("/customers/new-trial").get_data(as_text=True))
    resp = manager.post(url, data={**form, "csrf_token": token})
    assert resp.status_code == 302
    assert _trial(app, result.customer_id)[1]["extensions"] == 1
    # and the customer page shows the trial card with its state
    html = manager.get(f"/customers/{result.customer_id}").get_data(as_text=True)
    assert 'id="trial-card"' in html and 'data-trial-state="RUNNING"' in html


# ------------------------------------------------ a trial can never EXPIRE by itself

def test_a_trial_subscription_can_never_be_moved_to_expired(app, seeded, kyc):
    from app.subscriptions.services import InvalidTransitionError, transition_subscription

    w = _world(app)
    result = onboard(app, w)
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        sub = db_session.get(Subscription, result.subscription_id)
        with pytest.raises(InvalidTransitionError):  # wall 1: the table
            transition_subscription(sub, "EXPIRED", w["admin"])
        sub.status = "ACTIVE"  # wall 2 on its own: a hand-forced ACTIVE with an unpaid trial behind it
        db_session.commit()
        with pytest.raises(InvalidTransitionError) as caught:
            transition_subscription(sub, "EXPIRED", w["admin"])
        assert "trial" in str(caught.value).lower()
        # allow: once the trial is CONVERTED (paid) the subscription is an ordinary one and can expire
        from app.models.commercial_ops import PilotRecord

        db_session.execute(select(PilotRecord)).scalars().one().status = "CONVERTED"
        db_session.commit()
        transition_subscription(sub, "EXPIRED", w["admin"])
        assert sub.status == "EXPIRED"


def test_the_expiry_scan_never_touches_a_trial(app, seeded, kyc):
    from app.commercial_ops.expiry_scan import run_expiry_scan

    w = _world(app)
    result = onboard(app, w)
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        sub = db_session.get(Subscription, result.subscription_id)
        sub.start_date, sub.end_date = date(2020, 1, 1), date(2020, 1, 8)  # long past
        db_session.commit()
        run_expiry_scan(as_of=date(2030, 1, 1), dry_run=False)
        db_session.expire_all()
        assert db_session.get(Subscription, result.subscription_id).status == "PILOT"
