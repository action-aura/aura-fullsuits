"""Append-only, tamper-evident audit log (Part T, ADR-8).

record() is the ONLY function anywhere in the codebase that writes an
AuditLog row. No route, service, or admin tool updates or deletes a row here.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from flask import request
from sqlalchemy import select

from app.extensions import db_session
from app.models.audit import AuditLog

# Anything matching (case-insensitive substring) one of these is stripped from
# before/after state before it is ever written to the audit log.
_SECRET_FIELD_MARKERS = (
    "password",
    "mfa_secret",
    "totp_secret",
    "recovery_code",
    "recovery_codes",
    "key_secret",
    "full_key",
    "token",
    "token_hash",
    "credential",
    "pepper",
    "secret",
)


def redact(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        k: ("<redacted>" if any(marker in k.lower() for marker in _SECRET_FIELD_MARKERS) else v)
        for k, v in state.items()
    }


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _last_hash() -> str | None:
    stmt = select(AuditLog.current_hash).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(1)
    return db_session.execute(stmt).scalars().first()


def record(
    *,
    actor_staff_user_id,
    actor_role_snapshot: str | None,
    action_code: str,
    entity_type: str,
    entity_public_id: str | None,
    result: str = "SUCCESS",
    reason: str | None = None,
    before_state: dict | None = None,
    after_state: dict | None = None,
    correlation_id: str | None = None,
) -> AuditLog:
    previous_hash = _last_hash()
    before_redacted = redact(before_state)
    after_redacted = redact(after_state)
    payload = _canonical(
        {
            "actor_staff_user_id": str(actor_staff_user_id) if actor_staff_user_id else None,
            "action_code": action_code,
            "entity_type": entity_type,
            "entity_public_id": entity_public_id,
            "result": result,
            "before_state": before_redacted,
            "after_state": after_redacted,
        }
    )
    current_hash = hashlib.sha256(f"{previous_hash or ''}{payload}".encode("utf-8")).hexdigest()

    try:
        ip = request.remote_addr
        ua = (request.user_agent.string or "")[:512] if request.user_agent else None
    except RuntimeError:
        ip = None
        ua = None

    row = AuditLog(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=actor_role_snapshot,
        action_code=action_code,
        entity_type=entity_type,
        entity_public_id=entity_public_id,
        correlation_id=correlation_id,
        ip_address=ip,
        user_agent=ua,
        result=result,
        reason=reason,
        before_state_redacted=before_redacted,
        after_state_redacted=after_redacted,
        previous_hash=previous_hash,
        current_hash=current_hash,
    )
    db_session.add(row)
    db_session.commit()
    return row


def verify_chain() -> tuple[bool, str | None]:
    """Walks the full audit log in insertion order, recomputing each hash.
    Returns (ok, first_broken_entity_public_id_or_None)."""
    stmt = select(AuditLog).order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
    rows = db_session.execute(stmt).scalars().all()
    previous_hash = None
    for row in rows:
        payload = _canonical(
            {
                "actor_staff_user_id": str(row.actor_staff_user_id) if row.actor_staff_user_id else None,
                "action_code": row.action_code,
                "entity_type": row.entity_type,
                "entity_public_id": row.entity_public_id,
                "result": row.result,
                "before_state": row.before_state_redacted,
                "after_state": row.after_state_redacted,
            }
        )
        expected = hashlib.sha256(f"{previous_hash or ''}{payload}".encode("utf-8")).hexdigest()
        if row.previous_hash != previous_hash or row.current_hash != expected:
            return False, str(row.id)
        previous_hash = row.current_hash
    return True, None
