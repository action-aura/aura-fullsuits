"""Phase 9.5E -- Expense numbering. Reuses the concurrency-safe
DocumentNumberCounter table and SELECT...FOR UPDATE + SAVEPOINT pattern
Phase 9.5D Milestone 5 already proved (12-thread race test) -- Milestone 1's
audit found this generic table, genuinely no need for a second numbering
mechanism. See docs/owner/phase9_5d/quote-numbering-contract.md and
docs/owner/phase9_5e/expense-numbering-concurrency-report.md."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_sales import DocumentNumberCounter

EXPENSE_DOCUMENT_TYPE = "EXPENSE"
EXPENSE_NUMBER_PREFIX = "EXP"


def allocate_expense_number(*, as_of: date | None = None) -> str:
    as_of = as_of or utcnow().date()
    period_key = str(as_of.year)

    counter = db_session.execute(
        select(DocumentNumberCounter)
        .where(DocumentNumberCounter.document_type == EXPENSE_DOCUMENT_TYPE, DocumentNumberCounter.period_key == period_key)
        .with_for_update()
    ).scalars().first()

    if counter is None:
        try:
            with db_session.begin_nested():
                counter = DocumentNumberCounter(document_type=EXPENSE_DOCUMENT_TYPE, period_key=period_key, next_value=1)
                db_session.add(counter)
                db_session.flush()
        except IntegrityError:
            counter = db_session.execute(
                select(DocumentNumberCounter)
                .where(DocumentNumberCounter.document_type == EXPENSE_DOCUMENT_TYPE, DocumentNumberCounter.period_key == period_key)
                .with_for_update()
            ).scalars().first()

    value = counter.next_value
    counter.next_value = value + 1
    db_session.flush()
    return f"{EXPENSE_NUMBER_PREFIX}-{period_key}-{value:04d}"
