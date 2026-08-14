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
    'low_stock_recipient_phone': '',  # E.164, e.g. '+15551234567' -- where a queued low-stock alert goes
    'low_stock_template_name': '',    # must match a template already approved in Meta Business Manager
    'max_attempts': '8',
    'submit_interval_seconds': '60',
}

_KNOWN_KEYS = frozenset(DEFAULTS.keys())

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
