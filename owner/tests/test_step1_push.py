"""Step 1 (2026-10-25) -- managers' phones are told when a sale is waiting.

The owner's rules this file holds the code to:
  * a seller records a sale + payment -> every ACTIVE staff member who could
    confirm it gets a push on each device that turned notifications on,
    EXCEPT the seller and whoever recorded it; leaving / being deactivated
    stops it at once;
  * the outbox row is written in the SAME transaction as the payment: a
    crash after the commit still sends (once), a rolled-back payment sends
    nothing;
  * no money and no customer on the lock screen; English/Arabic per person;
    tapping opens the waiting list, "already confirmed" when someone was first;
  * a burst of sales does not spam: one message per device per minute,
    coalesced, one notification tag;
  * desktop managers get a count badge; push is OFF without VAPID keys and
    everything else still works.

The push service is replaced by a recorder (FakePush) -- every assertion is
on what WOULD have been posted, per device, and on the rows. The transport
itself is proven in tests/test_step1_push_transport.py and in a real browser.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import timedelta

import pytest

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_staff
from tests.test_issuance_sale import PEPPER, _codes, _confirm, _email, _profile, _record, _world


# ----------------------------------------------------------------- helpers --

class FakePush:
    """Stands in for app.push.webpush.send. `script[endpoint]` is a list of
    answers to give in order: an int status, (status, retry_after), or
    "neterr" for no answer at all. Default: 201."""

    def __init__(self):
        self.calls = []
        self.script = {}
        self.lock = threading.Lock()

    def __call__(self, *, endpoint, p256dh, auth, payload, keys, ttl_seconds, topic=None, urgency="high"):
        from app.push.webpush import PushResponse, PushTransportError

        with self.lock:
            self.calls.append({"endpoint": endpoint, "payload": json.loads(payload.decode("utf-8")),
                               "raw": payload, "topic": topic, "ttl": ttl_seconds})
            answers = self.script.get(endpoint)
            answer = answers.pop(0) if answers else 201
        if answer == "neterr":
            raise PushTransportError("URLError")
        if isinstance(answer, tuple):
            return PushResponse(status=answer[0], retry_after_seconds=answer[1])
        return PushResponse(status=answer)

    def endpoints(self):
        return [c["endpoint"] for c in self.calls]


@pytest.fixture()
def push_on(app):
    from app.push.vapid import generate_private_key_b64

    app.config["PUSH_VAPID_PRIVATE_KEY"], _public = generate_private_key_b64()
    app.config["PUSH_VAPID_SUBJECT"] = "mailto:info@actionaura.me"
    return app


@pytest.fixture()
def fake_push(monkeypatch):
    fake = FakePush()
    monkeypatch.setattr("app.push.webpush.send", fake)
    return fake


def _browser_keys():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    from app.push.vapid import b64url_encode

    key = ec.generate_private_key(ec.SECP256R1())
    p256dh = b64url_encode(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    return p256dh, b64url_encode(os.urandom(16))


def _device(app, staff_id, *, locale=None, endpoint=None):
    """One subscribed device for staff_id; returns its endpoint."""
    endpoint = endpoint or f"https://fcm.googleapis.com/fcm/send/{uuid.uuid4().hex}"
    p256dh, auth = _browser_keys()
    with app.app_context():
        from app.push.subscriptions import save_subscription

        save_subscription(staff_user_id=staff_id, endpoint=endpoint, p256dh=p256dh, auth=auth,
                          locale=locale, user_agent="pytest")
    return endpoint


def _run(app):
    with app.app_context():
        from app.push.worker import run_once

        return run_once()


def _rows(app, model_name):
    with app.app_context():
        from sqlalchemy import select

        import app.models.push as push_models
        from app.extensions import db_session

        model = getattr(push_models, model_name)
        rows = db_session.execute(select(model)).scalars().all()
        return [{c.name: getattr(r, c.name) for c in model.__table__.columns} for r in rows]


def _set(app, model_name, values, **where):
    with app.app_context():
        from sqlalchemy import update

        import app.models.push as push_models
        from app.extensions import db_session

        model = getattr(push_models, model_name)
        stmt = update(model).values(**values)
        for key, value in where.items():
            stmt = stmt.where(getattr(model, key) == value)
        db_session.execute(stmt)
        db_session.commit()


def _role(app, code, permission_codes):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.staff import Permission, Role, RolePermission

        role = Role(code=code, name=code, description=code, is_system_role=False)
        db_session.add(role)
        db_session.flush()
        for perm in db_session.execute(select(Permission).where(Permission.code.in_(permission_codes))).scalars():
            db_session.add(RolePermission(role_id=role.id, permission_id=perm.id))
        db_session.commit()


def _staff_world(app):
    """_world (seller SALES, manager FINANCE, admin SUPER_ADMIN) plus a Sales
    Manager, a viewer, a holder of payments.confirm ALONE, each with a device."""
    w = _world(app)
    w["sales_manager"] = make_staff(app, f"sm-{uuid.uuid4().hex[:6]}@example.com", role_codes=["SALES_MANAGER"], mfa=True)
    _profile(app, w["sales_manager"], f"EMP-SM-{uuid.uuid4().hex[:4]}", "Salma Manager")
    w["viewer"] = make_staff(app, f"v-{uuid.uuid4().hex[:6]}@example.com", role_codes=["VIEWER"])
    _role(app, "CONFIRM_ONLY", ["payments.confirm"])
    w["confirm_only"] = make_staff(app, f"co-{uuid.uuid4().hex[:6]}@example.com", role_codes=["CONFIRM_ONLY"])
    w["devices"] = {who: _device(app, w[who]) for who in
                    ("seller", "manager", "admin", "sales_manager", "viewer", "confirm_only")}
    return w


def _past_the_burst_window(app):
    """Moves every device's last message back past the one-per-minute window."""
    for device in _rows(app, "PushSubscription"):
        if device["last_sent_at"] is not None:
            _set(app, "PushSubscription", {"last_sent_at": device["last_sent_at"] - timedelta(seconds=61)}, id=device["id"])


def _outbox_count(app):
    return len(_rows(app, "PushOutbox"))


# --------------------------------------------- the outbox and its transaction --

def test_recording_a_sale_writes_one_outbox_row_for_its_payment(app, seeded, push_on, fake_push):
    """RED before Step 1 (no outbox at all). The seller's sale -> exactly one
    PENDING outbox row naming the sale and its payment; nothing sent yet."""
    w = _staff_world(app)
    result, _key = _record(app, w)
    rows = _rows(app, "PushOutbox")
    assert len(rows) == 1
    assert rows[0]["sales_order_id"] == result["order_id"]
    assert rows[0]["payment_record_id"] == result["payment_id"]
    assert rows[0]["status"] == "PENDING" and rows[0]["event_type"] == "SALE_AWAITING_CONFIRMATION"
    assert fake_push.calls == []


def test_a_crash_right_after_the_commit_still_sends_exactly_once(app, seeded, push_on, fake_push, monkeypatch):
    """The process dies after the payment's commit (simulated: the audit
    write that follows it raises). The outbox row committed WITH the payment,
    so the next worker pass sends it -- once per device, and never again."""
    w = _staff_world(app)
    import app.subscriptions.services as services

    def _crash(**_kwargs):
        raise RuntimeError("simulated crash after the payment commit")

    monkeypatch.setattr(services, "audit_record", _crash)
    with pytest.raises(RuntimeError, match="simulated crash"):
        _record(app, w)
    monkeypatch.undo()
    monkeypatch.setattr("app.push.webpush.send", fake_push)
    with app.app_context():
        from sqlalchemy import func, select

        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord

        assert db_session.execute(select(func.count()).select_from(PaymentRecord)).scalar_one() == 1
    assert _outbox_count(app) == 1
    _run(app)
    expected = sorted(w["devices"][who] for who in ("manager", "admin", "sales_manager"))
    assert sorted(fake_push.endpoints()) == expected
    _run(app)
    _run(app)
    assert sorted(fake_push.endpoints()) == expected


def test_a_payment_that_never_commits_writes_no_outbox_row(app, seeded, push_on, fake_push, monkeypatch):
    """The payment's commit fails (the database refuses it). The outbox row
    was part of that transaction, so it is gone too -- no notification for a
    sale that does not exist. A notification written in its own transaction
    (before or after) would survive here and fail this test."""
    w = _staff_world(app)
    import app.subscriptions.services as services
    from app.extensions import db_session
    from app.models.subscriptions import PaymentRecord

    class _PaymentCommitRefused:
        """record_payment's session, except that its commit fails like a
        refused COMMIT: the transaction is rolled back and an error raised."""

        def __getattr__(self, name):
            return getattr(db_session, name)

        def commit(self):
            db_session.rollback()
            raise RuntimeError("simulated: the database refused the payment commit")

    monkeypatch.setattr(services, "db_session", _PaymentCommitRefused())
    with pytest.raises(RuntimeError, match="refused the payment commit"):
        _record(app, w)
    monkeypatch.undo()
    with app.app_context():
        from sqlalchemy import func, select

        assert db_session.execute(select(func.count()).select_from(PaymentRecord)).scalar_one() == 0
    assert _outbox_count(app) == 0
    _run(app)
    assert fake_push.calls == []


def test_a_replayed_form_is_announced_once(app, seeded, push_on, fake_push):
    w = _staff_world(app)
    _result, key = _record(app, w)
    replay, _ = _record(app, w, key=key)
    assert replay["replayed"] is True
    assert _outbox_count(app) == 1
    _run(app)
    assert len(fake_push.calls) == 3


# ----------------------------------------------------------- who is told --

def test_recipients_are_exactly_the_people_who_could_confirm(app, seeded, push_on, fake_push):
    """Seller (also the recorder): no. Viewer: no. payments.confirm alone: no
    (cannot confirm a sale). FINANCE, Super Admin, Sales Manager: yes."""
    w = _staff_world(app)
    _record(app, w)
    _run(app)
    assert sorted(fake_push.endpoints()) == sorted(w["devices"][who] for who in ("manager", "admin", "sales_manager"))


def test_a_manager_who_records_for_a_seller_is_not_told_and_neither_is_the_seller(app, seeded, push_on, fake_push):
    w = _staff_world(app)
    _record(app, w, actor=w["admin"], sales_owner_employee_profile_id=w["seller_profile"])
    _run(app)
    assert sorted(fake_push.endpoints()) == sorted(w["devices"][who] for who in ("manager", "sales_manager"))


def test_a_seller_who_could_confirm_is_still_not_told_about_their_own_sale(app, seeded, push_on, fake_push):
    """The Sales Manager sold it (the Super Admin typed it in for them): the
    seller holds every confirm permission, and is still left out -- the
    two-person rule, not the role, decides."""
    w = _staff_world(app)
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.employees import EmployeeProfile

        sm_profile = db_session.execute(
            select(EmployeeProfile.id).where(EmployeeProfile.staff_user_id == w["sales_manager"])
        ).scalar_one()
    _record(app, w, actor=w["admin"], sales_owner_employee_profile_id=sm_profile)
    _run(app)
    assert fake_push.endpoints() == [w["devices"]["manager"]]


def test_every_device_of_a_recipient_is_told(app, seeded, push_on, fake_push):
    w = _staff_world(app)
    second = _device(app, w["manager"])
    _record(app, w)
    _run(app)
    assert sorted(fake_push.endpoints()) == sorted(
        [w["devices"]["manager"], second, w["devices"]["admin"], w["devices"]["sales_manager"]])


@pytest.mark.parametrize("how", ["disable_staff", "suspend_employee", "terminate_employee"])
def test_deactivation_removes_the_devices_and_cancels_what_was_queued(app, seeded, push_on, fake_push, how):
    w = _staff_world(app)
    _record(app, w)
    with app.app_context():
        from app.push.worker import expand_pending

        expand_pending()
    assert {r["status"] for r in _rows(app, "PushDelivery")} == {"QUEUED"}
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.employees import EmployeeProfile
        from app.models.staff import StaffUser

        if how == "disable_staff":
            from app.staff.services import disable_staff

            disable_staff(db_session.get(StaffUser, w["manager"]), "left the company", w["admin"])
        else:
            import app.employees.services as employees

            profile = db_session.execute(
                select(EmployeeProfile).where(EmployeeProfile.staff_user_id == w["manager"])
            ).scalars().one()
            getattr(employees, how)(profile, "left the company", w["admin"])
    assert [d for d in _rows(app, "PushSubscription") if d["staff_user_id"] == w["manager"]] == []
    manager_rows = [r for r in _rows(app, "PushDelivery") if r["staff_user_id"] == w["manager"]]
    assert [(r["status"], r["last_error"], r["subscription_id"]) for r in manager_rows] == [
        ("CANCELLED", "STAFF_DEACTIVATED", None)]
    _run(app)
    assert w["devices"]["manager"] not in fake_push.endpoints()
    assert sorted(fake_push.endpoints()) == sorted(w["devices"][who] for who in ("admin", "sales_manager"))


def test_losing_the_right_to_confirm_before_the_send_cancels_instead_of_sending(app, seeded, push_on, fake_push):
    """Checked again at send time, not only when the deliveries were made: a
    role taken away (or an account switched off by any path) in between."""
    w = _staff_world(app)
    _record(app, w)
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser
        from app.push.worker import expand_pending
        from app.staff.services import assign_roles

        expand_pending()
        assign_roles(db_session.get(StaffUser, w["sales_manager"]), ["VIEWER"], w["admin"])
        admin = db_session.get(StaffUser, w["admin"])
        admin.is_active = False  # not through disable_staff: the send-time check must still catch it
        db_session.commit()
    _run(app)
    assert fake_push.endpoints() == [w["devices"]["manager"]]
    cancelled = {r["staff_user_id"]: r["last_error"] for r in _rows(app, "PushDelivery") if r["status"] == "CANCELLED"}
    assert cancelled == {w["sales_manager"]: "RECIPIENT_NOT_ELIGIBLE", w["admin"]: "RECIPIENT_NOT_ELIGIBLE"}


def test_confirm_permission_set_is_exactly_what_the_confirm_route_demands(app, client, seeded):
    """Who is NOTIFIED is derived from CONFIRM_SALE_PERMISSIONS; this proves
    that set is exactly what confirming takes: a role holding only these
    confirms (and gets the key), a role missing any one of them is refused."""
    from app.commercial_sales.direct_sale import CONFIRM_SALE_PERMISSIONS

    w = _world(app)
    base = ["payments.view"]  # nothing that confirms on its own
    for missing in sorted(CONFIRM_SALE_PERMISSIONS):
        result, _ = _record(app, w)
        code = f"ALL_BUT_{missing.replace('.', '_').upper()}"
        _role(app, code, base + sorted(CONFIRM_SALE_PERMISSIONS - {missing}))
        staff = make_staff(app, f"{code.lower()}@example.com", role_codes=[code], mfa=True)
        c = app.test_client()
        login_and_verify_mfa(c, _email(app, staff))
        csrf = get_csrf(c.get("/licenses/issuance/new").get_data(as_text=True)) or get_csrf(
            c.get("/profile").get_data(as_text=True))
        resp = c.post(f"/licenses/issuance/sales/{result['order_id']}/confirm", data={"csrf_token": csrf})
        assert resp.status_code == 403, (missing, resp.status_code)
    _role(app, "EXACTLY_CONFIRM", sorted(CONFIRM_SALE_PERMISSIONS))
    staff = make_staff(app, "exactly-confirm@example.com", role_codes=["EXACTLY_CONFIRM"], mfa=True)
    login_and_verify_mfa(client, _email(app, staff))
    page = client.get("/licenses/issuance/sales")
    assert page.status_code == 200
    resp = client.post(f"/licenses/issuance/sales/{result['order_id']}/confirm",
                       data={"csrf_token": get_csrf(page.get_data(as_text=True))})
    assert resp.status_code == 200 and "AURA-" in resp.get_data(as_text=True)


# ------------------------------------------------- answers from the service --

@pytest.mark.parametrize("status", [404, 410])
def test_a_gone_subscription_is_removed_and_never_used_again(app, seeded, push_on, fake_push, status):
    w = _staff_world(app)
    gone = w["devices"]["manager"]
    fake_push.script[gone] = [status]
    _record(app, w)
    _run(app)
    assert gone in fake_push.endpoints()
    assert gone not in [d["endpoint"] for d in _rows(app, "PushSubscription")]
    rows = [r for r in _rows(app, "PushDelivery") if r["staff_user_id"] == w["manager"]]
    assert [(r["status"], r["last_error"], r["subscription_id"]) for r in rows] == [
        ("FAILED_PERMANENT", "SUBSCRIPTION_GONE", None)]
    fake_push.calls.clear()
    _past_the_burst_window(app)
    _record(app, w)
    _run(app)
    assert gone not in fake_push.endpoints() and len(fake_push.calls) == 2


def test_a_temporary_failure_backs_off_then_succeeds(app, seeded, push_on, fake_push):
    w = _staff_world(app)
    device = w["devices"]["manager"]
    fake_push.script[device] = [503, "neterr", (429, 900)]
    _record(app, w)
    _run(app)
    row = [r for r in _rows(app, "PushDelivery") if r["staff_user_id"] == w["manager"]][0]
    assert (row["status"], row["attempt_count"], row["last_error"]) == ("QUEUED", 1, "HTTP_503")
    first_delay = (row["next_attempt_at"] - row["created_at"]).total_seconds()
    assert 20 < first_delay < 40  # 30 s +/- 20%
    _run(app)  # not due yet: nothing is sent
    assert fake_push.endpoints().count(device) == 1

    _set(app, "PushDelivery", {"next_attempt_at": row["created_at"]}, id=row["id"])
    _run(app)
    row = [r for r in _rows(app, "PushDelivery") if r["id"] == row["id"]][0]
    assert (row["status"], row["attempt_count"], row["last_error"]) == ("QUEUED", 2, "URLError")

    _set(app, "PushDelivery", {"next_attempt_at": row["created_at"]}, id=row["id"])
    _run(app)
    row = [r for r in _rows(app, "PushDelivery") if r["id"] == row["id"]][0]
    assert (row["status"], row["attempt_count"], row["last_error"]) == ("QUEUED", 3, "HTTP_429")
    from datetime import datetime, timezone

    assert (row["next_attempt_at"] - datetime.now(timezone.utc)).total_seconds() > 850  # Retry-After honoured

    _set(app, "PushDelivery", {"next_attempt_at": row["created_at"]}, id=row["id"])
    _run(app)
    row = [r for r in _rows(app, "PushDelivery") if r["id"] == row["id"]][0]
    assert row["status"] == "SENT" and row["sent_at"] is not None
    assert fake_push.endpoints().count(device) == 4


def test_retries_stop_after_the_last_attempt(app, seeded, push_on, fake_push):
    from app.push.worker import MAX_ATTEMPTS

    w = _staff_world(app)
    device = w["devices"]["manager"]
    fake_push.script[device] = [500] * (MAX_ATTEMPTS + 3)
    _record(app, w)
    for _ in range(MAX_ATTEMPTS + 2):
        _set(app, "PushDelivery", {"next_attempt_at": None}, staff_user_id=w["manager"], status="QUEUED")
        _run(app)
    row = [r for r in _rows(app, "PushDelivery") if r["staff_user_id"] == w["manager"]][0]
    assert (row["status"], row["last_error"]) == ("FAILED_PERMANENT", "HTTP_500")
    assert fake_push.endpoints().count(device) == MAX_ATTEMPTS


def test_a_refused_message_is_not_retried_and_the_device_is_kept(app, seeded, push_on, fake_push):
    w = _staff_world(app)
    device = w["devices"]["manager"]
    fake_push.script[device] = [403]
    _record(app, w)
    _run(app)
    _set(app, "PushDelivery", {"next_attempt_at": None}, staff_user_id=w["manager"])
    _run(app)
    row = [r for r in _rows(app, "PushDelivery") if r["staff_user_id"] == w["manager"]][0]
    assert (row["status"], row["last_error"]) == ("FAILED_PERMANENT", "HTTP_403")
    assert fake_push.endpoints().count(device) == 1
    assert device in [d["endpoint"] for d in _rows(app, "PushSubscription")]


# ----------------------------------------------------- exactly once per device --

def test_re_running_and_re_expanding_never_sends_a_sale_to_a_device_twice(app, seeded, push_on, fake_push):
    w = _staff_world(app)
    _record(app, w)
    _run(app)
    assert len(fake_push.calls) == 3
    # Even if the outbox row were processed again (a crash between making the
    # deliveries and marking it EXPANDED), the unique (row, device) holds.
    _set(app, "PushOutbox", {"status": "PENDING", "processed_at": None})
    _run(app)
    _run(app)
    assert len(fake_push.calls) == 3
    assert len(_rows(app, "PushDelivery")) == 3


def test_a_device_another_worker_is_sending_to_is_left_alone(app, seeded, push_on, fake_push):
    """The per-device lease: while one worker holds a phone (mid-send), a
    second worker sends nothing to it -- and takes it once the lease ends."""
    from datetime import datetime, timezone

    w = _staff_world(app)
    _record(app, w)
    busy = w["devices"]["manager"]
    device = [d for d in _rows(app, "PushSubscription") if d["endpoint"] == busy][0]
    _set(app, "PushSubscription", {"lease_expires_at": datetime.now(timezone.utc) + timedelta(seconds=30)}, id=device["id"])
    result = _run(app)
    assert result["send"].get("busy") == 1
    assert busy not in fake_push.endpoints() and len(fake_push.calls) == 2
    _set(app, "PushSubscription", {"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}, id=device["id"])
    _run(app)
    assert fake_push.endpoints().count(busy) == 1


def test_two_workers_at_once_send_each_device_once(app, seeded, push_on, fake_push):
    w = _staff_world(app)
    for _ in range(3):
        _record(app, w)
    errors = []

    def worker():
        try:
            _run(app)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            from app.extensions import db_session as scoped

            scoped.remove()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)
    assert errors == [], errors
    _run(app)
    # Each recipient device got the three sales, and never one sale twice.
    sent = [r for r in _rows(app, "PushDelivery") if r["status"] == "SENT"]
    assert len(sent) == 9
    assert len({(r["outbox_id"], r["subscription_id"]) for r in sent}) == 9
    # The three sales were waiting before any worker ran: one message per device.
    assert sorted(fake_push.endpoints()) == sorted(w["devices"][who] for who in ("manager", "admin", "sales_manager"))


# ------------------------------------------------------- what the phone shows --

def test_the_message_has_no_money_and_no_customer(app, seeded, push_on, fake_push):
    w = _staff_world(app)
    _record(app, w, new_customer_legal_name="Zarqa Supermarket LLC", reference="CLIQ-SECRET-4711")
    _run(app)
    assert len(fake_push.calls) == 3
    for call in fake_push.calls:
        raw = call["raw"].decode("utf-8")
        for forbidden in ("Zarqa", "Supermarket", "CLIQ-SECRET-4711", "100", "JOD", "Aura Retail Yearly", "AURA-"):
            assert forbidden not in raw, (forbidden, raw)
        payload = call["payload"]
        assert set(payload) == {"title", "body", "url", "tag", "lang", "dir"}
        assert payload["title"] == "New sale waiting for confirmation"
        assert payload["body"] == "From Sami"  # the SELLER's first name (an employee), nothing else
        assert payload["tag"] == "aura-owner-sales-waiting" and call["topic"] == "sales-waiting"
        assert payload["url"].startswith("/licenses/issuance/sales?sale=")
        assert call["ttl"] == 24 * 3600


def test_the_message_is_in_each_persons_language(app, seeded, push_on, fake_push):
    w = _staff_world(app)
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        db_session.get(StaffUser, w["manager"]).locale = "ar"
        db_session.commit()
    # No saved account language: the language the device subscribed in.
    arabic_device = _device(app, w["sales_manager"], locale="ar")
    _record(app, w)
    _run(app)
    by_endpoint = {c["endpoint"]: c["payload"] for c in fake_push.calls}
    for endpoint in (w["devices"]["manager"], arabic_device):
        assert by_endpoint[endpoint]["title"] == "بيع جديد بانتظار التأكيد"
        assert by_endpoint[endpoint]["body"] == "من Sami"
        assert (by_endpoint[endpoint]["lang"], by_endpoint[endpoint]["dir"]) == ("ar", "rtl")
    assert by_endpoint[w["devices"]["admin"]]["title"] == "New sale waiting for confirmation"
    assert by_endpoint[w["devices"]["admin"]]["dir"] == "ltr"


def test_a_burst_of_sales_is_one_message_per_device_per_minute(app, seeded, push_on, fake_push):
    """Sale 1 goes out at once. Sales 2 and 3, inside the same minute, wait
    for that device's window and then go out as ONE message saying how many
    are waiting -- three sales, two buzzes per phone, not three."""
    w = _staff_world(app)
    first, _ = _record(app, w)
    _run(app)
    assert len(fake_push.calls) == 3
    _record(app, w)
    _record(app, w)
    _run(app)
    assert len(fake_push.calls) == 3  # inside the window: held back, not sent
    assert {r["status"] for r in _rows(app, "PushDelivery")} == {"SENT", "QUEUED"}
    queued = [r for r in _rows(app, "PushDelivery") if r["status"] == "QUEUED"]
    assert len(queued) == 6 and all(r["next_attempt_at"] is not None for r in queued)

    _past_the_burst_window(app)
    _set(app, "PushDelivery", {"next_attempt_at": None}, status="QUEUED")
    _run(app)
    later = fake_push.calls[3:]
    assert len(later) == 3  # one per device, carrying both new sales
    for call in later:
        assert call["payload"]["body"] == "From Sami. Sales waiting for you now: 3"
        assert call["payload"]["url"] == "/licenses/issuance/sales"
    assert {r["status"] for r in _rows(app, "PushDelivery")} == {"SENT"}
    assert first["order_id"]  # the first message named its sale
    assert fake_push.calls[0]["payload"]["url"] == (
        f"/licenses/issuance/sales?sale={first['order_id']}#sale-{first['order_id']}")


def test_a_sale_confirmed_before_the_send_is_not_announced(app, seeded, push_on, fake_push):
    w = _staff_world(app)
    result, _ = _record(app, w)
    _confirm(app, result["order_id"], w["manager"])
    _run(app)
    assert fake_push.calls == []
    assert [(r["status"], r["skip_reason"]) for r in _rows(app, "PushOutbox")] == [("SKIPPED", "NO_LONGER_WAITING")]


def test_a_sale_confirmed_between_expand_and_send_is_cancelled(app, seeded, push_on, fake_push):
    w = _staff_world(app)
    result, _ = _record(app, w)
    with app.app_context():
        from app.push.worker import expand_pending

        expand_pending()
    _confirm(app, result["order_id"], w["manager"])
    _run(app)
    assert fake_push.calls == []
    assert {(r["status"], r["last_error"]) for r in _rows(app, "PushDelivery")} == {("CANCELLED", "NO_LONGER_WAITING")}


def test_a_day_old_outbox_row_is_skipped(app, seeded, push_on, fake_push):
    w = _staff_world(app)
    _record(app, w)
    row = _rows(app, "PushOutbox")[0]
    _set(app, "PushOutbox", {"created_at": row["created_at"] - timedelta(hours=25)}, id=row["id"])
    _run(app)
    assert fake_push.calls == []
    assert [(r["status"], r["skip_reason"]) for r in _rows(app, "PushOutbox")] == [("SKIPPED", "TOO_OLD")]


# ------------------------------------------------------------ tapping it --

def test_tapping_after_another_manager_confirmed_says_already_confirmed(app, client, seeded, push_on):
    w = _world(app)
    result, _ = _record(app, w)
    force_login(client, app, w["admin"])
    page = client.get(f"/licenses/issuance/sales?sale={result['order_id']}").get_data(as_text=True)
    assert f'id="sale-{result["order_id"]}" class="is-highlighted"' in page
    assert "Already confirmed" not in page

    _confirm(app, result["order_id"], w["manager"])
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        manager_name = db_session.get(StaffUser, w["manager"]).display_name
    page = client.get(f"/licenses/issuance/sales?sale={result['order_id']}").get_data(as_text=True)
    assert "Already confirmed" in page
    assert f"was already confirmed by {manager_name}" in page
    assert f'id="sale-{result["order_id"]}"' not in page


def test_tapping_a_rejected_sale_says_already_rejected(app, client, seeded):
    w = _world(app)
    result, _ = _record(app, w)
    with app.app_context():
        from app.commercial_sales.direct_sale import reject_direct_sale

        reject_direct_sale(result["order_id"], reason="money never arrived", actor_staff_user_id=w["manager"])
    force_login(client, app, w["admin"])
    page = client.get(f"/licenses/issuance/sales?sale={result['order_id']}").get_data(as_text=True)
    assert "Already rejected" in page and "was already rejected" in page
    assert client.get("/licenses/issuance/sales?sale=not-a-uuid").status_code == 200


# ----------------------------------------------------------------- the badge --

def test_badge_counts_what_this_person_could_confirm(app, client, seeded):
    w = _world(app)
    _record(app, w)
    _record(app, w)
    # The admin records one for the seller: the admin can never confirm that one.
    _record(app, w, actor=w["admin"], sales_owner_employee_profile_id=w["seller_profile"])

    force_login(client, app, w["manager"])
    assert client.get("/licenses/issuance/sales/count").get_json() == {"count": 3}
    html = client.get("/attention").get_data(as_text=True)
    assert 'id="aura-sales-waiting-badge"\n' in html or 'id="aura-sales-waiting-badge"' in html
    assert 'data-count-url="/licenses/issuance/sales/count"' in html
    assert 'Sales waiting for your confirmation: 3' in html
    assert '<span class="aura-topbar__sales-waiting-count" data-count-badge-value aria-hidden="true">3</span>' in html
    assert 'class="aura-nav-link__count"' in html

    admin = app.test_client()
    force_login(admin, app, w["admin"])
    assert admin.get("/licenses/issuance/sales/count").get_json() == {"count": 2}

    seller = app.test_client()
    force_login(seller, app, w["seller"])
    resp = seller.get("/licenses/issuance/sales/count")
    assert resp.status_code == 403 and resp.headers["Cache-Control"] == "no-store"
    assert "aura-sales-waiting-badge" not in seller.get("/attention").get_data(as_text=True)
    assert app.test_client().get("/licenses/issuance/sales/count").status_code == 401


def test_badge_ships_hidden_at_zero_and_drops_after_a_confirm(app, client, seeded):
    w = _world(app)
    force_login(client, app, w["manager"])
    html = client.get("/attention").get_data(as_text=True)
    assert 'id="aura-sales-waiting-badge" hidden' in html
    result, _ = _record(app, w)
    assert client.get("/licenses/issuance/sales/count").get_json() == {"count": 1}
    _confirm(app, result["order_id"], w["admin"])
    assert client.get("/licenses/issuance/sales/count").get_json() == {"count": 0}


# ------------------------------------------------------------ the kill switch --

def test_push_off_writes_nothing_sends_nothing_and_shows_no_button(app, client, seeded, fake_push):
    """No VAPID key (the default): the sale, the list, the confirm and the
    badge all work; no outbox row, no thread, /push/* is 404, no card."""
    w = _staff_world(app)
    result, _ = _record(app, w)
    assert _outbox_count(app) == 0
    assert _run(app) == {"ran": False, "reason": "push_not_configured"}
    assert fake_push.calls == []
    login_and_verify_mfa(client, _email(app, w["manager"]))
    page = client.get("/licenses/issuance/sales")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "aura-push-optin" not in html and "js/push.js" not in html
    csrf = get_csrf(html)
    assert client.post("/push/subscribe", json={}, headers={"X-CSRFToken": csrf}).status_code == 404
    assert client.post(f"/licenses/issuance/sales/{result['order_id']}/confirm", data={"csrf_token": csrf}).status_code == 200


def test_the_thread_starts_only_with_a_key_and_never_in_tests(app):
    from app.push.vapid import generate_private_key_b64
    from app.push.worker import start_worker_if_configured

    class _App:
        def __init__(self, config):
            self.config = config
            self.extensions = {}

    assert start_worker_if_configured(_App({"TESTING": False, "PUSH_VAPID_PRIVATE_KEY": ""})) is None
    key, _ = generate_private_key_b64()
    assert start_worker_if_configured(_App({"TESTING": True, "PUSH_VAPID_PRIVATE_KEY": key,
                                            "PUSH_VAPID_SUBJECT": "mailto:a@b.c"})) is None
    started = start_worker_if_configured(_App({"TESTING": False, "PUSH_VAPID_PRIVATE_KEY": key,
                                               "PUSH_VAPID_SUBJECT": "mailto:a@b.c"}))
    try:
        assert started is not None
    finally:
        started.stop()


# ------------------------------------------------- turning it on and off --

def _manager_client(app, staff_id):
    c = app.test_client()
    force_login(c, app, staff_id)
    page = c.get("/licenses/issuance/sales").get_data(as_text=True)
    return c, get_csrf(page), page


def test_the_card_and_subscribe_route_are_for_people_who_could_confirm(app, seeded, push_on):
    w = _world(app)
    c, csrf, page = _manager_client(app, w["manager"])
    assert 'id="aura-push-optin"' in page and "js/push.js" in page
    from app.push.vapid import keys_from_config

    assert f'data-public-key="{keys_from_config(app.config).public_key_b64}"' in page
    p256dh, auth = _browser_keys()
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/aaa", "keys": {"p256dh": p256dh, "auth": auth},
           "staff_user_id": str(w["admin"])}
    assert c.post("/push/subscribe", json=sub).status_code == 400  # no CSRF header
    resp = c.post("/push/subscribe", json=sub, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200 and resp.get_json() == {"ok": True}
    devices = _rows(app, "PushSubscription")
    assert [(d["staff_user_id"], d["endpoint"]) for d in devices] == [(w["manager"], sub["endpoint"])]  # the SESSION's person

    # A seller can confirm nothing: no card, and the route refuses.
    s, s_csrf, _ = _manager_client(app, w["seller"])
    s_csrf = s_csrf or get_csrf(s.get("/licenses/issuance/new").get_data(as_text=True))
    assert s.post("/push/subscribe", json=sub, headers={"X-CSRFToken": s_csrf}).status_code == 403
    assert app.test_client().post("/push/subscribe", json=sub, headers={"Accept": "application/json"}).status_code in (400, 401)

    bad = dict(sub, endpoint="https://evil.example/collect")
    resp = c.post("/push/subscribe", json=bad, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 400 and resp.get_json()["code"] == "ENDPOINT_HOST_NOT_ALLOWED"

    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.audit import AuditLog

        audits = db_session.execute(select(AuditLog).where(AuditLog.action_code.like("PUSH_%"))).scalars().all()
        assert [a.action_code for a in audits] == ["PUSH_DEVICE_SUBSCRIBED"]
        assert "fcm.googleapis.com" not in json.dumps(
            [[a.entity_public_id, a.after_state_redacted, a.reason] for a in audits], default=str)
    # Refreshing the same device (every visit does) is not a new audit row.
    assert c.post("/push/subscribe", json=sub, headers={"X-CSRFToken": csrf}).status_code == 200
    resp = c.post("/push/unsubscribe", json={"endpoint": sub["endpoint"]}, headers={"X-CSRFToken": csrf})
    assert resp.get_json() == {"ok": True, "removed": True}
    assert _rows(app, "PushSubscription") == []


def test_a_device_that_changes_hands_stops_getting_the_previous_persons_messages(app, seeded, push_on, fake_push):
    w = _staff_world(app)
    shared = w["devices"]["manager"]
    _record(app, w)
    with app.app_context():
        from app.push.worker import expand_pending

        expand_pending()
    # The manager logs out on that phone; the Sales Manager logs in and turns it on.
    _device(app, w["sales_manager"], endpoint=shared)
    _run(app)
    # The manager's queued message never reaches the phone now in other hands.
    assert sorted(fake_push.endpoints()) == sorted([w["devices"]["admin"], w["devices"]["sales_manager"]])
    rows = {r["staff_user_id"]: (r["status"], r["last_error"]) for r in _rows(app, "PushDelivery")
            if r["staff_user_id"] == w["manager"]}
    assert rows == {w["manager"]: ("CANCELLED", "DEVICE_CHANGED_HANDS")}


def test_one_person_keeps_at_most_ten_devices(app, seeded, push_on):
    from app.push.subscriptions import MAX_DEVICES_PER_STAFF

    w = _world(app)
    endpoints = [_device(app, w["manager"]) for _ in range(MAX_DEVICES_PER_STAFF + 2)]
    kept = [d["endpoint"] for d in _rows(app, "PushSubscription")]
    assert len(kept) == MAX_DEVICES_PER_STAFF
    assert set(kept) == set(endpoints[2:])  # the two least recently refreshed went


# ------------------------------------------------------------------- the CLI --

def test_cli_send_pending_runs_one_pass(app, seeded, push_on, fake_push):
    w = _staff_world(app)
    _record(app, w)
    result = app.test_cli_runner().invoke(args=["notifications", "send-pending"])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output.splitlines()[0])
    assert summary == {"ran": True, "expand": {"expanded": 1, "skipped": 0, "deliveries": 3}, "send": {"sent": 3}}
    assert len(fake_push.calls) == 3
    idle = app.test_cli_runner().invoke(args=["notifications", "send-pending", "--quiet-when-idle"])
    assert idle.exit_code == 0 and idle.output == ""  # the every-minute timer's journal stays clean
    status = json.loads(app.test_cli_runner().invoke(args=["notifications", "status"]).output)
    assert status["push_enabled"] is True and status["devices"] == 6 and status["deliveries"] == {"SENT": 3}


def test_cli_generate_keys_refuses_a_terminal_and_writes_one_env_line(app, monkeypatch):
    import app.push.cli as push_cli
    from app.push.vapid import parse_private_key, public_key_b64_of

    runner = app.test_cli_runner()
    monkeypatch.setattr(push_cli, "_stdout_is_terminal", lambda: True)
    refused = runner.invoke(args=["notifications", "generate-vapid-keys"])
    assert refused.exit_code != 0
    assert "OWNER_PUSH_VAPID_PRIVATE_KEY=" not in refused.stdout and "Refusing" in refused.stderr

    monkeypatch.setattr(push_cli, "_stdout_is_terminal", lambda: False)
    result = runner.invoke(args=["notifications", "generate-vapid-keys"])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.stdout.splitlines() if line]
    assert len(lines) == 1 and lines[0].startswith("OWNER_PUSH_VAPID_PRIVATE_KEY=")  # stdout is ONLY the env line
    key = lines[0].split("=", 1)[1]
    assert public_key_b64_of(parse_private_key(key)) in result.stderr
    assert key not in result.stderr


# ------------------------------------------------------ the service worker --

ORIGIN = "https://owner.test"


def test_the_service_worker_shows_what_the_server_wrote_and_opens_only_this_site(app, client, tmp_path):
    """The REAL rendered /sw.js, run in Node (tests/pwa_push_harness.js):
    a push shows exactly the server's title/body with the fixed tag; a push
    with no or broken data still shows a notification (userVisibleOnly is a
    promise); a tap opens an Aura Owner window at the message's page, never
    another site; and nothing touches the cache."""
    from tests.test_pwa import TESTS_DIR, _run_node

    source = client.get("/sw.js").get_data(as_text=True)
    sw_path = tmp_path / "sw.js"
    sw_path.write_text(source, encoding="utf-8")
    message = json.dumps({"title": "بيع جديد بانتظار التأكيد", "body": "من Sami", "url": "/licenses/issuance/sales?sale=1#sale-1",
                          "tag": "aura-owner-sales-waiting", "lang": "ar", "dir": "rtl"}, ensure_ascii=False)
    result = _run_node(TESTS_DIR / "pwa_push_harness.js", {
        "swPath": str(sw_path), "origin": ORIGIN,
        "pushes": [
            {"id": "full", "data": message},
            {"id": "empty", "data": None},
            {"id": "broken", "data": "{not json"},
            {"id": "elsewhere", "data": json.dumps({"title": "x", "url": "https://evil.example/phish"})},
            {"id": "protocol_relative", "data": json.dumps({"title": "x", "url": "//evil.example/phish"})},
        ],
        "clicks": [
            {"id": "open_window", "url": "/licenses/issuance/sales?sale=1#sale-1", "windows": [ORIGIN + "/attention"]},
            {"id": "no_window", "url": "/licenses/issuance/sales", "windows": []},
            {"id": "foreign_window", "url": "/licenses/issuance/sales", "windows": ["https://other.example/"]},
            {"id": "evil_url", "url": "https://evil.example/phish", "windows": []},
        ],
    })
    assert result["pushErrors"] == {}
    full = result["notifications"]["full"]
    assert full["title"] == "بيع جديد بانتظار التأكيد"
    assert full["options"]["body"] == "من Sami"
    assert full["options"]["tag"] == "aura-owner-sales-waiting" and full["options"]["renotify"] is True
    assert (full["options"]["lang"], full["options"]["dir"]) == ("ar", "rtl")
    assert full["options"]["data"] == {"url": "/licenses/issuance/sales?sale=1#sale-1"}
    assert full["options"]["icon"] == "/static/img/brand/icon-192.png"
    for quiet in ("empty", "broken"):
        shown = result["notifications"][quiet]
        assert shown["title"] == "Aura Owner" and shown["options"]["tag"] == "aura-owner"
        assert shown["options"]["data"] == {"url": "/licenses/issuance/sales"}
    for elsewhere in ("elsewhere", "protocol_relative"):
        assert result["notifications"][elsewhere]["options"]["data"] == {"url": "/licenses/issuance/sales"}

    clicks = result["clicks"]
    assert clicks["open_window"]["navigated"] == ["/licenses/issuance/sales?sale=1#sale-1"]
    assert clicks["open_window"]["opened"] == [] and clicks["open_window"]["closed"] is True
    assert clicks["open_window"]["focused"] == [ORIGIN + "/licenses/issuance/sales?sale=1#sale-1"]
    assert clicks["no_window"]["opened"] == ["/licenses/issuance/sales"]
    assert clicks["foreign_window"]["navigated"] == [] and clicks["foreign_window"]["opened"] == ["/licenses/issuance/sales"]
    assert clicks["evil_url"]["opened"] == ["/licenses/issuance/sales"]
    assert result["cacheNamesAfter"] == result["cacheNamesBefore"]


def test_sending_does_not_count_as_the_page_refreshing_the_device(app, seeded, push_on, fake_push):
    """updated_at orders the device cap (least recently REFRESHED goes); the
    worker's lease and last_sent_at writes must leave it alone."""
    w = _staff_world(app)
    before = {d["id"]: d["updated_at"] for d in _rows(app, "PushSubscription")}
    _record(app, w)
    _run(app)
    after = {d["id"]: (d["updated_at"], d["last_sent_at"]) for d in _rows(app, "PushSubscription")}
    assert sum(1 for _id, (_u, sent) in after.items() if sent is not None) == 3
    assert {k: v[0] for k, v in after.items()} == before
