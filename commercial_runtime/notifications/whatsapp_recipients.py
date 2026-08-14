"""Outbound WhatsApp -- recipient routing: "who gets which report".

Answers the real gap whatsapp_settings.py's DEFAULTS left open --
low_stock_recipient_phone is one phone number for one purpose. A real
install needs many numbers (owner, per-branch manager, accountant, ...),
each subscribed to a subset of report types, some scoped to one branch and
some to the whole company. This module owns the whatsapp_recipients table
(see commercial_runtime/notifications/schema.py::
apply_whatsapp_recipients_schema for the table definition and the reasoning
behind its column shapes).

`role_label` is a free-form display label, never a foreign key into a
permissions table -- this codebase has no real RBAC (root CLAUDE.md: "only a
bare `role` string, no permission matrix"), so routing never branches on it;
only `report_types_json` and `branch_id` decide who gets what.

recipients_for() is the single routing function every enqueue call site
(whatsapp_hook.py) must go through -- never re-derive this matching rule
elsewhere, same convention whatsapp_settings.py::is_enabled documents for
itself.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

from .whatsapp_settings import REPORT_TYPES

_E164_RE = re.compile(r'^\+[1-9]\d{7,14}$')
_VALID_STATUSES = frozenset({'active', 'inactive'})

_COLUMNS = (
    'id', 'company_id', 'branch_id', 'display_name', 'role_label',
    'phone_e164', 'report_types_json', 'language_code', 'status',
    'created_at', 'updated_at',
)


class InvalidRecipientError(ValueError):
    pass


class RecipientNotFoundError(LookupError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_phone(phone_e164: str) -> str:
    if not phone_e164 or not _E164_RE.match(phone_e164):
        raise InvalidRecipientError(
            f"phone_e164 must be E.164 format (e.g. '+15551234567'), got {phone_e164!r}."
        )
    return phone_e164


def _validate_display_name(display_name: str) -> str:
    if not display_name or not display_name.strip():
        raise InvalidRecipientError("display_name is required.")
    return display_name.strip()


def _validate_report_types(report_types) -> str:
    types = set(report_types or ())
    unknown = types - REPORT_TYPES
    if unknown:
        raise InvalidRecipientError(f"Unknown report type(s): {sorted(unknown)!r}.")
    return json.dumps(sorted(types))


def _validate_branch_id(branch_id) -> Optional[int]:
    if branch_id is None or branch_id == '':
        return None
    try:
        return int(branch_id)
    except (TypeError, ValueError):
        raise InvalidRecipientError(f"branch_id must be an integer or None, got {branch_id!r}.") from None


def _validate_status(status: str) -> str:
    if status not in _VALID_STATUSES:
        raise InvalidRecipientError(f"status must be one of {sorted(_VALID_STATUSES)!r}, got {status!r}.")
    return status


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d['report_types'] = json.loads(d.pop('report_types_json') or '[]')
    return d


def create_recipient(
    conn: sqlite3.Connection, company_id, *,
    display_name: str, phone_e164: str, role_label: Optional[str] = None,
    branch_id=None, report_types=(), language_code: str = 'en_US',
) -> str:
    recipient_id = str(uuid.uuid4())
    now = _now()
    conn.execute(
        "INSERT INTO whatsapp_recipients "
        "(id, company_id, branch_id, display_name, role_label, phone_e164, "
        " report_types_json, language_code, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?, 'active', ?, ?)",
        (
            recipient_id, company_id, _validate_branch_id(branch_id),
            _validate_display_name(display_name), role_label or None,
            _validate_phone(phone_e164), _validate_report_types(report_types),
            language_code or 'en_US', now, now,
        ),
    )
    return recipient_id


_FIELD_VALIDATORS = {
    'display_name': _validate_display_name,
    'phone_e164': _validate_phone,
    'role_label': lambda v: (v or None),
    'branch_id': _validate_branch_id,
    'report_types': _validate_report_types,
    'language_code': lambda v: (v or 'en_US'),
    'status': _validate_status,
}
_FIELD_TO_COLUMN = {'report_types': 'report_types_json'}


def update_recipient(conn: sqlite3.Connection, company_id, recipient_id: str, **fields) -> None:
    """Partial update -- only columns present in `fields` are touched. Each
    field is validated the same way create_recipient() validates it, so a
    PUT with a bad phone number or an unknown report type is rejected the
    same as a POST would be, never silently written."""
    unknown_fields = set(fields) - set(_FIELD_VALIDATORS)
    if unknown_fields:
        raise InvalidRecipientError(f"Unknown recipient field(s): {sorted(unknown_fields)!r}.")

    set_clauses = []
    params = []
    for field, value in fields.items():
        column = _FIELD_TO_COLUMN.get(field, field)
        set_clauses.append(f"{column}=?")
        params.append(_FIELD_VALIDATORS[field](value))
    set_clauses.append("updated_at=?")
    params.append(_now())
    params.extend((recipient_id, company_id))

    cur = conn.execute(
        f"UPDATE whatsapp_recipients SET {', '.join(set_clauses)} WHERE id=? AND company_id=?",
        params,
    )
    if cur.rowcount == 0:
        raise RecipientNotFoundError(f"No recipient {recipient_id!r} for this company.")


def delete_recipient(conn: sqlite3.Connection, company_id, recipient_id: str) -> None:
    """Hard DELETE -- recipients are configuration, not a financial or
    audit-trailed record, and this table is not part of
    commercial_runtime/sync/'s outbox, so there is no reason to soft-delete
    (unlike sales/returns, which are never deleted per this codebase's
    'never edit/delete, only reverse' policy for actual transactions)."""
    cur = conn.execute(
        "DELETE FROM whatsapp_recipients WHERE id=? AND company_id=?", (recipient_id, company_id)
    )
    if cur.rowcount == 0:
        raise RecipientNotFoundError(f"No recipient {recipient_id!r} for this company.")


def list_recipients(conn: sqlite3.Connection, company_id) -> list:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM whatsapp_recipients WHERE company_id=? ORDER BY display_name",
        (company_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def recipients_for(conn: sqlite3.Connection, company_id, report_type: str, branch_id=None) -> list:
    """The routing function every enqueue call site must use.

    Matching rule: an active recipient subscribed to `report_type` receives
    this event when EITHER the recipient is all-branches (their own
    branch_id is NULL) OR the event itself is company-wide (branch_id=None,
    e.g. a daily summary or AR alert with no single branch to scope to) OR
    the recipient's branch_id equals the event's branch_id. A branch-scoped
    recipient (e.g. "Downtown Manager") never receives an event scoped to a
    DIFFERENT branch.

    Subscription membership is checked in Python via json.loads, not SQL
    LIKE-on-JSON -- this table holds at most a handful of rows per company,
    so there is no query the LIKE approach would meaningfully speed up, and
    it avoids the false-positive risk of a substring match (e.g.
    'low_stock_alert' matching inside a longer, unrelated key)."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM whatsapp_recipients "
        "WHERE company_id=? AND status='active'",
        (company_id,),
    ).fetchall()

    matched = []
    for row in rows:
        subscribed = json.loads(row['report_types_json'] or '[]')
        if report_type not in subscribed:
            continue
        recipient_branch = row['branch_id']
        if recipient_branch is None or branch_id is None or recipient_branch == branch_id:
            matched.append(_row_to_dict(row))
    return matched
