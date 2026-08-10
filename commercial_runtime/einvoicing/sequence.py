"""JoFotara e-invoicing -- the gapless, per-company sequence actually
submitted to ISTD.

Deliberately independent of Retail's sales.sale_number and Clinic's
clinic_invoices.invoice_number -- see
docs/einvoicing/phase1/invoice-numbering-audit.md for the full reasoning.
Structurally identical to products/retail/backend/api/retail_api.py's
proven-correct `_next_ref()`: INSERT OR IGNORE + UPDATE ... SET
last_no=last_no+1 + SELECT, against a (company_id, series) keyed row. Just
like _next_ref(), this function does NOT open its own transaction -- it must
be called by a caller already inside a `BEGIN IMMEDIATE` that either commits
the number together with the outbox row it belongs to, or rolls both back
together. A number is only ever "spent" if the row that consumed it actually
landed.
"""
from __future__ import annotations

import sqlite3

VALID_SERIES = ('income', 'general_sales')

_PREFIX = {'income': 'INC', 'general_sales': 'GS'}


class InvalidSeriesError(ValueError):
    pass


def allocate_einvoice_number(conn: sqlite3.Connection, company_id: int, series: str) -> str:
    if series not in VALID_SERIES:
        raise InvalidSeriesError(f"Unknown e-invoice series: {series!r}. Must be one of {VALID_SERIES}.")

    conn.execute(
        "INSERT OR IGNORE INTO einvoice_sequence (company_id, series, last_no) VALUES (?,?,0)",
        (company_id, series),
    )
    conn.execute(
        "UPDATE einvoice_sequence SET last_no = last_no + 1 WHERE company_id=? AND series=?",
        (company_id, series),
    )
    n = conn.execute(
        "SELECT last_no FROM einvoice_sequence WHERE company_id=? AND series=?", (company_id, series)
    ).fetchone()[0]
    return f"{_PREFIX[series]}-{int(n):06d}"
