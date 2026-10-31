"""Step 1 (2026-10-25) -- a commission taken back AFTER the seller was already
paid is deducted automatically from the seller's next pay-out.

Owner decision (final): "a commission taken back after the seller was paid is
deducted from the seller's next pay-out."

WHAT A DEBT IS. Nothing new is stored for it. A debt is a REVERSED ledger row
(a take-back, always a NEW append-only row) whose parent entry is PAID and
which was posted AFTER that payment (reversal.earned_at > parent.paid_at). A
REVERSED row posted before the payment is NOT a debt: record_payout already
paid only what was left of the parent. A REVERSED row whose parent was never
PAID (still EARNED/APPROVED/CANCELLED) is not a debt either.

Two writers must therefore agree on "before or after the payment":
  * ledger.record_payout takes the seller lock first, then nets every
    take-back already there;
  * every take-back writer (ledger.reverse_commission_entry, the ladder's
    shrunk-share row, the renewal take-back) takes the same seller lock and
    stamps its row through take_back_stamp(), which never lets a row written
    against a PAID parent carry a timestamp at or before that payment (the
    callers pick their `now` before they win the lock, so after a payout that
    committed in between it could otherwise land "before" it and the debt would
    silently vanish).

WHAT IS TAKEN OFF. owner_commission_clawback_deductions (revision
c7d41e90b2a5) records every amount taken off a debt, so a debt larger than one
pay-out is taken in pieces over several (remaining = |debt| - sum of its
deductions) and nothing is edited in place. record_payout deducts oldest debt
first (created_at, id), never more than the pay-out has left, never below 0.

HAND SETTLEMENT. Before this, "the owner settles it by hand" lived outside the
system. settle_by_hand() records it: a real Super Admin only (StaffUser.
is_super_admin, as the late-refund override), a written reason, never more
than what remains. It runs under the same seller lock and row lock as a
pay-out, so a hand settlement racing a pay-out can never settle the same
money twice.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.audit.services import record as audit_record
from app.commissions.errors import CommissionError
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commissions import CommissionClawbackDeduction, CommissionLedgerEntry
from app.models.staff import StaffUser

_ZERO = Decimal("0.00")


def _debt_select(*, employee_profile_id: uuid.UUID | None = None, currency: str | None = None,
                 debt_id: uuid.UUID | None = None, lock: bool = False):
    parent = aliased(CommissionLedgerEntry)
    stmt = (
        select(CommissionLedgerEntry)
        .join(parent, parent.id == CommissionLedgerEntry.reversal_of_ledger_entry_id)
        .where(CommissionLedgerEntry.status == "REVERSED",
               CommissionLedgerEntry.commission_amount < 0,
               parent.status == "PAID",
               CommissionLedgerEntry.earned_at > parent.paid_at)
        .order_by(CommissionLedgerEntry.created_at, CommissionLedgerEntry.id)
    )
    if employee_profile_id is not None:
        stmt = stmt.where(CommissionLedgerEntry.employee_profile_id == employee_profile_id)
    if currency is not None:
        stmt = stmt.where(CommissionLedgerEntry.currency == currency)
    if debt_id is not None:
        stmt = stmt.where(CommissionLedgerEntry.id == debt_id)
    if lock:
        # OF the debt row only: the parent is not ours to lock here. populate_existing so
        # the amounts we compute from are the committed ones, not a stale identity-map copy.
        stmt = stmt.with_for_update(of=CommissionLedgerEntry).execution_options(populate_existing=True)
    return stmt


def deducted_so_far(debt_ledger_entry_id: uuid.UUID) -> Decimal:
    total = db_session.execute(
        select(func.coalesce(func.sum(CommissionClawbackDeduction.amount), _ZERO))
        .where(CommissionClawbackDeduction.debt_ledger_entry_id == debt_ledger_entry_id)
    ).scalar_one()
    return Decimal(total)


def remaining(debt: CommissionLedgerEntry) -> Decimal:
    return -Decimal(debt.commission_amount) - deducted_so_far(debt.id)


def open_debts(employee_profile_id: uuid.UUID, currency: str, *, lock: bool = False) -> list[tuple[CommissionLedgerEntry, Decimal]]:
    """The seller's outstanding debts in this currency, oldest first, each with
    what still remains of it (fully settled debts are left out)."""
    rows = db_session.execute(_debt_select(employee_profile_id=employee_profile_id, currency=currency, lock=lock)).scalars().all()
    out = []
    for debt in rows:
        left = remaining(debt)
        if left > 0:
            out.append((debt, left))
    return out


def take_back_stamp(parent_id: uuid.UUID, now):
    """The earned_at a take-back row against `parent_id` must carry. Re-reads the
    parent's status under a row lock (only two columns, so no identity-map state is
    overwritten). Parent PAID: strictly after paid_at, so it is a debt. Otherwise:
    `now` unchanged -- the next payout nets it."""
    from datetime import timedelta

    status, paid_at = db_session.execute(
        select(CommissionLedgerEntry.status, CommissionLedgerEntry.paid_at)
        .where(CommissionLedgerEntry.id == parent_id).with_for_update()
    ).one()
    if status == "PAID" and paid_at is not None and now <= paid_at:
        return paid_at + timedelta(microseconds=1)
    return now


def plan_payout_deductions(employee_profile_id: uuid.UUID, currency: str, payable: Decimal) -> list[tuple[CommissionLedgerEntry, Decimal]]:
    """Which debts a pay-out of `payable` pays down, and by how much: oldest first,
    each min(remaining, what is left of the pay-out). Caller holds the seller lock.
    The sum never exceeds `payable`, so the net can never go below 0."""
    left_to_take = Decimal(payable)
    plan = []
    for debt, left in open_debts(employee_profile_id, currency, lock=True):
        if left_to_take <= 0:
            break
        take = min(left, left_to_take)
        plan.append((debt, take))
        left_to_take -= take
    return plan


def audit_deductions(rows: list[CommissionClawbackDeduction], *, batch_reference: str, actor_staff_user_id: uuid.UUID) -> None:
    for row in rows:
        audit_record(
            actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
            action_code="COMMISSION_CLAWBACK_DEDUCTED", entity_type="commission_ledger_entry",
            entity_public_id=str(row.debt_ledger_entry_id),
            after_state={"debt_ledger_entry_id": str(row.debt_ledger_entry_id),
                         "commission_payout_batch_id": str(row.commission_payout_batch_id),
                         "payout_batch_reference": batch_reference, "amount": str(row.amount),
                         "currency": row.currency},
        )


def settle_by_hand(debt_ledger_entry_id: uuid.UUID, *, actor_staff_user_id: uuid.UUID, reason: str,
                   amount: Decimal | None = None) -> CommissionClawbackDeduction:
    """Record that a Super Admin settled (all or `amount` of) what remains of a
    debt outside the system. Refused unless the actor is a real Super Admin
    (CLAWBACK_SUPER_ADMIN_ONLY), the reason is non-empty, `amount` is > 0 and not
    above what remains, and the debt really is one (and not already settled)."""
    staff = db_session.get(StaffUser, actor_staff_user_id)
    if staff is None or not staff.is_super_admin:
        raise CommissionError("COMMISSION_CLAWBACK_SUPER_ADMIN_ONLY")
    if not reason or not reason.strip():
        raise CommissionError("COMMISSION_CLAWBACK_REASON_REQUIRED")
    if amount is not None and Decimal(amount) <= 0:
        raise CommissionError("COMMISSION_CLAWBACK_AMOUNT_INVALID")

    from app.commissions.ladder import lock_seller

    seen = db_session.get(CommissionLedgerEntry, debt_ledger_entry_id)
    if seen is None:
        raise CommissionError("COMMISSION_CLAWBACK_NOT_A_DEBT")
    # Same order as record_payout: the seller lock first, then the row. A
    # pay-out and a hand settlement for one seller are serialised here, so
    # whichever comes second sees what the first took.
    lock_seller(seen.employee_profile_id)
    debt = db_session.execute(_debt_select(debt_id=debt_ledger_entry_id, lock=True)).scalars().first()
    if debt is None:
        db_session.rollback()
        raise CommissionError("COMMISSION_CLAWBACK_NOT_A_DEBT")
    left = remaining(debt)
    if left <= 0:
        db_session.rollback()
        raise CommissionError("COMMISSION_CLAWBACK_NOTHING_REMAINING")
    take = left if amount is None else Decimal(amount).quantize(Decimal("0.01"))
    if take > left:
        db_session.rollback()
        raise CommissionError("COMMISSION_CLAWBACK_AMOUNT_TOO_LARGE", remaining=str(left))
    row = CommissionClawbackDeduction(
        debt_ledger_entry_id=debt.id, employee_profile_id=debt.employee_profile_id, amount=take,
        currency=debt.currency, kind="HAND", reason=reason.strip(), created_by_staff_user_id=actor_staff_user_id,
    )
    db_session.add(row)
    db_session.flush()
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
        action_code="COMMISSION_CLAWBACK_SETTLED_BY_HAND", entity_type="commission_ledger_entry",
        entity_public_id=str(debt.id), reason=reason.strip(),
        after_state={"debt_ledger_entry_id": str(debt.id), "amount": str(take), "currency": debt.currency,
                     "remaining_after": str(left - take)},
    )
    return row


def all_open_debts(limit: int = 200) -> list[dict]:
    """Every seller's outstanding debts, oldest first, for the pay-out page."""
    rows = db_session.execute(_debt_select()).scalars().all()
    out = []
    for debt in rows:
        left = remaining(debt)
        if left > 0:
            out.append({"id": debt.id, "employee_profile_id": debt.employee_profile_id, "currency": debt.currency,
                        "amount": -Decimal(debt.commission_amount), "remaining": left})
        if len(out) >= limit:
            break
    return out


def seller_summary(employee_profile_id: uuid.UUID, currency: str) -> dict:
    """What the seller sees: still owed back, taken off pay-outs so far, settled
    by hand so far (this currency). Pure read."""
    outstanding = sum((left for _d, left in open_debts(employee_profile_id, currency)), _ZERO)
    by_kind = dict(db_session.execute(
        select(CommissionClawbackDeduction.kind, func.coalesce(func.sum(CommissionClawbackDeduction.amount), _ZERO))
        .where(CommissionClawbackDeduction.employee_profile_id == employee_profile_id,
               CommissionClawbackDeduction.currency == currency)
        .group_by(CommissionClawbackDeduction.kind)
    ).all())
    return {
        "clawback_outstanding": outstanding,
        "clawback_deducted_from_payouts": Decimal(by_kind.get("PAYOUT", _ZERO)),
        "clawback_settled_by_hand": Decimal(by_kind.get("HAND", _ZERO)),
    }
