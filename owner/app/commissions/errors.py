"""Phase 9.5D Milestones 14/15 -- stable-code exceptions for the
commission domain (plan/rule/assignment management, ledger posting/
approval/payout).

Reuses the shared StableCodeError base from commercial_ops (Phase
9.5B-R3), matching app/leads/errors.py::LeadError's pattern exactly --
a separate subclass per domain, not one shared error class across
unrelated domains. app/commissions/ is its own established module
boundary (Phase 9.5A), distinct from app/commercial_sales/.
"""
from __future__ import annotations

from app.commercial_ops.errors import StableCodeError

COMMISSION_ENTRY_TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"EARNED", "CANCELLED"},
    "EARNED": {"APPROVED", "CANCELLED", "DISPUTED"},
    "APPROVED": {"PAID", "DISPUTED"},
    "PAID": set(),
    "REVERSED": set(),
    "CANCELLED": set(),
    "DISPUTED": {"EARNED", "CANCELLED"},
}


class CommissionError(StableCodeError):
    _MESSAGES = {
        "COMMISSION_RULE_TYPE_NOT_IMPLEMENTED": "Commission rule type {rule_type} is not implemented in this phase.",
        "INVALID_COMMISSION_RATE": "Commission rate must be a percentage greater than 0 and at most 100.",
        "INVALID_COMMISSION_FIXED_AMOUNT": "Commission fixed amount must be a positive value with a valid currency.",
        "NO_ACTIVE_COMMISSION_RULE": "The employee has no active commission plan/rule assignment for this date.",
        "COMMISSION_ALREADY_EARNED_FOR_ALLOCATION": "A commission entry already exists for this payment allocation.",
        "COMMISSION_INVALID_TRANSITION": "Cannot change commission entry status from {from_status} to {to_status}.",
        "COMMISSION_SELF_APPROVAL_FORBIDDEN": "You cannot approve or adjust your own commission entry.",
        "COMMISSION_PAYOUT_REFERENCE_REQUIRED": "An external payout reference is required to record a commission payout.",
        "COMMISSION_REASON_REQUIRED": "A reason is required for this commission action.",
        "COMMISSION_ALREADY_IN_PAYOUT_BATCH": "This commission entry is already included in a payout batch.",
    }
