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
