"""Multi-device sync relay push/pull routes (Task 2 of the
multi-device-sync-foundation plan). Reuses the same signed-request fixture
pattern owner/tests/test_phase6_checkin_protocol.py uses for checkin.py --
real activation through the actual /api/licensing/v1/activations endpoint,
so these installations/device keys are exactly what production would create,
not hand-inserted rows.

Includes the fix-round tests added after coordinator review found: (1) no
nonce/timestamp replay protection plus an unsigned `since` query param made
a captured pull request a permanent, key-free read credential, and (2) a
concurrent duplicate-id push would surface an uncaught IntegrityError as a
raw 500 instead of a clean idempotent no-op."""
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


def _signed_body(private_key, installation_id, extra: dict) -> dict:
    """Every real request: installation_id + a fresh timestamp/nonce +
    whatever operation-specific fields (events, since) -- all signed
    together, so tampering with ANY field (including `since`) after the
    fact invalidates the signature."""
    body = {
        "installation_id": installation_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "nonce": uuid.uuid4().hex,
        **extra,
    }
    return sign_body(private_key, body)


def _push_body(private_key, installation_id, events):
    return _signed_body(private_key, installation_id, {"events": events})


def _pull_body(private_key, installation_id, since=0):
    return _signed_body(private_key, installation_id, {"since": since})


def _push(client, body):
    return client.post("/api/sync/v1/push", data=json.dumps(body), content_type="application/json")


def _pull(client, body):
    return client.get("/api/sync/v1/pull", data=json.dumps(body), content_type="application/json")


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
    body = {
        "installation_id": "00000000-0000-0000-0000-000000000000",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "nonce": uuid.uuid4().hex,
        "signature": "bogus",
    }
    resp = _push(client, body)
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INSTALLATION_NOT_FOUND"


def test_push_missing_required_field_rejected_400(client, seeded, signing_key):
    # No nonce/timestamp at all -- must fail closed with INVALID_REQUEST,
    # not silently proceed to installation lookup without replay protection.
    resp = _push(client, {"installation_id": "00000000-0000-0000-0000-000000000000", "signature": "bogus"})
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_REQUEST"


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
    pull_b = _pull(client, _pull_body(private_key_b, installation_b, since=0))
    assert pull_b.status_code == 200
    pull_b_data = pull_b.get_json()
    assert len(pull_b_data["events"]) == 1
    assert pull_b_data["events"][0]["id"] == event["id"]
    assert pull_b_data["cursor"] > 0

    # Device A pulls: does NOT see its own event.
    pull_a = _pull(client, _pull_body(private_key_a, installation_a, since=0))
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
    # response) -- a fresh push request (new nonce/timestamp, so it isn't
    # rejected as a nonce replay) carrying the same event id must be a
    # genuine no-op: stored == 0, no second row, no new seq assigned to the
    # existing row.
    second = _push(client, _push_body(private_key, installation_id, [event]))
    assert second.status_code == 200
    assert second.get_json() == {"stored": 0, "received": 1}

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        rows = db_session.query(SyncEvent).filter(SyncEvent.id == uuid.UUID(event["id"])).all()
        assert len(rows) == 1
        assert rows[0].seq == seq_after_first


def test_push_concurrent_duplicate_id_race_caught_not_500(app, client, seeded, signing_key, monkeypatch):
    """Directly forces the exact race window _store_events must survive:
    true OS-thread-level concurrency is nondeterministic to force reliably
    in a test, so this instead makes the existence pre-check itself report
    "not found" for one specific push -- exactly what a genuinely concurrent
    request's pre-check would observe if it ran before the other's INSERT
    committed -- while the row is, in fact, already committed underneath
    (as if a concurrent request had just won the race). The resulting
    flush() then hits a REAL Postgres primary-key uniqueness violation
    (not a mocked exception), proving the IntegrityError branch in
    _store_events is what actually catches it, not a happy-path illusion."""
    actor_id = make_staff(app, "sync-race@example.com")
    _, _, installation_id, private_key = _do_activation(app, client, actor_id)

    event = _make_event()
    first = _push(client, _push_body(private_key, installation_id, [event]))
    assert first.status_code == 200
    assert first.get_json() == {"stored": 1, "received": 1}

    from app.sync import routes as sync_routes

    real_get = sync_routes.db_session.get

    def _pretend_not_found(model, pk, *args, **kwargs):
        if model is sync_routes.SyncEvent:
            return None  # simulate the losing side of the race's pre-check
        return real_get(model, pk, *args, **kwargs)

    monkeypatch.setattr(sync_routes.db_session, "get", _pretend_not_found)

    second = _push(client, _push_body(private_key, installation_id, [event]))
    assert second.status_code == 200, second.get_json()  # NOT a 500
    assert second.get_json() == {"stored": 0, "received": 1}

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        rows = db_session.query(SyncEvent).filter(SyncEvent.id == uuid.UUID(event["id"])).all()
        assert len(rows) == 1  # still exactly one row -- no duplicate, no crash


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
    pull_other_license = _pull(client, _pull_body(key_2, installation_2, since=0))
    assert pull_other_license.status_code == 200
    assert pull_other_license.get_json()["events"] == []

    # Sanity: the sibling device on the SAME license does see it.
    pull_same_license = _pull(client, _pull_body(key_1b, installation_1b, since=0))
    assert pull_same_license.status_code == 200
    assert len(pull_same_license.get_json()["events"]) == 1


def test_pull_replayed_request_rejected_nonce_reused(app, client, seeded, signing_key):
    """A captured, valid signed pull request must not be replayable even
    once more with byte-identical content -- proves the nonce is actually
    consumed (not merely present-but-unchecked)."""
    actor_id = make_staff(app, "sync-replay@example.com")
    _, _, installation_id, private_key = _do_activation(app, client, actor_id)

    body = _pull_body(private_key, installation_id, since=0)

    first = _pull(client, body)
    assert first.status_code == 200

    replayed = _pull(client, body)  # attacker (or a buggy client) resends the exact same captured request
    assert replayed.status_code == 400
    assert replayed.get_json()["reason_code"] == "NONCE_REUSED"


def test_pull_captured_request_cannot_be_replayed_with_different_since(app, client, seeded, signing_key):
    """The vulnerability found in review: `since` must be part of the
    signed payload, not a free query parameter -- otherwise a captured
    request's `since` could be changed to 0 to walk a license's entire
    history without ever needing the device's private key again. Proves
    that tampering `since` post-signing (leaving the original signature
    untouched, since an attacker who merely captured the request never had
    the private key) is caught as a signature mismatch."""
    actor_id = make_staff(app, "sync-tamper@example.com")
    _, _, installation_id, private_key = _do_activation(app, client, actor_id)

    captured = _pull_body(private_key, installation_id, since=100)  # legit request the attacker captured
    tampered = dict(captured)
    tampered["since"] = 0  # attacker widens the read window; signature is now stale

    resp = _pull(client, tampered)
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_SIGNATURE"


def test_pull_missing_since_rejected_400(app, client, seeded, signing_key):
    actor_id = make_staff(app, "sync-nosince@example.com")
    _, _, installation_id, private_key = _do_activation(app, client, actor_id)

    body = sign_body(private_key, {
        "installation_id": installation_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "nonce": uuid.uuid4().hex,
    })  # no "since" field at all
    resp = _pull(client, body)
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_SINCE"


def test_push_acquires_per_license_advisory_lock_before_storing_events(app, client, seeded, signing_key, monkeypatch):
    """Unit-level wiring proof (fast, deterministic, no real Postgres
    concurrency needed for this one -- that's what
    test_sync_ordering_concurrency.py's real-connection tests are for):
    push() must call _lock_license_stream exactly once, with the license_id
    _authenticate() resolved from the verified installation (never from
    request body input), and it must do so BEFORE _store_events runs.
    Spies on both functions and records call order the same way
    test_push_concurrent_duplicate_id_race_caught_not_500 above spies on
    db_session.get."""
    actor_id = make_staff(app, "sync-lockwiring@example.com")
    license_id, _, installation_id, private_key = _do_activation(app, client, actor_id)

    from app.sync import routes as sync_routes

    call_order = []
    lock_calls = []
    real_store_events = sync_routes._store_events

    def _spy_lock(passed_license_id):
        lock_calls.append(passed_license_id)
        call_order.append("lock")

    def _spy_store_events(events, passed_license_id, device_id):
        call_order.append("store")
        return real_store_events(events, passed_license_id, device_id)

    monkeypatch.setattr(sync_routes, "_lock_license_stream", _spy_lock)
    monkeypatch.setattr(sync_routes, "_store_events", _spy_store_events)

    event = _make_event()
    resp = _push(client, _push_body(private_key, installation_id, [event]))
    assert resp.status_code == 200, resp.get_json()

    assert lock_calls == [license_id]  # exactly once, with the AUTHENTICATED license_id
    assert call_order == ["lock", "store"]  # lock acquired strictly before storage


def test_push_events_not_a_list_rejected_400(app, client, seeded, signing_key):
    """The boundary that stays a hard 400 even after Phase 5 prerequisite
    #3's quarantine change: a structurally malformed BATCH (not a list at
    all) is caught in push() itself, before _store_events (and therefore
    before any per-event quarantine logic) ever runs -- quarantine only
    ever applies to one bad EVENT inside an otherwise well-shaped batch,
    never to a batch that isn't a list in the first place."""
    actor_id = make_staff(app, "sync-notalist@example.com")
    _, _, installation_id, private_key = _do_activation(app, client, actor_id)

    body = _push_body(private_key, installation_id, "not-a-list")
    resp = _push(client, body)
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_BATCH"

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent, SyncQuarantineEvent

        assert db_session.query(SyncEvent).count() == 0
        assert db_session.query(SyncQuarantineEvent).count() == 0  # never even reached quarantine logic


def test_push_event_type_too_long_entity_type_is_quarantined_not_400(app, client, seeded, signing_key):
    """Phase 5 prerequisite #3 (docs/launch-readiness/phase5-prerequisites.md
    section 3) deliberately changed this test's expected outcome -- this is
    a DESIGN CHANGE, not a weakened assertion. Before: a single malformed
    event rolled back the ENTIRE batch and returned 400 INVALID_EVENT,
    documented (until this pass) in InvalidEventError's own docstring as
    "one malformed row stops that shop syncing forever" once Phase 5 widens
    the sync allowlist. Now: the malformed event is quarantined
    (owner_sync_quarantine) and skipped; the push still succeeds (200),
    with `stored` correctly reflecting that THIS event was not stored. See
    app/sync/routes.py::_store_events's own docstring for the full
    reasoning, and app/models/sync.py::SyncQuarantineEvent for the schema.
    InvalidEventError's docstring documents the OLD behavior explicitly as
    history, not silently drops it."""
    actor_id = make_staff(app, "sync-longfield@example.com")
    license_id, _, installation_id, private_key = _do_activation(app, client, actor_id)

    bad_event = _make_event()
    bad_event["entity_type"] = "x" * 65  # exceeds SyncEvent.entity_type's String(64) column
    resp = _push(client, _push_body(private_key, installation_id, [bad_event]))
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json() == {"stored": 0, "received": 1}

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent, SyncQuarantineEvent

        assert db_session.query(SyncEvent).count() == 0  # never stored as a real event

        quarantined = db_session.query(SyncQuarantineEvent).filter(
            SyncQuarantineEvent.device_id == uuid.UUID(installation_id)
        ).all()
        assert len(quarantined) == 1
        row = quarantined[0]
        assert row.status == "PENDING"
        assert row.batch_index == 0
        assert row.rejection_reason == "entity_type"  # _build_event's own InvalidEventError message
        assert row.raw_payload == bad_event  # verbatim, not truncated/modified
        assert str(row.license_id) == str(license_id)


def test_push_mixed_batch_good_events_survive_one_bad_one(app, client, seeded, signing_key):
    """The core mutation-proof of the skip-and-surface trade: a THREE-event
    batch where the MIDDLE event is malformed must still store BOTH good
    events (before and after the bad one in batch order), quarantine only
    the bad one at its correct batch_index, and return 200 -- proving
    _store_events genuinely continues the loop past a bad event rather than
    merely catching the exception and discarding the rest of the batch too
    (which a naive `except: return` at the top of the function would do)."""
    actor_id = make_staff(app, "sync-mixedbatch@example.com")
    _, _, installation_id, private_key = _do_activation(app, client, actor_id)

    good_1 = _make_event()
    bad = _make_event()
    bad["event_type"] = "not_a_real_event_type"  # fails _ALLOWED_EVENT_TYPES
    good_2 = _make_event()

    resp = _push(client, _push_body(private_key, installation_id, [good_1, bad, good_2]))
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json() == {"stored": 2, "received": 3}

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent, SyncQuarantineEvent

        stored_ids = {str(r.id) for r in db_session.query(SyncEvent).all()}
        assert stored_ids == {good_1["id"], good_2["id"]}

        quarantined = db_session.query(SyncQuarantineEvent).all()
        assert len(quarantined) == 1
        assert quarantined[0].batch_index == 1  # the middle item, exactly
        assert quarantined[0].rejection_reason == "event_type"
        assert quarantined[0].raw_payload == bad


def test_push_nul_byte_payload_hits_real_dataerror_and_is_quarantined(app, client, seeded, signing_key):
    """A NUL byte (\\x00) embedded in a JSONB string value is valid
    Python/JSON but Postgres genuinely rejects it at the DB layer
    (untranslatable character) -- a REAL sqlalchemy.exc.DataError from a
    live Postgres flush, not a mocked exception, proving the DataError
    branch in _store_events (distinct from the InvalidEventError branch
    above, which never reaches the database at all) actually quarantines
    and continues rather than propagating and rolling back the batch.

    Also the real, live-Postgres-discovered case that forced
    _quarantine_event's own sanitize-and-retry fallback to exist: the
    poison payload's NUL byte is what triggers the FIRST DataError, but
    the raw payload being quarantined VERBATIM contains that same NUL
    byte -- so the naive "just INSERT the verbatim payload into
    owner_sync_quarantine" would hit the IDENTICAL DataError a second
    time, on the quarantine write itself, and (before the fix) that
    second, unhandled DataError propagated all the way out to push()'s
    own except clause and rolled back the WHOLE batch with a 400 --
    exactly the wedged-shop failure this prerequisite exists to prevent,
    reached through the escape hatch instead of the original path. This
    test is what caught that for real."""
    actor_id = make_staff(app, "sync-nulbyte@example.com")
    _, _, installation_id, private_key = _do_activation(app, client, actor_id)

    good = _make_event()
    poison = _make_event()
    poison["payload"] = {"name": "bad\x00null"}

    resp = _push(client, _push_body(private_key, installation_id, [poison, good]))
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json() == {"stored": 1, "received": 2}

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent, SyncQuarantineEvent

        assert db_session.query(SyncEvent).count() == 1
        assert db_session.get(SyncEvent, uuid.UUID(good["id"])) is not None

        quarantined = db_session.query(SyncQuarantineEvent).all()
        assert len(quarantined) == 1
        assert quarantined[0].batch_index == 0
        # NOT byte-identical to `poison` here, on purpose: the NUL byte is
        # the one thing Postgres cannot store verbatim in ANY text-based
        # column (see _sanitize_nul_bytes' docstring) -- it is escaped to
        # its visible form, everything else about the payload survives
        # unchanged, and the substitution is disclosed in the reason.
        stored_payload = quarantined[0].raw_payload
        assert stored_payload["payload"]["name"] == "bad\\u0000null"
        assert stored_payload["id"] == poison["id"]
        assert stored_payload["entity_type"] == poison["entity_type"]
        assert "\x00" not in json.dumps(stored_payload)  # genuinely storable, no lingering NUL
        assert "not byte-identical" in quarantined[0].rejection_reason
        # A driver-level message, not one of _build_event's own short
        # strings -- proves this went through the DataError branch.
        assert "entity_type" not in quarantined[0].rejection_reason.split("[NOTE:")[0]
        assert "event_type" not in quarantined[0].rejection_reason.split("[NOTE:")[0]


def test_push_second_batch_after_quarantine_does_not_rewedge_licence(app, client, seeded, signing_key):
    """Mutation-proof #5: this is the entire point of quarantine-and-skip --
    a SECOND push, after one that contained a poison event, must succeed
    normally (the license is never permanently wedged the way the old
    roll-back-the-whole-batch behavior would have made it)."""
    actor_id = make_staff(app, "sync-rewedge@example.com")
    _, _, installation_id, private_key = _do_activation(app, client, actor_id)

    bad_event = _make_event()
    bad_event["entity_type"] = "x" * 65
    first = _push(client, _push_body(private_key, installation_id, [bad_event]))
    assert first.status_code == 200, first.get_json()
    assert first.get_json() == {"stored": 0, "received": 1}

    # A client that retries a batch containing the SAME poison event again
    # (the realistic client-outbox-retry scenario the design doc describes)
    # must still succeed, not fail identically forever.
    retry_same_bad_event = _push(client, _push_body(private_key, installation_id, [bad_event]))
    assert retry_same_bad_event.status_code == 200, retry_same_bad_event.get_json()

    good_event = _make_event()
    second = _push(client, _push_body(private_key, installation_id, [good_event]))
    assert second.status_code == 200, second.get_json()
    assert second.get_json() == {"stored": 1, "received": 1}

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent, SyncQuarantineEvent

        assert db_session.get(SyncEvent, uuid.UUID(good_event["id"])) is not None
        # Each push of the same malformed event quarantines it again
        # (quarantine has no id-based idempotency of its own -- every
        # rejected attempt is its own visible record) -- two rows here,
        # from the two identical pushes above, is the correct, expected
        # count, not a bug.
        quarantined = db_session.query(SyncQuarantineEvent).filter(
            SyncQuarantineEvent.device_id == uuid.UUID(installation_id)
        ).all()
        assert len(quarantined) == 2


def test_pull_persists_device_cursor_push_does_not(app, client, seeded, signing_key):
    """Phase 5 prerequisite #2: pull() must persist SyncDeviceCursor;
    push() must NOT (see SyncDeviceCursor's own docstring for why a device
    never needs to "acknowledge" events it authored itself via push)."""
    actor_id = make_staff(app, "sync-cursorpersist@example.com")
    license_id, full_key, installation_a, key_a = _do_activation(app, client, actor_id, device_limit=2)
    _, _, installation_b, key_b = _do_activation(app, client, actor_id, license_id=license_id, full_key=full_key, device_limit=2)

    push_resp = _push(client, _push_body(key_a, installation_a, [_make_event()]))
    assert push_resp.status_code == 200

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncDeviceCursor

        # push() alone must not create a cursor row for A.
        assert db_session.get(SyncDeviceCursor, uuid.UUID(installation_a)) is None

    pull_resp = _pull(client, _pull_body(key_b, installation_b, since=0))
    assert pull_resp.status_code == 200
    returned_cursor = pull_resp.get_json()["cursor"]
    assert returned_cursor > 0

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncDeviceCursor

        row = db_session.get(SyncDeviceCursor, uuid.UUID(installation_b))
        assert row is not None
        assert row.last_acked_seq == returned_cursor
        assert str(row.license_id) == str(license_id)


def test_pull_persists_cursor_even_with_zero_new_rows(app, client, seeded, signing_key):
    """A device catching itself up (since=<current max seq>, nothing NEW to
    receive) must still get its cursor recorded -- pruning depends on this
    exact mechanism for a push-only device to ever have a non-zero cursor
    at all (see SyncDeviceCursor's own docstring)."""
    actor_id = make_staff(app, "sync-cursorzero@example.com")
    license_id, full_key, installation_a, key_a = _do_activation(app, client, actor_id, device_limit=2)
    _, _, installation_b, key_b = _do_activation(app, client, actor_id, license_id=license_id, full_key=full_key, device_limit=2)

    push_resp = _push(client, _push_body(key_a, installation_a, [_make_event()]))
    assert push_resp.status_code == 200

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        max_seq = db_session.query(SyncEvent).one().seq

    # A pulls with since=max_seq: it authored the only event, so pull()
    # excludes it (device_id != installation.id) -- zero rows returned --
    # but the cursor must still be recorded at max_seq.
    pull_a = _pull(client, _pull_body(key_a, installation_a, since=max_seq))
    assert pull_a.status_code == 200
    assert pull_a.get_json()["events"] == []
    assert pull_a.get_json()["cursor"] == max_seq

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncDeviceCursor

        row = db_session.get(SyncDeviceCursor, uuid.UUID(installation_a))
        assert row is not None
        assert row.last_acked_seq == max_seq


def test_pull_cursor_never_regresses(app, client, seeded, signing_key):
    """_advance_device_cursor's GREATEST-style guard: a lower value must
    never overwrite an already-higher persisted cursor. Exercises the
    helper directly (rather than trying to construct a real out-of-order
    network race) since the guard's contract is unconditional regardless of
    how a lower value could arrive -- see the helper's own docstring."""
    actor_id = make_staff(app, "sync-cursorregress@example.com")
    license_id, full_key, installation_id, private_key = _do_activation(app, client, actor_id)

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncDeviceCursor
        from app.sync.routes import _advance_device_cursor

        _advance_device_cursor(uuid.UUID(installation_id), license_id, 100)
        db_session.commit()
        _advance_device_cursor(uuid.UUID(installation_id), license_id, 5)  # attempted regression
        db_session.commit()

        row = db_session.get(SyncDeviceCursor, uuid.UUID(installation_id))
        assert row.last_acked_seq == 100  # unchanged, not regressed to 5
