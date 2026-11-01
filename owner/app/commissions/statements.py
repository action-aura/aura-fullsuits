"""Seller agreement clause 5 -- the monthly commission statement and the right to object.

THE CLAUSE. Commission is paid at the end of each month for the subscriptions
confirmed in it; the seller gets a detailed statement in Aura Owner and may OBJECT
within 7 days; the company replies within 2 working days.

WHAT A STATEMENT IS. One per seller per CLOSED Jordan calendar month, DERIVED from the
commission ledger on every view (nothing is stored, so it can never disagree with the
money). It lists the subscriptions confirmed in the month (the ladder's COUNTED sales),
their ladder position and amount, the renewals, and splits the money into what is HELD
(inside the 30-day hold), PAYABLE (past the hold, or approved), PAID, and CLAWED BACK
(take-back rows, and sales that stopped counting). Deductions already taken from
pay-outs are shown as information.

WHEN IT IS ISSUED. The first day after the month closes: 00:00 Jordan time on the 1st of
the next month. Before that the month is still open and there is no statement.

THE WINDOW. [issued, issued + 7 days): the button is there from 00:00 on the 1st until
00:00 on the 8th (seven full Jordan days). The edge is exact and tested; the database
also refuses an objection stamped outside it (ck ..._in_window).

THE REPLY DEADLINE. 2 Jordan WORKING days. Jordan's weekend is Friday + Saturday.
Counting starts the day AFTER the objection and the objection's own day never counts:
an objection on Wednesday is due by the end of Sunday (Thu, Sun); on Thursday, Monday
(Sun, Mon); on Friday or Saturday, Monday (Sun, Mon). Public holidays are NOT skipped:
no holiday table exists in Owner and none is invented here (a manager can answer
earlier; an overdue flag is a nudge, not a penalty). The deadline is stored as the
instant that working day ENDS (00:00 Jordan the next calendar day).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError

from app.audit.services import record as audit_record
from app.commissions.errors import CommissionError
from app.extensions import db_session
from app.models.base import utcnow
from app.models.catalog import Plan
from app.models.commercial_sales import SalesOrder
from app.models.commissions import (
    CommissionClawbackDeduction,
    CommissionLadderSale,
    CommissionLedgerEntry,
    CommissionStatementObjection,
)
from app.models.customers import Customer
from app.models.employees import EmployeeProfile
from app.models.staff import StaffUser
from app.models.subscriptions import PaymentRecord
from app.operational_reports.periods import OWNER_TIMEZONE
from app.seller_discipline import withhold

OBJECTION_WINDOW_DAYS = 7
REPLY_WORKING_DAYS = 2
# Python's weekday(): Monday=0 ... Friday=4, Saturday=5. Jordan's weekend.
JORDAN_WEEKEND = frozenset({4, 5})
STATEMENTS_SHOWN = 12
_ZERO = Decimal("0.00")


# --------------------------------------------------------------- the calendar

def _local_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=OWNER_TIMEZONE).astimezone(timezone.utc)


def month_of(moment: datetime) -> date:
    return moment.astimezone(OWNER_TIMEZONE).date().replace(day=1)


def next_month(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1)


def statement_issued_at(month: date) -> datetime:
    """The first day after the month closes, 00:00 Jordan time (as UTC)."""
    return _local_midnight(next_month(month))


def objection_window_closes_at(month: date) -> datetime:
    """Exclusive end of the 7-day window: 00:00 Jordan on the 8th of the next month."""
    return _local_midnight(next_month(month) + timedelta(days=OBJECTION_WINDOW_DAYS))


def month_is_closed(month: date, now: datetime) -> bool:
    return now >= statement_issued_at(month)


def objection_window_is_open(month: date, now: datetime) -> bool:
    return statement_issued_at(month) <= now < objection_window_closes_at(month)


def reply_due_at(objected_at: datetime) -> datetime:
    """The end (00:00 Jordan, next calendar day) of the 2nd Jordan working day after the
    objection's own day. See the module docstring for the worked days."""
    day = objected_at.astimezone(OWNER_TIMEZONE).date()
    counted = 0
    while counted < REPLY_WORKING_DAYS:
        day += timedelta(days=1)
        if day.weekday() not in JORDAN_WEEKEND:
            counted += 1
    return _local_midnight(day + timedelta(days=1))


def is_overdue(objection: CommissionStatementObjection, now: datetime) -> bool:
    return objection.status == "OPEN" and now > objection.due_at


# ------------------------------------------------------------- the statement

def _jordan_day(moment: datetime) -> str:
    return moment.astimezone(OWNER_TIMEZONE).date().isoformat()


def _money(value) -> str:
    return str(Decimal(value).quantize(Decimal("0.01")))


def _bucket(entry: CommissionLedgerEntry, now: datetime, held_orders: dict) -> str:
    amount = Decimal(entry.commission_amount)
    if amount < 0:
        return "clawed_back"
    if entry.status == "PAID":
        return "paid"
    # Clause 16: the commission of a deal under an applied COMMISSION_WITHHOLD sanction is neither
    # held (30 days) nor payable: it is withheld, whatever its status or hold date.
    if entry.status in ("EARNED", "APPROVED") and withhold.case_number_for_entry(entry, held_orders) is not None:
        return "withheld"
    if entry.status == "APPROVED":
        return "payable"
    if entry.status == "EARNED":
        return "held" if entry.payable_after is not None and entry.payable_after > now else "payable"
    return "other"


def _month_entries(profile_id, month: date) -> list[CommissionLedgerEntry]:
    start, end = _local_midnight(month), _local_midnight(next_month(month))
    return list(db_session.execute(
        select(CommissionLedgerEntry).where(
            CommissionLedgerEntry.employee_profile_id == profile_id, CommissionLedgerEntry.status != "CANCELLED",
            or_(CommissionLedgerEntry.ladder_month == month,
                and_(CommissionLedgerEntry.ladder_month.is_(None), CommissionLedgerEntry.earned_at >= start,
                     CommissionLedgerEntry.earned_at < end)),
        ).order_by(CommissionLedgerEntry.created_at, CommissionLedgerEntry.id)
    ).scalars().all())


def _customer_name_for_payment(payment_id) -> str:
    payment = db_session.get(PaymentRecord, payment_id) if payment_id else None
    customer = db_session.get(Customer, payment.customer_id) if payment is not None else None
    return customer.legal_name if customer is not None else ""


def _withheld_case_number(rows, held_orders: dict) -> int | None:
    """The sanction case holding back any of these ledger rows (clause 16), or None."""
    for row in rows:
        number = withhold.case_number_for_entry(row, held_orders) if row.status in ("EARNED", "APPROVED") else None
        if number is not None:
            return number
    return None


def build_statement(employee_profile_id, month: date, *, now: datetime | None = None) -> dict:
    """The seller's statement for one month, as plain JSON-able data (strings for money and
    dates): the same dict is rendered, snapshotted into an objection, and hashed."""
    now = now or utcnow()
    entries = _month_entries(employee_profile_id, month)
    held_orders = withhold.withheld_orders()
    sales = list(db_session.execute(
        select(CommissionLadderSale).where(
            CommissionLadderSale.employee_profile_id == employee_profile_id, CommissionLadderSale.ladder_month == month
        ).order_by(CommissionLadderSale.qualified_at, CommissionLadderSale.id)
    ).scalars().all())
    by_sale: dict = {}
    for entry in entries:
        if entry.ladder_sale_id is not None:
            by_sale.setdefault(entry.ladder_sale_id, []).append(entry)

    totals = {"held": _ZERO, "payable": _ZERO, "paid": _ZERO, "clawed_back": _ZERO, "withheld": _ZERO, "other": _ZERO}
    for entry in entries:
        bucket = _bucket(entry, now, held_orders)
        totals[bucket] += -Decimal(entry.commission_amount) if bucket == "clawed_back" else Decimal(entry.commission_amount)

    lines = []
    for sale in sales:
        rows = by_sale.get(sale.id, [])
        parts = {"held": _ZERO, "payable": _ZERO, "paid": _ZERO, "clawed_back": _ZERO, "withheld": _ZERO, "other": _ZERO}
        for row in rows:
            bucket = _bucket(row, now, held_orders)
            parts[bucket] += -Decimal(row.commission_amount) if bucket == "clawed_back" else Decimal(row.commission_amount)
        order = db_session.get(SalesOrder, sale.sales_order_id)
        customer = db_session.get(Customer, order.customer_id) if order is not None else None
        plan = db_session.get(Plan, sale.package_plan_id)
        positions = [r.ladder_position for r in rows if r.ladder_position is not None]
        lines.append({
            "kind": "SALE", "customer": customer.legal_name if customer is not None else "",
            "plan": plan.name if plan is not None else "", "confirmed_at": sale.qualified_at.isoformat(), "confirmed_on": _jordan_day(sale.qualified_at),
            "position": positions[-1] if positions else None, "counted": sale.status == "COUNTED",
            "uncount_cause": sale.uncount_cause, "net": _money(sum((Decimal(r.commission_amount) for r in rows), _ZERO)),
            "withheld_case": _withheld_case_number(rows, held_orders),
            **{key: _money(value) for key, value in parts.items()},
        })
    for entry in entries:
        if entry.ladder_sale_id is not None:
            continue  # shown inside its sale's line
        bucket = _bucket(entry, now, held_orders)
        parts = {key: _ZERO for key in totals}
        parts[bucket] = -Decimal(entry.commission_amount) if bucket == "clawed_back" else Decimal(entry.commission_amount)
        lines.append({
            "kind": entry.entry_kind, "customer": _customer_name_for_payment(entry.source_payment_record_id), "plan": "",
            "confirmed_at": (entry.earned_at or entry.created_at).isoformat(),
            "confirmed_on": _jordan_day(entry.earned_at or entry.created_at), "position": entry.ladder_position,
            "counted": True, "uncount_cause": None, "net": _money(entry.commission_amount),
            "withheld_case": _withheld_case_number([entry], held_orders),
            **{key: _money(value) for key, value in parts.items()},
        })

    start, end = _local_midnight(month), _local_midnight(next_month(month))
    deducted = db_session.execute(
        select(CommissionClawbackDeduction).where(
            CommissionClawbackDeduction.employee_profile_id == employee_profile_id,
            CommissionClawbackDeduction.created_at >= start, CommissionClawbackDeduction.created_at < end)
    ).scalars().all()
    # clawed_back is kept as a positive number for display; it SUBTRACTS from what the seller keeps.
    net = totals["held"] + totals["payable"] + totals["paid"] + totals["withheld"] + totals["other"] - totals["clawed_back"]
    return {
        "month": month.isoformat(), "currency": "JOD", "issued_at": statement_issued_at(month).isoformat(),
        "objection_window_closes_at": objection_window_closes_at(month).isoformat(),
        "sales_confirmed": len([s for s in sales if s.status == "COUNTED"]),
        "totals": {**{key: _money(value) for key, value in totals.items()}, "net": _money(net)},
        "deducted_from_payouts": _money(sum((Decimal(d.amount) for d in deducted), _ZERO)),
        "lines": lines,
    }


def statement_sha256(statement: dict) -> str:
    return hashlib.sha256(json.dumps(statement, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def closed_statement_months(employee_profile_id, *, now: datetime | None = None) -> list[date]:
    """Closed months in which the seller has anything on the ledger (or an objection), newest first."""
    now = now or utcnow()
    months = set(db_session.execute(
        select(CommissionLadderSale.ladder_month).where(CommissionLadderSale.employee_profile_id == employee_profile_id)
    ).scalars().all())
    for ladder_month, earned_at in db_session.execute(
        select(CommissionLedgerEntry.ladder_month, CommissionLedgerEntry.earned_at).where(
            CommissionLedgerEntry.employee_profile_id == employee_profile_id, CommissionLedgerEntry.status != "CANCELLED")
    ).all():
        if ladder_month is not None:
            months.add(ladder_month)
        elif earned_at is not None:
            months.add(month_of(earned_at))
    return sorted((m for m in months if month_is_closed(m, now)), reverse=True)[:STATEMENTS_SHOWN]


# ------------------------------------------------------------ the objection

def objection_for(staff_user_id, month: date) -> CommissionStatementObjection | None:
    return db_session.execute(
        select(CommissionStatementObjection).where(
            CommissionStatementObjection.staff_user_id == staff_user_id, CommissionStatementObjection.statement_month == month)
    ).scalars().first()


def object_to_statement(*, staff: StaffUser, profile: EmployeeProfile, month: date, reason: str,
                        now: datetime | None = None) -> CommissionStatementObjection:
    """Records the seller's objection. Every check is made before the first write:
    the month must be closed (a statement exists), inside the 7-day window, with a written
    reason, and not objected to before. The statement is snapshotted as the seller sees it."""
    now = now or utcnow()
    reason = (reason or "").strip()
    if month.day != 1:
        raise CommissionError("STATEMENT_MONTH_INVALID")
    if not month_is_closed(month, now):
        raise CommissionError("STATEMENT_NOT_ISSUED")
    if not objection_window_is_open(month, now):
        raise CommissionError("STATEMENT_OBJECTION_WINDOW_CLOSED")
    if not reason:
        raise CommissionError("STATEMENT_OBJECTION_REASON_REQUIRED")
    if len(reason) > 4000:
        raise CommissionError("STATEMENT_OBJECTION_REASON_TOO_LONG")
    if objection_for(staff.id, month) is not None:
        raise CommissionError("STATEMENT_ALREADY_OBJECTED")
    snapshot = build_statement(profile.id, month, now=now)
    row = CommissionStatementObjection(
        id=uuid.uuid4(), staff_user_id=staff.id, employee_profile_id=profile.id, statement_month=month, reason=reason,
        statement_snapshot=snapshot, statement_sha256=statement_sha256(snapshot), objected_at=now,
        window_closes_at=objection_window_closes_at(month), due_at=reply_due_at(now), status="OPEN",
    )
    db_session.add(row)
    try:
        db_session.commit()
    except IntegrityError:
        db_session.rollback()  # two taps at once: the unique row is the first objection
        raise CommissionError("STATEMENT_ALREADY_OBJECTED")
    audit_record(
        actor_staff_user_id=staff.id, actor_role_snapshot=None, action_code="COMMISSION_STATEMENT_OBJECTED",
        entity_type="commission_statement_objection", entity_public_id=str(row.id), reason=reason,
        after_state={"month": month.isoformat(), "due_at": row.due_at.isoformat(), "statement_sha256": row.statement_sha256},
    )
    return row


def answer_objection(objection_id, *, answerer: StaffUser, outcome: str, reply_text: str,
                     now: datetime | None = None) -> CommissionStatementObjection:
    """A manager's written answer. Under a row lock so two managers cannot both answer; refuses
    the seller's own objection (a manager who also sells), a blank reply, or an unknown outcome."""
    now = now or utcnow()
    outcome = (outcome or "").strip().upper()
    reply_text = (reply_text or "").strip()
    if outcome not in ("ACCEPTED", "REJECTED"):
        raise CommissionError("STATEMENT_OBJECTION_OUTCOME_INVALID")
    if not reply_text:
        raise CommissionError("STATEMENT_OBJECTION_REPLY_REQUIRED")
    if len(reply_text) > 4000:
        raise CommissionError("STATEMENT_OBJECTION_REASON_TOO_LONG")
    row = db_session.execute(
        select(CommissionStatementObjection).where(CommissionStatementObjection.id == objection_id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalars().first()
    if row is None:
        raise CommissionError("STATEMENT_OBJECTION_NOT_FOUND")
    if row.staff_user_id == answerer.id:
        db_session.rollback()
        raise CommissionError("STATEMENT_OBJECTION_SELF_ANSWER_FORBIDDEN")
    if row.status != "OPEN":
        db_session.rollback()
        raise CommissionError("STATEMENT_OBJECTION_ALREADY_ANSWERED")
    row.status, row.reply_text, row.answered_by_staff_user_id, row.answered_at = outcome, reply_text, answerer.id, now
    db_session.commit()
    audit_record(
        actor_staff_user_id=answerer.id, actor_role_snapshot=None, action_code="COMMISSION_STATEMENT_OBJECTION_ANSWERED",
        entity_type="commission_statement_objection", entity_public_id=str(row.id), reason=reply_text,
        after_state={"outcome": outcome, "month": row.statement_month.isoformat(), "was_overdue": now > row.due_at},
    )
    return row


def list_objections() -> list[CommissionStatementObjection]:
    """The manager queue: open ones first, the earliest deadline first (so the overdue ones
    lead); then the answered ones, most recently answered first."""
    rows = list(db_session.execute(select(CommissionStatementObjection)).scalars().all())
    open_rows = sorted((r for r in rows if r.status == "OPEN"), key=lambda r: (r.due_at, r.id))
    done_rows = sorted((r for r in rows if r.status != "OPEN"), key=lambda r: (r.answered_at, r.id), reverse=True)
    return open_rows + done_rows


def open_and_overdue_counts(*, now: datetime | None = None) -> tuple[int, int]:
    from sqlalchemy import func

    now = now or utcnow()
    base = select(func.count()).select_from(CommissionStatementObjection).where(CommissionStatementObjection.status == "OPEN")
    return (db_session.execute(base).scalar_one(),
            db_session.execute(base.where(CommissionStatementObjection.due_at < now)).scalar_one())
