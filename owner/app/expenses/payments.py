"""Phase 9.5E -- ExpensePayment service. Mirrors the row-lock +
re-validation pattern Phase 9.5D proved twice (allocate_payment(),
confirm_refund()) for preventing concurrent overpayment/over-refund races --
see docs/owner/phase9_5d/commercial-sales-financial-property-pass.md."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.expenses.errors import ExpenseError
from app.extensions import db_session
from app.models.base import utcnow
from app.models.expenses import Expense, ExpensePayment


def _valid_paid_amount(expense_id: uuid.UUID) -> Decimal:
    """Sum of RECORDED (non-reversed) payments. A reversal row itself is
    never summed (status REVERSED), and the payment it reverses is excluded
    too -- see record_reversal() below, which flips the original to REVERSED
    rather than leaving both counted."""
    rows = db_session.execute(
        select(ExpensePayment.amount).where(
            ExpensePayment.expense_id == expense_id, ExpensePayment.status == "RECORDED"
        )
    ).scalars().all()
    return sum(rows, Decimal("0"))


def outstanding_amount(expense: Expense) -> Decimal:
    if expense.approved_amount is None:
        return Decimal("0")
    return expense.approved_amount - _valid_paid_amount(expense.id)


def _recalculate_status(expense: Expense) -> None:
    outstanding = outstanding_amount(expense)
    if outstanding <= Decimal("0"):
        expense.status = "PAID"
    elif _valid_paid_amount(expense.id) > Decimal("0"):
        expense.status = "PARTIALLY_PAID"


def record_expense_payment(
    expense: Expense,
    *,
    amount: Decimal,
    currency: str,
    payment_method: str,
    payment_reference: str | None,
    recorded_by_staff_user_id: uuid.UUID,
    idempotency_key: str | None = None,
) -> ExpensePayment:
    if expense.status not in ("APPROVED", "PARTIALLY_PAID"):
        if expense.status in ("PAID", "REJECTED", "VOID", "CANCELLED"):
            raise ExpenseError("EXPENSE_TERMINAL_STATE", status=expense.status)
        raise ExpenseError("EXPENSE_NOT_APPROVED")

    if amount is None or amount <= 0 or not amount.is_finite():
        raise ExpenseError("NON_FINITE_AMOUNT")
    if currency != expense.currency:
        raise ExpenseError("CURRENCY_MISMATCH", given=currency, expected=expense.currency)

    if idempotency_key:
        existing = db_session.execute(
            select(ExpensePayment).where(ExpensePayment.idempotency_key == idempotency_key)
        ).scalars().first()
        if existing is not None:
            if existing.expense_id != expense.id or existing.amount != amount or existing.currency != currency:
                raise ExpenseError("IDEMPOTENCY_CONFLICT")
            return existing

    # Row-lock the expense so a concurrent payment cannot read a stale
    # outstanding balance -- the exact pattern Phase 9.5D's allocate_payment()
    # and confirm_refund() both use.
    db_session.execute(select(Expense).where(Expense.id == expense.id).with_for_update())
    outstanding = outstanding_amount(expense)
    if amount > outstanding:
        raise ExpenseError("PAYMENT_EXCEEDS_OUTSTANDING", amount=str(amount), outstanding=str(outstanding))

    payment = ExpensePayment(
        expense_id=expense.id, amount=amount, currency=currency, payment_method=payment_method,
        payment_reference=payment_reference, status="RECORDED", idempotency_key=idempotency_key,
        recorded_by_staff_user_id=recorded_by_staff_user_id, paid_at=utcnow(),
    )
    db_session.add(payment)
    db_session.flush()

    _recalculate_status(expense)
    db_session.commit()

    audit_record(
        actor_staff_user_id=recorded_by_staff_user_id,
        actor_role_snapshot=None,
        action_code="EXPENSE_PAYMENT_RECORDED",
        entity_type="expense_payment",
        entity_public_id=str(payment.id),
        after_state={"amount": str(amount), "expense_status": expense.status},
    )
    return payment


def reverse_expense_payment(payment: ExpensePayment, expense: Expense, *, actor_staff_user_id: uuid.UUID, reason: str) -> ExpensePayment:
    if not reason or not reason.strip():
        raise ExpenseError("REASON_REQUIRED")
    if payment.status == "REVERSED":
        raise ExpenseError("PAYMENT_ALREADY_REVERSED")

    existing_reversal = db_session.execute(
        select(ExpensePayment).where(ExpensePayment.reversal_of_payment_id == payment.id)
    ).scalars().first()
    if existing_reversal is not None:
        raise ExpenseError("PAYMENT_ALREADY_REVERSED")

    payment.status = "REVERSED"
    reversal = ExpensePayment(
        expense_id=payment.expense_id, amount=payment.amount, currency=payment.currency,
        payment_method=payment.payment_method, payment_reference=f"REVERSAL:{payment.id}",
        status="REVERSED", recorded_by_staff_user_id=actor_staff_user_id,
        reversal_of_payment_id=payment.id, paid_at=utcnow(),
    )
    db_session.add(reversal)
    db_session.flush()

    if expense.status == "PAID":
        expense.status = "PARTIALLY_PAID" if _valid_paid_amount(expense.id) > 0 else "APPROVED"
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EXPENSE_PAYMENT_REVERSED",
        entity_type="expense_payment",
        entity_public_id=str(payment.id),
        reason=reason,
        after_state={"expense_status": expense.status},
    )
    return payment
