from datetime import datetime, timedelta, timezone

import pytest

from commercial_runtime.licensing_contracts import trusted_time as trusted_time_module
from commercial_runtime.licensing_contracts.trusted_time import (
    TrustedTimeError,
    cache_fresh_anchor,
    detect_rollback,
    get_cached_or_rehydrate_anchor,
    new_anchor,
    rehydrate_anchor,
    trusted_now,
)


@pytest.fixture(autouse=True)
def _clear_anchor_cache():
    trusted_time_module._ANCHOR_CACHE.clear()
    yield
    trusted_time_module._ANCHOR_CACHE.clear()


def test_trusted_now_advances_with_monotonic_time(monkeypatch):
    base = datetime(2026, 7, 22, 10, 0, 0, tzinfo=timezone.utc)
    fake_monotonic = [100.0]
    monkeypatch.setattr("commercial_runtime.licensing_contracts.trusted_time.time.monotonic", lambda: fake_monotonic[0])
    anchor = new_anchor(base)
    fake_monotonic[0] = 105.0
    now = trusted_now(anchor)
    assert now == base + timedelta(seconds=5)


def test_anchor_rejects_naive_datetime():
    with pytest.raises(TrustedTimeError):
        new_anchor(datetime(2026, 7, 22, 10, 0, 0))


def test_rehydrate_preserves_server_time():
    base = datetime(2026, 7, 22, 10, 0, 0, tzinfo=timezone.utc)
    anchor = rehydrate_anchor(base)
    assert anchor.server_time == base


def test_detect_rollback_true_when_local_clock_far_behind():
    anchor_time = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
    anchor = new_anchor(anchor_time)
    local_now = anchor_time - timedelta(hours=2)
    assert detect_rollback(anchor, local_now, tolerance_seconds=300) is True


def test_detect_rollback_false_within_tolerance():
    anchor_time = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
    anchor = new_anchor(anchor_time)
    local_now = anchor_time - timedelta(seconds=60)
    assert detect_rollback(anchor, local_now, tolerance_seconds=300) is False


def test_detect_rollback_false_for_timezone_change_same_utc_instant():
    anchor_time = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
    anchor = new_anchor(anchor_time)
    # Same UTC instant, expressed in a different timezone -- not a rollback.
    local_now = anchor_time.astimezone(timezone(timedelta(hours=-5)))
    assert detect_rollback(anchor, local_now, tolerance_seconds=1) is False


def test_detect_rollback_false_when_local_clock_ahead():
    anchor_time = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
    anchor = new_anchor(anchor_time)
    local_now = anchor_time + timedelta(hours=1)
    assert detect_rollback(anchor, local_now, tolerance_seconds=300) is False


def test_detect_rollback_rejects_naive_local_clock():
    anchor = new_anchor(datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc))
    with pytest.raises(TrustedTimeError):
        detect_rollback(anchor, datetime(2026, 7, 22, 12, 0, 0), tolerance_seconds=1)


def test_cache_fresh_anchor_then_get_cached_reflects_real_elapsed_time(monkeypatch):
    """Phase 7V-F regression: the anchor cached synchronously at sync time
    (cache_fresh_anchor, called by activation.py and checkin_scheduler.py's
    _persist_fresh_assertion at the moment server_time is persisted) must be
    the SAME object a later get_cached_or_rehydrate_anchor() call returns,
    so real elapsed monotonic time between the two calls is correctly
    reflected -- not silently collapsed to ~0 by re-pinning fresh each time.
    """
    import time as time_module

    fake_monotonic = [500.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: fake_monotonic[0])

    server_time = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
    anchor_at_sync = cache_fresh_anchor("inst-cache-test", server_time)

    fake_monotonic[0] = 500.0 + 75.0  # 75 real seconds later, still offline
    anchor_later = get_cached_or_rehydrate_anchor("inst-cache-test", server_time.isoformat())

    assert anchor_later is anchor_at_sync
    assert (trusted_now(anchor_later) - server_time).total_seconds() == 75.0


def test_get_cached_or_rehydrate_anchor_falls_back_when_server_time_changes():
    """A genuinely NEW successful sync (different server_time) must not
    reuse the stale cached anchor -- it should re-pin fresh for the new
    value, exactly like a real new sync arriving."""
    t1 = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 7, 22, 12, 5, 0, tzinfo=timezone.utc)

    cache_fresh_anchor("inst-cache-test-2", t1)
    anchor2 = get_cached_or_rehydrate_anchor("inst-cache-test-2", t2.isoformat())

    assert anchor2.server_time == t2
