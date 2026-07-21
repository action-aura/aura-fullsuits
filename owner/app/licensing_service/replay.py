"""Replay protection: nonce + timestamp freshness (Part L, ADR-6.3: PostgreSQL,
not Redis). Fails closed -- see check_replay_protection_available()."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db_session, get_engine
from app.models.licensing_service import SecurityNonceRecord

NONCE_MIN_LENGTH = 16
NONCE_MAX_LENGTH = 64


class ReplayError(ValueError):
    pass


class ReplayStoreUnavailableError(RuntimeError):
    pass


def validate_timestamp(request_timestamp: datetime, skew_seconds: int, *, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    if request_timestamp.tzinfo is None:
        raise ReplayError("INVALID_TIMESTAMP")
    delta = (now - request_timestamp).total_seconds()
    if abs(delta) > skew_seconds:
        raise ReplayError("TIMESTAMP_OUTSIDE_ALLOWED_WINDOW")


def consume_nonce(nonce: str, scope: str, ttl_seconds: int) -> None:
    """Atomically claims a nonce. The UNIQUE constraint on (nonce, scope) IS
    the replay check -- a duplicate INSERT fails with IntegrityError, which we
    translate to ReplayError('NONCE_REUSED'). No separate SELECT-then-INSERT
    race window exists."""
    if not (NONCE_MIN_LENGTH <= len(nonce) <= NONCE_MAX_LENGTH):
        raise ReplayError("INVALID_REQUEST")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    record = SecurityNonceRecord(nonce=nonce, scope=scope, expires_at=expires_at)
    db_session.add(record)
    try:
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
        raise ReplayError("NONCE_REUSED")


def purge_expired_nonces(*, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    result = db_session.query(SecurityNonceRecord).filter(SecurityNonceRecord.expires_at < now).delete()
    db_session.commit()
    return result


def check_replay_protection_available() -> bool:
    """A real connectivity probe, not just 'the config flag says true'. Used
    by health checks and by the fail-closed startup/request-path gate."""
    try:
        with get_engine().connect() as conn:
            conn.exec_driver_sql("SELECT 1 FROM owner_security_nonce_records LIMIT 1")
        return True
    except Exception:
        return False
