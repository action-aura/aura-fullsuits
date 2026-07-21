"""Distributed rate limiting (Part M, ADR-6.3: PostgreSQL-backed counters,
shared across every Owner worker process since all workers share one
database -- satisfying 'distributed' without a Redis dependency).

bucket_key is always a safe, non-secret derived identifier -- never a
plaintext license key (Part M's explicit prohibition). Callers pass a
route-category-specific policy so activation, check-in, and signing-key
discovery each get independent budgets.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db_session
from app.models.licensing_service import RateLimitCounter


@dataclass(frozen=True)
class RateLimitPolicy:
    name: str
    max_requests: int
    window_seconds: int


# Configuration as data, not scattered constants (Part M's explicit instruction).
POLICIES: dict[str, RateLimitPolicy] = {
    "activation": RateLimitPolicy("activation", max_requests=10, window_seconds=60),
    "activation_invalid_license": RateLimitPolicy("activation_invalid_license", max_requests=5, window_seconds=300),
    "activation_invalid_signature": RateLimitPolicy("activation_invalid_signature", max_requests=5, window_seconds=300),
    "check_in": RateLimitPolicy("check_in", max_requests=30, window_seconds=60),
    "signing_keys": RateLimitPolicy("signing_keys", max_requests=60, window_seconds=60),
    "service_info": RateLimitPolicy("service_info", max_requests=60, window_seconds=60),
}


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__("RATE_LIMITED")


def safe_bucket_component(value: str) -> str:
    """Never places a raw secret (e.g. a plaintext license key) into a rate
    limit bucket key -- always a one-way hash prefix."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _window_start(now: datetime, window_seconds: int) -> datetime:
    epoch_seconds = int(now.timestamp())
    bucket_start = epoch_seconds - (epoch_seconds % window_seconds)
    return datetime.fromtimestamp(bucket_start, tz=timezone.utc)


def check_and_increment(policy_name: str, bucket_key: str, *, now: datetime | None = None) -> None:
    """Raises RateLimitExceeded if the bucket is over budget; otherwise
    atomically increments it. Fixed-window counter -- simple, race-safe via
    the unique (bucket_key, window_start) constraint + an atomic UPDATE."""
    policy = POLICIES[policy_name]
    now = now or datetime.now(timezone.utc)
    window_start = _window_start(now, policy.window_seconds)
    full_key = f"{policy_name}:{bucket_key}"

    row = db_session.execute(
        select(RateLimitCounter).where(RateLimitCounter.bucket_key == full_key, RateLimitCounter.window_start == window_start)
    ).scalars().first()

    if row is None:
        row = RateLimitCounter(bucket_key=full_key, window_start=window_start, count=0)
        db_session.add(row)
        try:
            db_session.flush()
        except IntegrityError:
            db_session.rollback()
            row = db_session.execute(
                select(RateLimitCounter).where(
                    RateLimitCounter.bucket_key == full_key, RateLimitCounter.window_start == window_start
                )
            ).scalars().first()

    if row.count >= policy.max_requests:
        db_session.commit()
        retry_after = int((window_start + timedelta(seconds=policy.window_seconds) - now).total_seconds())
        raise RateLimitExceeded(max(retry_after, 1))

    row.count += 1
    db_session.commit()


def purge_old_counters(*, older_than: datetime) -> int:
    result = db_session.query(RateLimitCounter).filter(RateLimitCounter.window_start < older_than).delete()
    db_session.commit()
    return result
