from datetime import datetime, timedelta, timezone

import pytest

from commercial_runtime.licensing_contracts.trusted_time import (
    TrustedTimeError,
    detect_rollback,
    new_anchor,
    rehydrate_anchor,
    trusted_now,
)


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
