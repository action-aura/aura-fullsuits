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
        # Step 0 ladder (2026-10-24): the owner's 30-day hold, and the ladder
        # rule's own shape (amounts are the owner's, in JOD; no rate).
        "COMMISSION_ON_HOLD": "This commission is still inside its 30-day hold and cannot be approved yet.",
        # Step 0 (2026-10-25): an approved row whose amount was taken back
        # (REVERSED rows) before it was paid out pays only what is left.
        "COMMISSION_TAKEN_BACK": "This commission was taken back (a refund or a correction) and nothing is left to pay.",
        "INVALID_LADDER_RULE": "The monthly ladder rule is in JOD and takes no rate or fixed amount.",
        # Step 1 (2026-10-25): settling an earlier take-back by hand.
        "COMMISSION_CLAWBACK_SUPER_ADMIN_ONLY": "Only a Super Admin can settle a take-back by hand.",
        "COMMISSION_CLAWBACK_REASON_REQUIRED": "A written reason is required to settle a take-back by hand.",
        "COMMISSION_CLAWBACK_NOT_A_DEBT": "This is not a take-back of money that was already paid out.",
        "COMMISSION_CLAWBACK_NOTHING_REMAINING": "Nothing remains of this take-back; it is already settled.",
        "COMMISSION_CLAWBACK_AMOUNT_INVALID": "The amount to settle must be greater than 0.",
        "COMMISSION_CLAWBACK_AMOUNT_TOO_LARGE": "The amount is more than what remains of this take-back.",
        # Seller agreement clause 5 (2026-10-31): objections to a monthly statement.
        "STATEMENT_MONTH_INVALID": "This is not a valid statement month.",
        "STATEMENT_NOT_ISSUED": "This month has not closed yet, so there is no statement to object to.",
        "STATEMENT_OBJECTION_WINDOW_CLOSED": "The 7 days to object to this statement are over.",
        "STATEMENT_OBJECTION_REASON_REQUIRED": "Write the reason for your objection.",
        "STATEMENT_OBJECTION_REASON_TOO_LONG": "The text is too long.",
        "STATEMENT_ALREADY_OBJECTED": "You have already objected to this statement.",
        "STATEMENT_OBJECTION_OUTCOME_INVALID": "Choose accepted or rejected.",
        "STATEMENT_OBJECTION_REPLY_REQUIRED": "Write the reply the seller will read.",
        "STATEMENT_OBJECTION_NOT_FOUND": "That objection does not exist.",
        "STATEMENT_OBJECTION_SELF_ANSWER_FORBIDDEN": "You cannot answer your own objection; another manager must.",
        "STATEMENT_OBJECTION_ALREADY_ANSWERED": "This objection has already been answered.",
    }
