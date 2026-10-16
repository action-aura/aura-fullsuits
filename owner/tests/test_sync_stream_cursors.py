"""SYNC-BOOT Phase 1 (docs: SYNC-BOOT plan sections 0, 2, 4, 5.1) -- per-stream
cursors, pruned_through, RESYNC_REQUIRED, and the compatibility rule that an
old client / an old Owner keep working.

Real activation + real push/pull routes, same as test_sync_pruning.py; no
hand-inserted cursor rows except where a test is about the migration backfill
or must force a stalled position the routes cannot produce.

MUTATION PROOFS (each one was run for real; the red output is quoted in the
commit message of the change that introduced this file):
  M1 single cursor        -- key _advance_device_cursor / device_position on
                             installation only (ignore `stream`)  -> the
                             lagging-registry and separate-rows tests go red.
  M2 RESYNC check removed -- delete the `since < pruned_through` branch in
                             pull()  -> the 409 tests go red (silent partial).
  M3 cursor on 409        -- move the 409 return after _advance_device_cursor
                             -> test_resync_required_writes_no_cursor goes red.
  M4 missing stream != 0  -- device_position uses only the streams present
                             -> test_a_missing_stream_counts_as_zero goes red.
  M5 old min_retained backfill -- re-add INSERT ... MIN(seq) - 1 to the
                             migration -> test_backfill_claims_nothing_* red.
  M6 dry run raises        -- call _record_pruned_through in the dry_run branch
                             -> test_dry_run_never_moves_pruned_through red.
  M7 global not per-licence -- record the global max seq instead of the batch
                             max for the licence -> test_real_prune_raises_*
                             red (9 != 7).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import timedelta

from tests.conftest import make_staff
from tests.test_sync_pruning import _do_activation, _event_count, _make_event, _push, _signed_body


def _pull_stream(client, key, installation_id, since=0, *, stream=None, caps=None, method="post"):
    extra = {"since": since}
    if stream is not None:
        extra["stream"] = stream
    if caps is not None:
        extra["caps"] = caps
    fn = client.post if method == "post" else client.get
    return fn(
        "/api/sync/v1/pull", data=json.dumps(_signed_body(key, installation_id, extra)),
        content_type="application/json",
    )


def _devices(app, client, n=2):
    """n active devices on ONE licence -> (license_id, [(installation_id, key), ...])."""
    actor_id = make_staff(app, f"streams-{uuid.uuid4().hex[:6]}@example.com")
    license_id, full_key, inst, key = _do_activation(app, client, actor_id, device_limit=n)
    out = [(inst, key)]
    for _ in range(n - 1):
        _, _, i, k = _do_activation(app, client, actor_id, license_id=license_id, full_key=full_key, device_limit=n)
        out.append((i, k))
    return license_id, out


def _cursor_rows(app, installation_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncDeviceCursor

        rows = db_session.query(SyncDeviceCursor).filter(
            SyncDeviceCursor.installation_id == uuid.UUID(installation_id)).all()
        return {r.stream: r.last_acked_seq for r in rows}


def _push_n(client, key, installation_id, n):
    for _ in range(n):
        assert _push(client, key, installation_id, [_make_event()]).status_code == 200


def _prune(app, **kw):
    with app.app_context():
        from app.sync.pruning import prune_owner_sync_events

        return prune_owner_sync_events(dry_run=False, **kw)


def _pruned_through(app, license_id) -> int:
    with app.app_context():
        from app.sync.routes import _license_pruned_through

        return _license_pruned_through(license_id)


def _catch_up(client, who, upto, streams=("retail", "registry")):
    for inst, key in who:
        for s in streams:
            r = _pull_stream(client, key, inst, upto, stream=s)
            assert r.status_code == 200, r.get_json()


# ---------------------------------------------------------------- cursors
def test_each_stream_gets_its_own_cursor_row(app, client, seeded, signing_key):
    """M1: one installation, two streams -> two rows (a single cursor leaves one)."""
    _, ((ia, ka), (ib, kb)) = _devices(app, client)
    _push_n(client, ka, ia, 3)
    assert _pull_stream(client, kb, ib, 0, stream="retail").get_json()["cursor"] == 3
    assert _cursor_rows(app, ib) == {"retail": 3}
    assert _pull_stream(client, kb, ib, 0, stream="registry").get_json()["cursor"] == 3
    assert _cursor_rows(app, ib) == {"retail": 3, "registry": 3}


def test_an_old_client_with_no_stream_uses_the_legacy_row(app, client, seeded, signing_key):
    """Compatibility, direction 1: OLD client (sends no stream) vs NEW Owner."""
    _, ((ia, ka), (ib, kb)) = _devices(app, client)
    _push_n(client, ka, ia, 2)
    r = _pull_stream(client, kb, ib, 0)
    assert r.status_code == 200 and len(r.get_json()["events"]) == 2
    assert _cursor_rows(app, ib) == {"legacy": 2}


def test_get_with_a_body_still_works_with_a_stream(app, client, seeded, signing_key):
    """Android pulls GET-with-body; the new field must ride that verb too."""
    _, ((ia, ka), (ib, kb)) = _devices(app, client)
    _push_n(client, ka, ia, 1)
    r = _pull_stream(client, kb, ib, 0, stream="retail", method="get")
    assert r.status_code == 200 and _cursor_rows(app, ib) == {"retail": 1}


def test_unknown_stream_and_bad_caps_are_rejected_without_a_cursor(app, client, seeded, signing_key):
    _, ((ia, ka), (ib, kb)) = _devices(app, client)
    for extra in ({"stream": "banana"}, {"stream": 7}, {"caps": "bootstrap_v1"}, {"caps": [1]},
                  {"caps": ["x"] * 17}, {"caps": ["x" * 49]}):
        body = _signed_body(kb, ib, {"since": 0, **extra})
        r = client.post("/api/sync/v1/pull", data=json.dumps(body), content_type="application/json")
        assert r.status_code == 400, extra
        assert r.get_json()["reason_code"] in ("INVALID_STREAM", "INVALID_CAPS")
    assert _cursor_rows(app, ib) == {}


def test_caps_are_stored_and_last_pulled_at_is_written_at_most_hourly(app, client, seeded, signing_key):
    from app.extensions import db_session
    from app.models.sync import SyncDeviceCursor

    _, ((ia, ka), (ib, kb)) = _devices(app, client)
    assert _pull_stream(client, kb, ib, 0, stream="retail", caps=["z_cap", "bootstrap_v1"]).status_code == 200
    key = (uuid.UUID(ib), "retail")
    with app.app_context():
        row = db_session.get(SyncDeviceCursor, key)
        assert row.caps == ["bootstrap_v1", "z_cap"]
        first = row.last_pulled_at
        assert first is not None
    assert _pull_stream(client, kb, ib, 0, stream="retail").status_code == 200
    with app.app_context():
        row = db_session.get(SyncDeviceCursor, key)
        assert row.last_pulled_at == first               # inside the hour: untouched
        assert row.caps == ["bootstrap_v1", "z_cap"]     # no caps sent: kept, not wiped
        row.last_pulled_at = first - timedelta(hours=2)
        db_session.commit()
    assert _pull_stream(client, kb, ib, 0, stream="retail").status_code == 200
    with app.app_context():
        row = db_session.get(SyncDeviceCursor, key)
        assert row.last_pulled_at >= first - timedelta(minutes=1)   # past the hour: rewritten


def test_new_client_against_an_old_shaped_owner_still_works(app, client, seeded, signing_key, monkeypatch):
    """Compatibility, direction 2: an OLD Owner never read `stream`/`caps` (nor
    pruned_through) but still verifies the signature over the WHOLE body, so a
    new client's extra signed fields must not break it. Simulated by making the
    Owner ignore the fields; the new client's request must still succeed."""
    from app.sync import routes

    monkeypatch.setattr(routes, "_requested_stream", lambda body: (routes.LEGACY_STREAM, None))
    _, ((ia, ka), (ib, kb)) = _devices(app, client)
    _push_n(client, ka, ia, 2)
    r = _pull_stream(client, kb, ib, 0, stream="retail", caps=["bootstrap_v1"])
    assert r.status_code == 200 and len(r.get_json()["events"]) == 2
    assert _cursor_rows(app, ib) == {"legacy": 2}


# ------------------------------------------------------------ prune position
def test_a_lagging_registry_stream_holds_the_prune_watermark(app, client, seeded, signing_key):
    """M1: B's registry stream stalled at seq 1 while its retail stream is at 4,
    and A is fully caught up on both. Prune may delete only seq<=1. A single
    per-installation cursor reads 4 and would delete everything B's registry
    stream never applied."""
    license_id, ((ia, ka), (ib, kb)) = _devices(app, client)
    _push_n(client, ka, ia, 1)
    assert _pull_stream(client, kb, ib, 0, stream="registry").get_json()["cursor"] == 1
    _push_n(client, ka, ia, 3)
    assert _pull_stream(client, kb, ib, 0, stream="retail").get_json()["cursor"] == 4
    _catch_up(client, [(ia, ka)], 4)
    assert _cursor_rows(app, ib) == {"registry": 1, "retail": 4}
    report = _prune(app)
    assert report.total_deleted == 1 and _event_count(app, license_id) == 3
    assert _pruned_through(app, license_id) == 1


def test_a_missing_stream_counts_as_zero(app, client, seeded, signing_key):
    """M4: B has only ever reported its retail stream. 'Registry has not
    reported' must HOLD the watermark at 0, not be skipped."""
    license_id, ((ia, ka), (ib, kb)) = _devices(app, client)
    _push_n(client, ka, ia, 3)
    assert _pull_stream(client, kb, ib, 0, stream="retail").get_json()["cursor"] == 3
    _catch_up(client, [(ia, ka)], 3)
    assert _prune(app).total_deleted == 0 and _event_count(app, license_id) == 3
    assert _pruned_through(app, license_id) == 0


def test_legacy_row_is_ignored_once_stream_rows_exist(app, client, seeded, signing_key):
    """A stale pre-upgrade 'legacy' cursor (low) must not pin the licence once
    the same device reports both real streams. Mutation: min over ALL rows."""
    from app.extensions import db_session
    from app.models.sync import SyncDeviceCursor

    license_id, ((ia, ka), (ib, kb)) = _devices(app, client)
    _push_n(client, ka, ia, 3)
    assert _pull_stream(client, kb, ib, 0).status_code == 200         # legacy row
    with app.app_context():
        db_session.get(SyncDeviceCursor, (uuid.UUID(ib), "legacy")).last_acked_seq = 0
        db_session.commit()
    _catch_up(client, [(ib, kb), (ia, ka)], 3)
    assert _cursor_rows(app, ib)["legacy"] == 0
    assert _prune(app).total_deleted == 3


def test_device_position_rules():
    from app.sync.pruning import device_position

    assert device_position({"retail": 9, "registry": 4, "legacy": 1, "all": 2}) == 4
    assert device_position({"retail": 9}) == 0
    assert device_position({"registry": 9}) == 0
    assert device_position({"all": 7, "legacy": 1}) == 7
    assert device_position({"legacy": 5}) == 5
    assert device_position({}) == 0


# ---------------------------------------------------------- RESYNC_REQUIRED
def _pruned_licence_with_three_devices(app, client):
    license_id, ((ia, ka), (ib, kb), (ic, kc)) = _devices(app, client, 3)
    _push_n(client, ka, ia, 3)
    _catch_up(client, [(ib, kb)], 3)
    _catch_up(client, [(ia, ka)], 3)
    # C is registered but has never pulled -> it would pin the watermark at 0.
    # Suspend it the way an operator would so the prune can run for A and B.
    actor_id = make_staff(app, f"susp-{uuid.uuid4().hex[:6]}@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.installations.services import transition_installation
        from app.models.installations import Installation

        transition_installation(db_session.get(Installation, uuid.UUID(ic)), "SUSPENDED", actor_id)
        db_session.commit()
    assert _prune(app).total_deleted == 3
    return license_id, (ia, ka), (ib, kb), (ic, kc)


def test_pull_below_pruned_through_is_told_to_resync(app, client, seeded, signing_key):
    """M2: without the check this returns 200 with zero events -- a silent,
    gap-free-looking partial stream."""
    license_id, _a, (ib, kb), _c = _pruned_licence_with_three_devices(app, client)
    assert _pruned_through(app, license_id) == 3
    for stream in ("retail", "registry", None):
        r = _pull_stream(client, kb, ib, 0, stream=stream)
        assert r.status_code == 409, stream
        body = r.get_json()
        if stream == "registry":
            # SYNC-BOOT Phase 3 (plan 2.2): the registry stream is never copied, so Owner also names where
            # its log ends NOW (`fast_forward_to`) for the device to skip to. Nothing else may be added, and
            # the number is checked in test_sync_bootstrap_routes (it equals the licence's top seq).
            assert set(body) == {"reason_code", "pruned_through", "fast_forward_to"}, body
            assert isinstance(body["fast_forward_to"], int) and body["fast_forward_to"] >= 3
            body = {k: v for k, v in body.items() if k != "fast_forward_to"}
        assert body == {"reason_code": "RESYNC_REQUIRED", "pruned_through": 3}


def test_resync_required_writes_no_cursor(app, client, seeded, signing_key):
    """M3: a refused pull must not create or move any cursor row, for an
    existing device (rows unchanged) and for one that never pulled (no row)."""
    from app.extensions import db_session
    from app.installations.services import transition_installation  # noqa: F401
    from app.models.installations import Installation

    license_id, _a, (ib, kb), (ic, kc) = _pruned_licence_with_three_devices(app, client)
    before_b = _cursor_rows(app, ib)
    assert _pull_stream(client, kb, ib, 1, stream="retail").status_code == 409
    assert _cursor_rows(app, ib) == before_b
    with app.app_context():                       # bring C back so it can authenticate
        inst = db_session.get(Installation, uuid.UUID(ic))
        inst.status = "ACTIVE"
        db_session.commit()
    assert _pull_stream(client, kc, ic, 0, stream="retail").status_code == 409
    assert _cursor_rows(app, ic) == {}


def test_pull_at_or_above_pruned_through_still_works(app, client, seeded, signing_key):
    license_id, (ia, ka), (ib, kb), _c = _pruned_licence_with_three_devices(app, client)
    _push_n(client, ka, ia, 2)
    r = _pull_stream(client, kb, ib, 3, stream="retail")
    assert r.status_code == 200 and len(r.get_json()["events"]) == 2


def test_dry_run_never_moves_pruned_through(app, client, seeded, signing_key):
    license_id, ((ia, ka), (ib, kb)) = _devices(app, client)
    _push_n(client, ka, ia, 2)
    _catch_up(client, [(ib, kb), (ia, ka)], 2)
    with app.app_context():
        from app.sync.pruning import prune_owner_sync_events

        assert prune_owner_sync_events(dry_run=True).total_deleted == 2
    assert _event_count(app, license_id) == 2 and _pruned_through(app, license_id) == 0
    assert _pull_stream(client, kb, ib, 0, stream="retail").status_code == 200


def test_pruned_through_follows_each_delete_batch_and_never_regresses(app, client, seeded, signing_key):
    license_id, ((ia, ka), (ib, kb)) = _devices(app, client)
    _push_n(client, ka, ia, 5)
    _catch_up(client, [(ib, kb), (ia, ka)], 5)
    _prune(app, batch_size=2)
    assert _event_count(app, license_id) == 0 and _pruned_through(app, license_id) == 5
    from app.extensions import db_session
    from app.sync.pruning import _record_pruned_through

    with app.app_context():
        _record_pruned_through(license_id, 2)     # a lower value must never win
        db_session.commit()
    assert _pruned_through(app, license_id) == 5


# ------------------------------------------------------------------ backfill
def _alembic(*args):
    owner_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "OWNER_DATABASE_URL": os.environ["OWNER_TEST_DATABASE_URL"]}
    subprocess.run([sys.executable, "-m", "alembic", *args], cwd=owner_dir, env=env, check=True, capture_output=True)


def test_backfill_claims_nothing_and_a_late_starting_licence_can_still_join(app, client, seeded, signing_key):
    """M5 (regression guard for the shared-seq defect). Licence P looks pruned
    (oldest retained seq 3, a device cursor beyond it); licence Q was NEVER
    pruned but its first event sits at seq 5 because seq is one sequence shared
    by every licence. The migration must claim NOTHING for either (old heuristic:
    P -> 2, Q -> 0 here, but Q-like licences got first_seq-1 whenever a cursor
    passed their first event). Run through a real downgrade + upgrade so the SQL
    in the migration is what is under test, then prove the allow-half: a device
    with since=0 on the late-starting licence receives EVERYTHING."""
    from app.extensions import db_session
    from sqlalchemy import text

    lp, ((pa, pka), (pb, pkb)) = _devices(app, client)
    _push_n(client, pka, pa, 4)                                  # seqs 1..4
    assert _pull_stream(client, pkb, pb, 0).status_code == 200   # legacy cursor at 4
    lq, ((qa, qka), (qb, qkb)) = _devices(app, client)
    _push_n(client, qka, qa, 3)                                  # seqs 5..7, never pruned
    assert _pull_stream(client, qkb, qb, 0).status_code == 200   # a cursor BEYOND Q's first event
    with app.app_context():
        db_session.execute(text("DELETE FROM owner_sync_events WHERE license_id = :l AND seq <= 2"), {"l": str(lp)})
        db_session.commit()
        db_session.remove()
    _alembic("downgrade", "c7a4f2d8e613")
    _alembic("upgrade", "head")
    assert _pruned_through(app, lp) == 0
    assert _pruned_through(app, lq) == 0
    assert _cursor_rows(app, pb) == {"legacy": 4}                # old row survived as 'legacy'
    r = _pull_stream(client, qkb, qb, 0, stream="retail")        # a new device on Q, since=0
    assert r.status_code == 200 and len(r.get_json()["events"]) == 3


def _two_licences_q_prunable(app, client):
    """P (seqs 1-4, nobody caught up -> never prunes) then Q (seqs 5-7, both
    devices caught up) then P pushes again (8-9) so the GLOBAL max seq (9) is
    above Q's own max (7)."""
    lp, ((pa, pka), (pb, pkb)) = _devices(app, client)
    _push_n(client, pka, pa, 4)
    lq, ((qa, qka), (qb, qkb)) = _devices(app, client)
    _push_n(client, qka, qa, 3)
    _push_n(client, pka, pa, 2)
    _catch_up(client, [(qa, qka), (qb, qkb)], 7)
    return lp, lq, (qa, qka), (qb, qkb)


def test_real_prune_raises_pruned_through_to_this_licences_max_deleted_seq(app, client, seeded, signing_key):
    """Deny-half + per-licence rule. After a real --apply prune of Q, a new
    device is told to resync, and pruned_through is Q's highest DELETED seq (7),
    not the global watermark/max (9)."""
    lp, lq, _qa, (qb, qkb) = _two_licences_q_prunable(app, client)
    _prune(app)
    assert _event_count(app, lq) == 0
    assert _pruned_through(app, lq) == 7
    assert _pruned_through(app, lp) == 0                         # P deleted nothing -> no claim
    r = _pull_stream(client, qkb, qb, 0, stream="retail")
    assert r.status_code == 409 and r.get_json()["pruned_through"] == 7


def test_mark_pruned_sets_one_licence_and_a_new_device_is_then_told_to_resync(app, client, seeded, signing_key):
    lp, lq, _qa, (qb, qkb) = _two_licences_q_prunable(app, client)
    runner = app.test_cli_runner()
    dry = runner.invoke(args=["sync", "mark-pruned", "--license", str(lq), "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert json.loads(dry.output)["through"] == 4                # lowest stored seq 5, minus 1
    assert _pruned_through(app, lq) == 0                         # dry run writes nothing
    assert _pull_stream(client, qkb, qb, 0, stream="retail").status_code == 200
    real = runner.invoke(args=["sync", "mark-pruned", "--license", str(lq)])
    assert real.exit_code == 0, real.output
    assert _pruned_through(app, lq) == 4 and _pruned_through(app, lp) == 0
    r = _pull_stream(client, qkb, qb, 0, stream="retail")
    assert r.status_code == 409 and r.get_json()["pruned_through"] == 4
    assert _pull_stream(client, qkb, qb, 4, stream="retail").status_code == 200
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog

        assert db_session.query(AuditLog).filter(
            AuditLog.action_code == "SYNC_LICENSE_MARKED_PRUNED", AuditLog.entity_public_id == str(lq)).count() == 1
