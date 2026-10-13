"""SYNC-BOOT Phase 2, device side: what SyncService does with the bootstrap hooks.

  * include_own_once: the pull asks for this device's OWN events once, and stops asking only when the
    relay ECHOES that it honoured the request (one that ignores the field would otherwise get the
    flag cleared for nothing). Deny half: a relay that does not echo keeps the flag armed.
  * the REGISTRY stream fast-forwards on a RESYNC_REQUIRED that names `fast_forward_to`; the RETAIL
    stream never does (it needs a copy of the shop, not a skip -- skipping would be exactly the silent
    partial history the refusal exists to prevent).
  * on_resync_required runs once per refusal, a hook that raises costs nothing, and the stream stays
    stopped while a copy waits to be installed.
  * bootstrap requests a hub piggybacks reach the product; a failing hook is ignored.
  * after an install the device's unpushed outbox is re-applied ONCE to the new database and stays in
    the outbox.
  * caps and include_own reach only a client that says it supports them.

Mutations that turned a named test red: include_own_done on any response; fast-forward on every
stream; fast-forward ignoring the target; hook exceptions propagated; hook run on every tick;
requests dropped; outbox re-applied every tick / never; caps sent to a client that cannot take them.
"""
from __future__ import annotations

import json
import logging
import uuid

import pytest

from commercial_runtime.sync import sync_service as ss
from commercial_runtime.sync.bootstrap.hooks import BootstrapHooks
from commercial_runtime.sync.relay_client import RESYNC_REQUIRED, RelayRejected
from commercial_runtime.sync.tests.test_sync_service import db_path, get_conn  # noqa: F401  (fixtures)


class BootClient:
    """Advertises both capabilities, like the real SyncRelayClient."""
    supports_stream = True
    supports_bootstrap = True

    def __init__(self, responses, base_url=None):
        self._responses = list(responses)
        self.calls = []
        if base_url is not None:
            self.base_url = base_url

    def pull(self, since, stream=None, caps=None, include_own=False):
        self.calls.append({"since": since, "stream": stream, "caps": caps, "include_own": include_own})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def push(self, events):
        return {"stored": len(events), "received": len(events)}


class OldClient:
    """supports_stream only (a relay client from before Phase 2)."""
    supports_stream = True

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def pull(self, since, stream=None):
        self.calls.append((since, stream))
        return self._responses.pop(0)

    def push(self, events):
        return {"stored": len(events), "received": len(events)}


@pytest.fixture(autouse=True)
def _clean():
    yield
    with ss._RESYNC_STREAMS_LOCK:
        ss._RESYNC_STREAMS.clear()
    ss.unregister_active_service()


def _svc(client, get_conn, hooks, stream="retail"):
    svc = ss.SyncService(lambda: client, get_conn, lambda: "co", stream_label=stream)
    svc.set_bootstrap_hooks(hooks)
    return svc


def _cursor(get_conn):
    c = get_conn()
    try:
        return c.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()["last_seq"]
    finally:
        c.close()


def _empty(**extra):
    return {"events": [], "cursor": 0, **extra}


# ── include_own_once ────────────────────────────────────────────────────────

class Flag:
    def __init__(self, armed=True):
        self.armed = armed
        self.cleared = 0

    def pending(self):
        return self.armed

    def done(self):
        self.cleared += 1
        self.armed = False


def test_include_own_is_asked_until_the_relay_echoes_it_then_never_again(get_conn):
    flag = Flag()
    client = BootClient([_empty(include_own=True), _empty(), _empty()])
    svc = _svc(client, get_conn, BootstrapHooks(include_own_once=flag.pending, include_own_done=flag.done))
    svc.pull_once()
    svc.pull_once()
    svc.pull_once()
    assert [c["include_own"] for c in client.calls] == [True, False, False]
    assert flag.cleared == 1


def test_a_relay_that_ignores_include_own_leaves_the_flag_armed(get_conn):
    """Owner, until Phase 3, verifies the signed body and ignores the field: no echo, so we must not
    pretend the request was honoured."""
    flag = Flag()
    client = BootClient([_empty(), _empty()])
    svc = _svc(client, get_conn, BootstrapHooks(include_own_once=flag.pending, include_own_done=flag.done))
    svc.pull_once()
    svc.pull_once()
    assert [c["include_own"] for c in client.calls] == [True, True]
    assert flag.cleared == 0 and flag.armed


def test_a_failed_pull_does_not_clear_the_flag(get_conn):
    flag = Flag()
    from commercial_runtime.sync.relay_client import NetworkError
    client = BootClient([NetworkError("NETWORK_UNAVAILABLE", "off"), _empty(include_own=True)])
    svc = _svc(client, get_conn, BootstrapHooks(include_own_once=flag.pending, include_own_done=flag.done))
    with pytest.raises(NetworkError):
        svc.pull_once()
    assert flag.armed
    svc.pull_once()
    assert flag.cleared == 1


def test_caps_and_include_own_are_only_given_to_a_client_that_supports_them(get_conn):
    flag = Flag()
    old = OldClient([_empty()])
    svc = _svc(old, get_conn, BootstrapHooks(
        caps=("bootstrap_producer_v1",), include_own_once=flag.pending, include_own_done=flag.done))
    svc.pull_once()                                           # would TypeError if caps/include_own were passed
    assert old.calls == [(0, "retail")] and flag.armed
    new = BootClient([_empty()])
    _svc(new, get_conn, BootstrapHooks(caps=("bootstrap_producer_v1",))).pull_once()
    assert new.calls[0]["caps"] == ["bootstrap_producer_v1"]


def test_a_service_with_no_hooks_behaves_exactly_as_before(get_conn):
    client = BootClient([_empty()])
    ss.SyncService(lambda: client, get_conn, lambda: "co", stream_label="retail").pull_once()
    assert client.calls == [{"since": 0, "stream": "retail", "caps": None, "include_own": False}]


# ── registry fast-forward ───────────────────────────────────────────────────

def _resync(**details):
    return RelayRejected(RESYNC_REQUIRED, "resync", {"pruned_through": 41, **details})


def test_the_registry_stream_fast_forwards_to_where_the_relay_says_its_log_now_ends(get_conn, caplog):
    client = BootClient([_resync(fast_forward_to=9), _empty(cursor=9), ])
    svc = _svc(client, get_conn, BootstrapHooks(), stream="registry")
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        svc.pull_once()                                      # no exception: it skipped ahead
    assert _cursor(get_conn) == 9
    assert svc.get_health()["resync_required"] is False
    svc.pull_once()
    assert client.calls[1]["since"] == 9                      # the next pull resumes from there
    assert any("skipped to position 9" in r.getMessage() for r in caplog.records)


def test_the_retail_stream_is_never_fast_forwarded(get_conn):
    """Deny half: skipping history on the retail stream is the silent partial copy of the shop."""
    client = BootClient([_resync(fast_forward_to=9)])
    svc = _svc(client, get_conn, BootstrapHooks(), stream="retail")
    with pytest.raises(RelayRejected):
        svc.pull_once()
    assert _cursor(get_conn) == 0 and svc.get_health()["resync_required"] is True


def test_a_registry_refusal_naming_no_target_takes_the_ordinary_stop_path(get_conn):
    """Owner (until its mailbox exists) names no target."""
    client = BootClient([_resync()])
    svc = _svc(client, get_conn, BootstrapHooks(), stream="registry")
    with pytest.raises(RelayRejected):
        svc.pull_once()
    assert svc.get_health()["resync_required"] is True and _cursor(get_conn) == 0


@pytest.mark.parametrize("bad", [-1, "9", True, None])
def test_a_nonsense_fast_forward_target_is_ignored(get_conn, bad):
    client = BootClient([_resync(fast_forward_to=bad)])
    svc = _svc(client, get_conn, BootstrapHooks(), stream="registry")
    with pytest.raises(RelayRejected):
        svc.pull_once()
    assert _cursor(get_conn) == 0


# ── on_resync_required ──────────────────────────────────────────────────────

def test_the_resync_hook_runs_once_and_the_stream_stays_stopped_while_a_copy_waits(get_conn):
    seen = []

    def hook(stream, client, relay_url, details):
        seen.append((stream, relay_url, details))
        return True                                           # a copy was staged

    client = BootClient([_resync()], base_url="https://hub.lan:5443")
    c = get_conn()
    c.execute("ALTER TABLE sync_cursor ADD COLUMN relay_url TEXT")
    c.commit()
    c.close()
    svc = _svc(client, get_conn, BootstrapHooks(on_resync_required=hook))
    svc.run_once()
    svc.run_once()
    svc.run_once()
    assert seen == [("retail", "https://hub.lan:5443", {"pruned_through": 41})]     # ONCE, not per tick
    assert len(client.calls) == 1                                                    # no further pulls
    assert svc._resync_required["staged"] is True


def test_a_resync_hook_that_raises_costs_nothing(get_conn):
    def boom(*a):
        raise RuntimeError("hub is on fire")
    client = BootClient([_resync()])
    svc = _svc(client, get_conn, BootstrapHooks(on_resync_required=boom))
    with pytest.raises(RelayRejected):                       # the refusal still surfaces, as before
        svc.pull_once()
    assert svc.get_health()["resync_required"] is True


# ── bootstrap requests ──────────────────────────────────────────────────────

def test_bootstrap_requests_from_a_hub_reach_the_product_after_the_pull_commits(get_conn):
    got = []
    requests = [{"requester": "late-1", "stream": "registry", "requested_at": 1.0}]
    client = BootClient([_empty(bootstrap_requests=requests)])
    svc = _svc(client, get_conn, BootstrapHooks(on_bootstrap_requests=got.append))
    svc.pull_once()
    assert got == [requests]


def test_a_failing_requests_hook_never_breaks_the_pull(get_conn):
    def boom(requests):
        raise RuntimeError("no")
    client = BootClient([_empty(bootstrap_requests=[{"requester": "x", "stream": "registry"}]), _empty()])
    svc = _svc(client, get_conn, BootstrapHooks(on_bootstrap_requests=boom))
    svc.pull_once()
    svc.pull_once()


# ── re-applying the device's own outbox after an install ────────────────────

def _put_in_outbox(get_conn, category_id, name):
    c = get_conn()
    c.execute("INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload) VALUES (?,?,?,?,?)",
              (str(uuid.uuid4()), "category", category_id, "create",
               json.dumps({"id": category_id, "name": name, "description": "", "row_version": 1})))
    c.commit()
    c.close()


def _category_names(get_conn):
    c = get_conn()
    try:
        return [r["name"] for r in c.execute("SELECT name FROM categories ORDER BY name")]
    finally:
        c.close()


def test_the_unpushed_outbox_is_reapplied_once_and_stays_queued(get_conn):
    cid = str(uuid.uuid4())
    _put_in_outbox(get_conn, cid, "Written before the copy")
    state = {"pending": True, "done": 0}

    def done():
        state["pending"] = False
        state["done"] += 1

    client = BootClient([_empty(), _empty()])
    svc = _svc(client, get_conn, BootstrapHooks(reapply_outbox_pending=lambda: state["pending"],
                                                reapply_outbox_done=done))
    assert _category_names(get_conn) == []
    svc.pull_once()
    assert _category_names(get_conn) == ["Written before the copy"]       # re-applied locally
    c = get_conn()
    assert c.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 1   # ...and STILL going to be pushed
    c.close()
    svc.pull_once()
    assert state["done"] == 1                                              # once, not every tick


def test_nothing_is_reapplied_when_the_flag_is_not_armed(get_conn):
    _put_in_outbox(get_conn, str(uuid.uuid4()), "Should stay out")
    svc = _svc(BootClient([_empty()]), get_conn, BootstrapHooks(reapply_outbox_pending=lambda: False))
    svc.pull_once()
    assert _category_names(get_conn) == []
