"""Multi-device sync relay push/pull routes (Task 2 of the
multi-device-sync-foundation plan). Reuses the same signed-request fixture
pattern owner/tests/test_phase6_checkin_protocol.py uses for checkin.py --
real activation through the actual /api/licensing/v1/activations endpoint,
so these installations/device keys are exactly what production would create,
not hand-inserted rows."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff, sign_body


def _activate(client, body):
    return client.post("/api/licensing/v1/activations", data=json.dumps(body), content_type="application/json")


def _do_activation(app, client, actor_id, license_id=None, full_key=None, device_limit=1):
    if license_id is None or full_key is None:
        license_id, full_key = make_license(app, actor_id, device_limit=device_limit)
    private_key = make_device_keypair()
    resp = _activate(client, build_activation_body(private_key, full_key=full_key, installation_id=str(uuid.uuid4())))
    assert resp.status_code == 200, resp.get_json()
    installation_id = resp.get_json()["installation_id"]
    return license_id, full_key, installation_id, private_key


def _push_body(private_key, installation_id, events):
    return sign_body(private_key, {"installation_id": installation_id, "events": events})


def _pull_body(private_key, installation_id):
    return sign_body(private_key, {"installation_id": installation_id})


def _push(client, body):
    return client.post("/api/sync/v1/push", data=json.dumps(body), content_type="application/json")


def _pull(client, body, since=0):
    return client.get(f"/api/sync/v1/pull?since={since}", data=json.dumps(body), content_type="application/json")


def _make_event(entity_id=None):
    return {
        "id": str(uuid.uuid4()),
        "entity_type": "category",
        "entity_id": str(entity_id or uuid.uuid4()),
        "event_type": "create",
        "payload": {"name": "Test Category"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def test_push_unknown_installation_rejected_400(client, seeded, signing_key):
    resp = _push(client, {"installation_id": "00000000-0000-0000-0000-000000000000", "signature": "bogus"})
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INSTALLATION_NOT_FOUND"


def test_push_bad_signature_rejected_400(app, client, seeded, signing_key):
    actor_id = make_staff(app, "sync-badsig@example.com")
    _, _, installation_id, private_key = _do_activation(app, client, actor_id)
    wrong_key = make_device_keypair()

    body = _push_body(wrong_key, installation_id, [])
    resp = _push(client, body)
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_SIGNATURE"


def test_push_oversized_batch_rejected_400_not_truncated(app, client, seeded, signing_key):
    actor_id = make_staff(app, "sync-batch@example.com")
    _, _, installation_id, private_key = _do_activation(app, client, actor_id)

    events = [_make_event() for _ in range(201)]
    body = _push_body(private_key, installation_id, events)
    resp = _push(client, body)
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_BATCH"

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        assert db_session.query(SyncEvent).count() == 0  # rejected outright, nothing silently stored


def test_push_then_pull_real_signed_round_trip(app, client, seeded, signing_key):
    """The core positive path: device A (real activation, real Ed25519 key)
    pushes a real signed event; device B, activated against the SAME
    license, pulls and receives it; device A's own pull excludes its own
    event. Proves auth, license_id scoping-from-installation (never from
    body), and the own-device pull exclusion all work end to end against a
    real Postgres-backed Flask test client -- not a unit-level mock."""
    actor_id = make_staff(app, "sync-roundtrip@example.com")
    license_id, full_key, installation_a, private_key_a = _do_activation(app, client, actor_id, device_limit=2)
    _, _, installation_b, private_key_b = _do_activation(app, client, actor_id, license_id=license_id, full_key=full_key, device_limit=2)

    event = _make_event()
    push_resp = _push(client, _push_body(private_key_a, installation_a, [event]))
    assert push_resp.status_code == 200, push_resp.get_json()
    push_data = push_resp.get_json()
    assert push_data == {"stored": 1, "received": 1}

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        row = db_session.get(SyncEvent, uuid.UUID(event["id"]))
        assert row is not None
        assert str(row.license_id) == str(license_id)
        assert str(row.device_id) == installation_a
        assert row.entity_type == "category"
        assert row.payload == {"name": "Test Category"}

    # Device B (same license) pulls: sees device A's event.
    pull_b = _pull(client, _pull_body(private_key_b, installation_b))
    assert pull_b.status_code == 200
    pull_b_data = pull_b.get_json()
    assert len(pull_b_data["events"]) == 1
    assert pull_b_data["events"][0]["id"] == event["id"]
    assert pull_b_data["cursor"] > 0

    # Device A pulls: does NOT see its own event.
    pull_a = _pull(client, _pull_body(private_key_a, installation_a))
    assert pull_a.status_code == 200
    assert pull_a.get_json()["events"] == []


def test_push_idempotent_retry_no_duplicate_no_new_seq(app, client, seeded, signing_key):
    actor_id = make_staff(app, "sync-idem@example.com")
    _, _, installation_id, private_key = _do_activation(app, client, actor_id)

    event = _make_event()
    first = _push(client, _push_body(private_key, installation_id, [event]))
    assert first.status_code == 200
    assert first.get_json() == {"stored": 1, "received": 1}

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        seq_after_first = db_session.get(SyncEvent, uuid.UUID(event["id"])).seq

    # Retry the exact same event id (e.g. a client retrying after a dropped
    # response) -- must be a genuine no-op: stored == 0, no second row, no
    # new seq assigned to the existing row.
    second = _push(client, _push_body(private_key, installation_id, [event]))
    assert second.status_code == 200
    assert second.get_json() == {"stored": 0, "received": 1}

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        rows = db_session.query(SyncEvent).filter(SyncEvent.id == uuid.UUID(event["id"])).all()
        assert len(rows) == 1
        assert rows[0].seq == seq_after_first


def test_pull_license_scoping_never_leaks_across_licenses(app, client, seeded, signing_key):
    actor_id = make_staff(app, "sync-scope@example.com")
    license_1, full_key_1, installation_1, key_1 = _do_activation(app, client, actor_id, device_limit=2)
    _, _, installation_1b, key_1b = _do_activation(app, client, actor_id, license_id=license_1, full_key=full_key_1, device_limit=2)
    _, _, installation_2, key_2 = _do_activation(app, client, actor_id, device_limit=1)  # separate license

    event = _make_event()
    push_resp = _push(client, _push_body(key_1, installation_1, [event]))
    assert push_resp.status_code == 200

    # A device on a DIFFERENT license must never see it, even though it is
    # a real, validly-authenticated installation.
    pull_other_license = _pull(client, _pull_body(key_2, installation_2))
    assert pull_other_license.status_code == 200
    assert pull_other_license.get_json()["events"] == []

    # Sanity: the sibling device on the SAME license does see it.
    pull_same_license = _pull(client, _pull_body(key_1b, installation_1b))
    assert pull_same_license.status_code == 200
    assert len(pull_same_license.get_json()["events"]) == 1
