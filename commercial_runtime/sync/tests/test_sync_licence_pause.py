"""Keep-selling rule, sync half: an UNPAID subscription pauses sync, it does not break it.

Owner rule: a shop whose subscription lapsed keeps selling; it only loses multi-device sync.
After renewal sync must catch up and NOTHING created during the lapse may be lost.

So SyncService takes an optional `sync_paused` callable (None = never paused, which is every
pre-existing caller and the clinic). While it says True:

  * the relay client is never even built (zero client_factory calls, so zero network),
  * outbox rows and the cursor are untouched -- and rows keep ACCUMULATING (enqueueing is not
    gated; that is the catch-up),
  * neither a success nor a failure is recorded (no false "synced", no false "sync failing"),
  * health carries `paused_by_licence: True`,
  * one info log entering the pause, one leaving it (not one per tick).

The tests assert THE CHECK RAN (client_factory call count, pause-callable read count), not only
an outcome: with the guard removed, a fake client that happened to have nothing queued would
still "pass" an outcome-only test.

Run (one file per process):
    pytest commercial_runtime/sync/tests/test_sync_licence_pause.py -q
"""
import logging
import time

from commercial_runtime.sync import sync_service as sync_service_module
from commercial_runtime.sync.sync_service import (
    SyncService,
    nudge,
    register_active_service,
    unregister_active_service,
)
from commercial_runtime.sync.tests.test_sync_service import (  # noqa: F401
    FakeRelayClient,
    _cursor,
    _insert_outbox_row,
    _outbox_rows,
    db_path,
    get_conn,
)


class _CountingFactory:
    """Counts how many times the service tried to build a relay client at all."""

    def __init__(self, client):
        self.client = client
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.client


class _Switch:
    def __init__(self, paused):
        self.paused = paused
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return self.paused


def _service(get_conn, switch, client=None):
    client = client or FakeRelayClient(
        push_responses=[{}] * 5, pull_responses=[{"events": [], "cursor": 0}] * 5)
    factory = _CountingFactory(client)
    return SyncService(factory, get_conn, sync_paused=switch), factory, client


def test_paused_makes_zero_client_calls_and_leaves_outbox_and_cursor_alone(get_conn):
    _insert_outbox_row(get_conn)
    _insert_outbox_row(get_conn)
    switch = _Switch(True)
    service, factory, client = _service(get_conn, switch)

    service.push_once()
    service.pull_once()
    service.run_once()

    assert switch.reads > 0, "the pause callable was never consulted -- the gate did not run"
    assert factory.calls == 0, "a paused service still built a relay client"
    assert client.push_calls == [] and client.pull_calls == []
    assert len(_outbox_rows(get_conn)) == 2
    assert _cursor(get_conn) == 0


def test_paused_push_once_alone_makes_no_client_call(get_conn):
    _insert_outbox_row(get_conn)
    service, factory, _client = _service(get_conn, _Switch(True))
    service.push_once()
    assert factory.calls == 0 and len(_outbox_rows(get_conn)) == 1


def test_paused_pull_once_alone_makes_no_client_call(get_conn):
    service, factory, _client = _service(get_conn, _Switch(True))
    service.pull_once()
    assert factory.calls == 0 and _cursor(get_conn) == 0


def test_paused_run_once_records_neither_success_nor_failure(get_conn):
    switch = _Switch(True)
    service, _factory, _client = _service(get_conn, switch)

    service.run_once()

    health = service.get_health()
    assert health["paused_by_licence"] is True
    for half in ("push", "pull"):
        assert health[half]["last_success_at"] is None, "a paused tick claimed it synced"
        assert health[half]["last_failure_at"] is None, "a paused tick raised a false failure"
        assert health[half]["consecutive_failures"] == 0
        assert health[half]["healthy"] is True
    assert health["never_synced"] is True


def test_health_flag_is_always_present_and_false_when_unpaused_or_unconfigured(get_conn):
    plain = SyncService(lambda: FakeRelayClient(), get_conn)
    assert plain.get_health()["paused_by_licence"] is False
    service, _f, _c = _service(get_conn, _Switch(False))
    assert service.get_health()["paused_by_licence"] is False


def test_rows_queued_during_the_pause_are_all_pushed_after_renewal(get_conn):
    """The catch-up: pre-pause row + rows created during the lapse all reach the relay."""
    _insert_outbox_row(get_conn)                   # before the lapse
    switch = _Switch(True)
    service, factory, client = _service(get_conn, switch)

    service.run_once()                             # lapsed tick: nothing leaves
    _insert_outbox_row(get_conn)                   # sold while lapsed
    _insert_outbox_row(get_conn)                   # sold while lapsed
    service.run_once()
    assert factory.calls == 0 and len(_outbox_rows(get_conn)) == 3

    switch.paused = False                          # renewal -- no restart, same instance
    service.run_once()

    assert len(client.push_calls) == 1
    assert len(client.push_calls[0]) == 3, "not every lapse-era row was pushed after renewal"
    assert _outbox_rows(get_conn) == []
    assert len(client.pull_calls) == 1, "pull did not resume after renewal"
    health = service.get_health()
    assert health["paused_by_licence"] is False
    assert health["push"]["last_success_at"] is not None


def test_a_pause_callable_that_raises_means_not_paused_and_sync_runs(get_conn, caplog):
    """Sync was never gated before; if we cannot read the licence we fail toward today's behaviour."""
    _insert_outbox_row(get_conn)

    def boom():
        raise RuntimeError("licence store unreadable")

    client = FakeRelayClient(push_responses=[{}], pull_responses=[{"events": [], "cursor": 0}] * 2)
    service = SyncService(_CountingFactory(client), get_conn, sync_paused=boom)
    with caplog.at_level(logging.WARNING, logger=sync_service_module.logger.name):
        service.run_once()
        service.run_once()

    assert len(client.push_calls) == 1 and len(client.pull_calls) == 2
    assert service.get_health()["paused_by_licence"] is False
    raised_logs = [r for r in caplog.records if "sync_paused" in r.getMessage()]
    assert len(raised_logs) == 1, "the unreadable-licence error must be logged once, not per tick"


def test_pause_and_resume_are_each_logged_once_not_per_tick(get_conn, caplog):
    switch = _Switch(True)
    service, _f, _c = _service(get_conn, switch)
    with caplog.at_level(logging.INFO, logger=sync_service_module.logger.name):
        for _ in range(4):
            service.run_once()
        switch.paused = False
        for _ in range(2):
            service.run_once()
    entering = [r for r in caplog.records if "PAUSED" in r.getMessage()]
    leaving = [r for r in caplog.records if "RESUMED" in r.getMessage()]
    assert len(entering) == 1 and len(leaving) == 1
    assert all(r.levelno == logging.INFO for r in entering + leaving)


def test_nudge_does_not_push_or_record_success_while_paused(get_conn):
    """nudge() is the route-triggered push entry; it must honour the pause too."""
    _insert_outbox_row(get_conn)
    switch = _Switch(True)
    service, factory, client = _service(get_conn, switch)
    register_active_service(service)
    try:
        nudge()
        deadline = time.time() + 1.0
        while time.time() < deadline:      # give the daemon thread a chance to (wrongly) run
            time.sleep(0.05)
    finally:
        unregister_active_service()
    assert switch.reads > 0, "nudge never consulted the pause -- the gate did not run"
    assert factory.calls == 0 and client.push_calls == []
    assert len(_outbox_rows(get_conn)) == 1
    assert service.get_health()["push"]["last_success_at"] is None
