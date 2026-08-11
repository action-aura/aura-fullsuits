"""Phase 9.5D Milestone 5 -- concurrency-safe sequential document numbering.

Milestone 1's audit found no existing numbering-sequence generator to
reuse (license-key generation and EmployeeProfile.employee_number are
both "caller supplies it, DB enforces uniqueness" shapes, not sequence
generators). This is genuinely new authority.

Real Postgres concurrency mechanism: SELECT ... FOR UPDATE on the counter
row serializes concurrent allocators for the same (document_type,
period_key); the first-ever-allocation-of-the-year race (no counter row
exists yet) is handled via a SAVEPOINT (begin_nested) so a losing
transaction's insert failure only rolls back that nested savepoint, never
the caller's outer transaction (which may already contain a partially
built parent Quote/Order/Invoice row). See
docs/owner/phase9_5d/quote-numbering-contract.md.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_sales import DocumentNumberCounter

DOCUMENT_TYPE_PREFIXES = {
    "QUOTE": "Q",
    "SALES_ORDER": "SO",
    "COMMERCIAL_INVOICE": "INV",
    "COMMERCIAL_REFUND": "REF",
}


def allocate_document_number(document_type: str, *, as_of: date | None = None) -> str:
    if document_type not in DOCUMENT_TYPE_PREFIXES:
        raise ValueError(f"Unknown document_type for numbering: {document_type}")
    prefix = DOCUMENT_TYPE_PREFIXES[document_type]
    as_of = as_of or utcnow().date()
    period_key = str(as_of.year)

    counter = db_session.execute(
        select(DocumentNumberCounter)
        .where(DocumentNumberCounter.document_type == document_type, DocumentNumberCounter.period_key == period_key)
        .with_for_update()
    ).scalars().first()

    if counter is None:
        try:
            with db_session.begin_nested():
                counter = DocumentNumberCounter(document_type=document_type, period_key=period_key, next_value=1)
                db_session.add(counter)
                db_session.flush()
        except IntegrityError:
            counter = db_session.execute(
                select(DocumentNumberCounter)
                .where(DocumentNumberCounter.document_type == document_type, DocumentNumberCounter.period_key == period_key)
                .with_for_update()
            ).scalars().first()

    value = counter.next_value
    counter.next_value = value + 1
    db_session.flush()
    return f"{prefix}-{period_key}-{value:04d}"
