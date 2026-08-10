"""Phase 9.5D Milestone 15 -- append-only Commission ledger.

Non-Negotiable basis rules: commission is earned only from confirmed
Payment Allocation -- never Quote/Order/Invoice totals or an unconfirmed
Payment. Tax excluded by default. Historical earnings are never
overwritten or deleted; a reversal is always a NEW append-only row.
Duplicate earning under concurrency is structurally impossible via a
real DB-level partial unique index on source_payment_allocation_id
(migration a3c8e5d29f47) -- not merely an application-level check,
which could still race. Duplicate payout is structurally impossible via
the existing unique constraint on commission_ledger_entry_id
(Phase 9.5A, owner_commission_payout_lines). See
docs/owner/phase9_5d/commission-ledger-contract.md,
commission-attribution-policy.md, commission-refund-reversal-contract.md.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit.services import record as audit_record
from app.commissions.errors import CommissionError
from app.commissions.services import calculate_commission, resolve_active_rule
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_sales import CommercialInvoice, PaymentAllocation
from app.models.commissions import (
    CommissionLedgerEntry,
    CommissionPayoutBatch,
    CommissionPayoutLine,
)
from app.models.employees import EmployeeProfile


def _tax_excluded_base(invoice: CommercialInvoice, allocated_amount: Decimal) -> Decimal:
    """Non-Negotiable: tax excluded by default. Prorates the allocated
    amount by the invoice's own taxable fraction ((subtotal -
    discount_total) / total) rather than assuming the whole allocation
    is tax-free or blindly subtracting a flat tax figure -- a partial
    allocation should exclude tax in the same proportion the invoice
    itself carries it."""
    if invoice.total <= 0:
        return Decimal("0.00")
    taxable_fraction = (invoice.subtotal - invoice.discount_total) / invoice.total
    return (allocated_amount * taxable_fraction).quantize(Decimal("0.01"))


def post_earning_for_allocation(allocation: PaymentAllocation, *, actor_staff_user_id: uuid.UUID) -> CommissionLedgerEntry | None:
    """The real earning trigger -- called from allocation.py::allocate_payment()
    immediately after a real, confirmed allocation is created (never
    from Quote/Order/Invoice creation or from payment confirmation
    alone, which has no allocation-specific amount to earn against yet).
    Returns None (not an error) when the crediting employee has no
    active commission plan/rule assignment -- nothing to earn, a normal
    condition, not a failure."""
    invoice = db_session.get(CommercialInvoice, allocation.commercial_invoice_id)
    employee_profile_id = invoice.created_by_employee_profile_id

    rule = resolve_active_rule(employee_profile_id, allocation.allocated_at.date())
    if rule is None:
        return None

    base_amount = _tax_excluded_base(invoice, allocation.allocated_amount)
    commission_amount = calculate_commission(rule, base_amount)
    rate_or_fixed = rule.rate_percentage if rule.rate_percentage is not None else rule.fixed_amount

    entry = CommissionLedgerEntry(
        employee_profile_id=employee_profile_id,
        commission_rule_version_id=rule.id,
        source_commercial_invoice_id=invoice.id,
        source_payment_record_id=allocation.payment_record_id,
        source_payment_allocation_id=allocation.id,
        base_amount=base_amount,
        rate_or_fixed_applied=rate_or_fixed,
        commission_amount=commission_amount,
        currency=allocation.currency,
        status="EARNED",
        earned_at=utcnow(),
    )
    db_session.add(entry)
    try:
        db_session.flush()
    except IntegrityError as exc:
        # The real DB-level guarantee (item: "duplicate earning ...
        # impossible under concurrency") -- the partial unique index
        # catches a race the application-level check above cannot fully
        # rule out (two concurrent allocate_payment() calls could both
        # pass a naive pre-check before either commits).
        db_session.rollback()
        raise CommissionError("COMMISSION_ALREADY_EARNED_FOR_ALLOCATION") from exc
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="COMMISSION_EARNED",
        entity_type="commission_ledger_entry",
        entity_public_id=str(entry.id),
        after_state={"employee_profile_id": str(employee_profile_id), "commission_amount": str(commission_amount)},
    )
    return entry


def _resolve_staff_user_id(employee_profile_id: uuid.UUID) -> uuid.UUID | None:
    profile = db_session.get(EmployeeProfile, employee_profile_id)
    return profile.staff_user_id if profile else None


def approve_commission_entry(entry: CommissionLedgerEntry, *, actor_staff_user_id: uuid.UUID) -> CommissionLedgerEntry:
    """Non-Negotiable: beneficiary cannot approve their own adjustment --
    checked in the service layer regardless of permission grant, same
    self-approval-block pattern used throughout this phase
    (CommercialApproval, payment confirmation, refund approval)."""
    if entry.status != "EARNED":
        raise CommissionError("COMMISSION_INVALID_TRANSITION", from_status=entry.status, to_status="APPROVED")

    beneficiary_staff_user_id = _resolve_staff_user_id(entry.employee_profile_id)
    if beneficiary_staff_user_id == actor_staff_user_id:
        raise CommissionError("COMMISSION_SELF_APPROVAL_FORBIDDEN")

    entry.status = "APPROVED"
    entry.approved_at = utcnow()
    entry.approved_by_staff_user_id = actor_staff_user_id
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="COMMISSION_APPROVED",
        entity_type="commission_ledger_entry",
        entity_public_id=str(entry.id),
        after_state={"status": "APPROVED"},
    )
    return entry


def reverse_commission_entry(
    entry: CommissionLedgerEntry, *, reversal_amount: Decimal, reason: str, actor_staff_user_id: uuid.UUID
) -> CommissionLedgerEntry:
    """Refunds create append-only proportional reversal entries -- never
    an edit of the original. reversal_amount may be less than
    entry.commission_amount for a proportional (partial-refund-driven)
    reversal."""
    if entry.status not in ("EARNED", "APPROVED", "PAID"):
        raise CommissionError("COMMISSION_INVALID_TRANSITION", from_status=entry.status, to_status="REVERSED")
    if not reason or not reason.strip():
        raise CommissionError("COMMISSION_REASON_REQUIRED")
    if reversal_amount <= 0 or reversal_amount > entry.commission_amount:
        raise CommissionError("INVALID_COMMISSION_RATE")

    reversal = CommissionLedgerEntry(
        employee_profile_id=entry.employee_profile_id,
        commission_rule_version_id=entry.commission_rule_version_id,
        source_commercial_invoice_id=entry.source_commercial_invoice_id,
        source_payment_record_id=entry.source_payment_record_id,
        source_payment_allocation_id=entry.source_payment_allocation_id,
        base_amount=entry.base_amount,
        rate_or_fixed_applied=entry.rate_or_fixed_applied,
        commission_amount=-reversal_amount,
        currency=entry.currency,
        status="REVERSED",
        earned_at=utcnow(),
        reversal_of_ledger_entry_id=entry.id,
    )
    db_session.add(reversal)
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="COMMISSION_REVERSED",
        entity_type="commission_ledger_entry",
        entity_public_id=str(reversal.id),
        reason=reason,
        after_state={"reversal_of_ledger_entry_id": str(entry.id), "commission_amount": str(-reversal_amount)},
    )
    return reversal


def _already_reversed_amount(entry: CommissionLedgerEntry) -> Decimal:
    reversed_amounts = db_session.execute(
        select(CommissionLedgerEntry.commission_amount).where(CommissionLedgerEntry.reversal_of_ledger_entry_id == entry.id)
    ).scalars().all()
    # Reversal rows store a negative commission_amount -- sum their
    # absolute value to get total already reversed against this entry.
    return -sum(reversed_amounts, Decimal("0.00"))


def reverse_commissions_for_refund(
    invoice: CommercialInvoice, *, refund_amount: Decimal, collected_amount: Decimal, reason: str, actor_staff_user_id: uuid.UUID
) -> list[CommissionLedgerEntry]:
    """The real refund-driven reversal trigger -- wired from
    refunds.py::confirm_refund(). Reverses each active (non-fully-reversed)
    earning entry against this invoice proportionally to the fraction of
    collected money being given back, never the full entry unless the
    refund itself is full -- matching 'refunds create append-only
    proportional reversal entries', never a blanket all-or-nothing
    reversal of every entry regardless of the refund's actual size."""
    if collected_amount <= 0:
        return []
    proportion = min(refund_amount / collected_amount, Decimal("1"))

    entries = db_session.execute(
        select(CommissionLedgerEntry).where(
            CommissionLedgerEntry.source_commercial_invoice_id == invoice.id,
            CommissionLedgerEntry.reversal_of_ledger_entry_id.is_(None),
            CommissionLedgerEntry.status.in_(("EARNED", "APPROVED", "PAID")),
        )
    ).scalars().all()

    reversals = []
    for entry in entries:
        remaining = entry.commission_amount - _already_reversed_amount(entry)
        reversal_amount = (remaining * proportion).quantize(Decimal("0.01"))
        if reversal_amount > 0:
            reversals.append(reverse_commission_entry(entry, reversal_amount=reversal_amount, reason=reason, actor_staff_user_id=actor_staff_user_id))
    return reversals


def create_payout_batch(fields: dict, actor_staff_user_id: uuid.UUID) -> CommissionPayoutBatch:
    if not fields.get("batch_reference") or not fields["batch_reference"].strip():
        raise CommissionError("COMMISSION_PAYOUT_REFERENCE_REQUIRED")

    batch = CommissionPayoutBatch(
        batch_reference=fields["batch_reference"],
        period_start=fields["period_start"],
        period_end=fields["period_end"],
        status="DRAFT",
        payment_method=fields.get("payment_method"),
        created_by_staff_user_id=actor_staff_user_id,
    )
    db_session.add(batch)
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="COMMISSION_PAYOUT_BATCH_CREATED",
        entity_type="commission_payout_batch",
        entity_public_id=str(batch.id),
        after_state={"batch_reference": batch.batch_reference},
    )
    return batch


def approve_payout_batch(batch: CommissionPayoutBatch, *, actor_staff_user_id: uuid.UUID) -> CommissionPayoutBatch:
    if batch.status != "DRAFT":
        raise CommissionError("COMMISSION_INVALID_TRANSITION", from_status=batch.status, to_status="APPROVED")
    if batch.created_by_staff_user_id == actor_staff_user_id:
        raise CommissionError("COMMISSION_SELF_APPROVAL_FORBIDDEN")

    batch.status = "APPROVED"
    batch.approved_by_staff_user_id = actor_staff_user_id
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="COMMISSION_PAYOUT_BATCH_APPROVED",
        entity_type="commission_payout_batch",
        entity_public_id=str(batch.id),
        after_state={"status": "APPROVED"},
    )
    return batch


def record_payout(
    entry: CommissionLedgerEntry, batch: CommissionPayoutBatch, *, actor_staff_user_id: uuid.UUID
) -> CommissionPayoutLine:
    """Payout recording requires authority (route-layer permission,
    commissions.pay -- pre-seeded, SUPER_ADMIN-only per Milestone 1's
    audit) and an external reference (batch.batch_reference, required
    non-empty at batch-creation time). Duplicate payout for the same
    entry is structurally impossible via the existing
    uq_commission_payout_one_per_entry unique constraint (Phase 9.5A) --
    not merely an application-level check."""
    if batch.status != "APPROVED":
        raise CommissionError("COMMISSION_INVALID_TRANSITION", from_status=batch.status, to_status="APPROVED")
    if entry.status != "APPROVED":
        raise CommissionError("COMMISSION_INVALID_TRANSITION", from_status=entry.status, to_status="PAID")

    line = CommissionPayoutLine(
        commission_payout_batch_id=batch.id,
        employee_profile_id=entry.employee_profile_id,
        commission_ledger_entry_id=entry.id,
        amount=entry.commission_amount,
        currency=entry.currency,
    )
    db_session.add(line)
    try:
        db_session.flush()
    except IntegrityError as exc:
        db_session.rollback()
        raise CommissionError("COMMISSION_ALREADY_IN_PAYOUT_BATCH") from exc

    entry.status = "PAID"
    entry.paid_at = utcnow()
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="COMMISSION_PAID",
        entity_type="commission_ledger_entry",
        entity_public_id=str(entry.id),
        after_state={"payout_batch_reference": batch.batch_reference, "amount": str(entry.commission_amount)},
    )
    return line
