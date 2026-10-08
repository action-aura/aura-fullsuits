"""The daily poll: check only, never downloads or installs, never dies."""
import logging

from commercial_runtime.updates.scheduler import (
    CHECK_INTERVAL_SECONDS,
    FIRST_CHECK_DELAY_SECONDS,
    UpdateScheduler,
)


class FakeTimer:
    created = []

    def __init__(self, delay, fn):
        self.delay, self.fn = delay, fn
        self.daemon = False
        self.started = False
        self.cancelled = False
        FakeTimer.created.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


class FakeService:
    """Records which methods the scheduler touches. Only check_quietly is allowed."""

    def __init__(self, raises=None):
        self.checks = 0
        self.raises = raises
        self.touched = []

    def check_quietly(self):
        self.checks += 1
        if self.raises:
            raise self.raises

    def __getattr__(self, name):          # download/install/anything else
        self.touched.append(name)
        raise AssertionError("the scheduler must not call " + name)


def setup_function(_):
    FakeTimer.created = []


def test_first_check_is_120_seconds_after_start_then_daily_and_timers_are_daemons():
    assert FIRST_CHECK_DELAY_SECONDS == 120 and CHECK_INTERVAL_SECONDS == 86400
    svc = FakeService()
    sched = UpdateScheduler(svc, timer_factory=FakeTimer)
    sched.start()
    first = FakeTimer.created[0]
    assert first.delay == 120 and first.started and first.daemon is True
    first.fn()                                         # the timer fires
    assert svc.checks == 1                             # the check RAN
    second = FakeTimer.created[1]
    assert second.delay == 86400 and second.started and second.daemon is True


def test_a_crashing_check_does_not_break_the_chain(caplog):
    svc = FakeService(raises=RuntimeError("boom"))
    sched = UpdateScheduler(svc, timer_factory=FakeTimer)
    sched.start()
    with caplog.at_level(logging.ERROR, logger="aura.updates"):
        FakeTimer.created[0].fn()
    assert len(FakeTimer.created) == 2                 # next poll still scheduled
    assert any("crashed" in r.getMessage() for r in caplog.records)


def test_it_only_ever_checks_never_downloads_or_installs():
    svc = FakeService()
    sched = UpdateScheduler(svc, timer_factory=FakeTimer)
    sched.start()
    FakeTimer.created[0].fn()
    assert svc.touched == []


def test_stop_cancels_and_prevents_rearming():
    svc = FakeService()
    sched = UpdateScheduler(svc, timer_factory=FakeTimer)
    sched.start()
    sched.stop()
    assert FakeTimer.created[0].cancelled
    FakeTimer.created[0].fn()                          # a tick racing the stop
    assert len(FakeTimer.created) == 1


def test_start_twice_arms_one_timer():
    sched = UpdateScheduler(FakeService(), timer_factory=FakeTimer)
    sched.start()
    sched.start()
    assert len(FakeTimer.created) == 1
