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

#: Aseel-parity wave A-PAR (schema v34, Retail's per-document-type
#: numbering-series feature -- deliberately not named more precisely here;
#: see that feature's own core module, sibling to promotions.py in
#: products/retail/backend/core/retail/, for why its name must never
#: appear anywhere in this package's own tracked text): the local
#: document-number "book" codes a shop configures for itself must never
#: collide with an ISTD series prefix, or a shop's own local sale book and
#: the tax authority's own submitted sequence would both mint strings that
#: START the same way, which is exactly confusable enough to defeat the
#: whole point of a "gapless, dedicated ISTD sequence" -- see
#: docs/einvoicing/phase1/invoice-numbering-audit.md, "Decision: option 2".
#:
#: DERIVED, not hand-typed, so it cannot drift the moment a third family is
#: ever added to `_PREFIX` above -- and derived LIVE, not merely "computed
#: from `_PREFIX`'s values once at import": implemented via module-level
#: `__getattr__` (PEP 562) below rather than a plain module constant, so
#: EVERY access re-reads `_PREFIX`'s current contents. That feature's own
#: reserved-prefix test proves the derivation is genuinely live, not just
#: today's two values, by monkeypatching `_PREFIX` to add a third family
#: AFTER this module has already been imported and confirming the new
#: prefix is picked up on the very next access -- a plain
#: `frozenset(_PREFIX.values())` assigned once at module load would not
#: see that change, and the test would have to settle for asserting
#: today's two literals, which is the exact "the two literals must match"
#: copy this feature exists to avoid. The book-creation route
#: (products/retail/backend/api/retail_api.py) imports this LAZILY, inside
#: the function, matching how that file already reaches into this package
#: elsewhere -- never imported at module scope by that feature's own core
#: module, which must import nothing from this package at all (see that
#: module's own docstring, "THE CONSTRAINT THAT DOMINATES THIS FEATURE").
def __getattr__(name):
    if name == 'RESERVED_LOCAL_PREFIXES':
        return frozenset(_PREFIX.values())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
