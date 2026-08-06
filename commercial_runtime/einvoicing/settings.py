"""JoFotara e-invoicing -- per-company settings and the enabled/disabled
resolution.

Three independent disable layers, evaluated most-forceful first (each one
alone is sufficient to fully disable the feature, matching
docs/einvoicing/phase1/operational-runbook-and-kill-switch.md's rollback
ladder):

  1. AURA_EINVOICING_DISABLED=1  -- build/ops hard off, no DB or file I/O.
  2. killswitch.py's DISABLED flag file -- fastest per-install override.
  3. This company's own `enabled` setting in einvoice_settings -- the normal
     per-company opt-in. Default '0' (OFF).

Settings live in the einvoice_settings table (see
commercial_runtime/einvoicing/schema.py) -- a dedicated key/value store, not
Retail's retail_settings (which Clinic doesn't have at all), so the whole
feature's state is one set of tables the kill switch can reason about
without touching product settings.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from . import killswitch

DEFAULTS = {
    'enabled': '0',
    'provider': 'mock',
    'invoice_family': 'income',           # 'income' | 'general_sales'
    'default_payment_type': 'cash',       # 'cash' | 'credit'
    'seller_tin': '',
    'seller_name': '',
    'seller_activity_code': '',
    'currency': 'JOD',
    'buyer_id_required': '1',
    'submit_interval_seconds': '60',
    'max_attempts': '20',
    'enabled_at': '',
    'istd_base_url': '',
}

_KNOWN_KEYS = frozenset(DEFAULTS.keys())


class UnknownSettingError(ValueError):
    pass


def get_setting(conn: sqlite3.Connection, company_id: int, key: str) -> str:
    if key not in _KNOWN_KEYS:
        raise UnknownSettingError(f"Unknown e-invoicing setting: {key!r}.")
    row = conn.execute(
        "SELECT svalue FROM einvoice_settings WHERE company_id=? AND skey=?", (company_id, key)
    ).fetchone()
    if row is None or row[0] is None:
        return DEFAULTS[key]
    return row[0]


def get_all_settings(conn: sqlite3.Connection, company_id: int) -> dict:
    result = dict(DEFAULTS)
    for row in conn.execute(
        "SELECT skey, svalue FROM einvoice_settings WHERE company_id=?", (company_id,)
    ).fetchall():
        skey, svalue = row[0], row[1]
        if skey in _KNOWN_KEYS and svalue is not None:
            result[skey] = svalue
    return result


def set_setting(conn: sqlite3.Connection, company_id: int, key: str, value: str) -> None:
    if key not in _KNOWN_KEYS:
        raise UnknownSettingError(f"Unknown e-invoicing setting: {key!r}.")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO einvoice_settings (company_id, skey, svalue, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(company_id, skey) DO UPDATE SET svalue=excluded.svalue, updated_at=excluded.updated_at",
        (company_id, key, value, now),
    )
    if key == 'enabled' and value == '1' and not get_setting(conn, company_id, 'enabled_at'):
        conn.execute(
            "INSERT INTO einvoice_settings (company_id, skey, svalue, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(company_id, skey) DO UPDATE SET svalue=excluded.svalue, updated_at=excluded.updated_at",
            (company_id, 'enabled_at', now, now),
        )


def is_enabled(conn: sqlite3.Connection, app_data_dir: str, company_id: int) -> bool:
    """The single source of truth every enqueue/worker/route call must go
    through -- never re-implement this precedence check elsewhere."""
    import os
    if os.environ.get('AURA_EINVOICING_DISABLED') == '1':
        return False
    if killswitch.is_disabled(app_data_dir).disabled:
        return False
    return get_setting(conn, company_id, 'enabled') == '1'


def enabled_at(conn: sqlite3.Connection, company_id: int) -> Optional[str]:
    """The timestamp this company's feature was first turned on, or None if
    never enabled. Used by the outbox reconciliation sweep (Step 6/9) to
    guarantee pre-enablement sales/invoices are never retroactively
    submitted."""
    value = get_setting(conn, company_id, 'enabled_at')
    return value or None
