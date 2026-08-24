"""Phase 5 prerequisite #3's required Owner console visibility
(docs/launch-readiness/phase5-prerequisites.md section 3: "The console
view is part of this task, not a follow-up"). Exercises the REAL staff
console routes (app/sync/quarantine_routes.py) through the Flask test
client with a real session/CSRF round trip -- not the underlying model
directly -- so a permission-decorator regression or a CSRF wiring mistake
would actually be caught here."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from tests.conftest import build_activation_body, force_login, get_csrf, make_device_keypair, make_license, make_staff


def _activate(client, body):
    return client.post("/api/licensing/v1/activations", data=json.dumps(body), content_type="application/json")


def _do_activation(app, client, actor_id):
    license_id, full_key = make_license(app, actor_id)
    private_key = make_device_keypair()
    resp = _activate(client, build_activation_body(private_key, full_key=full_key, installation_id=str(uuid.uuid4())))
    assert resp.status_code == 200, resp.get_json()
    return license_id, resp.get_json()["installation_id"]


def _insert_quarantine_row(app, license_id, device_id, *, raw_payload, reason="entity_type", batch_index=0):
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncQuarantineEvent

        row = SyncQuarantineEvent(
            license_id=license_id, device_id=uuid.UUID(device_id), batch_index=batch_index,
            raw_payload=raw_payload, rejection_reason=reason,
        )
        db_session.add(row)
        db_session.commit()
        return row.id


def _valid_payload(entity_id=None):
    return {
        "id": str(uuid.uuid4()),
        "entity_type": "category",
        "entity_id": str(entity_id or uuid.uuid4()),
        "event_type": "create",
        "payload": {"name": "Replayed Category"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _grant_recent_auth(app, staff_id):
    """require_recent_auth (app/security/rbac.py) checks the CURRENT
    session's mfa_verified_at against RECENT_AUTH_WINDOW_SECONDS.
    force_login() (tests/conftest.py) deliberately never sets it -- same
    established pattern test_licensing.py::test_issuance_route_redirects_to_reauth_without_recent_mfa
    uses to simulate an EXPIRED recent-auth by shifting mfa_verified_at into
    the past; this is the same mechanism used to simulate a CURRENTLY VALID
    one instead, by not shifting it at all."""
    with app.app_context():
        from app.extensions import db_session
        from app.models.base import utcnow
        from app.models.staff import StaffSession

        session_row = db_session.query(StaffSession).filter(
            StaffSession.staff_user_id == staff_id
        ).order_by(StaffSession.created_at.desc()).first()
        session_row.mfa_verified_at = utcnow()
        db_session.commit()


def test_list_requires_view_permission(app, client, seeded):
    staff_id = make_staff(app, "quar-noperm@example.com", role_codes=[])
    force_login(client, app, staff_id)
    resp = client.get("/sync/quarantine")
    assert resp.status_code == 403


def test_list_shows_pending_rows_by_default(app, client, seeded, signing_key):
    actor_id = make_staff(app, "quar-list-actor@example.com", role_codes=["SUPPORT"])
    license_id, device_id = _do_activation(app, client, actor_id)
    _insert_quarantine_row(app, license_id, device_id, raw_payload={"bad": "row"})

    staff_id = make_staff(app, "quar-list@example.com", role_codes=["SUPPORT"])  # SUPPORT has sync_quarantine.view
    force_login(client, app, staff_id)
    resp = client.get("/sync/quarantine")
    assert resp.status_code == 200
    assert str(license_id)[:8].encode() in resp.data  # template truncates to 8 chars, matching device_keys.html convention


def test_list_excludes_resolved_rows_from_default_pending_view(app, client, seeded, signing_key):
    """Real, discriminating proof the default filter is doing something --
    not just that the page renders. A DISCARDED row must NOT appear on the
    default (status=PENDING) view but MUST appear once the status filter is
    changed to DISCARDED."""
    actor_id = make_staff(app, "quar-filter-actor@example.com", role_codes=["SUPPORT"])
    license_id, device_id = _do_activation(app, client, actor_id)
    quarantine_id = _insert_quarantine_row(app, license_id, device_id, raw_payload={"bad": "row"})
    with app.app_context():
        from app.extensions import db_session
        from app.models.base import utcnow
        from app.models.sync import SyncQuarantineEvent

        row = db_session.get(SyncQuarantineEvent, quarantine_id)
        row.status = "DISCARDED"
        row.resolved_at = utcnow()
        db_session.commit()

    staff_id = make_staff(app, "quar-filter@example.com", role_codes=["SUPPORT"])
    force_login(client, app, staff_id)

    default_page = client.get("/sync/quarantine")
    assert default_page.status_code == 200
    assert str(license_id)[:8].encode() not in default_page.data

    discarded_page = client.get("/sync/quarantine?status=DISCARDED")
    assert discarded_page.status_code == 200
    assert str(license_id)[:8].encode() in discarded_page.data


def test_replay_requires_replay_permission_not_just_view(app, client, seeded, signing_key):
    """SUPPORT holds sync_quarantine.view but NOT sync_quarantine.replay
    (same precedent as device_keys.view/revoke) -- proves the route is
    independently gated, not merely hidden in the template."""
    actor_id = make_staff(app, "quar-replayperm-actor@example.com", role_codes=["SUPPORT"])
    license_id, device_id = _do_activation(app, client, actor_id)
    quarantine_id = _insert_quarantine_row(app, license_id, device_id, raw_payload=_valid_payload())

    staff_id = make_staff(app, "quar-replayperm@example.com", role_codes=["SUPPORT"])
    force_login(client, app, staff_id)
    _grant_recent_auth(app, staff_id)  # even WITH recent auth, permission alone must block this
    page = client.get("/sync/quarantine")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(f"/sync/quarantine/{quarantine_id}/replay", data={"csrf_token": csrf})
    assert resp.status_code == 403


def test_replay_without_recent_auth_redirects_to_reauth(app, client, seeded, signing_key):
    """Mirrors test_licensing.py's own recent-auth boundary test: a
    super-admin WITH sync_quarantine.replay but WITHOUT a currently-valid
    mfa_verified_at must be redirected to reauth, not allowed through."""
    actor_id = make_staff(app, "quar-noreauth-actor@example.com", super_admin=True)
    license_id, device_id = _do_activation(app, client, actor_id)
    quarantine_id = _insert_quarantine_row(app, license_id, device_id, raw_payload=_valid_payload())

    staff_id = make_staff(app, "quar-noreauth@example.com", super_admin=True)
    force_login(client, app, staff_id)  # never sets mfa_verified_at
    page = client.get("/sync/quarantine")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(f"/sync/quarantine/{quarantine_id}/replay", data={"csrf_token": csrf})
    assert resp.status_code == 302
    assert "reauth" in resp.headers["Location"]

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent, SyncQuarantineEvent

        assert db_session.get(SyncEvent, uuid.UUID(_valid_payload()["id"])) is None  # nothing applied
        row = db_session.get(SyncQuarantineEvent, quarantine_id)
        assert row.status == "PENDING"  # unchanged


def test_successful_replay_inserts_real_event_and_marks_replayed(app, client, seeded, signing_key):
    actor_id = make_staff(app, "quar-replay-actor@example.com", super_admin=True)
    license_id, device_id = _do_activation(app, client, actor_id)
    payload = _valid_payload()
    quarantine_id = _insert_quarantine_row(app, license_id, device_id, raw_payload=payload)

    staff_id = make_staff(app, "quar-replay@example.com", super_admin=True)
    force_login(client, app, staff_id)
    _grant_recent_auth(app, staff_id)
    page = client.get("/sync/quarantine")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(f"/sync/quarantine/{quarantine_id}/replay", data={"csrf_token": csrf})
    assert resp.status_code == 302
    assert "reauth" not in resp.headers["Location"]

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent, SyncQuarantineEvent

        event = db_session.get(SyncEvent, uuid.UUID(payload["id"]))
        assert event is not None
        assert str(event.license_id) == str(license_id)
        assert str(event.device_id) == device_id

        row = db_session.get(SyncQuarantineEvent, quarantine_id)
        assert row.status == "REPLAYED"
        assert row.resolved_at is not None
        assert row.resolved_by_staff_user_id == staff_id


def test_replay_of_still_invalid_payload_stays_pending(app, client, seeded, signing_key):
    """If the underlying cause was never actually fixed, replay must not
    pretend it worked -- the row stays PENDING and visible, and no
    SyncEvent is created."""
    actor_id = make_staff(app, "quar-stillbad-actor@example.com", super_admin=True)
    license_id, device_id = _do_activation(app, client, actor_id)
    bad_payload = _valid_payload()
    bad_payload["entity_type"] = "x" * 65  # still invalid
    quarantine_id = _insert_quarantine_row(app, license_id, device_id, raw_payload=bad_payload)

    staff_id = make_staff(app, "quar-stillbad@example.com", super_admin=True)
    force_login(client, app, staff_id)
    _grant_recent_auth(app, staff_id)
    page = client.get("/sync/quarantine")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(f"/sync/quarantine/{quarantine_id}/replay", data={"csrf_token": csrf})
    assert resp.status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent, SyncQuarantineEvent

        assert db_session.get(SyncEvent, uuid.UUID(bad_payload["id"])) is None
        row = db_session.get(SyncQuarantineEvent, quarantine_id)
        assert row.status == "PENDING"  # unchanged
        assert row.resolved_at is None


def test_replay_already_landed_event_is_idempotent(app, client, seeded, signing_key):
    """If the real event already exists (e.g. the client's own outbox
    retried and landed it before an operator got to this row), replay
    must recognize that as success rather than raising an IntegrityError
    or creating a duplicate."""
    actor_id = make_staff(app, "quar-already-actor@example.com", super_admin=True)
    license_id, device_id = _do_activation(app, client, actor_id)
    payload = _valid_payload()
    quarantine_id = _insert_quarantine_row(app, license_id, device_id, raw_payload=payload)

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent
        from app.sync.routes import _build_event

        event = _build_event(payload)
        event.license_id = license_id
        event.device_id = uuid.UUID(device_id)
        db_session.add(event)
        db_session.commit()

    staff_id = make_staff(app, "quar-already@example.com", super_admin=True)
    force_login(client, app, staff_id)
    _grant_recent_auth(app, staff_id)
    page = client.get("/sync/quarantine")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(f"/sync/quarantine/{quarantine_id}/replay", data={"csrf_token": csrf})
    assert resp.status_code == 302  # no 500, no exception

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent, SyncQuarantineEvent

        assert db_session.query(SyncEvent).filter(SyncEvent.id == uuid.UUID(payload["id"])).count() == 1  # still exactly one
        row = db_session.get(SyncQuarantineEvent, quarantine_id)
        assert row.status == "REPLAYED"


def test_discard_requires_discard_permission_not_just_view(app, client, seeded, signing_key):
    actor_id = make_staff(app, "quar-discperm-actor@example.com", role_codes=["SUPPORT"])
    license_id, device_id = _do_activation(app, client, actor_id)
    quarantine_id = _insert_quarantine_row(app, license_id, device_id, raw_payload={"bad": "row"})

    staff_id = make_staff(app, "quar-discperm@example.com", role_codes=["SUPPORT"])
    force_login(client, app, staff_id)
    _grant_recent_auth(app, staff_id)
    page = client.get("/sync/quarantine")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(f"/sync/quarantine/{quarantine_id}/discard", data={"csrf_token": csrf})
    assert resp.status_code == 403


def test_discard_marks_discarded_and_stays_visible(app, client, seeded, signing_key):
    actor_id = make_staff(app, "quar-discard-actor@example.com", super_admin=True)
    license_id, device_id = _do_activation(app, client, actor_id)
    quarantine_id = _insert_quarantine_row(app, license_id, device_id, raw_payload={"bad": "row"})

    staff_id = make_staff(app, "quar-discard@example.com", super_admin=True)
    force_login(client, app, staff_id)
    _grant_recent_auth(app, staff_id)
    page = client.get("/sync/quarantine")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(f"/sync/quarantine/{quarantine_id}/discard", data={"csrf_token": csrf})
    assert resp.status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncQuarantineEvent

        row = db_session.get(SyncQuarantineEvent, quarantine_id)
        assert row.status == "DISCARDED"  # never deleted, still a real row
        assert row.resolved_at is not None
        assert row.resolved_by_staff_user_id == staff_id

    # Still visible under the DISCARDED filter.
    discarded_page = client.get("/sync/quarantine?status=DISCARDED")
    assert discarded_page.status_code == 200
    assert str(license_id)[:8].encode() in discarded_page.data
