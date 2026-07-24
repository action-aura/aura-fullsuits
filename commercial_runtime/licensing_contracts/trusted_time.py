"""Trusted-time tracking and clock-rollback detection (Part N).

Never trust the raw local wall clock for a licensing decision. Current
trusted time is derived from the last Owner-verified server timestamp plus
elapsed monotonic time since that observation -- immune to wall-clock
changes (DST, timezone, NTP correction, deliberate rollback) between
observations. See docs/licensing/phase7/phase7-threat-model.md's "Trusted
time" section for the full design rationale.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone


class TrustedTimeError(ValueError):
    pass


@dataclass(frozen=True)
class TrustedTimeAnchor:
    """A pairing of an Owner-verified wall-clock time with the monotonic
    clock reading taken at the same instant. Persisted (via
    LicenseStateRepository) so it survives process restarts; monotonic
    readings do not carry meaning across process boundaries, so on load the
    anchor is re-pinned to a *fresh* monotonic reading taken at load time,
    with the wall-clock value carried over unchanged -- this is safe because
    the wall-clock value is only ever compared for consistency (rollback
    detection), never used to compute elapsed time by itself."""

    server_time: datetime
    monotonic_at_anchor: float

    def __post_init__(self):
        if self.server_time.tzinfo is None:
            raise TrustedTimeError("server_time must be timezone-aware.")


def new_anchor(server_time: datetime) -> TrustedTimeAnchor:
    return TrustedTimeAnchor(server_time=server_time, monotonic_at_anchor=time.monotonic())


def rehydrate_anchor(server_time: datetime) -> TrustedTimeAnchor:
    """Re-pin a persisted anchor's wall-clock value to a fresh monotonic
    reading after a process restart. See TrustedTimeAnchor's docstring."""
    return new_anchor(server_time)


# Phase 7V-F: process-lifetime cache so the SAME TrustedTimeAnchor object
# (and thus the SAME monotonic_at_anchor pin) is reused across every caller
# that resolves an anchor for a given installation, for as long as the
# persisted server_time hasn't changed (i.e. no new successful sync has
# happened). Discovered via live production-like validation: routes.py
# deliberately builds a fresh request-scoped object graph on every HTTP call
# (so config/key changes take effect without a restart), and
# rehydrate_anchor() re-pins monotonic_at_anchor to "right now" every time
# it's called -- calling it lazily, on whatever request happens to be first
# to ask after an outage began, silently collapses elapsed offline duration
# back to ~0 no matter how much real time has actually passed, because the
# pin only captures a valid "how long ago was server_time" relationship if
# it's taken at the SAME moment server_time was actually established. The
# fix: whoever persists a fresh trusted_time_anchor_server_time (activation,
# a successful check-in) must also populate this cache synchronously, at
# that exact moment -- see cache_fresh_anchor(). Every later resolution
# (get_cached_anchor()) then correctly measures real elapsed monotonic time
# from that instant, until the next successful sync replaces it.
_ANCHOR_CACHE: dict = {}


def cache_fresh_anchor(cache_key, server_time: datetime) -> TrustedTimeAnchor:
    """Call synchronously at the moment server_time is persisted (activation
    or a successful check-in) -- this is the only place a monotonic pin can
    correctly correspond to "server_time == real now"."""
    anchor = new_anchor(server_time)
    _ANCHOR_CACHE[cache_key] = (server_time.isoformat(), anchor)
    return anchor


def get_cached_or_rehydrate_anchor(cache_key, server_time_iso: str) -> TrustedTimeAnchor:
    """Reuse the cached anchor if server_time hasn't changed since it was
    cached; otherwise fall back to rehydrate_anchor() (e.g. after a process
    restart with no successful sync yet in this process -- the residual gap
    documented in phase7v-final/final-residual-risk-register.md)."""
    cached = _ANCHOR_CACHE.get(cache_key)
    if cached is not None and cached[0] == server_time_iso:
        return cached[1]
    anchor = rehydrate_anchor(datetime.fromisoformat(server_time_iso))
    _ANCHOR_CACHE[cache_key] = (server_time_iso, anchor)
    return anchor


def trusted_now(anchor: TrustedTimeAnchor) -> datetime:
    elapsed = time.monotonic() - anchor.monotonic_at_anchor
    if elapsed < 0:
        # A negative monotonic delta is not supposed to be possible on any
        # supported platform; fail closed rather than silently going back in
        # time by treating it as zero elapsed.
        raise TrustedTimeError("Monotonic clock moved backward -- refusing to compute trusted time.")
    from datetime import timedelta
    return anchor.server_time + timedelta(seconds=elapsed)


def detect_rollback(
    anchor: TrustedTimeAnchor,
    local_wall_clock_now: datetime,
    tolerance_seconds: int,
) -> bool:
    """True if the local wall clock reads suspiciously earlier than the
    trusted anchor by more than tolerance_seconds, in UTC-normalized terms
    (so timezone/DST changes -- which shift the *local representation* but
    not the UTC instant -- never trigger a false positive; only a genuine
    backward jump in the UTC instant does)."""
    if local_wall_clock_now.tzinfo is None:
        raise TrustedTimeError("local_wall_clock_now must be timezone-aware.")
    local_utc = local_wall_clock_now.astimezone(timezone.utc)
    anchor_utc = anchor.server_time.astimezone(timezone.utc)
    delta_seconds = (anchor_utc - local_utc).total_seconds()
    return delta_seconds > tolerance_seconds
