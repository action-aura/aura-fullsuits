"""Phase 9.5E -- stable-code exceptions for the Management Notes service."""
from __future__ import annotations

from app.commercial_ops.errors import StableCodeError

MANAGEMENT_NOTE_TRANSITIONS: dict[str, set[str]] = {
    "OPEN": {"IN_PROGRESS", "DONE", "ARCHIVED"},
    "IN_PROGRESS": {"DONE", "OPEN", "ARCHIVED"},
    "DONE": {"ARCHIVED", "OPEN"},
    "ARCHIVED": set(),
}


class ManagementNoteError(StableCodeError):
    _MESSAGES = {
        "INVALID_MANAGEMENT_NOTE_TRANSITION": "Cannot change note status from {from_status} to {to_status}.",
        "RECORD_NOT_FOUND": "Record not found.",
        "RECORD_ACCESS_DENIED": "You do not have access to this record.",
        "VISIBILITY_INVALID": "visibility must be one of MANAGEMENT_ONLY, SPECIFIC_EMPLOYEES, ALL_STAFF.",
        "SPECIFIC_EMPLOYEES_REQUIRES_GRANTS": "SPECIFIC_EMPLOYEES visibility requires at least one employee grant.",
        "TITLE_REQUIRED": "A title is required.",
        "BODY_REQUIRED": "A body is required.",
        "STALE_VERSION": "This note was changed by someone else. Reload and try again.",
        "AUTHOR_SPOOF_FORBIDDEN": "author_staff_user_id cannot be supplied by the client.",
    }
