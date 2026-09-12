"""Phase 5 prerequisite #2 (docs/launch-readiness/phase5-prerequisites.md
section 2) -- owner_sync_events pruning below the slowest ACTIVE device's
cursor. Real activation/push/pull through the actual routes (same pattern
test_sync_routes.py uses), never hand-inserted SyncEvent/SyncDeviceCursor
rows with a license/device relationship that wouldn't exist in production.

The two cases the design doc calls out as most likely to be got wrong get
their own dedicated, explicitly-named tests: a license with ZERO active
devices (must prune nothing), and a REVOKED device (must not pin the
table forever)."""
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
    body = {
        "installation_id": installation_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "nonce": uuid.uuid4().hex,
        **extra,
    }
    return sign_body(private_key, body)


def _push(client, private_key, installation_id, events):
    return client.post(
        "/api/sync/v1/push", data=json.dumps(_signed_body(private_key, installation_id, {"events": events})),
        content_type="application/json",
    )


def _pull(client, private_key, installation_id, since=0):
    return client.get(
        "/api/sync/v1/pull", data=json.dumps(_signed_body(private_key, installation_id, {"since": since})),
        content_type="application/json",
    )


def _make_event(entity_id=None):
    return {
        "id": str(uuid.uuid4()),
        "entity_type": "category",
        "entity_id": str(entity_id or uuid.uuid4()),
        "event_type": "create",
        "payload": {"name": "Test Category"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _event_count(app, license_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        return db_session.query(SyncEvent).filter(SyncEvent.license_id == license_id).count()


def _push_then_fully_catch_up_both_devices(client, key_a, installation_a, key_b, installation_b, n_events):
    """Pushes N events via device A, then has BOTH devices pull to the
    current max seq -- B actually receives them (a different device than
    the author); A receives nothing back (pull() excludes a device's own
    events) but still records its own cursor at that seq (see
    SyncDeviceCursor's docstring). This is what it takes for EVERY
    currently-pushed event to become eligible for pruning: every active
    device's persisted cursor, A's included, must reach the max seq."""
    for _ in range(n_events):
        assert _push(client, key_a, installation_a, [_make_event()]).status_code == 200
    pull_b = _pull(client, key_b, installation_b, since=0)
    assert pull_b.status_code == 200
    assert len(pull_b.get_json()["events"]) == n_events
    max_seq = pull_b.get_json()["cursor"]
    pull_a = _pull(client, key_a, installation_a, since=max_seq)
    assert pull_a.status_code == 200
    assert pull_a.get_json()["cursor"] == max_seq
    return max_seq


def test_zero_active_devices_prunes_nothing(app, client, seeded, signing_key):
    """The design doc's own words: "A licence with ZERO active devices
    prunes NOTHING... this is the rule most likely to be got wrong; test
    it explicitly." A license that was activated, pushed an event, and
    then had its only device SUSPENDED must retain every event -- there is
    no one left to lose history for, but pruning must still refuse, not
    "safely" delete it.

    THE DEVICE IS DELIBERATELY CAUGHT FULLY UP BEFORE IT IS SUSPENDED, and
    that detail is the whole discriminating power of this test -- do not
    "simplify" it away. An earlier version of this test suspended a device
    that had never pulled, so it had no SyncDeviceCursor row and therefore
    an implied watermark of 0; `seq` is a Postgres Identity starting at 1,
    so `WHERE seq <= 0` matches nothing and the test passed *for the wrong
    reason* under at least two genuinely broken implementations (verified
    by mutation, not assumed):

      * `return 0` instead of `return None` for the empty-device case, and
      * dropping the `Installation.status.in_(...)` filter from
        _active_device_ids entirely, so a SUSPENDED device still counts as
        active.

    Both are silently wrong and both went green. Now that the device is
    caught up to max_seq first, EITHER of those mutations produces a real
    numeric watermark at max_seq and deletes the event -- so the row-count
    assertion below genuinely fails.

    The `_watermark_for_license(...) is None` assertion is the other half:
    it asserts THE CHECK RAN, not merely that the outcome happened to come
    out right. `None` and `0` are not interchangeable here (see
    _watermark_for_license's own docstring) and only a direct assertion on
    the returned value can tell them apart -- a row count cannot.

    MUTATION PROOF #1: comment out the `if not active_ids: return None`
    early-return in pruning.py::_watermark_for_license and this test
    fails: with zero active devices, `_active_device_ids` returns [], and
    the caller must never fall through to computing SOME numeric watermark
    from an empty device set."""
    actor_id = make_staff(app, "prune-zero-devices@example.com")
    license_id, full_key, installation_id, private_key = _do_activation(app, client, actor_id)

    push_resp = _push(client, private_key, installation_id, [_make_event()])
    assert push_resp.status_code == 200, push_resp.get_json()
    assert _event_count(app, license_id) == 1

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        max_seq = db_session.query(SyncEvent).filter(SyncEvent.license_id == license_id).one().seq

    # Catch the device fully up FIRST (see the docstring above): it now has
    # a real, persisted cursor at max_seq, so "nothing was deleted" can no
    # longer be produced by an accidental watermark of 0.
    pull_resp = _pull(client, private_key, installation_id, since=max_seq)
    assert pull_resp.status_code == 200
    assert pull_resp.get_json()["cursor"] == max_seq

    with app.app_context():
        from app.extensions import db_session
        from app.installations.services import transition_installation
        from app.models.installations import Installation
        from app.models.sync import SyncDeviceCursor
        from app.sync.pruning import _watermark_for_license, prune_owner_sync_events

        cursor_row = db_session.get(SyncDeviceCursor, uuid.UUID(installation_id))
        assert cursor_row is not None and cursor_row.last_acked_seq == max_seq

        installation = db_session.get(Installation, uuid.UUID(installation_id))
        transition_installation(installation, "SUSPENDED", actor_id)
        db_session.commit()

        # Asserts the CHECK RAN, not just that the outcome looked right:
        # None means "this license is not prunable at all on this run",
        # which is categorically different from a computed 0.
        assert _watermark_for_license(license_id) is None

        report = prune_owner_sync_events(dry_run=False)

    assert str(license_id) not in report.per_license_deleted  # never even considered
    assert _event_count(app, license_id) == 1  # nothing deleted


def test_stale_active_device_blocks_everything_after_it(app, client, seeded, signing_key):
    """MUTATION PROOF #2: two active devices, A pushes THREE events, B
    pulls only the FIRST one and stops (simulating "went offline"). A also
    catches its own cursor up to the max seq (so the watermark is driven
    by B alone, not accidentally by A never having a cursor row at all).
    Pruning must delete NOTHING beyond B's own acknowledged point -- not
    even the second event, which B has not yet seen."""
    actor_id = make_staff(app, "prune-stale-device@example.com")
    license_id, full_key, installation_a, key_a = _do_activation(app, client, actor_id, device_limit=2)
    _, _, installation_b, key_b = _do_activation(app, client, actor_id, license_id=license_id, full_key=full_key, device_limit=2)

    assert _push(client, key_a, installation_a, [_make_event()]).status_code == 200
    pull_b_first = _pull(client, key_b, installation_b, since=0)
    assert pull_b_first.status_code == 200
    first_seq = pull_b_first.get_json()["cursor"]

    assert _push(client, key_a, installation_a, [_make_event()]).status_code == 200
    assert _push(client, key_a, installation_a, [_make_event()]).status_code == 200
    # B never pulls again from here -- stale/offline. A catches its own
    # cursor up so the watermark is driven by B's stale cursor, not by A
    # having no cursor row at all.
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        max_seq = max(r.seq for r in db_session.query(SyncEvent).filter(SyncEvent.license_id == license_id).all())
    pull_a = _pull(client, key_a, installation_a, since=max_seq)
    assert pull_a.status_code == 200
    assert pull_a.get_json()["cursor"] == max_seq

    with app.app_context():
        from app.sync.pruning import prune_owner_sync_events

        report = prune_owner_sync_events(dry_run=False)

    # Watermark = min(A=max_seq, B=first_seq) = first_seq -- only the FIRST
    # event (seq <= first_seq) is prunable; the second and third (which B
    # has never acknowledged) must survive untouched.
    assert report.per_license_deleted.get(str(license_id), 0) == 1
    assert _event_count(app, license_id) == 2

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        remaining_seqs = sorted(r.seq for r in db_session.query(SyncEvent).filter(SyncEvent.license_id == license_id).all())
    assert len(remaining_seqs) == 2
    assert remaining_seqs[0] > first_seq  # nothing at or below B's stale (unacknowledged-past) cursor was deleted


def test_revoked_device_excluded_does_not_pin_table_forever(app, client, seeded, signing_key):
    """MUTATION PROOF #3: a REVOKED device must not count toward "active
    devices" -- otherwise one revoked terminal that will never pull again
    pins the whole license's event history forever. Two devices: A pushes
    and catches its own cursor up to the max seq; B never pulls and then
    gets its device key revoked. Pruning must proceed using ONLY A's
    cursor -- B's absence (never having pulled, now revoked) must not
    block pruning at all."""
    actor_id = make_staff(app, "prune-revoked@example.com")
    license_id, full_key, installation_a, key_a = _do_activation(app, client, actor_id, device_limit=2)
    _, _, installation_b, key_b = _do_activation(app, client, actor_id, license_id=license_id, full_key=full_key, device_limit=2)

    assert _push(client, key_a, installation_a, [_make_event()]).status_code == 200
    assert _event_count(app, license_id) == 1

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        max_seq = db_session.query(SyncEvent).filter(SyncEvent.license_id == license_id).one().seq
    pull_a = _pull(client, key_a, installation_a, since=max_seq)
    assert pull_a.status_code == 200
    assert pull_a.get_json()["cursor"] == max_seq

    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service.device_identity import get_active_device_key, revoke_device_key
        from app.sync.pruning import prune_owner_sync_events

        device_key_b = get_active_device_key(uuid.UUID(installation_b))
        revoke_device_key(device_key_b)
        db_session.commit()

        report = prune_owner_sync_events(dry_run=False)

    # Only A remains active; A's cursor is caught up to max_seq, so
    # everything is prunable -- proves B's revoked, never-pulled state did
    # NOT block pruning (the bug this test guards against: if revocation
    # were not honored, B would have no cursor row -> watermark 0 ->
    # nothing deleted).
    assert report.per_license_deleted.get(str(license_id), 0) == 1
    assert _event_count(app, license_id) == 0


def test_active_device_that_never_pulled_blocks_everything(app, client, seeded, signing_key):
    """A device with NO SyncDeviceCursor row at all must count as watermark
    0 ("nothing acknowledged yet"), NEVER as "not counted" -- the rule
    pruning.py::_watermark_for_license's docstring states explicitly and
    which nothing else in this file exercised.

    This is the complement of test_revoked_device_excluded_does_not_pin_
    table_forever above, and the pair only works as a pair: that test has a
    cursor-less device that MUST be ignored (because it is revoked), this
    one has a cursor-less device that MUST be honoured (because it is
    active). A single implementation cannot satisfy both by accident --
    it has to actually distinguish "revoked" from "just hasn't pulled yet".

    Concretely: device B is activated and fully ACTIVE but has never
    pulled, so it has no cursor row. Device A has pushed and caught itself
    up to max_seq. If _watermark_for_license computed its MIN over only the
    devices that HAVE cursor rows (i.e. `min(acked_by_device.values())`
    rather than `min(acked_by_device.get(d, 0) for d in active_ids)`), B
    would silently drop out of the calculation, the watermark would be A's
    max_seq, and B's entire unseen backlog would be deleted before B ever
    got a chance to pull it -- the exact "resync into a hole with no error"
    failure the design doc says has no salvage after the fact. Verified by
    mutation: that substitution passes every OTHER test in this file."""
    actor_id = make_staff(app, "prune-neverpulled@example.com")
    license_id, full_key, installation_a, key_a = _do_activation(app, client, actor_id, device_limit=2)
    _, _, installation_b, key_b = _do_activation(app, client, actor_id, license_id=license_id, full_key=full_key, device_limit=2)

    assert _push(client, key_a, installation_a, [_make_event()]).status_code == 200
    assert _event_count(app, license_id) == 1

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        max_seq = db_session.query(SyncEvent).filter(SyncEvent.license_id == license_id).one().seq

    pull_a = _pull(client, key_a, installation_a, since=max_seq)
    assert pull_a.status_code == 200
    assert pull_a.get_json()["cursor"] == max_seq

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncDeviceCursor
        from app.sync.pruning import _watermark_for_license, prune_owner_sync_events

        # The precondition this test is built on: A has a cursor, B has none.
        assert db_session.get(SyncDeviceCursor, uuid.UUID(installation_a)) is not None
        assert db_session.get(SyncDeviceCursor, uuid.UUID(installation_b)) is None

        # Asserts THE CHECK RAN: the watermark must be 0 (B's implied
        # "nothing acknowledged"), not A's max_seq.
        assert _watermark_for_license(license_id) == 0

        report = prune_owner_sync_events(dry_run=False)

    assert str(license_id) not in report.per_license_deleted
    assert _event_count(app, license_id) == 1  # B has never pulled -- nothing is prunable


def test_prunes_below_slowest_active_devices_cursor(app, client, seeded, signing_key):
    """Two devices, A and B, both catch up. Pruning must delete events
    at/below the SLOWER of the two cursors, and nothing above it."""
    actor_id = make_staff(app, "prune-slowest@example.com")
    license_id, full_key, installation_a, key_a = _do_activation(app, client, actor_id, device_limit=2)
    _, _, installation_b, key_b = _do_activation(app, client, actor_id, license_id=license_id, full_key=full_key, device_limit=2)

    assert _push(client, key_a, installation_a, [_make_event()]).status_code == 200
    assert _push(client, key_a, installation_a, [_make_event()]).status_code == 200
    pull_b = _pull(client, key_b, installation_b, since=0)
    assert pull_b.status_code == 200
    assert len(pull_b.get_json()["events"]) == 2
    second_seq = pull_b.get_json()["cursor"]

    assert _push(client, key_a, installation_a, [_make_event()]).status_code == 200
    assert _event_count(app, license_id) == 3

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        third_seq = max(r.seq for r in db_session.query(SyncEvent).filter(SyncEvent.license_id == license_id).all())
    assert third_seq > second_seq

    pull_a = _pull(client, key_a, installation_a, since=third_seq)
    assert pull_a.status_code == 200
    assert pull_a.get_json()["cursor"] == third_seq

    with app.app_context():
        from app.sync.pruning import prune_owner_sync_events

        report = prune_owner_sync_events(dry_run=False)

    assert report.per_license_deleted[str(license_id)] == 2  # first and second seq, not third
    assert _event_count(app, license_id) == 1

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        remaining = db_session.query(SyncEvent).filter(SyncEvent.license_id == license_id).all()
        assert remaining[0].seq == third_seq


def test_dry_run_reports_without_deleting(app, client, seeded, signing_key):
    actor_id = make_staff(app, "prune-dryrun@example.com")
    license_id, full_key, installation_a, key_a = _do_activation(app, client, actor_id, device_limit=2)
    _, _, installation_b, key_b = _do_activation(app, client, actor_id, license_id=license_id, full_key=full_key, device_limit=2)

    _push_then_fully_catch_up_both_devices(client, key_a, installation_a, key_b, installation_b, 1)

    with app.app_context():
        from app.sync.pruning import prune_owner_sync_events

        report = prune_owner_sync_events(dry_run=True)

    assert report.dry_run is True
    assert report.per_license_deleted.get(str(license_id), 0) == 1  # reports what WOULD be deleted
    assert _event_count(app, license_id) == 1  # nothing actually deleted


def test_bounded_batching_never_one_unbounded_delete(app, client, seeded, signing_key, monkeypatch):
    """MUTATION PROOF #6: batch_size=1 with 5 prunable events forces 5
    separate DELETE round-trips. Proven two ways: (a) the final result is
    still fully correct (all 5 gone), and (b) a spy on db_session.execute
    counts exactly 5 DELETE statements issued -- a single unbounded
    `DELETE ... WHERE seq <= watermark` would instead issue exactly ONE,
    which this spy would catch."""
    actor_id = make_staff(app, "prune-batches@example.com")
    license_id, full_key, installation_a, key_a = _do_activation(app, client, actor_id, device_limit=2)
    _, _, installation_b, key_b = _do_activation(app, client, actor_id, license_id=license_id, full_key=full_key, device_limit=2)

    _push_then_fully_catch_up_both_devices(client, key_a, installation_a, key_b, installation_b, 5)

    with app.app_context():
        from app.extensions import db_session
        from app.sync import pruning as pruning_module

        real_execute = db_session.execute
        delete_statement_count = 0

        def _spy_execute(statement, *args, **kwargs):
            nonlocal delete_statement_count
            if type(statement).__name__ == "Delete":
                delete_statement_count += 1
            return real_execute(statement, *args, **kwargs)

        monkeypatch.setattr(db_session, "execute", _spy_execute)

        report = pruning_module.prune_owner_sync_events(dry_run=False, batch_size=1)

    assert report.per_license_deleted[str(license_id)] == 5
    assert _event_count(app, license_id) == 0
    assert delete_statement_count == 5  # 5 separate bounded batches, never one unbounded statement


def test_row_count_alarm_fires_above_threshold(app, client, seeded, signing_key):
    actor_id = make_staff(app, "prune-alarm@example.com")
    license_id, full_key, installation_a, key_a = _do_activation(app, client, actor_id)

    for _ in range(3):
        assert _push(client, key_a, installation_a, [_make_event()]).status_code == 200

    with app.app_context():
        from app.sync.pruning import check_row_count_alarms

        alarms_low = check_row_count_alarms(threshold=2)
        alarms_high = check_row_count_alarms(threshold=100)

    assert any(a.license_id == str(license_id) for a in alarms_low)
    assert not any(a.license_id == str(license_id) for a in alarms_high)


def test_quarantine_rate_alarm_fires_on_pending_only(app, client, seeded, signing_key):
    actor_id = make_staff(app, "prune-qalarm@example.com")
    license_id, full_key, installation_a, key_a = _do_activation(app, client, actor_id)

    bad_events = []
    for _ in range(3):
        e = _make_event()
        e["entity_type"] = "x" * 65  # exceeds the column length -- always quarantined
        bad_events.append(e)
    resp = _push(client, key_a, installation_a, bad_events)
    assert resp.status_code == 200
    assert resp.get_json()["stored"] == 0

    with app.app_context():
        from app.sync.pruning import check_quarantine_rate_alarm

        alarms_low = check_quarantine_rate_alarm(threshold=2)
        alarms_high = check_quarantine_rate_alarm(threshold=100)

    assert any(a.license_id == str(license_id) for a in alarms_low)
    assert not any(a.license_id == str(license_id) for a in alarms_high)

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncQuarantineEvent

        rows = db_session.query(SyncQuarantineEvent).filter(SyncQuarantineEvent.license_id == license_id).all()
        assert len(rows) == 3
        # Discarding drops them out of the alarm even though the rows
        # themselves are never deleted.
        for row in rows:
            row.status = "DISCARDED"
        db_session.commit()

    with app.app_context():
        from app.sync.pruning import check_quarantine_rate_alarm

        alarms_after_discard = check_quarantine_rate_alarm(threshold=2)
    assert not any(a.license_id == str(license_id) for a in alarms_after_discard)


def test_overlarge_since_cannot_pre_acknowledge_future_events(app, client, seeded, signing_key):
    """REGRESSION -- found by testing pull()'s cursor persistence directly,
    and it really did destroy data before the fix.

    pull() validates `since` only as a non-negative int (deliberately: a
    client cannot know the server's current max seq, so there is no upper
    bound on the wire). When a pull matches zero rows, pull() computes
    `cursor = since` VERBATIM. Before routes.py::_advance_device_cursor
    clamped it, that unbounded value was persisted as this device's
    `last_acked_seq` -- a device on record as having acknowledged events
    THAT DO NOT EXIST YET.

    The damage is not to the events present at that moment; it is to every
    event written AFTERWARDS. A bogus cursor of 10**15 pre-acknowledges the
    entire future of the stream, so each new event becomes instantly
    prunable the moment it is written -- deleted before the device that
    over-claimed ever actually receives it. And because _advance_device_cursor
    is GREATEST-style (correctly, for its own reasons), the bogus watermark
    can never be walked back down by any later pull: it is unrecoverable
    through the protocol.

    This test pins the real invariant: a device's persisted cursor may never
    exceed the license's real MAX(seq) at the time of its pull, so events
    written later are still genuinely unacknowledged and survive pruning.

    MUTATION PROOF: in routes.py::pull(), replace the zero-rows branch's
    `cursor = min(since, max_seq)` with the original `cursor = since` and
    this test fails -- device A is recorded at 10**15, and events 2 and 3,
    which A never received, are deleted by the next prune run."""
    actor_id = make_staff(app, "prune-overlarge-since@example.com")
    license_id, full_key, installation_a, key_a = _do_activation(app, client, actor_id, device_limit=2)
    _, _, installation_b, key_b = _do_activation(
        app, client, actor_id, license_id=license_id, full_key=full_key, device_limit=2
    )

    # B authors event 1; A pulls it legitimately.
    assert _push(client, key_b, installation_b, [_make_event()]).status_code == 200
    pull_a = _pull(client, key_a, installation_a, since=0)
    assert pull_a.status_code == 200
    assert len(pull_a.get_json()["events"]) == 1
    first_seq = pull_a.get_json()["cursor"]

    # A now sends a wildly-too-large `since` (a restored-from-backup client,
    # a version-skewed client, or a plain integer bug -- all validly signed
    # by A's own key, so no auth check can reject it). It matches no rows.
    bogus = _pull(client, key_a, installation_a, since=10**15)
    assert bogus.status_code == 200
    assert bogus.get_json()["events"] == []
    # The RETURNED cursor is the clamped, true high-water mark -- not the
    # over-large value the client asked from. Echoing the bogus value back
    # would leave this client permanently blind (forever asking from a point
    # the stream never reaches); handing back server truth lets it self-heal
    # on its next pull.
    assert bogus.get_json()["cursor"] == first_seq

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncDeviceCursor

        cursor_a = db_session.get(SyncDeviceCursor, uuid.UUID(installation_a))
        assert cursor_a.last_acked_seq == first_seq, "persisted cursor must be clamped to real MAX(seq)"

    # Two more events arrive AFTER A's bogus claim. A has never seen them.
    assert _push(client, key_b, installation_b, [_make_event()]).status_code == 200
    assert _push(client, key_b, installation_b, [_make_event()]).status_code == 200

    # B catches its own cursor up, so B is not what blocks pruning here --
    # only A's (correctly clamped) cursor is.
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        max_seq = max(r.seq for r in db_session.query(SyncEvent).filter(SyncEvent.license_id == license_id).all())
    assert _pull(client, key_b, installation_b, since=max_seq).status_code == 200

    with app.app_context():
        from app.sync.pruning import prune_owner_sync_events

        prune_owner_sync_events(dry_run=False)

    # Only event 1 (which A genuinely received) may be pruned. Events 2 and
    # 3 must survive -- A never acknowledged them, and no bogus `since` may
    # make the server believe otherwise.
    assert _event_count(app, license_id) == 2, "future events were pre-acknowledged by an over-large `since`"

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        remaining = sorted(r.seq for r in db_session.query(SyncEvent).filter(SyncEvent.license_id == license_id).all())
    assert all(seq > first_seq for seq in remaining)
