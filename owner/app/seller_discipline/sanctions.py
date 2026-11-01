"""Seller agreement clause 16 -- sanctions, applied by degrees, with a chance to reply first.

THE CLAUSE. Sanctions are applied by degree according to the seriousness of the violation and its
repetition, and the seller gets two days to reply before ANY sanction is applied -- except
termination for money manipulation, disclosure of confidential information, or reverse
engineering, which the company may do at once and without notice. The degrees: (1) a written
notice through Aura Owner, (2) a temporary suspension of the account, (3) withholding the
commission of the deal in question, (4) termination.

THE FLOW.
  open_case      a holder of sellers.discipline opens a case for a kind of sanction. The seller is told
                 (email + the case page in Aura Owner) and the reply deadline is the end of the 2nd
                 Jordan WORKING day (commissions.statements.reply_due_at: Friday + Saturday are the
                 weekend; the clause-5 objection reply uses the very same helper).
  reply_to_case  the seller writes their reply on the case page, once.
  apply_case     allowed only AFTER the seller replied or the deadline passed. WARNING is recorded and
                 shown; SUSPENSION disables the account until a date (employees.services.suspend_employee:
                 sessions killed) and the date ending re-opens it; COMMISSION_WITHHOLD holds the deal's
                 commission (withhold.py); TERMINATION ends the agreement at once (terminations.py).
  drop_case      the manager drops it, at any time.
  IMMEDIATE      a case whose category is one of the three named violations IS a termination, applied
                 on the spot with no reply period (an immediate case with any other kind is refused,
                 so the screen can never say "warning" about something the company treats as ending
                 the agreement).
A manager can never decide a case about themself.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.commissions.statements import reply_due_at
from app.employees.services import InvalidEmploymentTransitionError, reactivate_employee, suspend_employee
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_sales import SalesOrder
from app.models.seller_discipline import (
    IMMEDIATE_CATEGORIES, SANCTION_CATEGORIES, SANCTION_KINDS, SANCTION_LINK_KINDS, SanctionCase)
from app.models.staff import StaffUser
from app.seller_discipline import mail, terminations
from app.seller_discipline.errors import DisciplineError
from app.seller_discipline.people import load_seller, profile_of

MAX_TEXT = 4000
OPEN_STATUSES = ("NOTICE_SENT", "RESPONDED")


# ------------------------------------------------------------------ the queries

def case_by_id(case_id) -> SanctionCase | None:
    try:
        return db_session.get(SanctionCase, uuid.UUID(str(case_id)))
    except (ValueError, TypeError):
        return None


def cases_for_seller(seller_staff_user_id) -> list[SanctionCase]:
    return list(db_session.execute(
        select(SanctionCase).where(SanctionCase.seller_staff_user_id == seller_staff_user_id)
        .order_by(SanctionCase.case_number.desc())
    ).scalars().all())


def all_cases() -> list[SanctionCase]:
    """Open ones first (earliest reply deadline first), then the decided ones, newest first."""
    rows = list(db_session.execute(select(SanctionCase)).scalars().all())
    open_rows = sorted((r for r in rows if r.status in OPEN_STATUSES),
                       key=lambda r: (r.response_due_at or r.notice_sent_at, r.case_number))
    done_rows = sorted((r for r in rows if r.status not in OPEN_STATUSES), key=lambda r: r.case_number, reverse=True)
    return open_rows + done_rows


def awaiting_reply_count(seller_staff_user_id) -> int:
    return len([c for c in cases_for_seller(seller_staff_user_id) if c.status == "NOTICE_SENT"])


def open_counts(*, now: datetime | None = None) -> tuple[int, int]:
    """(cases not yet decided, of which a decision is allowed now) -- the manager dashboard tile."""
    now = now or utcnow()
    open_rows = [c for c in all_cases() if c.status in OPEN_STATUSES]
    return len(open_rows), len([c for c in open_rows if can_decide_now(c, now)])


def can_decide_now(case: SanctionCase, now: datetime) -> bool:
    """The clause: only after the seller replied, or the 2 working days passed."""
    if case.status not in OPEN_STATUSES:
        return False
    return case.responded_at is not None or (case.response_due_at is not None and now >= case.response_due_at)


# ------------------------------------------------------------------ opening

def _validated_order(order_id, seller_profile) -> SalesOrder:
    try:
        order = db_session.get(SalesOrder, uuid.UUID(str(order_id)))
    except (ValueError, TypeError):
        order = None
    if order is None or order.created_by_employee_profile_id != seller_profile.id:
        raise DisciplineError("WITHHOLD_ORDER_NOT_THE_SELLERS")
    return order


def open_case(
    *, seller_staff_user_id, actor: StaffUser, kind: str, category: str = "OTHER", reason_text: str,
    suspension_until: datetime | None = None, withheld_sales_order_id=None, link_kind: str | None = None,
    link_id=None, now: datetime | None = None,
) -> SanctionCase:
    """Every check is made before the first write."""
    now = now or utcnow()
    reason_text = (reason_text or "").strip()
    kind, category = (kind or "").strip().upper(), (category or "OTHER").strip().upper()
    if kind not in SANCTION_KINDS:
        raise DisciplineError("KIND_INVALID")
    if category not in SANCTION_CATEGORIES:
        raise DisciplineError("CATEGORY_INVALID")
    if not reason_text:
        raise DisciplineError("REASON_REQUIRED")
    if len(reason_text) > MAX_TEXT:
        raise DisciplineError("TEXT_TOO_LONG")
    seller, profile = load_seller(seller_staff_user_id)
    if actor.id == seller.id:
        raise DisciplineError("SELF_ACTION_FORBIDDEN")
    immediate = category in IMMEDIATE_CATEGORIES
    if immediate and kind != "TERMINATION":
        raise DisciplineError("IMMEDIATE_CATEGORY_IS_TERMINATION")
    if kind == "SUSPENSION" and (suspension_until is None or suspension_until <= now):
        raise DisciplineError("SUSPENSION_DATE_REQUIRED")
    order_id = None
    if kind == "COMMISSION_WITHHOLD":
        if not withheld_sales_order_id:
            raise DisciplineError("WITHHOLD_ORDER_REQUIRED")
        order_id = _validated_order(withheld_sales_order_id, profile).id
        if db_session.execute(
            select(SanctionCase.id).where(
                SanctionCase.kind == "COMMISSION_WITHHOLD", SanctionCase.withheld_sales_order_id == order_id,
                SanctionCase.status.in_(OPEN_STATUSES + ("DECIDED_APPLIED",)))
        ).first() is not None:
            raise DisciplineError("ALREADY_WITHHELD")
    if (link_kind or link_id) and (link_kind not in SANCTION_LINK_KINDS or not link_id):
        raise DisciplineError("LINK_INVALID")
    case = SanctionCase(
        id=uuid.uuid4(), seller_staff_user_id=seller.id, opened_by_staff_user_id=actor.id, kind=kind, reason_category=category,
        reason_text=reason_text, link_kind=link_kind or None, link_id=uuid.UUID(str(link_id)) if link_id else None,
        suspension_until=suspension_until if kind == "SUSPENSION" else None, withheld_sales_order_id=order_id,
        status="DECIDED_APPLIED" if immediate else "NOTICE_SENT", immediate=immediate, notice_sent_at=now,
        response_due_at=None if immediate else reply_due_at(now),
        decided_at=now if immediate else None, decided_by_staff_user_id=actor.id if immediate else None,
    )
    db_session.add(case)
    db_session.flush()
    termination = None
    if immediate:
        termination = terminations.give_notice(
            seller.id, actor=actor, by_seller=False, reason=reason_text, notice_days=0, sanction_case_id=case.id,
            commit=False, supersede_running=True, now=now)
    db_session.commit()
    _audit("SELLER_SANCTION_OPENED", actor.id, case, reason_text, {"immediate": immediate})
    if termination is not None:
        terminations.announce(termination, seller)
        terminations.apply_termination(termination.id, now=now)
        _decision_mail(case, seller)
    else:
        mail.queue_to_staff(seller, "seller_sanction_notice", [
            f"Case: {case.case_number}", f"Kind: {kind}", "Reply by: " + case.response_due_at.strftime("%Y-%m-%d %H:%M UTC")])
    return case


# ----------------------------------------------------------------- the reply

def _lock(case_id) -> SanctionCase:
    case = db_session.execute(
        select(SanctionCase).where(SanctionCase.id == case_id).with_for_update().execution_options(populate_existing=True)
    ).scalars().first()
    if case is None:
        db_session.rollback()
        raise DisciplineError("CASE_NOT_FOUND")
    return case


def reply_to_case(case_id, *, seller: StaffUser, text: str, now: datetime | None = None) -> SanctionCase:
    """The seller's written reply. Their own case only, once, while it is undecided."""
    now = now or utcnow()
    text = (text or "").strip()
    if not text:
        raise DisciplineError("REPLY_REQUIRED")
    if len(text) > MAX_TEXT:
        raise DisciplineError("TEXT_TOO_LONG")
    case = _lock(case_id)
    if case.seller_staff_user_id != seller.id:
        db_session.rollback()
        raise DisciplineError("CASE_NOT_FOUND")  # not theirs: indistinguishable from "does not exist"
    if case.status != "NOTICE_SENT":
        db_session.rollback()
        raise DisciplineError("CASE_NOT_AWAITING_REPLY")
    case.response_text, case.responded_at, case.status = text, now, "RESPONDED"
    db_session.commit()
    _audit("SELLER_SANCTION_REPLIED", seller.id, case, text, {})
    return case


# ------------------------------------------------------------- the decision

def _audit(code: str, actor_id, case: SanctionCase, reason: str | None, extra: dict) -> None:
    audit_record(
        actor_staff_user_id=actor_id, actor_role_snapshot=None, action_code=code, entity_type="sanction_case",
        entity_public_id=str(case.id), reason=reason,
        after_state={"case_number": case.case_number, "seller_staff_user_id": str(case.seller_staff_user_id),
                     "kind": case.kind, "status": case.status, **extra},
    )


def _decision_mail(case: SanctionCase, seller: StaffUser) -> None:
    lines = [f"Case: {case.case_number}", f"Kind: {case.kind}",
             "Decision: " + ("applied" if case.status == "DECIDED_APPLIED" else "dropped")]
    mail.queue_to_staff(seller, "seller_sanction_decision", lines)


def _decidable(case_id, actor: StaffUser, now: datetime, *, needs_reply_window: bool) -> SanctionCase:
    case = _lock(case_id)
    if case.status not in OPEN_STATUSES:
        db_session.rollback()
        raise DisciplineError("CASE_ALREADY_DECIDED")
    if case.seller_staff_user_id == actor.id:
        db_session.rollback()
        raise DisciplineError("SELF_ACTION_FORBIDDEN")
    if needs_reply_window and not can_decide_now(case, now):
        db_session.rollback()
        raise DisciplineError("WAIT_FOR_REPLY", due_at=case.response_due_at.strftime("%Y-%m-%d %H:%M UTC"))
    return case


def apply_case(case_id, *, actor: StaffUser, note: str = "", now: datetime | None = None) -> SanctionCase:
    """Applies the sanction -- only after the seller replied or the deadline passed."""
    now = now or utcnow()
    note = (note or "").strip()
    if len(note) > MAX_TEXT:
        raise DisciplineError("TEXT_TOO_LONG")
    case = _decidable(case_id, actor, now, needs_reply_window=True)
    seller = db_session.get(StaffUser, case.seller_staff_user_id)
    profile = profile_of(seller.id)
    case.status, case.decided_at, case.decided_by_staff_user_id, case.decision_note = "DECIDED_APPLIED", now, actor.id, note or None
    termination = None
    try:
        if case.kind == "SUSPENSION":
            if case.suspension_until <= now:
                raise DisciplineError("SUSPENSION_DATE_PASSED")
            if profile is None or profile.employment_status != "ACTIVE":
                raise DisciplineError("SELLER_NOT_CURRENT")
            # Commits the case and the suspension together.
            suspend_employee(profile, f"Sanction case {case.case_number} (clause 16): {case.reason_text}"[:2000], actor.id)
        elif case.kind == "TERMINATION":
            termination = terminations.give_notice(
                seller.id, actor=actor, by_seller=False, reason=case.reason_text, notice_days=0, sanction_case_id=case.id,
                commit=False, supersede_running=True, now=now)
            db_session.commit()
        else:  # WARNING (recorded, shown on the seller's page) and COMMISSION_WITHHOLD (the case itself is the flag)
            db_session.commit()
    except (DisciplineError, InvalidEmploymentTransitionError):
        db_session.rollback()
        raise
    _audit("SELLER_SANCTION_APPLIED", actor.id, case, note or None, {})
    if termination is not None:
        terminations.announce(termination, seller)
        terminations.apply_termination(termination.id, now=now)
    _decision_mail(case, seller)
    return case


def drop_case(case_id, *, actor: StaffUser, note: str = "", now: datetime | None = None) -> SanctionCase:
    """The company decides not to apply it. Allowed at any time before a decision."""
    now = now or utcnow()
    note = (note or "").strip()
    if len(note) > MAX_TEXT:
        raise DisciplineError("TEXT_TOO_LONG")
    case = _decidable(case_id, actor, now, needs_reply_window=False)
    case.status, case.decided_at, case.decided_by_staff_user_id, case.decision_note = "DECIDED_DROPPED", now, actor.id, note or None
    db_session.commit()
    _audit("SELLER_SANCTION_DROPPED", actor.id, case, note or None, {})
    _decision_mail(case, db_session.get(StaffUser, case.seller_staff_user_id))
    return case


# ------------------------------------------------------- suspensions that end

def _due_suspension_ids(now: datetime, seller_staff_user_id=None) -> list:
    stmt = select(SanctionCase.id).where(
        SanctionCase.kind == "SUSPENSION", SanctionCase.status == "DECIDED_APPLIED",
        SanctionCase.suspension_ended_at.is_(None), SanctionCase.suspension_until <= now)
    if seller_staff_user_id is not None:
        stmt = stmt.where(SanctionCase.seller_staff_user_id == seller_staff_user_id)
    return list(db_session.execute(stmt.order_by(SanctionCase.case_number)).scalars().all())


def _end_suspension(case_id, now: datetime) -> bool:
    case = _lock(case_id)
    if case.suspension_ended_at is not None or case.suspension_until > now:
        db_session.rollback()
        return False
    profile = profile_of(case.seller_staff_user_id)
    reopened = False
    case.suspension_ended_at = now
    if profile is not None and profile.employment_status == "SUSPENDED":
        # Commits the case and the re-opened account together. A separate, independent disable of the
        # account (StaffUser.disabled_at) is NOT overridden: reactivate_employee leaves it blocked.
        reactivate_employee(profile, case.decided_by_staff_user_id)
        reopened = True
    else:
        db_session.commit()  # terminated meanwhile: nothing to re-open
    _audit("SELLER_SUSPENSION_ENDED", None, case, None, {"account_reopened": reopened})
    if reopened:
        mail.queue_to_staff(db_session.get(StaffUser, case.seller_staff_user_id), "seller_sanction_decision", [
            f"Case: {case.case_number}", "Your suspension has ended. You can sign in again."])
    return True


def end_due_suspensions(*, now: datetime | None = None, seller_staff_user_id=None) -> int:
    now = now or utcnow()
    return sum(1 for case_id in _due_suspension_ids(now, seller_staff_user_id) if _end_suspension(case_id, now))
