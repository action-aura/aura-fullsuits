"""Phase 9.5E -- scheduled report snapshot generation. In-process,
CLI-triggerable adapter (no Celery/APScheduler/OS-level task) -- Milestone 1's
duplication-risk report explicitly reasoned this is the right default for a
dependency-light codebase; an OS-level scheduled task would repeat exactly
the blast-radius class Milestone 0 just spent its whole effort auditing
(`.autosync`). Concurrency safety: a Postgres transaction-scoped advisory
lock (the same idiom Phase 9.5B-R3 already proved for flaky-test
serialization) keyed by the canonical key's hash, PLUS the real DB unique
constraint on ReportSnapshot's canonical key as the structural backstop --
two workers racing for the same key either serialize on the lock or the
loser's INSERT hits the unique constraint and falls back to reading the
winner's row. Either way, exactly one PUBLISHED snapshot ever exists per key."""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.audit.services import record as audit_record
from app.expenses.errors import ExpenseError
from app.extensions import db_session
from app.models.report_snapshots import REPORT_TYPES, ReportSnapshot
from app.operational_reports.aggregation import build_cash_closing_exceptions, build_operational_summary

DEFINITION_VERSION = 1


def _advisory_lock_key(report_type: str, scope: str, period_start: date, period_end: date, currency: str | None) -> int:
    raw = f"{report_type}|{scope}|{period_start}|{period_end}|{currency}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(digest[:15], 16)  # fits in a Postgres bigint


def _build_payload(report_type: str, period_start: date, period_end: date, currency: str | None) -> dict:
    if report_type in ("DAILY_OPERATIONAL_SUMMARY", "WEEKLY_OPERATIONAL_SUMMARY", "MONTHLY_OPERATIONAL_SUMMARY"):
        if currency is None:
            raise ValueError(f"{report_type} requires a currency")
        return build_operational_summary(period_start, period_end, currency)
    if report_type == "DAILY_CASH_CLOSING_EXCEPTIONS":
        return build_cash_closing_exceptions(period_start)
    raise ValueError(f"Unknown report_type: {report_type}")


def generate_snapshot(
    *,
    report_type: str,
    period_start: date,
    period_end: date,
    currency: str | None,
    generated_by: str,
    generated_by_staff_user_id: uuid.UUID | None = None,
    scope: str = "GLOBAL",
    request_id: str | None = None,
) -> ReportSnapshot:
    """Idempotent: a second call with the same canonical key returns the
    existing PUBLISHED row rather than raising or duplicating."""
    if report_type not in REPORT_TYPES:
        raise ValueError(f"Unknown report_type: {report_type}")

    lock_key = _advisory_lock_key(report_type, scope, period_start, period_end, currency)
    db_session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

    existing = db_session.execute(
        select(ReportSnapshot).where(
            ReportSnapshot.report_type == report_type, ReportSnapshot.scope == scope,
            ReportSnapshot.period_start == period_start, ReportSnapshot.period_end == period_end,
            ReportSnapshot.currency == currency, ReportSnapshot.definition_version == DEFINITION_VERSION,
            ReportSnapshot.status == "PUBLISHED",
        ).order_by(ReportSnapshot.snapshot_version.desc())
    ).scalars().first()
    if existing is not None:
        return existing

    payload = _build_payload(report_type, period_start, period_end, currency)

    snapshot = ReportSnapshot(
        report_type=report_type, scope=scope, period_start=period_start, period_end=period_end,
        currency=currency, definition_version=DEFINITION_VERSION, snapshot_version=1, status="PUBLISHED",
        payload=payload, generated_by=generated_by, generated_by_staff_user_id=generated_by_staff_user_id,
        cutoff_at=datetime.now(timezone.utc), request_id=request_id,
    )
    db_session.add(snapshot)
    try:
        db_session.flush()
    except IntegrityError:
        db_session.rollback()
        return db_session.execute(
            select(ReportSnapshot).where(
                ReportSnapshot.report_type == report_type, ReportSnapshot.scope == scope,
                ReportSnapshot.period_start == period_start, ReportSnapshot.period_end == period_end,
                ReportSnapshot.currency == currency, ReportSnapshot.definition_version == DEFINITION_VERSION,
                ReportSnapshot.status == "PUBLISHED",
            )
        ).scalars().first()

    db_session.commit()

    audit_record(
        actor_staff_user_id=generated_by_staff_user_id, actor_role_snapshot=None,
        action_code="REPORT_SNAPSHOT_GENERATED", entity_type="report_snapshot", entity_public_id=str(snapshot.id),
        after_state={"report_type": report_type, "snapshot_version": 1},
    )
    return snapshot


def regenerate_snapshot(
    *, report_type: str, period_start: date, period_end: date, currency: str | None,
    actor_staff_user_id: uuid.UUID, reason: str, scope: str = "GLOBAL",
) -> ReportSnapshot:
    if not reason or not reason.strip():
        raise ExpenseError("SNAPSHOT_REGENERATION_REQUIRES_REASON")

    lock_key = _advisory_lock_key(report_type, scope, period_start, period_end, currency)
    db_session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

    previous = db_session.execute(
        select(ReportSnapshot).where(
            ReportSnapshot.report_type == report_type, ReportSnapshot.scope == scope,
            ReportSnapshot.period_start == period_start, ReportSnapshot.period_end == period_end,
            ReportSnapshot.currency == currency, ReportSnapshot.definition_version == DEFINITION_VERSION,
            ReportSnapshot.status == "PUBLISHED",
        ).order_by(ReportSnapshot.snapshot_version.desc())
    ).scalars().first()
    next_version = (previous.snapshot_version + 1) if previous else 1

    payload = _build_payload(report_type, period_start, period_end, currency)
    snapshot = ReportSnapshot(
        report_type=report_type, scope=scope, period_start=period_start, period_end=period_end,
        currency=currency, definition_version=DEFINITION_VERSION, snapshot_version=next_version, status="PUBLISHED",
        payload=payload, generated_by="MANUAL", generated_by_staff_user_id=actor_staff_user_id,
        cutoff_at=datetime.now(timezone.utc),
    )
    db_session.add(snapshot)
    db_session.flush()

    if previous is not None:
        # The previous row's content is never touched -- only a status/
        # pointer update, never an overwrite of its payload.
        previous.status = "SUPERSEDED"
        previous.superseded_by_snapshot_id = snapshot.id
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
        action_code="REPORT_SNAPSHOT_REGENERATED", entity_type="report_snapshot", entity_public_id=str(snapshot.id),
        reason=reason, after_state={"report_type": report_type, "snapshot_version": next_version},
    )
    return snapshot
