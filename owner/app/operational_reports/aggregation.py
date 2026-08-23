"""Phase 9.5E -- operational-finance report aggregation. Pure read-only
queries over existing Commercial Sales / Commissions / Expenses tables --
never a second source of truth, never persisted here (persistence is
ReportSnapshot's job, in scheduler.py). Currency is never blended -- every
function takes one currency and returns figures for that currency alone, per
docs/owner/phase9_5e/operational-finance-funnel-contract.md."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import cast, Date, func, select

from app.extensions import db_session
from app.models.commercial_sales import CommercialInvoice, CommercialRefund, PaymentAllocation
from app.models.commissions import CommissionPayoutLine
from app.models.expenses import Expense, ExpensePayment
from app.models.subscriptions import PaymentRecord


def _date_only(column):
    return cast(column, Date)


def confirmed_payments_total(period_start: date, period_end: date, currency: str) -> Decimal:
    rows = db_session.execute(
        select(PaymentRecord.amount).where(
            PaymentRecord.status == "CONFIRMED", PaymentRecord.currency == currency,
            PaymentRecord.payment_date >= period_start, PaymentRecord.payment_date <= period_end,
        )
    ).scalars().all()
    return sum((Decimal(str(v)) for v in rows), Decimal("0"))


def confirmed_refunds_total(period_start: date, period_end: date, currency: str) -> Decimal:
    rows = db_session.execute(
        select(CommercialRefund.amount).where(
            CommercialRefund.status == "PAID", CommercialRefund.currency == currency,
        ).where(_date_only(CommercialRefund.paid_at) >= period_start, _date_only(CommercialRefund.paid_at) <= period_end)
    ).scalars().all()
    return sum(rows, Decimal("0"))


def paid_expenses_total(period_start: date, period_end: date, currency: str) -> Decimal:
    rows = db_session.execute(
        select(ExpensePayment.amount).where(
            ExpensePayment.status == "RECORDED", ExpensePayment.currency == currency,
        ).where(_date_only(ExpensePayment.paid_at) >= period_start, _date_only(ExpensePayment.paid_at) <= period_end)
    ).scalars().all()
    return sum(rows, Decimal("0"))


def recorded_commission_payouts_total(period_start: date, period_end: date, currency: str) -> Decimal:
    rows = db_session.execute(
        select(CommissionPayoutLine.amount).where(CommissionPayoutLine.currency == currency).where(
            _date_only(CommissionPayoutLine.created_at) >= period_start, _date_only(CommissionPayoutLine.created_at) <= period_end,
        )
    ).scalars().all()
    return sum(rows, Decimal("0"))


def outstanding_receivables_total(currency: str) -> Decimal:
    """Point-in-time snapshot (not period-bound) -- sum of (invoice.total -
    confirmed_allocated_amount) over every non-VOID, not-fully-paid invoice.

    AUDIT-perf: this was the third N+1 of the same shape as
    app/attention/service.py::_overdue_invoice_items() and
    app/operational_reports/dashboards.py::_overdue_invoice_count() -- one
    confirmed_allocated_amount() query per invoice in the currency, no
    LIMIT, discovered while measuring those two fixes and left for this
    pass. Batched the same way: one query for the candidate invoices
    (currency, non-VOID/DRAFT status) and one GROUP BY
    SUM(PaymentAllocation.allocated_amount) instead of one
    confirmed_allocated_amount() query per candidate.
    confirmed_allocated_amount() (app/commercial_sales/invoices.py) stays
    the canonical per-invoice definition -- its own "non-reversed
    PaymentAllocation rows" condition is reproduced below as a SQL WHERE
    clause. If that condition ever changes, update this to match (see
    tests/test_operational_finance_aggregation_query_counts.py's agreement
    test, which fails loudly if the two drift).

    Deliberately NO LIMIT, matching _overdue_invoice_count(): this is a
    total for a finance dashboard, not a capped list -- undercounting a
    long-history customer's receivables would be a silent correctness
    regression, not an acceptable trade-off."""
    invoices = db_session.execute(
        select(CommercialInvoice.id, CommercialInvoice.total).where(
            CommercialInvoice.currency == currency,
            CommercialInvoice.status.notin_(("DRAFT", "VOID")),
        )
    ).all()
    if not invoices:
        return Decimal("0")

    invoice_ids = [invoice_id for invoice_id, _total in invoices]
    allocated_by_invoice = dict(
        db_session.execute(
            select(PaymentAllocation.commercial_invoice_id, func.sum(PaymentAllocation.allocated_amount))
            .where(PaymentAllocation.commercial_invoice_id.in_(invoice_ids), PaymentAllocation.reversed_at.is_(None))
            .group_by(PaymentAllocation.commercial_invoice_id)
        ).all()
    )

    total = Decimal("0")
    for invoice_id, invoice_total in invoices:
        outstanding = invoice_total - allocated_by_invoice.get(invoice_id, Decimal("0.00"))
        if outstanding > 0:
            total += outstanding
    return total


def approved_unpaid_expenses_total(currency: str) -> Decimal:
    from app.expenses.payments import outstanding_amount

    expenses = db_session.execute(
        select(Expense).where(Expense.currency == currency, Expense.status.in_(("APPROVED", "PARTIALLY_PAID")))
    ).scalars().all()
    return sum((outstanding_amount(e) for e in expenses), Decimal("0"))


def net_operational_cash_movement(period_start: date, period_end: date, currency: str) -> Decimal:
    """confirmed_payments - confirmed_refunds - paid_expenses -
    recorded_commission_payouts. Never subtracts approved-but-unpaid
    Expenses, never includes unconfirmed Payments, never labeled 'profit'
    anywhere this value is surfaced."""
    return (
        confirmed_payments_total(period_start, period_end, currency)
        - confirmed_refunds_total(period_start, period_end, currency)
        - paid_expenses_total(period_start, period_end, currency)
        - recorded_commission_payouts_total(period_start, period_end, currency)
    )


def build_operational_summary(period_start: date, period_end: date, currency: str) -> dict:
    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "currency": currency,
        "confirmed_payments": str(confirmed_payments_total(period_start, period_end, currency)),
        "confirmed_refunds": str(confirmed_refunds_total(period_start, period_end, currency)),
        "paid_expenses": str(paid_expenses_total(period_start, period_end, currency)),
        "recorded_commission_payouts": str(recorded_commission_payouts_total(period_start, period_end, currency)),
        "net_operational_cash_movement": str(net_operational_cash_movement(period_start, period_end, currency)),
        "outstanding_receivables": str(outstanding_receivables_total(currency)),
        "approved_unpaid_expenses": str(approved_unpaid_expenses_total(currency)),
    }


def build_cash_closing_exceptions(business_date: date) -> dict:
    from app.models.cash_closing import CashClosing

    closings = db_session.execute(
        select(CashClosing).where(CashClosing.business_date == business_date)
    ).scalars().all()
    nonzero_variance = [c for c in closings if c.variance not in (None, Decimal("0"), Decimal("0.00"))]
    reopened = [c for c in closings if c.status == "REOPENED"]
    review_required = [c for c in closings if c.status == "REVIEW_REQUIRED"]
    return {
        "business_date": business_date.isoformat(),
        "closings_present_currencies": sorted(c.currency for c in closings),
        "nonzero_variance_closing_ids": [str(c.id) for c in nonzero_variance],
        "reopened_closing_ids": [str(c.id) for c in reopened],
        "review_required_closing_ids": [str(c.id) for c in review_required],
    }
