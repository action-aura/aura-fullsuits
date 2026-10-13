"""SYNC-BOOT Phase 1, client side: the pull body names its stream, and a
RESYNC_REQUIRED refusal becomes a visible, deduplicated health state instead of
a retry loop (or, worse, a silently partial history).

Owner's half is proved in owner/tests/test_sync_stream_cursors.py; the two are
joined end to end by owner/tests/test_sync_late_join_after_prune_cross_stack.py.

MUTATION PROOFS (run for real, red output quoted in the commit message):
  C1 stream always sent  -- drop the `supports_stream` check in
                            pull_for_stream  -> the double-without-stream tests
                            go red (TypeError: pull() got an unexpected keyword).
  C2 RESYNC treated as a plain failure -- delete the RESYNC branch in
                            run_once  -> the "logs once", "backoff pinned" and
                            "stops pulling" tests go red.
  C3 stop-pulling removed -- delete _raise_if_resync_pending  -> the
                            "second tick does not contact the relay" test is red.
  C4 stream label never wired -- pull_once ignoring self._stream_label  -> the
                            label-reaches-the-client test is red.
"""
from __future__ import annotations

import logging
import sqlite3

import pytest

from commercial_runtime.sync import sync_service as ss
from commercial_runtime.sync.relay_client import (
    NetworkError, RESYNC_REQUIRED, RelayRejected, SyncRelayClient, pull_for_stream,
)
from commercial_runtime.sync.tests.test_relay_client import FakeResponse, FakeSession, FakeSigner, RecordingSleep
from commercial_runtime.sync.tests.test_sync_service import db_path, get_conn  # noqa: F401  (fixtures)


def _client(session):
    return SyncRelayClient(
        base_url="http://127.0.0.1:5551", signer=FakeSigner(), installation_id="inst-1",
        session=session, sleep_fn=RecordingSleep(),
    )


# ------------------------------------------------------------ wire (client)
def test_pull_without_a_stream_sends_the_old_body_exactly():
    """Compatibility: an old caller's request is byte-for-byte what it was."""
    session = FakeSession([FakeResponse(200, {"events": [], "cursor": 0})])
    _client(session).pull(5)
    sent = session.calls[0]["json"]
    assert "stream" not in sent and "caps" not in sent and sent["since"] == 5


def test_pull_stream_and_caps_ride_inside_the_signed_body():
    session = FakeSession([FakeResponse(200, {"events": [], "cursor": 0})])
    signer = FakeSigner()
    client = SyncRelayClient(base_url="http://x", signer=signer, installation_id="i", session=session,
                             sleep_fn=RecordingSleep())
    client.pull(5, stream="registry", caps=["bootstrap_v1"])
    sent = session.calls[0]["json"]
    assert sent["stream"] == "registry" and sent["caps"] == ["bootstrap_v1"]
    signed_bytes = signer.sign_calls[0]
    # signed => a captured request cannot be replayed under another stream
    assert b'"stream":"registry"' in signed_bytes.replace(b" ", b"")


def test_409_resync_required_is_a_rejection_that_carries_pruned_through_and_is_not_retried():
    session = FakeSession([FakeResponse(409, {"reason_code": "RESYNC_REQUIRED", "pruned_through": 41})])
    with pytest.raises(RelayRejected) as exc:
        _client(session).pull(3, stream="retail")
    assert exc.value.reason_code == RESYNC_REQUIRED
    assert exc.value.details == {"pruned_through": 41}
    assert len(session.calls) == 1


def test_a_plain_rejection_has_empty_details():
    session = FakeSession([FakeResponse(400, {"reason_code": "INVALID_SIGNATURE"})])
    with pytest.raises(RelayRejected) as exc:
        _client(session).pull(0)
    assert exc.value.details == {}


# ------------------------------------------------- stream label (SyncService)
class StreamClient:
    """Advertises supports_stream like the real SyncRelayClient."""
    supports_stream = True

    def __init__(self, responses, base_url=None):
        self._responses = list(responses)
        self.calls = []
        self.pushes = []
        if base_url is not None:
            self.base_url = base_url

    def pull(self, since, stream=None, caps=None):
        self.calls.append((since, stream))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def push(self, events):
        self.pushes.append(events)
        return {"stored": len(events), "received": len(events)}


class PlainClient:
    """The documented surface: .pull(since) only, no supports_stream (the LAN
    hub's client and every test double look like this)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def pull(self, since):
        self.calls.append(since)
        return self._responses.pop(0)

    def push(self, events):
        return {"stored": len(events), "received": len(events)}


def test_the_stream_label_reaches_a_client_that_supports_it(get_conn):
    client = StreamClient([{"events": [], "cursor": 0}])
    ss.SyncService(lambda: client, get_conn, lambda: "co", stream_label="registry").pull_once()
    assert client.calls == [(0, "registry")]


def test_no_stream_label_sends_none(get_conn):
    client = StreamClient([{"events": [], "cursor": 0}])
    ss.SyncService(lambda: client, get_conn, lambda: "co").pull_once()
    assert client.calls == [(0, None)]


def test_a_client_without_supports_stream_is_never_handed_a_stream(get_conn):
    """C1: the LAN hub client / doubles have no `stream` parameter; passing one
    would TypeError every tick. (Old Owner + new client is covered owner-side.)"""
    client = PlainClient([{"events": [], "cursor": 0}])
    ss.SyncService(lambda: client, get_conn, lambda: "co", stream_label="retail").pull_once()
    assert client.calls == [0]


def test_pull_for_stream_helper_is_duck_typed():
    plain = PlainClient([{"events": [], "cursor": 0}])
    assert pull_for_stream(plain, 4, "retail") == {"events": [], "cursor": 0} and plain.calls == [4]
    sc = StreamClient([{"events": [], "cursor": 0}])
    pull_for_stream(sc, 4, "all")
    assert sc.calls == [(4, "all")]


# ------------------------------------------------------- RESYNC_REQUIRED
@pytest.fixture(autouse=True)
def _no_leaked_resync_state():
    yield
    with ss._RESYNC_STREAMS_LOCK:
        ss._RESYNC_STREAMS.clear()
    ss.unregister_active_service()


def _resync(n=41):
    return RelayRejected(RESYNC_REQUIRED, "resync", {"pruned_through": n})


def _cursor(get_conn):
    c = get_conn()
    try:
        return c.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()["last_seq"]
    finally:
        c.close()


def test_resync_required_stops_the_stream_but_not_the_push(get_conn, caplog):
    from commercial_runtime.sync.tests.test_sync_service import _insert_outbox_row as add_row

    add_row(get_conn)
    client = StreamClient([_resync()])
    svc = ss.SyncService(lambda: client, get_conn, lambda: "co", stream_label="retail")
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        svc.run_once()
        svc.run_once()
        svc.run_once()

    # the pull stopped: the relay was contacted once, then skipped
    assert client.calls == [(0, "retail")]
    # pushing kept working: the outbox row went up and was acked away
    assert len(client.pushes) == 1
    # visible
    h = svc.get_health()
    assert h["resync_required"] is True and h["resync_pruned_through"] == 41 and h["stream"] == "retail"
    assert h["pull"]["healthy"] is False and h["pull"]["last_failure_reason"] == RESYNC_REQUIRED
    # not a transient failure: backoff (shared with push cadence) must not grow
    assert h["pull"]["consecutive_failures"] == 1
    # dedup: ONE error line for three ticks, and none that is a generic pull FAILED
    resync_logs = [r for r in caplog.records if "RESYNC REQUIRED" in r.getMessage()]
    assert len(resync_logs) == 1
    assert not [r for r in caplog.records if "Sync pull FAILED" in r.getMessage()]
    # the cursor was NOT advanced past data it never got
    assert _cursor(get_conn) == 0


def test_resync_state_is_surfaced_in_the_active_health_for_every_stream(get_conn):
    """/sync/health is served from the one registered service; the registry
    stream lives in another instance and must still show up there."""
    retail = ss.SyncService(lambda: StreamClient([{"events": [], "cursor": 0}] * 3), get_conn, lambda: "co",
                            stream_label="retail")
    registry = ss.SyncService(lambda: StreamClient([_resync(9)]), get_conn, lambda: "co", stream_label="registry")
    ss.register_active_service(retail)
    retail.run_once()
    assert ss.get_active_health()["resync_required"] is False
    registry.run_once()
    health = ss.get_active_health()
    assert health["resync_required"] is True and health["resync_streams"] == {"registry": 9}
    assert health["healthy"] is False


def test_a_non_resync_rejection_still_counts_as_a_failure_with_backoff(get_conn):
    """The RESYNC branch must not swallow other rejections."""
    client = StreamClient([RelayRejected("INVALID_SIGNATURE", "bad"), NetworkError("NETWORK_UNAVAILABLE", "off")])
    svc = ss.SyncService(lambda: client, get_conn, lambda: "co", stream_label="retail")
    svc.run_once()
    svc.run_once()
    h = svc.get_health()
    assert h["resync_required"] is False and h["pull"]["consecutive_failures"] == 2
    assert h["pull"]["last_failure_reason"] == "NETWORK_UNAVAILABLE"


def test_switching_to_another_relay_leaves_the_resync_state(get_conn):
    """A different relay has its own pruned range: the refusal from the old one
    proves nothing about the new one, so the stream pulls again."""
    c = get_conn()   # the shared fixture's sync_cursor predates the v31 relay_url column
    c.execute("ALTER TABLE sync_cursor ADD COLUMN relay_url TEXT")
    c.commit()
    c.close()
    state = {"url": "https://old.example"}
    clients = {
        "https://old.example": StreamClient([_resync()], base_url="https://old.example"),
        "https://new.example": StreamClient([{"events": [], "cursor": 0}], base_url="https://new.example"),
    }
    svc = ss.SyncService(lambda: clients[state["url"]], get_conn, lambda: "co", stream_label="retail")
    svc.run_once()
    assert svc.get_health()["resync_required"] is True
    state["url"] = "https://new.example"
    svc.run_once()
    assert clients["https://new.example"].calls == [(0, "retail")]
    assert svc.get_health()["resync_required"] is False
    assert ss.resync_streams_snapshot() == {}
