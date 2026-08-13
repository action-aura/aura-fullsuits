"""Phase 9.5E -- Expense lifecycle service. Create (DRAFT) -> Submit
(SUBMITTED, opens a new PENDING ExpenseApproval cycle) -> [RETURNED -> revise
-> resubmit]* -> APPROVED/REJECTED (decided in app/expenses/approvals.py) ->
ExpensePayment(s) (app/expenses/payments.py) -> PARTIALLY_PAID/PAID.
Void is available from any non-terminal state. See
docs/owner/phase9_5e/operational-finance-funnel-contract.md."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from app.audit.services import record as audit_record
from app.expenses.errors import EXPENSE_TRANSITIONS, ExpenseError
from app.expenses.fingerprint import current_fingerprint_for_expense
from app.expenses.numbering import allocate_expense_number
from app.extensions import db_session
from app.models.base import utcnow
from app.models.expenses import Expense, ExpenseApproval, ExpenseCategory, Payee


def _check_transition(status: str, target: str) -> None:
    if target not in EXPENSE_TRANSITIONS.get(status, set()):
        raise ExpenseError("INVALID_EXPENSE_TRANSITION", from_status=status, to_status=target)


def create_expense(
    *,
    category_id: uuid.UUID,
    payee_id: uuid.UUID,
    amount: Decimal,
    currency: str,
    expense_date: date,
    description: str,
    external_reference: str | None,
    payment_method: str,
    payment_reference: str | None,
    entered_by_employee_profile_id: uuid.UUID,
) -> Expense:
    if amount <= 0 or not amount.is_finite():
        raise ExpenseError("NON_FINITE_AMOUNT")
    if len(currency) != 3:
        raise ExpenseError("INVALID_CURRENCY_CODE")

    category = db_session.get(ExpenseCategory, category_id)
    if category is None:
        raise ExpenseError("RECORD_NOT_FOUND")
    if not category.is_active:
        raise ExpenseError("CATEGORY_INACTIVE")

    payee = db_session.get(Payee, payee_id)
    if payee is None:
        raise ExpenseError("PAYEE_REQUIRED")
    if not payee.is_active:
        raise ExpenseError("PAYEE_INACTIVE")

    beneficiary_employee_profile_id = payee.employee_profile_id if payee.payee_type == "EMPLOYEE" else None

    expense = Expense(
        category_id=category_id,
        payee_id=payee_id,
        beneficiary_employee_profile_id=beneficiary_employee_profile_id,
        amount=amount,
        currency=currency,
        expense_date=expense_date,
        description=description,
        external_reference=external_reference,
        payment_method=payment_method,
        payment_reference=payment_reference,
        entered_by_employee_profile_id=entered_by_employee_profile_id,
        status="DRAFT",
    )
    db_session.add(expense)
    db_session.flush()
    db_session.commit()

    audit_record(
        actor_staff_user_id=None,
        actor_role_snapshot=None,
        action_code="EXPENSE_CREATED",
        entity_type="expense",
        entity_public_id=str(expense.id),
        after_state={"status": "DRAFT", "amount": str(amount), "currency": currency},
    )
    return expense


def submit_expense(expense: Expense, *, actor_staff_user_id: uuid.UUID) -> Expense:
    """Requesting approval IS submitting -- there is no separate 'request
    approval' step. Opens a new PENDING ExpenseApproval cycle with the
    fingerprint captured now."""
    _check_transition(expense.status, "SUBMITTED")

    if expense.expense_number is None:
        expense.expense_number = allocate_expense_number(as_of=expense.expense_date)

    expense.status = "SUBMITTED"
    db_session.flush()

    fingerprint = current_fingerprint_for_expense(expense)
    approval = ExpenseApproval(
        expense_id=expense.id,
        fingerprint=fingerprint,
        status="PENDING",
        requested_amount=expense.amount,
        requested_by_employee_profile_id=expense.entered_by_employee_profile_id,
        requested_at=utcnow(),
    )
    db_session.add(approval)
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EXPENSE_SUBMITTED",
        entity_type="expense",
        entity_public_id=str(expense.id),
        after_state={"status": "SUBMITTED", "expense_number": expense.expense_number},
    )
    return expense


def revise_expense(
    expense: Expense,
    *,
    actor_staff_user_id: uuid.UUID,
    amount: Decimal | None = None,
    category_id: uuid.UUID | None = None,
    payee_id: uuid.UUID | None = None,
    expense_date: date | None = None,
    description: str | None = None,
    external_reference: str | None = None,
) -> Expense:
    """Only valid from RETURNED. A material revision does not resubmit by
    itself -- the caller must call submit_expense() afterward to open a new
    approval cycle, matching 'material revision creates a new approval
    cycle' from the segregation contract."""
    if expense.status != "RETURNED":
        raise ExpenseError("INVALID_EXPENSE_TRANSITION", from_status=expense.status, to_status="REVISED")

    if amount is not None:
        if amount <= 0 or not amount.is_finite():
            raise ExpenseError("NON_FINITE_AMOUNT")
        expense.amount = amount
    if category_id is not None:
        category = db_session.get(ExpenseCategory, category_id)
        if category is None:
            raise ExpenseError("RECORD_NOT_FOUND")
        if not category.is_active:
            raise ExpenseError("CATEGORY_INACTIVE")
        expense.category_id = category_id
    if payee_id is not None:
        payee = db_session.get(Payee, payee_id)
        if payee is None:
            raise ExpenseError("PAYEE_REQUIRED")
        if not payee.is_active:
            raise ExpenseError("PAYEE_INACTIVE")
        expense.payee_id = payee_id
        expense.beneficiary_employee_profile_id = payee.employee_profile_id if payee.payee_type == "EMPLOYEE" else None
    if expense_date is not None:
        expense.expense_date = expense_date
    if description is not None:
        expense.description = description
    if external_reference is not None:
        expense.external_reference = external_reference

    expense.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EXPENSE_REVISED",
        entity_type="expense",
        entity_public_id=str(expense.id),
        after_state={"amount": str(expense.amount), "version": expense.version},
    )
    return expense


def void_expense(expense: Expense, *, actor_staff_user_id: uuid.UUID, reason: str) -> Expense:
    if not reason or not reason.strip():
        raise ExpenseError("REASON_REQUIRED")
    _check_transition(expense.status, "VOID")
    expense.status = "VOID"
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EXPENSE_VOIDED",
        entity_type="expense",
        entity_public_id=str(expense.id),
        reason=reason,
        after_state={"status": "VOID"},
    )
    return expense
