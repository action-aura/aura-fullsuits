"""Phase 9.5E -- Expense approval service. Implements the authoritative
expense-approval-and-segregation rules verbatim:

1. Approval is not payment -- this module never creates an ExpensePayment,
   never sets PAID, never touches cash reports or a journal.
2. Self-approval is always forbidden, regardless of the approver's other
   roles (checked here, in the service layer -- a permission grant alone is
   never sufficient, matching the exact reasoning
   app/commercial_sales/approvals.py already documents for
   SELF_APPROVAL_FORBIDDEN).
3. Beneficiary conflict: the approver cannot also be the recorded employee
   beneficiary. An external payee's creator/manager is never conflicted
   merely by that fact.
4. Approval fingerprint: staleness is decided by recomputing the expense's
   CURRENT fingerprint and comparing to the one captured at submit time --
   never a caller-supplied version.
5. Approved amount must be <= requested amount.
6. Resubmission after RETURNED opens a brand-new PENDING cycle; old
   decisions are immutable.
7. Approver eligibility: permission, active account, active EmployeeProfile,
   not suspended/terminated, not requester, not beneficiary.
8. Duplicate-review override is a separate action (see duplicates.py) and
   never itself approves anything.
"""
from __future__ import annotations

import uuid

from decimal import Decimal
from sqlalchemy import select

from app.audit.services import record as audit_record
from app.expenses.errors import EXPENSE_APPROVAL_TRANSITIONS, ExpenseError
from app.expenses.fingerprint import current_fingerprint_for_expense
from app.extensions import db_session
from app.models.base import utcnow
from app.models.employees import EmployeeProfile
from app.models.expenses import Expense, ExpenseApproval
from app.models.staff import StaffUser
from app.security.rbac import get_staff_permission_codes


def _check_transition(status: str, target: str) -> None:
    if target not in EXPENSE_APPROVAL_TRANSITIONS.get(status, set()):
        raise ExpenseError("INVALID_EXPENSE_APPROVAL_TRANSITION", from_status=status, to_status=target)


def _approver_employee_profile(staff_user: StaffUser) -> EmployeeProfile | None:
    return db_session.execute(
        select(EmployeeProfile).where(EmployeeProfile.staff_user_id == staff_user.id)
    ).scalars().first()


def check_approver_eligibility(staff_user: StaffUser, expense: Expense) -> None:
    """Rule 7. Raises ExpenseError on any ineligibility. Deliberately checked
    even for SUPER_ADMIN / accounts with multiple combined roles -- Rule 2's
    self-approval and Rule 3's beneficiary-conflict prohibitions apply
    regardless of what other permissions the account holds."""
    if not staff_user.is_active:
        raise ExpenseError("APPROVER_INELIGIBLE", reason="account inactive")

    permissions = get_staff_permission_codes(staff_user)
    if "expenses.approve" not in permissions and not staff_user.is_super_admin:
        raise ExpenseError("APPROVER_MISSING_PERMISSION")

    approver_profile = _approver_employee_profile(staff_user)
    if approver_profile is None:
        raise ExpenseError("APPROVER_MISSING_EMPLOYEE_PROFILE")
    if approver_profile.employment_status in ("SUSPENDED", "TERMINATED"):
        raise ExpenseError("APPROVER_SUSPENDED_OR_TERMINATED")

    if approver_profile.id == expense.entered_by_employee_profile_id:
        raise ExpenseError("SELF_APPROVAL_FORBIDDEN")
    if expense.beneficiary_employee_profile_id is not None and approver_profile.id == expense.beneficiary_employee_profile_id:
        raise ExpenseError("BENEFICIARY_APPROVAL_FORBIDDEN")


def decide_expense_approval(
    approval: ExpenseApproval,
    expense: Expense,
    *,
    decision: str,
    decided_by: StaffUser,
    approved_amount: Decimal | None = None,
    decision_reason: str | None = None,
) -> ExpenseApproval:
    """decision in {"APPROVED", "REJECTED", "RETURNED"}. Never creates a
    payment, never mutates Expense.status beyond APPROVED/REJECTED/RETURNED,
    never touches cash reports."""
    if decision not in ("APPROVED", "REJECTED", "RETURNED"):
        raise ValueError(f"Unknown decision: {decision!r}")

    check_approver_eligibility(decided_by, expense)
    _check_transition(approval.status, decision)

    if decision in ("REJECTED", "RETURNED") and (not decision_reason or not decision_reason.strip()):
        raise ExpenseError("REASON_REQUIRED")

    current_fingerprint = current_fingerprint_for_expense(expense)
    if current_fingerprint != approval.fingerprint:
        raise ExpenseError("APPROVAL_STALE")

    if decision == "APPROVED":
        if approved_amount is None:
            approved_amount = approval.requested_amount
        if approved_amount <= 0 or not approved_amount.is_finite():
            raise ExpenseError("NON_FINITE_AMOUNT")
        if approved_amount > approval.requested_amount:
            raise ExpenseError(
                "APPROVED_AMOUNT_EXCEEDS_REQUESTED",
                approved=str(approved_amount), requested=str(approval.requested_amount),
            )
        approval.approved_amount = approved_amount
        expense.approved_amount = approved_amount
        expense.approved_by_staff_user_id = decided_by.id
        expense.status = "APPROVED"
    elif decision == "REJECTED":
        expense.status = "REJECTED"
    else:  # RETURNED
        expense.status = "RETURNED"

    approval.status = decision
    approval.decided_by_staff_user_id = decided_by.id
    approval.decision_reason = decision_reason
    approval.decided_at = utcnow()
    db_session.commit()

    audit_record(
        actor_staff_user_id=decided_by.id,
        actor_role_snapshot=None,
        action_code=f"EXPENSE_APPROVAL_{decision}",
        entity_type="expense_approval",
        entity_public_id=str(approval.id),
        reason=decision_reason,
        after_state={"status": decision, "approved_amount": str(approval.approved_amount) if approval.approved_amount else None},
    )
    return approval


def cancel_pending_approval(approval: ExpenseApproval, expense: Expense, *, actor_staff_user_id: uuid.UUID) -> ExpenseApproval:
    """The requester withdraws a still-pending request (e.g. before voiding
    the expense). Distinct from a decision -- no approver eligibility check,
    since nothing is being approved."""
    _check_transition(approval.status, "CANCELLED")
    approval.status = "CANCELLED"
    approval.decided_at = utcnow()
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EXPENSE_APPROVAL_CANCELLED",
        entity_type="expense_approval",
        entity_public_id=str(approval.id),
        after_state={"status": "CANCELLED"},
    )
    return approval


def pending_approval_for_expense(expense: Expense) -> ExpenseApproval | None:
    return db_session.execute(
        select(ExpenseApproval)
        .where(ExpenseApproval.expense_id == expense.id, ExpenseApproval.status == "PENDING")
        .order_by(ExpenseApproval.requested_at.desc())
    ).scalars().first()
