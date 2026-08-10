"""Phase 9.5E -- stable-code exceptions for the Expenses/Payee/Cash-Closing
service layer. Reuses the shared StableCodeError base (Phase 9.5B-R3),
matching every other domain in this codebase -- see
app/commercial_sales/errors.py for the identical pattern this mirrors."""
from __future__ import annotations

from app.commercial_ops.errors import StableCodeError

EXPENSE_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"SUBMITTED", "VOID"},
    "SUBMITTED": {"RETURNED", "APPROVED", "REJECTED", "VOID"},
    "RETURNED": {"SUBMITTED", "VOID"},
    "APPROVED": {"PARTIALLY_PAID", "PAID", "VOID"},
    "PARTIALLY_PAID": {"PAID"},
    "PAID": set(),
    "REJECTED": set(),
    "VOID": set(),
}

EXPENSE_APPROVAL_TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"APPROVED", "REJECTED", "RETURNED", "CANCELLED"},
    "APPROVED": set(),
    "REJECTED": set(),
    "RETURNED": set(),
    "CANCELLED": set(),
}

CASH_CLOSING_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"SUBMITTED"},
    "SUBMITTED": {"REVIEW_REQUIRED", "APPROVED", "REJECTED"},
    "REVIEW_REQUIRED": {"APPROVED", "REJECTED"},
    "APPROVED": {"CLOSED", "REOPENED"},
    "REJECTED": {"DRAFT"},
    "REOPENED": {"DRAFT", "SUBMITTED", "REVIEW_REQUIRED", "APPROVED"},
    "CLOSED": {"REOPENED"},
}


class ExpenseError(StableCodeError):
    _MESSAGES = {
        # Lifecycle
        "INVALID_EXPENSE_TRANSITION": "Cannot change expense status from {from_status} to {to_status}.",
        "REASON_REQUIRED": "A reason is required for this action.",
        "RECORD_NOT_FOUND": "Record not found.",
        "RECORD_ACCESS_DENIED": "You do not have access to this record.",
        "NON_FINITE_AMOUNT": "Amount must be a finite, positive number.",
        "CURRENCY_MISMATCH": "Currency {given} does not match the expense's currency {expected}.",
        "INVALID_CURRENCY_CODE": "Currency must be a 3-letter ISO 4217 code.",
        "PAYEE_REQUIRED": "A payee is required.",
        "PAYEE_INACTIVE": "This payee is not active.",
        "CATEGORY_INACTIVE": "This expense category is not active.",
        # Payee
        "PAYEE_TYPE_INVALID": "payee_type must be EXTERNAL or EMPLOYEE.",
        "EMPLOYEE_BENEFICIARY_REQUIRED": "employee_profile_id is required for an EMPLOYEE payee.",
        "EXTERNAL_CONTACT_NOT_ALLOWED_FOR_EMPLOYEE": "An EMPLOYEE payee cannot have an external_contact_reference.",
        # Approval / segregation of duties (expense-approval-and-segregation-contract.md)
        "INVALID_EXPENSE_APPROVAL_TRANSITION": "Cannot change approval status from {from_status} to {to_status}.",
        "SELF_APPROVAL_FORBIDDEN": "You cannot approve your own expense request.",
        "BENEFICIARY_APPROVAL_FORBIDDEN": "You cannot approve an expense where you are the recorded beneficiary.",
        "APPROVAL_STALE": "This expense was changed after the approval was requested. Reload and try again.",
        "APPROVED_AMOUNT_EXCEEDS_REQUESTED": "Approved amount ({approved}) cannot exceed the requested amount ({requested}).",
        "APPROVER_INELIGIBLE": "This account is not eligible to approve this expense: {reason}.",
        "APPROVER_MISSING_PERMISSION": "This account does not have expenses.approve.",
        "APPROVER_SUSPENDED_OR_TERMINATED": "This approver's employee profile is suspended or terminated.",
        "APPROVER_MISSING_EMPLOYEE_PROFILE": "This approver has no active employee profile.",
        "EXPENSE_NOT_PENDING_APPROVAL": "This expense has no pending approval request.",
        # Payment
        "EXPENSE_NOT_APPROVED": "The expense must be approved before a payment can be recorded.",
        "EXPENSE_TERMINAL_STATE": "This expense is in a terminal state ({status}) and cannot be paid.",
        "PAYMENT_EXCEEDS_OUTSTANDING": "Payment amount ({amount}) exceeds the outstanding approved balance ({outstanding}).",
        "IDEMPOTENCY_CONFLICT": "This request conflicts with an earlier request using the same idempotency key.",
        "PAYMENT_ALREADY_REVERSED": "This payment has already been reversed.",
        "SELF_PAYMENT_RECORDING_FORBIDDEN": "You cannot record payment for your own expense request.",
        # Attachments
        "ATTACHMENT_TOO_LARGE": "Attachment exceeds the maximum allowed size.",
        "ATTACHMENT_TYPE_NOT_ALLOWED": "This file type is not allowed for attachments.",
        "ATTACHMENT_CONTENT_MISMATCH": "The file's actual content does not match its declared type.",
        "ATTACHMENT_EMPTY": "Attachment file is empty.",
        "ATTACHMENT_NOT_FOUND": "Attachment not found.",
        "ATTACHMENT_ACCESS_DENIED": "You do not have access to this attachment.",
        "ATTACHMENT_ARCHIVED": "This attachment has been archived.",
        "INVALID_STORAGE_KEY": "Invalid storage key.",
        # Cash Closing
        "INVALID_CASH_CLOSING_TRANSITION": "Cannot change cash closing status from {from_status} to {to_status}.",
        "CASH_CLOSING_ALREADY_EXISTS": "A cash closing already exists for {business_date} {currency}.",
        "OPENING_CASH_OVERRIDE_REQUIRES_REASON": "A manual opening-cash override requires a reason.",
        "OPENING_CASH_OVERRIDE_REQUIRES_PERMISSION": "You do not have permission to override opening cash.",
        "VARIANCE_EXPLANATION_REQUIRED": "A nonzero variance requires an explanation.",
        "SELF_APPROVAL_FORBIDDEN_CLOSING": "You cannot approve a cash closing you prepared.",
        "CLOSING_IMMUTABLE": "This cash closing is approved/closed and cannot be edited directly -- reopen it first.",
        "REOPEN_REQUIRES_REASON": "Reopening a cash closing requires a reason.",
        "REOPEN_REQUIRES_PERMISSION": "You do not have permission to reopen a cash closing.",
        "REOPEN_REQUIRES_RECENT_AUTHENTICATION": "Reopening a cash closing requires recent re-authentication.",
        "LATE_TRANSACTION_REQUIRES_REOPEN": "A closed business date requires reopening the closing before recording a late transaction.",
        # Duplicate detection
        "DUPLICATE_OVERRIDE_REQUIRES_REASON": "Overriding a duplicate-expense warning requires a reason.",
        # Scheduling / report snapshots
        "SNAPSHOT_ALREADY_PUBLISHED": "A snapshot already exists for this canonical key.",
        "SNAPSHOT_REGENERATION_REQUIRES_PERMISSION": "You do not have permission to regenerate a report snapshot.",
        "SNAPSHOT_REGENERATION_REQUIRES_REASON": "Regenerating a report snapshot requires a reason.",
    }
