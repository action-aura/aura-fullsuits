"""Phase 9.5E -- secure Expense attachment storage.

Threat model this closes (M20's attachment security test list): path
traversal, absolute paths, malicious/double-extension/unicode filenames,
MIME/magic-byte mismatch, executable/HTML/SVG/script payloads, oversized/
zero-byte files, and IDOR (never trust a client-supplied path -- storage_key
is server-generated, opaque, and every read is authorization-checked by the
caller before this module is ever reached).

No malware scanning is integrated -- MIME/magic-byte/extension allowlisting
only. This module never claims virus/malware scanning, per the governing
spec's own explicit instruction not to claim it without a real scanner."""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime

from flask import current_app

from app.audit.services import record as audit_record
from app.expenses.errors import ExpenseError
from app.extensions import db_session
from app.models.base import utcnow
from app.models.expenses import Expense, ExpenseAttachment

ALLOWED_CONTENT_TYPES = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
}
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_PATH_SEPARATOR_RE = re.compile(r"[\\/]+")
_DOT_RUN_RE = re.compile(r"\.{2,}")


def _sanitize_display_filename(original_filename: str) -> str:
    """Used only for the Content-Disposition header on download -- never as
    part of the storage path. Strips directory components and any
    non-alphanumeric character (defeats traversal sequences, absolute paths,
    unicode homograph tricks, and null-byte injection in one pass). Splits on
    BOTH separators explicitly rather than os.path.basename: basename only
    knows the host's separator, so on the Linux droplet "..\\..\\secrets.pdf"
    came through whole and reached the header as ".._.._secrets.pdf" (CI,
    2026-09-07) while every local run, on Windows, stripped it. Any run of
    two or more dots is then collapsed to "_": a display name that still
    contains ".." after the split is not a name anyone typed."""
    base = _PATH_SEPARATOR_RE.split(original_filename or "attachment")[-1]
    base = base.replace("\x00", "")
    base = _DOT_RUN_RE.sub("_", base)
    cleaned = _SAFE_FILENAME_RE.sub("_", base)
    return cleaned[:200] or "attachment"


def _detect_magic_bytes(content: bytes, declared_content_type: str) -> bool:
    signatures = ALLOWED_CONTENT_TYPES.get(declared_content_type)
    if not signatures:
        return False
    return any(content.startswith(sig) for sig in signatures)


def _storage_root() -> str:
    root = current_app.config["EXPENSE_ATTACHMENT_DIRECTORY"]
    os.makedirs(root, exist_ok=True)
    return root


def upload_attachment(
    expense: Expense,
    *,
    content: bytes,
    original_filename: str,
    declared_content_type: str,
    uploaded_by_employee_profile_id: uuid.UUID,
) -> ExpenseAttachment:
    if not content:
        raise ExpenseError("ATTACHMENT_EMPTY")
    max_bytes = current_app.config["EXPENSE_ATTACHMENT_MAX_BYTES"]
    if len(content) > max_bytes:
        raise ExpenseError("ATTACHMENT_TOO_LARGE")
    if declared_content_type not in ALLOWED_CONTENT_TYPES:
        raise ExpenseError("ATTACHMENT_TYPE_NOT_ALLOWED")
    if not _detect_magic_bytes(content, declared_content_type):
        raise ExpenseError("ATTACHMENT_CONTENT_MISMATCH")

    content_hash = hashlib.sha256(content).hexdigest()
    # storage_key is entirely server-generated -- never derived from the
    # client-supplied filename, so no traversal sequence, absolute path, or
    # double-extension trick in original_filename can ever reach the
    # filesystem path. original_filename is stored only for the
    # Content-Disposition header, sanitized separately.
    storage_key = f"{expense.id}/{uuid.uuid4().hex}"
    abs_path = os.path.join(_storage_root(), storage_key.replace("/", os.sep))
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(content)

    attachment = ExpenseAttachment(
        expense_id=expense.id,
        storage_key=storage_key,
        original_filename=_sanitize_display_filename(original_filename),
        content_type=declared_content_type,
        content_hash=content_hash,
        size_bytes=len(content),
        status="ACTIVE",
        uploaded_by_employee_profile_id=uploaded_by_employee_profile_id,
        uploaded_at=utcnow(),
    )
    db_session.add(attachment)
    db_session.commit()

    audit_record(
        actor_staff_user_id=None,
        actor_role_snapshot=None,
        action_code="EXPENSE_ATTACHMENT_UPLOADED",
        entity_type="expense_attachment",
        entity_public_id=str(attachment.id),
        # Never log the raw bytes, the storage path, or the original filename
        # verbatim -- only structural metadata.
        after_state={"content_type": declared_content_type, "size_bytes": len(content)},
    )
    return attachment


def read_attachment_bytes(attachment: ExpenseAttachment) -> bytes:
    """Caller MUST have already authorization-checked access to
    attachment.expense_id before calling this -- this function performs no
    authorization itself, only safe, traversal-proof path resolution."""
    if attachment.status == "ARCHIVED":
        raise ExpenseError("ATTACHMENT_ARCHIVED")

    root = os.path.realpath(_storage_root())
    candidate = os.path.realpath(os.path.join(root, attachment.storage_key.replace("/", os.sep)))
    if os.path.commonpath([root, candidate]) != root:
        raise ExpenseError("INVALID_STORAGE_KEY")
    if not os.path.isfile(candidate):
        raise ExpenseError("ATTACHMENT_NOT_FOUND")

    with open(candidate, "rb") as f:
        return f.read()


def archive_attachment(attachment: ExpenseAttachment, *, actor_staff_user_id: uuid.UUID) -> ExpenseAttachment:
    attachment.status = "ARCHIVED"
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EXPENSE_ATTACHMENT_ARCHIVED",
        entity_type="expense_attachment",
        entity_public_id=str(attachment.id),
        after_state={"status": "ARCHIVED"},
    )
    return attachment
