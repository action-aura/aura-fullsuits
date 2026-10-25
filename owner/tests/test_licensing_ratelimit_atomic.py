"""The licensing rate limiter (app/licensing_service/ratelimit.py) and the
single-use release-download token (app/releases/distribution.py) under real
concurrency, on real Postgres.

Both used to read a counter and then write `count += 1` from Python. Under
READ COMMITTED, several requests released together all read the same value,
all pass the `count < limit` check, and all write the same "+1" -- so the
activation / check-in brute-force limit (and the one-shot download token)
could be exceeded by simply sending requests in parallel. Each thread here
runs on its own scoped session, i.e. its own pooled Postgres connection, the
same as separate Gunicorn workers; a barrier releases them together.

Both directions are pinned: the deny half (exactly `limit` pass, never more)
and the allow half (under-limit requests pass, a new window starts from zero,
another bucket is untouched, the nightly housekeeping purge cannot reset a
live window)."""
from __future__ import annotations

import threading
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.extensions import db_session
from app.licensing_service import ratelimit
from app.models.licensing_service import RateLimitCounter

# A fixed instant well inside a 60 s window (window starts at :00), so no
# test can straddle a window boundary while it runs.
_NOW = datetime(2026, 10, 25, 9, 0, 20, tzinfo=timezone.utc)


def _policy(name: str, limit: int, window_seconds: int = 60) -> str:
    ratelimit.POLICIES[name] = ratelimit.RateLimitPolicy(name, max_requests=limit, window_seconds=window_seconds)
    return name


def _stored_count(full_key: str) -> int | None:
    rows = db_session.execute(select(RateLimitCounter).where(RateLimitCounter.bucket_key == full_key)).scalars().all()
    return sum(r.count for r in rows) if rows else None


def _run_together(app, threads_n: int, work) -> list[str]:
    """Run `work()` on `threads_n` threads released by one barrier, each on
    its own scoped session (own connection). Returns one outcome per thread:
    'ok', 'limited', or 'error:...' (surfaced in the caller's assertion)."""
    barrier = threading.Barrier(threads_n)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker():
        with app.app_context():
            try:
                # Check out (and open) this thread's connection BEFORE the
                # barrier, as a long-running worker's pool already would be.
                # Without it each thread spends tens of ms connecting after
                # release, which staggers them enough to hide the race: the
                # last-slot test passed on the read-then-write code until
                # this line was added (measured 2026-10-25).
                db_session.execute(text("SELECT 1"))
                barrier.wait(timeout=30)
                work()
                result = "ok"
            except ratelimit.RateLimitExceeded:
                result = "limited"
            except Exception as exc:  # noqa: BLE001 -- reported in the assertion
                code = getattr(exc, "internal_reason_code", None)
                result = f"rejected:{code}" if code else f"error:{type(exc).__name__}:{exc}"
            finally:
                db_session.remove()
            with lock:
                outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(outcomes) == threads_n, f"a worker thread hung: {outcomes}"
    return outcomes


# -- deny half: concurrency never passes the limit ----------------------------

def test_concurrent_requests_on_an_empty_bucket_pass_exactly_the_limit(app):
    name = _policy("atomic_fresh", limit=5)
    outcomes = _run_together(app, 12, lambda: ratelimit.check_and_increment(name, "203.0.113.7", now=_NOW))
    assert sorted(outcomes) == sorted(["ok"] * 5 + ["limited"] * 7), Counter(outcomes)
    with app.app_context():
        assert _stored_count(f"{name}:203.0.113.7") == 5


def test_concurrent_requests_cannot_both_take_the_last_slot(app):
    """The reported bug exactly: 4 of 5 used, then a burst. One may pass."""
    name = _policy("atomic_last_slot", limit=5)
    with app.app_context():
        for _ in range(4):
            ratelimit.check_and_increment(name, "203.0.113.8", now=_NOW)
    outcomes = _run_together(app, 12, lambda: ratelimit.check_and_increment(name, "203.0.113.8", now=_NOW))
    assert sorted(outcomes) == sorted(["ok"] * 1 + ["limited"] * 11), Counter(outcomes)
    with app.app_context():
        assert _stored_count(f"{name}:203.0.113.8") == 5


def test_refused_requests_do_not_grow_the_stored_count(app):
    name = _policy("atomic_refused", limit=3)
    with app.app_context():
        for _ in range(3):
            ratelimit.check_and_increment(name, "b", now=_NOW)
        for _ in range(4):
            with pytest.raises(ratelimit.RateLimitExceeded) as exc:
                ratelimit.check_and_increment(name, "b", now=_NOW)
        # 40 s left in the window that started at 09:00:00.
        assert exc.value.retry_after_seconds == 40
        assert _stored_count(f"{name}:b") == 3


# -- allow half ---------------------------------------------------------------

def test_requests_under_the_limit_all_pass_and_are_counted(app):
    name = _policy("atomic_under", limit=5)
    with app.app_context():
        for expected in range(1, 6):
            ratelimit.check_and_increment(name, "c", now=_NOW)
            assert _stored_count(f"{name}:c") == expected


def test_next_window_starts_from_zero(app):
    name = _policy("atomic_rollover", limit=2)
    with app.app_context():
        for _ in range(2):
            ratelimit.check_and_increment(name, "d", now=_NOW)
        with pytest.raises(ratelimit.RateLimitExceeded):
            ratelimit.check_and_increment(name, "d", now=_NOW)
        next_window = _NOW + timedelta(seconds=60)
        for _ in range(2):
            ratelimit.check_and_increment(name, "d", now=next_window)
        with pytest.raises(ratelimit.RateLimitExceeded):
            ratelimit.check_and_increment(name, "d", now=next_window)


def test_a_full_bucket_does_not_limit_another_bucket(app):
    name = _policy("atomic_isolated", limit=1)
    with app.app_context():
        ratelimit.check_and_increment(name, "e1", now=_NOW)
        with pytest.raises(ratelimit.RateLimitExceeded):
            ratelimit.check_and_increment(name, "e1", now=_NOW)
        ratelimit.check_and_increment(name, "e2", now=_NOW)  # must not raise


def test_housekeeping_purge_cannot_reset_a_live_window(app):
    """`flask commercial housekeeping --apply` deletes counters whose
    window_start is more than a day old (app/cli.py). Every licensing window
    is minutes long and window_start is the window's START, so a row is only
    purged long after its window ended. Pinned two ways: the purge leaves a
    current full window alone, and no policy may grow a window that a
    one-day purge could cut short (that would silently reset its budget
    every night -- the trap app/leads/discovery_budget.py had to avoid)."""
    name = _policy("atomic_purge", limit=1)
    with app.app_context():
        ratelimit.check_and_increment(name, "f", now=_NOW)
        ratelimit.purge_old_counters(older_than=_NOW - timedelta(days=1))
        with pytest.raises(ratelimit.RateLimitExceeded):
            ratelimit.check_and_increment(name, "f", now=_NOW)
    too_long = {p.name: p.window_seconds for p in ratelimit.POLICIES.values() if p.window_seconds >= 86400}
    assert too_long == {}, f"window >= 1 day would be cut short by the nightly purge: {too_long}"


# -- the single-use release download token ------------------------------------

def test_concurrent_fetches_of_a_single_use_token_download_once(app, client, seeded, signing_key):
    from app.releases.distribution import fetch_download
    from tests.test_phase9r_private_distribution import TestFetchDownload

    token, content = TestFetchDownload()._authorized_token(app, client, seeded, signing_key, "race@example.com")
    outcomes = _run_together(app, 8, lambda: fetch_download(token))
    assert sorted(outcomes) == sorted(["ok"] + ["rejected:TOKEN_ALREADY_USED"] * 7), Counter(outcomes)
    with app.app_context():
        from app.models.release_distribution import ReleaseDownloadAuthorization

        row = db_session.execute(select(ReleaseDownloadAuthorization)).scalars().one()
        assert row.use_count == 1
