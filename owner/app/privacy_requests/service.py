"""Logging and closing personal-data requests, and the counts the dashboard shows.

A request is logged the day it arrives (the date the customer asked, which may be earlier than the
day staff typed it), gets a due date 30 days later, and is closed with a written answer. The
actions that DO the work live next to it: export.py (the copy) and erase.py (the erasure).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.base import utcnow
from app.models.customers import Customer
from app.models.data_requests import REQUEST_CHANNELS, REQUEST_KINDS, RESPONSE_DAYS, DataRequest
from app.operational_reports.periods import OWNER_TIMEZONE, jordan_today
from app.privacy_requests.errors import DataRequestError

MAX_NOTE_LENGTH = 4000
# The due DATE is received + 30 days and the customer is still answered in time ON that day, so a
# request is late only from the day AFTER it ("more than 30 days"): due_at is the start of the due day.
LATE_AFTER_DUE = timedelta(days=1)


def _start_of_jordan_day(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=OWNER_TIMEZONE).astimezone(timezone.utc)


def log_request(*, customer_id, kind: str, channel: str, received_on: date | None, actor_staff_user_id) -> DataRequest:
    """Writes the request (OPEN, due 30 days after it was received) and its audit row.
    `received_on` is the Jordan date the customer asked; None = today. A date in the future is
    refused: a request cannot have arrived tomorrow."""
    if kind not in REQUEST_KINDS:
        raise DataRequestError("KIND_INVALID")
    if channel not in REQUEST_CHANNELS:
        raise DataRequestError("CHANNEL_INVALID")
    customer = db_session.get(Customer, customer_id)
    if customer is None:
        raise DataRequestError("CUSTOMER_NOT_FOUND")
    day = received_on or jordan_today()
    if day > jordan_today():
        raise DataRequestError("RECEIVED_IN_FUTURE")
    received_at = _start_of_jordan_day(day)
    row = DataRequest(
        customer_id=customer.id, kind=kind, channel=channel, received_at=received_at,
        due_at=received_at + timedelta(days=RESPONSE_DAYS), status="OPEN",
        logged_by_staff_user_id=actor_staff_user_id,
    )
    db_session.add(row)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="DATA_REQUEST_LOGGED",
        entity_type="data_request", entity_public_id=str(row.id),
        after_state={"customer_id": str(customer.id), "kind": kind, "channel": channel,
                     "received_at": received_at.isoformat(), "due_at": row.due_at.isoformat()},
    )
    return row


def get_open_for_update(request_id) -> DataRequest:
    """The request, row-locked, and still OPEN -- two staff cannot close or erase it at once."""
    row = db_session.execute(select(DataRequest).where(DataRequest.id == request_id).with_for_update()).scalars().first()
    if row is None:
        raise DataRequestError("REQUEST_NOT_FOUND")
    if row.status != "OPEN":
        raise DataRequestError("REQUEST_NOT_OPEN")
    return row


def close(row: DataRequest, *, status: str, note: str, actor_staff_user_id, now: datetime | None = None) -> DataRequest:
    """Closes an already-locked OPEN request. Does NOT commit: the caller's transaction also holds
    whatever made the closing true (the erasure). The caller writes the audit row after commit."""
    note = (note or "").strip()
    if not note:
        raise DataRequestError("ANSWER_REQUIRED")
    if len(note) > MAX_NOTE_LENGTH:
        raise DataRequestError("TEXT_TOO_LONG")
    now = now or utcnow()
    row.status = status
    row.answer_note = note
    row.closed_at = now
    row.closed_by_staff_user_id = actor_staff_user_id
    return row


def close_request(request_id, *, status: str, note: str, actor_staff_user_id) -> DataRequest:
    """The manager's 'mark as done / refused' button. An ERASE cannot be marked done by hand: only the
    erasure itself (erase.py) closes it DONE, so a request can never claim an erasure that did not run."""
    if status not in ("DONE", "REFUSED"):
        raise DataRequestError("STATUS_INVALID")
    row = get_open_for_update(request_id)
    if status == "DONE" and row.kind == "ERASE" and row.fulfilled_at is None:
        db_session.rollback()
        raise DataRequestError("ERASE_NOT_CARRIED_OUT")
    try:
        close(row, status=status, note=note, actor_staff_user_id=actor_staff_user_id)
    except DataRequestError:
        db_session.rollback()
        raise
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code=f"DATA_REQUEST_{status}",
        entity_type="data_request", entity_public_id=str(row.id),
        after_state={"customer_id": str(row.customer_id), "kind": row.kind}, reason=row.answer_note,
    )
    return row


def requests_for_customer(customer_id) -> list[DataRequest]:
    return list(db_session.execute(
        select(DataRequest).where(DataRequest.customer_id == customer_id).order_by(DataRequest.received_at.desc(), DataRequest.request_number.desc())
    ).scalars())


def list_requests(*, only_open: bool = False) -> list[DataRequest]:
    stmt = select(DataRequest).order_by(DataRequest.status.asc(), DataRequest.due_at.asc())
    if only_open:
        stmt = stmt.where(DataRequest.status == "OPEN")
    return list(db_session.execute(stmt).scalars())


def is_overdue(row: DataRequest, now: datetime | None = None) -> bool:
    return row.status == "OPEN" and row.due_at + LATE_AFTER_DUE < (now or utcnow())


def open_counts(now: datetime | None = None) -> tuple[int, int]:
    """(open requests, open requests past their due date) -- the manager's dashboard tile."""
    now = now or utcnow()
    open_n = db_session.execute(select(func.count()).select_from(DataRequest).where(DataRequest.status == "OPEN")).scalar_one()
    late_n = db_session.execute(
        select(func.count()).select_from(DataRequest).where(DataRequest.status == "OPEN", DataRequest.due_at < now - LATE_AFTER_DUE)
    ).scalar_one()
    return open_n, late_n
