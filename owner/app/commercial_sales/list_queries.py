"""UI modernization Stage D (enterprise-table-system) -- list-query
services for the seven commercial-sales/commissions list screens, factored
out of commercial_sales/routes.py's inline query building so each list is
unit-testable and follows the exact pattern already established by
app.customers.services.list_customers / app.leads.services.list_own_leads
(list_all_leads): real, opt-in ?page/?status/?q/?sort/?dir query params,
paginate() from app.services.pagination, apply_ownership_filter reused
verbatim (never re-implemented) for the three ownership-scoped entities.

Every one of these seven routes previously had NO pagination at all -- just
an unconditional `.limit(200)` (commercial_sales/routes.py, Milestone 19) --
the exact same disclosed performance risk enterprise-table-system.md found
and fixed for Customers ("Unbounded query, no LIMIT/OFFSET... will not
scale"). This module fixes it the same additive way: with no query params,
each function reproduces the prior `order_by(created_at.desc())` behavior,
now with real, always-on pagination (page_size=25, matching
customers/leads/employees) instead of a bare 200-row cap.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Select, false, select

from app.leads.ownership import apply_ownership_filter
from app.models.commercial_sales import CommercialInvoice, CommercialRefund, Quote, SalesOrder
from app.models.commissions import CommissionLedgerEntry, CommissionPayoutBatch
from app.models.subscriptions import PaymentRecord
from app.services.pagination import DEFAULT_PAGE_SIZE, paginate

QUOTE_SORT_COLUMNS = {
    "quote_number": Quote.quote_number,
    "status": Quote.status,
    "total": Quote.total,
    "created_at": Quote.created_at,
}

ORDER_SORT_COLUMNS = {
    "order_number": SalesOrder.order_number,
    "status": SalesOrder.status,
    "total": SalesOrder.total,
    "created_at": SalesOrder.created_at,
}

INVOICE_SORT_COLUMNS = {
    "invoice_number": CommercialInvoice.invoice_number,
    "status": CommercialInvoice.status,
    "total": CommercialInvoice.total,
    "due_date": CommercialInvoice.due_date,
    "created_at": CommercialInvoice.created_at,
}

PAYMENT_SORT_COLUMNS = {
    "amount": PaymentRecord.amount,
    "status": PaymentRecord.status,
    "payment_date": PaymentRecord.payment_date,
    "created_at": PaymentRecord.created_at,
}

REFUND_SORT_COLUMNS = {
    "amount": CommercialRefund.amount,
    "status": CommercialRefund.status,
    "created_at": CommercialRefund.created_at,
}

COMMISSION_SORT_COLUMNS = {
    "commission_amount": CommissionLedgerEntry.commission_amount,
    "status": CommissionLedgerEntry.status,
    "earned_at": CommissionLedgerEntry.earned_at,
    "created_at": CommissionLedgerEntry.created_at,
}

PAYOUT_BATCH_SORT_COLUMNS = {
    "batch_reference": CommissionPayoutBatch.batch_reference,
    "status": CommissionPayoutBatch.status,
    "period_start": CommissionPayoutBatch.period_start,
    "created_at": CommissionPayoutBatch.created_at,
}


def _ordered(stmt: Select, columns: dict, sort: str, direction: str, default_column, id_column) -> Select:
    column = columns.get(sort, default_column)
    order = column.asc() if direction == "asc" else column.desc()
    # Stable secondary sort on the real primary key -- two rows sharing the
    # exact same sorted value (e.g. two DRAFT quotes created in the same
    # second) would otherwise have an unstable/order-dependent relative
    # position across pages, the same real-world pagination-instability
    # risk enterprise-table-system.md's Customers/Leads pass already
    # guarded against (Customer.id / see customers/services.py).
    return stmt.order_by(order, id_column)


def list_quotes(
    *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, status: str | None = None, search: str | None = None,
    sort: str = "created_at", direction: str = "desc",
    actor_employee_profile_id: uuid.UUID | None, all_permission_held: bool,
) -> dict:
    stmt = select(Quote)
    stmt = apply_ownership_filter(stmt, Quote, actor_employee_profile_id, all_permission_held=all_permission_held)
    if status:
        stmt = stmt.where(Quote.status == status)
    if search:
        stmt = stmt.where(Quote.quote_number.ilike(f"%{search.strip()}%"))
    stmt = _ordered(stmt, QUOTE_SORT_COLUMNS, sort, direction, Quote.created_at, Quote.id)
    return paginate(stmt, page, page_size)


def list_orders(
    *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, status: str | None = None, search: str | None = None,
    sort: str = "created_at", direction: str = "desc",
    actor_employee_profile_id: uuid.UUID | None, all_permission_held: bool,
) -> dict:
    stmt = select(SalesOrder)
    stmt = apply_ownership_filter(stmt, SalesOrder, actor_employee_profile_id, all_permission_held=all_permission_held)
    if status:
        stmt = stmt.where(SalesOrder.status == status)
    if search:
        stmt = stmt.where(SalesOrder.order_number.ilike(f"%{search.strip()}%"))
    stmt = _ordered(stmt, ORDER_SORT_COLUMNS, sort, direction, SalesOrder.created_at, SalesOrder.id)
    return paginate(stmt, page, page_size)


def list_invoices(
    *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, status: str | None = None, search: str | None = None,
    sort: str = "created_at", direction: str = "desc",
    actor_employee_profile_id: uuid.UUID | None, all_permission_held: bool,
) -> dict:
    stmt = select(CommercialInvoice)
    stmt = apply_ownership_filter(stmt, CommercialInvoice, actor_employee_profile_id, all_permission_held=all_permission_held)
    if status:
        stmt = stmt.where(CommercialInvoice.status == status)
    if search:
        stmt = stmt.where(CommercialInvoice.invoice_number.ilike(f"%{search.strip()}%"))
    stmt = _ordered(stmt, INVOICE_SORT_COLUMNS, sort, direction, CommercialInvoice.created_at, CommercialInvoice.id)
    return paginate(stmt, page, page_size)


def list_payments(
    *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, status: str | None = None, search: str | None = None,
    sort: str = "created_at", direction: str = "desc",
) -> dict:
    """No ownership filter -- verified against commercial_sales/routes.py::
    list_payments, company-wide once payments.view is held (unchanged)."""
    stmt = select(PaymentRecord)
    if status:
        stmt = stmt.where(PaymentRecord.status == status)
    if search:
        stmt = stmt.where(PaymentRecord.reference.ilike(f"%{search.strip()}%"))
    stmt = _ordered(stmt, PAYMENT_SORT_COLUMNS, sort, direction, PaymentRecord.created_at, PaymentRecord.id)
    return paginate(stmt, page, page_size)


def list_refunds(
    *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, status: str | None = None, search: str | None = None,
    sort: str = "created_at", direction: str = "desc",
) -> dict:
    """No ownership filter -- verified against commercial_sales/routes.py::
    list_refunds, company-wide once refunds.create/refunds.approve is held
    (unchanged). CommercialRefund has no dedicated document-number column
    (unlike Quote/Order/Invoice) -- `reason` (a real, required Text column)
    is the only free-text field to search."""
    stmt = select(CommercialRefund)
    if status:
        stmt = stmt.where(CommercialRefund.status == status)
    if search:
        stmt = stmt.where(CommercialRefund.reason.ilike(f"%{search.strip()}%"))
    stmt = _ordered(stmt, REFUND_SORT_COLUMNS, sort, direction, CommercialRefund.created_at, CommercialRefund.id)
    return paginate(stmt, page, page_size)


def list_commissions(
    *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, status: str | None = None,
    sort: str = "created_at", direction: str = "desc",
    employee_profile_id: uuid.UUID | None, view_all: bool,
) -> dict:
    """Ownership shape here is genuinely different from Quote/Order/Invoice
    (permission-based view_own/view_all split on CommissionLedgerEntry.
    employee_profile_id, not a creator/assignee column) -- verified against
    commercial_sales/routes.py::list_commissions; apply_ownership_filter has
    no rule for CommissionLedgerEntry (by design, per its own docstring) so
    this reproduces the route's existing filter exactly, not a new
    mechanism. No real free-text field exists on this entity to search."""
    stmt = select(CommissionLedgerEntry)
    if not view_all:
        if employee_profile_id is None:
            return paginate(select(CommissionLedgerEntry).where(false()), page, page_size)
        stmt = stmt.where(CommissionLedgerEntry.employee_profile_id == employee_profile_id)
    if status:
        stmt = stmt.where(CommissionLedgerEntry.status == status)
    stmt = _ordered(stmt, COMMISSION_SORT_COLUMNS, sort, direction, CommissionLedgerEntry.created_at, CommissionLedgerEntry.id)
    return paginate(stmt, page, page_size)


def list_payout_batches(
    *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, search: str | None = None,
    sort: str = "created_at", direction: str = "desc",
) -> dict:
    stmt = select(CommissionPayoutBatch)
    if search:
        stmt = stmt.where(CommissionPayoutBatch.batch_reference.ilike(f"%{search.strip()}%"))
    stmt = _ordered(stmt, PAYOUT_BATCH_SORT_COLUMNS, sort, direction, CommissionPayoutBatch.created_at, CommissionPayoutBatch.id)
    return paginate(stmt, page, page_size)
