"""The SyncService `on_tick` hook (registry v9 device HEARTBEAT).

The registry stream's own timer is what re-runs the device announcer, so a till
that stays up for a week keeps telling its peers it is alive. These tests pin
the hook's CONTRACT by counting calls, never by checking an outcome:

  * it is called exactly once per scheduled tick, and BEFORE `run_once()` (so
    whatever it queues rides that tick's push);
  * a hook that raises is logged and swallowed: `run_once()` still runs and the
    next tick is still scheduled (a broken announcer must never stop sync);
  * with no hook the tick is exactly what it was before.

They drive the REAL `_schedule_next` timer path (a 10ms first delay), not a
private helper, so removing the call from `_tick` turns them red.
"""
from __future__ import annotations

import threading

from commercial_runtime.sync.sync_service import SyncService


def _service(on_tick=None):
    svc = SyncService(
        client_factory=lambda: None,
        get_conn=lambda: None,
        local_company_id_provider=lambda: "co-1",
        on_tick=on_tick,
    )
    return svc


def _drive_one_tick(svc, order):
    """Run exactly one real timer tick and return once it has finished."""
    done = threading.Event()

    def _run_once():
        order.append("run_once")
        done.set()

    svc.run_once = _run_once
    # Keep the reschedule from arming a second 10s timer we would have to chase.
    svc._next_interval_seconds = lambda: 3600.0
    svc._stopped.clear()
    svc._schedule_next(0.01)
    assert done.wait(5.0), "the scheduled tick never ran"
    svc.stop()


def test_on_tick_is_called_once_per_tick_and_before_run_once():
    order = []
    svc = _service(on_tick=lambda: order.append("on_tick"))
    _drive_one_tick(svc, order)
    assert order == ["on_tick", "run_once"]


def test_a_raising_on_tick_is_swallowed_and_run_once_still_runs(caplog):
    calls = []

    def _boom():
        calls.append("on_tick")
        raise RuntimeError("announcer exploded")

    order = []
    svc = _service(on_tick=_boom)
    with caplog.at_level("ERROR"):
        _drive_one_tick(svc, order)
    assert calls == ["on_tick"]
    assert order == ["run_once"]
    assert any("on_tick hook FAILED" in r.getMessage() for r in caplog.records)


def test_without_a_hook_the_tick_is_unchanged():
    order = []
    svc = _service(on_tick=None)
    _drive_one_tick(svc, order)
    assert order == ["run_once"]
