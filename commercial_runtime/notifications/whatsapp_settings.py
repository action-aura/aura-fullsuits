"""Outbound WhatsApp -- per-company settings and the enabled/disabled
resolution. Direct structural copy of settings.py (email's own settings
module) -- same two-gate precedence, same shape, see that module's
docstring for the full reasoning; only the key names differ.

  1. AURA_WHATSAPP_PHONE_NUMBER_ID unset (whatsapp_client.is_configured()
     is False) -- hard off, no DB read even needed to know the answer.
  2. This company's own `enabled` setting in whatsapp_settings -- the
     normal per-company opt-in. Default '0' (OFF).

Settings live in the whatsapp_settings table (see
commercial_runtime/notifications/schema.py), a dedicated key/value store
mirroring email_settings -- same reasoning: this module's state must be
one set of tables a future kill-switch/audit tool can reason about without
touching product-specific settings.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from . import whatsapp_client

DEFAULTS = {
    'enabled': '0',
    # Superseded by the whatsapp_recipients table (whatsapp_recipients.py) --
    # that table is the real, multi-recipient/role/branch-scoped routing
    # this codebase actually needs (see its own module docstring). Kept here
    # only because get_all_settings()/existing tests pin this exact key set;
    # the new routing path (whatsapp_hook.py) never reads this value.
    'low_stock_recipient_phone': '',  # E.164, e.g. '+15551234567' -- unused by new routing, see above
    'low_stock_template_name': '',    # must match a template already approved in Meta Business Manager
    'daily_sales_template_name': '',
    'shift_close_template_name': '',
    'ar_overdue_template_name': '',
    # Reference copy of each template's approved wording -- NOT wired to
    # sending in any way. WhatsApp's Cloud API sends whatever is actually
    # approved on Meta's side under `*_template_name`; this codebase has no
    # ability to change that from here (see whatsapp_client.py's docstring --
    # pre-approved templates only, no free text). These four fields exist so
    # an admin can edit/keep a local note of what they submitted to Meta
    # without leaving the app -- editing this text never changes what gets
    # sent. Defaults match the exact wording handed to the business owner at
    # feature-launch time; kept here only as the starting reference text, not
    # re-synced from anywhere.
    #
    # NO CURRENCY LITERAL BELONGS IN THESE BODIES. Every money placeholder
    # arrives already formatted with the company's own currency mark by
    # core/retail/money_format.format_money (whatsapp_hook.py:136-138,
    # 179-181, since 2026-09-03), so {{3}} substitutes as 'JD 12.345', not
    # '12.345'. These bodies used to append a literal ' JOD' after each
    # money placeholder, which -- as SUGGESTED WORDING an owner copies into
    # Meta Business Manager -- makes them submit a template that renders
    # 'JD 12.345 JOD' for a Jordanian shop and '$12.35 JOD' for any other.
    # Nothing renders that at runtime (the bodies are not a code path; see
    # the paragraph above), but the owner ships the mistake to Meta and only
    # finds out when a customer-facing message goes out wrong.
    'daily_sales_template_body': (
        'Aura Retail daily summary for {{1}} — {{2}}. Revenue: {{3}} across {{4}} '
        'transactions (avg ticket {{5}}). Gross profit: {{6}}.'
    ),
    'shift_close_template_body': (
        'Shift closed at {{1}} on {{2}}. Expected cash: {{3}}. Counted: {{4}}. '
        'Variance: {{5}}.'
    ),
    'low_stock_template_body': (
        'Low stock alert: {{1}} is down to {{2}} units (reorder level {{3}}) at {{4}}. '
        "Review the reorder request in Aura Retail's Admin Center."
    ),
    'ar_overdue_template_body': (
        'Receivables alert: {{1}} is overdue from {{2}} customers, including {{3}} '
        'outstanding more than 90 days.'
    ),
    'default_language_code': 'en_US',
    'max_attempts': '8',
    'submit_interval_seconds': '60',
}

_KNOWN_KEYS = frozenset(DEFAULTS.keys())

# The four report types whatsapp_hook.py can enqueue, and which DEFAULTS key
# holds the (Meta-approved) template name for each -- one place both the
# settings UI and the enqueue routing read from, so a new report type is
# added by extending this dict, never by hardcoding a key string at a call
# site.
REPORT_TYPES = frozenset({
    'daily_sales_summary', 'shift_close_report', 'low_stock_alert', 'ar_overdue_alert',
})
TEMPLATE_NAME_KEY_BY_REPORT_TYPE = {
    'daily_sales_summary': 'daily_sales_template_name',
    'shift_close_report': 'shift_close_template_name',
    'low_stock_alert': 'low_stock_template_name',
    'ar_overdue_alert': 'ar_overdue_template_name',
}

# Same real gap email's settings.py already found and fixed itself
# (AUDIT: HIGH -- notifications outbox settings write with zero numeric
# validation, a bad value wedges the outbox worker forever and crashes
# init_app() on restart) -- mirrored here from day one rather than
# reintroducing the same bug in a sibling module.
_NUMERIC_KEYS = frozenset({'max_attempts', 'submit_interval_seconds'})


class UnknownSettingError(ValueError):
    pass


class InvalidSettingValueError(ValueError):
    pass


def get_setting(conn: sqlite3.Connection, company_id, key: str) -> str:
    if key not in _KNOWN_KEYS:
        raise UnknownSettingError(f"Unknown whatsapp setting: {key!r}.")
    row = conn.execute(
        "SELECT svalue FROM whatsapp_settings WHERE company_id=? AND skey=?", (company_id, key)
    ).fetchone()
    if row is None or row[0] is None:
        return DEFAULTS[key]
    return row[0]


def get_all_settings(conn: sqlite3.Connection, company_id) -> dict:
    result = dict(DEFAULTS)
    for row in conn.execute(
        "SELECT skey, svalue FROM whatsapp_settings WHERE company_id=?", (company_id,)
    ).fetchall():
        skey, svalue = row[0], row[1]
        if skey in _KNOWN_KEYS and svalue is not None:
            result[skey] = svalue
    return result


def set_setting(conn: sqlite3.Connection, company_id, key: str, value: str) -> None:
    if key not in _KNOWN_KEYS:
        raise UnknownSettingError(f"Unknown whatsapp setting: {key!r}.")
    if key in _NUMERIC_KEYS:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise InvalidSettingValueError(
                f"{key!r} must be a positive integer, got {value!r}."
            ) from None
        if parsed < 1:
            raise InvalidSettingValueError(
                f"{key!r} must be a positive integer, got {value!r}."
            )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO whatsapp_settings (company_id, skey, svalue, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(company_id, skey) DO UPDATE SET svalue=excluded.svalue, updated_at=excluded.updated_at",
        (company_id, key, value, now),
    )


def is_enabled(conn: sqlite3.Connection, company_id) -> bool:
    """The single source of truth every enqueue/worker/route call must go
    through -- never re-implement this precedence check elsewhere (same
    rule email's own settings.py::is_enabled documents for itself)."""
    if not whatsapp_client.is_configured():
        return False
    return get_setting(conn, company_id, 'enabled') == '1'


def recipient_phone_for(conn: sqlite3.Connection, company_id, key: str) -> Optional[str]:
    """Convenience read for a recipient-shaped setting -- returns None
    instead of '' so callers can use a plain truthiness/`is None` check
    without re-deriving "not configured" from an empty string at every
    call site (mirrors settings.py::recipient_for)."""
    value = get_setting(conn, company_id, key)
    return value or None


def template_name_for(conn: sqlite3.Connection, company_id, report_type: str) -> Optional[str]:
    """Returns the configured (Meta-approved) template name for a report
    type, or None when blank/unset -- same None-not-empty-string contract as
    recipient_phone_for() above, so a caller can use one `is None` check
    instead of re-deriving "not configured" from an empty string. Raises
    UnknownSettingError via get_setting() if report_type isn't a real key in
    TEMPLATE_NAME_KEY_BY_REPORT_TYPE (KeyError surfaces as-is -- an unknown
    report_type here is a programming error at the call site, not a runtime
    condition to handle gracefully)."""
    key = TEMPLATE_NAME_KEY_BY_REPORT_TYPE[report_type]
    return get_setting(conn, company_id, key) or None
