"""Outbound email -- per-company settings and the enabled/disabled
resolution.

Two independent gates, most-forceful first -- deliberately a SIMPLER version
of einvoicing/settings.py's three-layer precedence, not a truncated copy:

  1. AURA_SMTP_HOST unset (smtp_client.is_configured() is False) -- hard
     off, no DB read even needed to know the answer. This is simultaneously
     this module's "build/ops hard off" layer AND its "fastest possible
     kill switch" layer -- einvoicing needs a separate file-based killswitch
     (commercial_runtime/einvoicing/killswitch.py) because
     AURA_EINVOICING_DISABLED alone would force operators to restart the
     whole process to flip it back on for one urgent invoice; email has no
     equivalent urgency (nothing here is a legal submission deadline), so
     one env var doing double duty -- unset it to stop everything on the
     next tick, set it to resume -- is enough. Unsetting an env var still
     requires a process restart to take effect in most deployments, which
     is an intentional, accepted trade-off for this feature's lower stakes.
  2. This company's own `enabled` setting in email_settings -- the normal
     per-company opt-in. Default '0' (OFF), same convention as
     einvoicing's own per-company `enabled` default.

Settings live in the email_settings table (see
commercial_runtime/notifications/schema.py) -- a dedicated key/value store,
not products/retail's own `retail_settings` (which Clinic doesn't have at
all), for the identical reason einvoicing keeps its own einvoice_settings
table rather than reusing retail_settings: this module's state must be one
set of tables a future kill-switch/audit tool can reason about without
touching product-specific settings.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from . import smtp_client

DEFAULTS = {
    'enabled': '0',
    'low_stock_recipient': '',      # where a queued low-stock alert email goes
    'reports_recipient': '',        # default recipient for POST /reports/email when none is given explicitly
    'max_attempts': '8',
    'submit_interval_seconds': '60',
}

_KNOWN_KEYS = frozenset(DEFAULTS.keys())

# Keys every caller eventually int()-casts with no try/except of its own --
# worker.py::_apply_retry's max_attempts read and app.py's
# _resume_notifications_workers()/routes.py's submit_interval_seconds reads
# all trust these are parseable. Validate here, at the single write path,
# rather than guarding every downstream int() call: a bad value must never
# reach the table in the first place, since an already-stored bad value
# still wedges the outbox (worker.py) or crashes init_app() on the next
# restart (app.py) the moment it's read back (AUDIT: HIGH -- notifications
# outbox settings write with zero numeric validation).
_NUMERIC_KEYS = frozenset({'max_attempts', 'submit_interval_seconds'})


class UnknownSettingError(ValueError):
    pass


class InvalidSettingValueError(ValueError):
    pass


def get_setting(conn: sqlite3.Connection, company_id, key: str) -> str:
    if key not in _KNOWN_KEYS:
        raise UnknownSettingError(f"Unknown notifications setting: {key!r}.")
    row = conn.execute(
        "SELECT svalue FROM email_settings WHERE company_id=? AND skey=?", (company_id, key)
    ).fetchone()
    if row is None or row[0] is None:
        return DEFAULTS[key]
    return row[0]


def get_all_settings(conn: sqlite3.Connection, company_id) -> dict:
    result = dict(DEFAULTS)
    for row in conn.execute(
        "SELECT skey, svalue FROM email_settings WHERE company_id=?", (company_id,)
    ).fetchall():
        skey, svalue = row[0], row[1]
        if skey in _KNOWN_KEYS and svalue is not None:
            result[skey] = svalue
    return result


def set_setting(conn: sqlite3.Connection, company_id, key: str, value: str) -> None:
    if key not in _KNOWN_KEYS:
        raise UnknownSettingError(f"Unknown notifications setting: {key!r}.")
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
        "INSERT INTO email_settings (company_id, skey, svalue, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(company_id, skey) DO UPDATE SET svalue=excluded.svalue, updated_at=excluded.updated_at",
        (company_id, key, value, now),
    )


def is_enabled(conn: sqlite3.Connection, company_id) -> bool:
    """The single source of truth every enqueue/worker/route call must go
    through -- never re-implement this precedence check elsewhere (same
    rule einvoicing/settings.py::is_enabled documents for itself)."""
    if not smtp_client.is_configured():
        return False
    return get_setting(conn, company_id, 'enabled') == '1'


def recipient_for(conn: sqlite3.Connection, company_id, key: str) -> Optional[str]:
    """Convenience read for a recipient-shaped setting (`low_stock_recipient`
    / `reports_recipient`) -- returns None instead of '' so callers can use
    a plain truthiness/`is None` check without re-deriving "not configured"
    from an empty string at every call site."""
    value = get_setting(conn, company_id, key)
    return value or None
