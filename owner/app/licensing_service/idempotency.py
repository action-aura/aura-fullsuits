"""Persistent idempotency for activation/deactivation (Part N)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.extensions import db_session
from app.models.licensing_service import ExternalIdempotencyRecord

IDEMPOTENCY_RECORD_TTL_SECONDS = 7 * 24 * 3600


class IdempotencyConflictError(ValueError):
    """Same idempotency key, materially different request payload."""


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def check_idempotency(idempotency_key: str, operation_type: str, request_fingerprint: str) -> ExternalIdempotencyRecord | None:
    """Returns the existing record if this exact (key, operation, fingerprint)
    combination was already processed -- caller should return the same
    logical result without redoing the operation. Raises IdempotencyConflictError
    if the key matches but the fingerprint differs (a client reusing a key for
    a different request, not a legitimate retry)."""
    key_hash = _hash(idempotency_key)
    existing = db_session.execute(
        select(ExternalIdempotencyRecord).where(
            ExternalIdempotencyRecord.idempotency_key_hash == key_hash,
            ExternalIdempotencyRecord.operation_type == operation_type,
        )
    ).scalars().first()
    if existing is None:
        return None
    fingerprint_hash = _hash(request_fingerprint)
    if existing.request_fingerprint_hash != fingerprint_hash:
        raise IdempotencyConflictError("IDEMPOTENCY_CONFLICT")
    return existing


def record_idempotency(
    idempotency_key: str, operation_type: str, request_fingerprint: str, result_reference: str | None,
    response_status: str, cached_response_json: str | None = None,
) -> ExternalIdempotencyRecord:
    """INSERTs a fresh record for the common case (one key, one terminal
    result, forever). UPDATEs in place when a record for this
    (key, operation_type) already exists -- the only way that happens is a
    prior PENDING write (Phase 8 Part O: manual activation approval leaves
    a non-terminal record with no `cached_response_json` so a retry can
    re-observe reality, see `licensing_service/activation.py`'s
    `_pending_review_response()`) now being finalized to SUCCESS/FAILURE.
    Every pre-Phase-8 caller only ever calls this once per key, so for them
    `existing` is always None and behavior is unchanged."""
    key_hash = _hash(idempotency_key)
    fingerprint_hash = _hash(request_fingerprint)
    existing = db_session.execute(
        select(ExternalIdempotencyRecord).where(
            ExternalIdempotencyRecord.idempotency_key_hash == key_hash,
            ExternalIdempotencyRecord.operation_type == operation_type,
        )
    ).scalars().first()
    if existing is not None:
        existing.request_fingerprint_hash = fingerprint_hash
        existing.result_reference = result_reference
        existing.response_status = response_status
        existing.cached_response_json = cached_response_json
        existing.expires_at = datetime.now(timezone.utc) + timedelta(seconds=IDEMPOTENCY_RECORD_TTL_SECONDS)
        db_session.commit()
        return existing

    row = ExternalIdempotencyRecord(
        idempotency_key_hash=key_hash,
        operation_type=operation_type,
        request_fingerprint_hash=fingerprint_hash,
        result_reference=result_reference,
        response_status=response_status,
        cached_response_json=cached_response_json,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=IDEMPOTENCY_RECORD_TTL_SECONDS),
    )
    db_session.add(row)
    db_session.commit()
    return row


def purge_expired(*, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    result = db_session.query(ExternalIdempotencyRecord).filter(ExternalIdempotencyRecord.expires_at < now).delete()
    db_session.commit()
    return result
