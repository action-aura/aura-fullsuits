"""Phase 9.5E -- Daily Cash Closing service. Operational cash control, never
bank reconciliation, never GL posting. Every expected-cash figure is
server-derived from real confirmed CASH-method transactions on the business
date -- the caller only ever supplies actual_counted_cash and reasons. See
docs/owner/phase9_5e/cash-closing-contract.md."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.expenses.errors import CASH_CLOSING_TRANSITIONS, ExpenseError
from app.extensions import db_session
from app.models.base import utcnow
from app.models.cash_closing import CashClosing, CashClosingAdjustment, CashClosingReopenEvent
from app.models.commercial_sales import CommercialRefund
from app.models.commissions import CommissionPayoutBatch, CommissionPayoutLine
from app.models.expenses import ExpensePayment
from app.models.subscriptions import PaymentRecord

VARIANCE_MATERIAL_THRESHOLD = Decimal("50.00")
CASH_METHOD = "CASH"


def _check_transition(status: str, target: str) -> None:
    if target not in CASH_CLOSING_TRANSITIONS.get(status, set()):
        raise ExpenseError("INVALID_CASH_CLOSING_TRANSITION", from_status=status, to_status=target)


def _confirmed_cash_collections(business_date: date, currency: str) -> Decimal:
    rows = db_session.execute(
        select(PaymentRecord.amount).where(
            PaymentRecord.status == "CONFIRMED", PaymentRecord.method == CASH_METHOD,
            PaymentRecord.payment_date == business_date, PaymentRecord.currency == currency,
        )
    ).scalars().all()
    return sum((Decimal(str(v)) for v in rows), Decimal("0"))


def _confirmed_cash_refunds(business_date: date, currency: str) -> Decimal:
    rows = db_session.execute(
        select(CommercialRefund.amount)
        .join(PaymentRecord, PaymentRecord.id == CommercialRefund.payment_record_id)
        .where(
            CommercialRefund.status == "PAID", PaymentRecord.method == CASH_METHOD,
            CommercialRefund.currency == currency,
        )
        .where(_date_only(CommercialRefund.paid_at) == business_date)
    ).scalars().all()
    return sum(rows, Decimal("0"))


def _date_only(column):
    from sqlalchemy import cast, Date
    return cast(column, Date)


def _cash_expense_payments(business_date: date, currency: str) -> Decimal:
    rows = db_session.execute(
        select(ExpensePayment.amount).where(
            ExpensePayment.status == "RECORDED", ExpensePayment.payment_method == CASH_METHOD,
            ExpensePayment.currency == currency,
        ).where(_date_only(ExpensePayment.paid_at) == business_date)
    ).scalars().all()
    return sum(rows, Decimal("0"))


def _cash_commission_payouts(business_date: date, currency: str) -> Decimal:
    rows = db_session.execute(
        select(CommissionPayoutLine.amount)
        .join(CommissionPayoutBatch, CommissionPayoutBatch.id == CommissionPayoutLine.commission_payout_batch_id)
        .where(
            CommissionPayoutBatch.payment_method == CASH_METHOD, CommissionPayoutLine.currency == currency,
        )
        .where(_date_only(CommissionPayoutLine.created_at) == business_date)
    ).scalars().all()
    return sum(rows, Decimal("0"))


def _approved_adjustments_total(closing_id: uuid.UUID) -> Decimal:
    rows = db_session.execute(
        select(CashClosingAdjustment.amount).where(
            CashClosingAdjustment.cash_closing_id == closing_id,
            CashClosingAdjustment.approved_by_staff_user_id.is_not(None),
        )
    ).scalars().all()
    return sum(rows, Decimal("0"))


def _prior_closing(business_date: date, currency: str) -> CashClosing | None:
    return db_session.execute(
        select(CashClosing)
        .where(CashClosing.currency == currency, CashClosing.business_date < business_date, CashClosing.status == "CLOSED")
        .order_by(CashClosing.business_date.desc())
    ).scalars().first()


def get_or_create_draft_closing(
    business_date: date, currency: str, *, prepared_by_staff_user_id: uuid.UUID,
    opening_cash_override: Decimal | None = None, opening_cash_override_reason: str | None = None,
) -> CashClosing:
    existing = db_session.execute(
        select(CashClosing).where(CashClosing.business_date == business_date, CashClosing.currency == currency)
    ).scalars().first()
    if existing is not None:
        return existing

    if opening_cash_override is not None:
        if not opening_cash_override_reason or not opening_cash_override_reason.strip():
            raise ExpenseError("OPENING_CASH_OVERRIDE_REQUIRES_REASON")
        opening_cash = opening_cash_override
        is_override = True
    else:
        prior = _prior_closing(business_date, currency)
        if prior is None:
            raise ExpenseError("OPENING_CASH_OVERRIDE_REQUIRES_REASON")
        opening_cash = prior.actual_counted_cash if prior.actual_counted_cash is not None else prior.expected_closing_cash
        is_override = False

    closing = CashClosing(
        business_date=business_date, currency=currency, status="DRAFT",
        opening_cash=opening_cash, opening_cash_is_override=is_override,
        opening_cash_override_reason=opening_cash_override_reason,
        expected_closing_cash=opening_cash,
        prepared_by_staff_user_id=prepared_by_staff_user_id,
    )
    db_session.add(closing)
    db_session.flush()
    recalculate_expected(closing)
    db_session.commit()

    audit_record(
        actor_staff_user_id=prepared_by_staff_user_id, actor_role_snapshot=None,
        action_code="CASH_CLOSING_CREATED", entity_type="cash_closing", entity_public_id=str(closing.id),
        after_state={"business_date": business_date.isoformat(), "currency": currency},
    )
    return closing


def recalculate_expected(closing: CashClosing) -> CashClosing:
    """Server-authoritative -- always recomputed from real confirmed
    transactions, never accepted from a caller."""
    closing.confirmed_cash_collections = _confirmed_cash_collections(closing.business_date, closing.currency)
    closing.confirmed_cash_refunds = _confirmed_cash_refunds(closing.business_date, closing.currency)
    closing.cash_expense_payments = _cash_expense_payments(closing.business_date, closing.currency)
    closing.cash_commission_payouts = _cash_commission_payouts(closing.business_date, closing.currency)
    closing.approved_cash_adjustments = _approved_adjustments_total(closing.id)
    closing.expected_closing_cash = (
        closing.opening_cash
        + closing.confirmed_cash_collections
        - closing.confirmed_cash_refunds
        - closing.cash_expense_payments
        - closing.cash_commission_payouts
        + closing.approved_cash_adjustments
    )
    if closing.actual_counted_cash is not None:
        closing.variance = closing.actual_counted_cash - closing.expected_closing_cash
    return closing


def submit_closing(
    closing: CashClosing, *, actor_staff_user_id: uuid.UUID, actual_counted_cash: Decimal, variance_explanation: str | None,
) -> CashClosing:
    _check_transition(closing.status, "SUBMITTED")

    recalculate_expected(closing)
    closing.actual_counted_cash = actual_counted_cash
    closing.variance = actual_counted_cash - closing.expected_closing_cash

    if closing.variance != 0 and (not variance_explanation or not variance_explanation.strip()):
        raise ExpenseError("VARIANCE_EXPLANATION_REQUIRED")
    closing.variance_explanation = variance_explanation

    target_status = "REVIEW_REQUIRED" if abs(closing.variance) > VARIANCE_MATERIAL_THRESHOLD else "SUBMITTED"
    if target_status == "REVIEW_REQUIRED":
        _check_transition("SUBMITTED", "REVIEW_REQUIRED")
    closing.status = target_status
    closing.submitted_at = utcnow()
    closing.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
        action_code="CASH_CLOSING_SUBMITTED", entity_type="cash_closing", entity_public_id=str(closing.id),
        after_state={"status": closing.status, "variance": str(closing.variance)},
    )
    return closing


def decide_closing(closing: CashClosing, *, decision: str, actor_staff_user_id: uuid.UUID, reason: str | None = None) -> CashClosing:
    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError(f"Unknown decision: {decision!r}")
    if closing.prepared_by_staff_user_id == actor_staff_user_id:
        raise ExpenseError("SELF_APPROVAL_FORBIDDEN_CLOSING")
    _check_transition(closing.status, decision)

    closing.status = decision
    closing.reviewed_by_staff_user_id = actor_staff_user_id
    closing.reviewed_at = utcnow()
    if decision == "APPROVED":
        closing.approved_by_staff_user_id = actor_staff_user_id
        closing.approved_at = utcnow()
    closing.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
        action_code=f"CASH_CLOSING_{decision}", entity_type="cash_closing", entity_public_id=str(closing.id),
        reason=reason, after_state={"status": decision},
    )
    return closing


def close_closing(closing: CashClosing, *, actor_staff_user_id: uuid.UUID) -> CashClosing:
    _check_transition(closing.status, "CLOSED")
    closing.status = "CLOSED"
    closing.closed_at = utcnow()
    closing.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
        action_code="CASH_CLOSING_CLOSED", entity_type="cash_closing", entity_public_id=str(closing.id),
        after_state={"status": "CLOSED"},
    )
    return closing


def reopen_closing(
    closing: CashClosing, *, actor_staff_user_id: uuid.UUID, reason: str, recent_auth_verified: bool,
) -> CashClosing:
    if not reason or not reason.strip():
        raise ExpenseError("REOPEN_REQUIRES_REASON")
    if not recent_auth_verified:
        raise ExpenseError("REOPEN_REQUIRES_RECENT_AUTHENTICATION")
    _check_transition(closing.status, "REOPENED")

    event = CashClosingReopenEvent(
        cash_closing_id=closing.id, reopened_by_staff_user_id=actor_staff_user_id, reason=reason,
        reopened_at=utcnow(), prior_status=closing.status,
        prior_approved_by_staff_user_id=closing.approved_by_staff_user_id, prior_approved_at=closing.approved_at,
        prior_expected_closing_cash=closing.expected_closing_cash, prior_actual_counted_cash=closing.actual_counted_cash,
        prior_variance=closing.variance,
    )
    db_session.add(event)

    closing.status = "REOPENED"
    closing.reopen_count += 1
    closing.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
        action_code="CASH_CLOSING_REOPENED", entity_type="cash_closing", entity_public_id=str(closing.id),
        reason=reason, after_state={"status": "REOPENED", "reopen_count": closing.reopen_count},
    )
    return closing


def add_adjustment(closing: CashClosing, *, amount: Decimal, reason: str, created_by_staff_user_id: uuid.UUID) -> CashClosingAdjustment:
    if closing.status in ("APPROVED", "CLOSED"):
        raise ExpenseError("CLOSING_IMMUTABLE")
    if not reason or not reason.strip():
        raise ExpenseError("REASON_REQUIRED")
    adjustment = CashClosingAdjustment(
        cash_closing_id=closing.id, amount=amount, reason=reason, created_by_staff_user_id=created_by_staff_user_id,
    )
    db_session.add(adjustment)
    db_session.commit()

    audit_record(
        actor_staff_user_id=created_by_staff_user_id, actor_role_snapshot=None,
        action_code="CASH_CLOSING_ADJUSTMENT_CREATED", entity_type="cash_closing_adjustment", entity_public_id=str(adjustment.id),
        reason=reason, after_state={"amount": str(amount)},
    )
    return adjustment


def approve_adjustment(adjustment: CashClosingAdjustment, *, actor_staff_user_id: uuid.UUID) -> CashClosingAdjustment:
    if adjustment.created_by_staff_user_id == actor_staff_user_id:
        raise ExpenseError("SELF_APPROVAL_FORBIDDEN_CLOSING")
    adjustment.approved_by_staff_user_id = actor_staff_user_id
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
        action_code="CASH_CLOSING_ADJUSTMENT_APPROVED", entity_type="cash_closing_adjustment",
        entity_public_id=str(adjustment.id), after_state={"amount": str(adjustment.amount)},
    )
    return adjustment
