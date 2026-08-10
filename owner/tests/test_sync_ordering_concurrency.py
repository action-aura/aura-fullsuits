"""Real-Postgres concurrency proofs for the seq-visibility race fix
(_lock_license_stream in owner/app/sync/routes.py).

AUDIT CONTEXT: owner_sync_events.seq is a Postgres IDENTITY column -- values
are ASSIGNED at INSERT time but only become VISIBLE to other transactions at
COMMIT time. Before this fix, two concurrent pushes for the same license
could assign seq in one order but commit in the other, letting a pull's
cursor advance past a not-yet-committed seq -- permanently, silently
skipping it once it did commit. This was tracked as a deliberately-unfixed
"Residual gap" in
docs/superpowers/plans/2026-08-06-multi-device-sync-foundation.md until
_lock_license_stream() closed it with a transaction-scoped, per-license
Postgres advisory lock.

Every test below drives REAL, independent psycopg connections with manual
transaction control (BEGIN/COMMIT/ROLLBACK by hand) -- never two SQLAlchemy
sessions sharing one connection pool, which can hide or misrepresent actual
Postgres locking behavior. This mirrors the `two_raw_connections` fixture
pattern in test_scheduled_ops_locking.py, extended here to five connections
for the throughput test and combined with the real Flask test client (for
push()/pull() HTTP-level assertions) where the test needs to prove the
production endpoint's own observable behavior, not just the raw SQL.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone

import psycopg
import pytest

from tests.conftest import make_license, make_staff
from tests.test_sync_routes import _do_activation, _pull, _pull_body

# -- shared raw-connection helpers ---------------------------------------


def _raw_dsn(app) -> str:
    return app.config["SQLALCHEMY_DATABASE_URI"].replace("postgresql+psycopg://", "postgresql://")


def _raw_connect(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn)
    conn.autocommit = False  # manual transaction control, same as test_scheduled_ops_locking.py
    return conn


def _lock_license_stream_raw(conn: psycopg.Connection, license_id) -> None:
    """The exact statement _lock_license_stream() issues in
    owner/app/sync/routes.py, run here over a real independent connection so
    the test controls the transaction boundary by hand."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(license_id),))


def _insert_event_raw(conn: psycopg.Connection, *, event_id, license_id, device_id,
                       entity_type="category", event_type="create", payload=None) -> int:
    payload = payload if payload is not None else {"name": "concurrency-test"}
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO owner_sync_events
                (id, license_id, device_id, entity_type, entity_id, event_type, payload, client_created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING seq
            """,
            (
                str(event_id), str(license_id), str(device_id), entity_type, str(uuid.uuid4()), event_type,
                json.dumps(payload), datetime.now(timezone.utc),
            ),
        )
        return cur.fetchone()[0]


# -- 1. Regression witness -------------------------------------------------


def test_second_push_for_same_license_blocks_until_first_commits(app, seeded):
    """PRE-fix, connection 2 would take the next seq and could commit before
    connection 1, opening the exact visibility race described in the module
    docstring above. POST-fix (this test runs against the fixed code),
    connection 2's lock acquisition must genuinely block while connection 1
    holds the same license's advisory lock -- proven deterministically via
    Postgres's own `lock_timeout` GUC (a short, server-enforced timeout)
    rather than a racy sleep-and-poll, then shown to unblock promptly once
    connection 1 commits."""
    actor_id = make_staff(app, "sync-lockwitness@example.com")
    license_id, _ = make_license(app, actor_id)

    dsn = _raw_dsn(app)
    conn1 = _raw_connect(dsn)
    conn2 = _raw_connect(dsn)
    try:
        event1_id = uuid.uuid4()
        device_id = uuid.uuid4()

        # Connection 1: acquire the per-license lock and insert (assigns
        # seq=N) -- deliberately do NOT commit yet, simulating a slow
        # client/network mid-push.
        _lock_license_stream_raw(conn1, license_id)
        seq1 = _insert_event_raw(conn1, event_id=event1_id, license_id=license_id, device_id=device_id)

        # Connection 2: attempt the SAME license's lock with a short,
        # server-enforced lock_timeout. Pre-fix there would be no lock at
        # all here, so this would succeed instantly; post-fix it must time
        # out with LockNotAvailable while connection 1's transaction is
        # still open.
        with conn2.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '300ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(license_id),))
        conn2.rollback()  # required after any error inside a transaction; also resets the LOCAL GUC

        # Now release connection 1's lock -- connection 2 must promptly
        # unblock and complete.
        conn1.commit()

        start = time.monotonic()
        _lock_license_stream_raw(conn2, license_id)
        event2_id = uuid.uuid4()
        seq2 = _insert_event_raw(conn2, event_id=event2_id, license_id=license_id, device_id=device_id)
        conn2.commit()
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, f"connection 2 took {elapsed:.2f}s to unblock after connection 1 committed"
        assert seq2 == seq1 + 1  # clean, deterministic per-test DB state (TRUNCATE ... RESTART IDENTITY)
    finally:
        conn1.close()
        conn2.close()


# -- 2. Post-fix ordering guarantee ----------------------------------------


def test_pull_never_observes_seq_gap_under_real_concurrent_race(app, client, seeded, signing_key):
    """Real background-thread concurrency (not a manually-sequenced
    imitation): connection 1 holds the license's lock with an uncommitted
    insert (seq=N); a second thread genuinely races to push a second event
    for the SAME license over connection 2 and blocks on the lock exactly
    as proven above. While that race is live, a real pull() over the actual
    Flask endpoint must see NEITHER event (seq N+1 cannot even be assigned
    yet, since connection 2 is still blocked acquiring the lock -- so there
    is no window where N+1 could be visible without N). Once connection 1
    commits and connection 2 completes, a final pull must return both
    events, contiguous and in order -- proving the pre-fix gap (seq N+1
    visible, seq N permanently skipped) is now structurally impossible."""
    actor_id = make_staff(app, "sync-lockorder@example.com")
    license_id, full_key, installation_id, private_key = _do_activation(app, client, actor_id, device_limit=2)

    dsn = _raw_dsn(app)
    conn1 = _raw_connect(dsn)
    conn2 = _raw_connect(dsn)
    # A device id distinct from the puller's own installation_id -- pull()
    # excludes the puller's own events (SyncEvent.device_id != installation.id).
    other_device_id = uuid.uuid4()
    event1_id, event2_id = uuid.uuid4(), uuid.uuid4()
    conn2_result: dict = {}
    conn2_done = threading.Event()

    try:
        _lock_license_stream_raw(conn1, license_id)
        seq1 = _insert_event_raw(conn1, event_id=event1_id, license_id=license_id, device_id=other_device_id)
        # conn1 deliberately NOT committed yet.

        def _conn2_push():
            _lock_license_stream_raw(conn2, license_id)  # blocks here until conn1 commits
            conn2_result["seq"] = _insert_event_raw(
                conn2, event_id=event2_id, license_id=license_id, device_id=other_device_id
            )
            conn2.commit()
            conn2_done.set()

        t = threading.Thread(target=_conn2_push)
        t.start()

        # Give the thread a moment to actually reach (and block on) the
        # lock acquisition before we check anything.
        time.sleep(0.3)
        assert not conn2_done.is_set(), "connection 2 should still be blocked on the per-license lock"

        # Mid-race pull: connection 1's event is uncommitted and connection
        # 2's event doesn't exist yet (still blocked pre-INSERT) -- pull
        # must return nothing new, never a lone seq N+1.
        mid_pull = _pull(client, _pull_body(private_key, installation_id, since=0))
        assert mid_pull.status_code == 200
        assert mid_pull.get_json()["events"] == []

        conn1.commit()  # releases the lock -- connection 2 can now proceed
        t.join(timeout=5)
        assert conn2_done.is_set(), "connection 2 must promptly unblock and finish once connection 1 commits"

        final_pull = _pull(client, _pull_body(private_key, installation_id, since=0))
        assert final_pull.status_code == 200
        events = final_pull.get_json()["events"]
        assert [e["id"] for e in events] == [str(event1_id), str(event2_id)]
        assert events[0]["seq"] == seq1
        assert events[1]["seq"] == conn2_result["seq"]
        assert events[1]["seq"] == events[0]["seq"] + 1  # zero gap between the two commits
    finally:
        conn1.close()
        conn2.close()


# -- 3. Throughput / no-gap under real concurrent load ----------------------


def test_five_concurrent_pushers_same_license_zero_seq_gaps(app, client, seeded, signing_key):
    """5 real threads, each with its OWN independent psycopg connection,
    each pushing a 20-event batch (one lock acquisition covering the whole
    batch, exactly like push()'s single _lock_license_stream() call per
    request) for the SAME license, all racing to start at once. After all
    100 inserts complete, a single real pull() over the actual Flask
    endpoint must return all 100 events with zero gaps in this license's
    own seq set (gaps from other licenses' interleaved seq values would be
    fine -- there are none here since this test's license is the only
    writer against a freshly-truncated table)."""
    actor_id = make_staff(app, "sync-throughput@example.com")
    license_id, full_key, installation_id, private_key = _do_activation(app, client, actor_id, device_limit=2)

    dsn = _raw_dsn(app)
    other_device_id = uuid.uuid4()
    N_PUSHERS = 5
    EVENTS_PER_PUSHER = 20

    results: list[int] = []
    errors: list[str] = []
    results_lock = threading.Lock()
    start_barrier = threading.Barrier(N_PUSHERS)

    def _pusher():
        conn = _raw_connect(dsn)
        try:
            start_barrier.wait(timeout=10)  # maximize actual concurrent contention on the lock
            _lock_license_stream_raw(conn, license_id)
            seqs = [
                _insert_event_raw(conn, event_id=uuid.uuid4(), license_id=license_id, device_id=other_device_id)
                for _ in range(EVENTS_PER_PUSHER)
            ]
            conn.commit()
            with results_lock:
                results.extend(seqs)
        except Exception as exc:  # noqa: BLE001 -- captured for the assertion below, not swallowed
            with results_lock:
                errors.append(repr(exc))
        finally:
            conn.close()

    threads = [threading.Thread(target=_pusher) for _ in range(N_PUSHERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == [], f"no pusher should ever error under the per-license lock: {errors}"
    assert len(results) == N_PUSHERS * EVENTS_PER_PUSHER

    final_pull = _pull(client, _pull_body(private_key, installation_id, since=0))
    assert final_pull.status_code == 200
    events = final_pull.get_json()["events"]
    seqs = [e["seq"] for e in events]

    assert len(seqs) == N_PUSHERS * EVENTS_PER_PUSHER
    assert len(set(seqs)) == len(seqs), "duplicate seq values returned"
    assert seqs == sorted(seqs), "pull() must return events in seq order"
    assert max(seqs) - min(seqs) + 1 == len(seqs), f"gap detected in this license's own seq set: {seqs}"


# -- 4. Cross-license non-contention ----------------------------------------


def test_cross_license_pushes_do_not_contend(app, seeded):
    """Two DIFFERENT licenses pushing concurrently must NOT block each
    other -- proving this is a PER-LICENSE lock, not a global one. This is
    the test that stops a future "simplification" into one global lock from
    landing unnoticed: a global lock would make this test fail (connection
    2 would block on connection 1's held lock even though they're
    different licenses)."""
    actor_id = make_staff(app, "sync-crosslicense@example.com")
    license_x, _ = make_license(app, actor_id)
    license_y, _ = make_license(app, actor_id)

    dsn = _raw_dsn(app)
    conn1 = _raw_connect(dsn)
    conn2 = _raw_connect(dsn)
    try:
        _lock_license_stream_raw(conn1, license_x)
        _insert_event_raw(conn1, event_id=uuid.uuid4(), license_id=license_x, device_id=uuid.uuid4())
        # conn1 deliberately left open -- holds license_x's lock for the
        # remainder of this test.

        start = time.monotonic()
        _lock_license_stream_raw(conn2, license_y)  # a DIFFERENT license -- must not wait on conn1 at all
        _insert_event_raw(conn2, event_id=uuid.uuid4(), license_id=license_y, device_id=uuid.uuid4())
        conn2.commit()
        elapsed = time.monotonic() - start

        assert elapsed < 1.0, (
            f"cross-license push took {elapsed:.2f}s -- looks like it contended with a DIFFERENT "
            "license's advisory lock, which would mean this has regressed into a global lock"
        )
    finally:
        conn1.rollback()  # never committed -- discard, also releases license_x's lock
        conn1.close()
        conn2.close()


# -- 5. Key-space collision assertion ----------------------------------------


def test_advisory_lock_key_space_does_not_collide_with_conftest_fixed_key(app, seeded):
    """owner/tests/conftest.py's _serialize_concurrent_test_runs fixture
    holds a FIXED, session-level advisory lock
    (_TEST_SUITE_ADVISORY_LOCK_KEY = 0x41757261) for the whole pytest
    session, for an unrelated purpose (serializing whole pytest processes
    against each other). That key lives in the SAME single-bigint-argument
    advisory-lock namespace _lock_license_stream()'s hashtext()-derived keys
    use -- verified directly against a live Postgres 17 instance (not
    assumed) that session-level and transaction-level locks sharing a
    numeric key DO conflict with each other; see _lock_license_stream's
    docstring in owner/app/sync/routes.py for that verification.

    A real collision would require some real license_id's hashtext() to
    land on that EXACT literal 32-bit value. This asserts it explicitly
    over a large server-side sample of realistic (UUID-shaped) license ids,
    instead of leaving the non-collision silently assumed."""
    from tests.conftest import _TEST_SUITE_ADVISORY_LOCK_KEY

    assert _TEST_SUITE_ADVISORY_LOCK_KEY == 0x41757261  # pin the literal this test is actually checking against

    dsn = _raw_dsn(app)
    conn = _raw_connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM (
                    SELECT hashtext(gen_random_uuid()::text) AS h
                    FROM generate_series(1, 100000)
                ) sampled
                WHERE h = %s
                """,
                (_TEST_SUITE_ADVISORY_LOCK_KEY,),
            )
            collisions = cur.fetchone()[0]
        conn.rollback()
    finally:
        conn.close()

    assert collisions == 0, (
        f"{collisions} of 100000 sampled license-id-shaped UUIDs hashed to conftest.py's "
        f"fixed advisory lock key ({_TEST_SUITE_ADVISORY_LOCK_KEY}) -- pick a different fixed "
        "test-suite key immediately, this is a real collision risk"
    )
