"""Phase 9.5E -- Expense duplicate/fraud-signal detection. Advisory only:
never blocks submission, never approves or rejects anything by itself
(Rule 8: 'Duplicate review is not approval'). Overriding a warning is a
separate, reasoned, audited action that leaves the expense's own status
untouched."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.expenses.errors import ExpenseError
from app.extensions import db_session
from app.models.expenses import Expense, ExpenseAttachment

_NON_ACTIVE_FOR_DUPLICATE_CHECK = ("VOID", "REJECTED")


def find_duplicate_signals(expense: Expense) -> list[dict]:
    """Returns a list of {"signal_type", "confidence", "matched_expense_ids"}
    dicts. confidence in {"EXACT", "LIKELY", "POSSIBLE"}. Never raises,
    never mutates -- pure read."""
    signals: list[dict] = []

    if expense.external_reference:
        matches = db_session.execute(
            select(Expense.id).where(
                Expense.external_reference == expense.external_reference,
                Expense.id != expense.id,
                Expense.status.notin_(_NON_ACTIVE_FOR_DUPLICATE_CHECK),
            )
        ).scalars().all()
        if matches:
            signals.append({"signal_type": "EXACT_EXTERNAL_REFERENCE", "confidence": "EXACT", "matched_expense_ids": list(matches)})

    my_hashes = db_session.execute(
        select(ExpenseAttachment.content_hash).where(
            ExpenseAttachment.expense_id == expense.id, ExpenseAttachment.status == "ACTIVE"
        )
    ).scalars().all()
    if my_hashes:
        matches = db_session.execute(
            select(ExpenseAttachment.expense_id).where(
                ExpenseAttachment.content_hash.in_(my_hashes),
                ExpenseAttachment.expense_id != expense.id,
                ExpenseAttachment.status == "ACTIVE",
            )
        ).scalars().all()
        if matches:
            signals.append({"signal_type": "EXACT_ATTACHMENT_HASH", "confidence": "EXACT", "matched_expense_ids": list(set(matches))})

    if expense.payee_id is not None:
        matches = db_session.execute(
            select(Expense.id).where(
                Expense.payee_id == expense.payee_id,
                Expense.amount == expense.amount,
                Expense.expense_date == expense.expense_date,
                Expense.id != expense.id,
                Expense.status.notin_(_NON_ACTIVE_FOR_DUPLICATE_CHECK),
            )
        ).scalars().all()
        if matches:
            signals.append({"signal_type": "AMOUNT_DATE_PAYEE_MATCH", "confidence": "LIKELY", "matched_expense_ids": list(matches)})

    return signals


def summarize_match_for_viewer(matched_expense: Expense, *, viewer_can_view_all: bool, viewer_employee_profile_id: uuid.UUID) -> dict:
    """Redacts the matched expense's details when the viewer has no access
    to it (M20: 'inaccessible matching Expense details are not exposed') --
    a viewer without expenses.view_all only ever sees a bare existence
    signal for someone else's expense, never its amount/payee/description."""
    owns_it = matched_expense.entered_by_employee_profile_id == viewer_employee_profile_id
    if viewer_can_view_all or owns_it:
        return {
            "expense_id": str(matched_expense.id),
            "expense_number": matched_expense.expense_number,
            "amount": str(matched_expense.amount),
            "currency": matched_expense.currency,
            "expense_date": matched_expense.expense_date.isoformat(),
            "status": matched_expense.status,
            "accessible": True,
        }
    return {"expense_id": str(matched_expense.id), "accessible": False}


def override_duplicate_warning(expense: Expense, *, actor_staff_user_id: uuid.UUID, reason: str, signals: list[dict]) -> None:
    """Records the override with its reason -- does NOT approve, reject, or
    otherwise change expense.status. A separate, later call to
    app.expenses.approvals.decide_expense_approval() is still required."""
    if not reason or not reason.strip():
        raise ExpenseError("DUPLICATE_OVERRIDE_REQUIRES_REASON")

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EXPENSE_DUPLICATE_WARNING_OVERRIDDEN",
        entity_type="expense",
        entity_public_id=str(expense.id),
        reason=reason,
        after_state={"signal_types": [s["signal_type"] for s in signals]},
    )
