"""Phase 9.5E -- Expense approval fingerprint. Mirrors the exact
deterministic-content-fingerprint pattern Phase 9.5D Milestone 6 proved for
Commercial Sales (app/commercial_sales/approvals.py
compute_line_commercial_fingerprint) -- a hash of the material fields an
approval decision is actually about, recomputed live from persisted state and
compared to what was captured at request time, never a caller-supplied
version number.

Attachment policy: this codebase has no per-category "attachment required"
flag today (Milestone 1's audit found none). Rather than add one without a
specified policy, the fingerprint includes the CURRENT sorted set of ACTIVE
attachment content hashes unconditionally -- a superset of "only when
required by policy" that never under-protects: any add/remove/replace of an
attachment always invalidates a pending approval, whether or not the expense's
category happens to require one."""
from __future__ import annotations

import hashlib
import json
import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.extensions import db_session
from app.models.expenses import Expense, ExpenseAttachment

_FINGERPRINT_CENT = Decimal("0.01")


def _quantize(value: Decimal) -> str:
    return str(value.quantize(_FINGERPRINT_CENT, rounding=ROUND_HALF_UP))


def _active_attachment_hashes(expense_id: uuid.UUID) -> list[str]:
    hashes = db_session.execute(
        select(ExpenseAttachment.content_hash)
        .where(ExpenseAttachment.expense_id == expense_id, ExpenseAttachment.status == "ACTIVE")
        .order_by(ExpenseAttachment.content_hash)
    ).scalars().all()
    return sorted(hashes)


def compute_expense_fingerprint(
    *,
    expense_id: uuid.UUID,
    requester_employee_profile_id: uuid.UUID,
    beneficiary_employee_profile_id: uuid.UUID | None,
    payee_id: uuid.UUID | None,
    category_id: uuid.UUID,
    requested_amount: Decimal,
    currency: str,
    expense_date,
    business_purpose: str,
    external_reference: str | None,
    attachment_hashes: list[str],
) -> str:
    payload = {
        "expense_id": str(expense_id),
        "requester_employee_profile_id": str(requester_employee_profile_id),
        "beneficiary_employee_profile_id": str(beneficiary_employee_profile_id) if beneficiary_employee_profile_id else None,
        "payee_id": str(payee_id) if payee_id else None,
        "category_id": str(category_id),
        "requested_amount": _quantize(requested_amount),
        "currency": currency,
        "expense_date": expense_date.isoformat(),
        "business_purpose": business_purpose,
        "external_reference": external_reference,
        "attachment_hashes": attachment_hashes,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def current_fingerprint_for_expense(expense: Expense) -> str:
    """Recomputes from the expense's CURRENT persisted state -- the only
    trustworthy source for a staleness comparison."""
    return compute_expense_fingerprint(
        expense_id=expense.id,
        requester_employee_profile_id=expense.entered_by_employee_profile_id,
        beneficiary_employee_profile_id=expense.beneficiary_employee_profile_id,
        payee_id=expense.payee_id,
        category_id=expense.category_id,
        requested_amount=expense.amount,
        currency=expense.currency,
        expense_date=expense.expense_date,
        business_purpose=expense.description,
        external_reference=expense.external_reference,
        attachment_hashes=_active_attachment_hashes(expense.id),
    )
