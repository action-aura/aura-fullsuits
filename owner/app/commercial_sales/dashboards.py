"""Phase 9.5D Milestone 17 -- commercial-sales dashboards.

Two scopes only, matching this phase's own RBAC/SoD model (Milestone 16):
employee (own pipeline + own commission earnings, commissions.view_own)
and finance (aggregate pipeline + actionable approval queues +
commission/payout summary, commissions.view_all). There is no separate
"management" scope here the way CRM (Phase 9.5C) has one -- commercial
document/commission data is financially sensitive, so the aggregate view
belongs to FINANCE (the role that actually holds commissions.view_all/
refunds.approve/invoices.issue), not a generic management/VIEWER role.
Every metric is a direct, ownership-scoped or explicitly-authorized COUNT/
SUM query -- no revenue/commission aggregation beyond what the caller's
own scope legitimately covers (Non-Negotiable: employees see their own
earnings, never a peer's).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.extensions import db_session
from app.leads.ownership import apply_ownership_filter
from app.metrics_contracts import EmployeeCommercialDashboard, FinanceCommercialDashboard
from app.models.commercial_sales import (
    CommercialApproval,
    CommercialInvoice,
    CommercialRefund,
    Quote,
    SalesOrder,
)
from app.models.commissions import (
    COMMISSION_AWAITING_APPROVAL_STATUS,
    CommissionLedgerEntry,
    CommissionPayoutBatch,
)
from app.operational_reports.aggregation import outstanding_receivables_total

# The currency the employee dashboard's scalar commission figures are scoped
# to when the caller names none -- the same default every other dashboard
# route in this codebase already uses (request.args.get("currency", "USD"),
# see operations_ui/routes.py and api_operations/expenses_and_operations.py).
DEFAULT_DASHBOARD_CURRENCY = "USD"

_COMMISSION_STAGE_BY_STATUS = {
    "EARNED": "earned_unapproved",
    "APPROVED": "approved_unpaid",
    "PAID": "paid_total",
    "REVERSED": "reversed_total",
}
_ZERO_COMMISSION_STAGES = {stage: Decimal("0.00") for stage in _COMMISSION_STAGE_BY_STATUS.values()}


def employee_commercial_dashboard(
    actor_employee_profile_id: uuid.UUID,
    actor_staff_user_id: uuid.UUID,
    currency: str = DEFAULT_DASHBOARD_CURRENCY,
) -> dict:
    quotes_by_status = dict(db_session.execute(
        apply_ownership_filter(select(Quote.status, func.count(Quote.id)), Quote, actor_employee_profile_id, all_permission_held=False)
        .group_by(Quote.status)
    ).all())
    orders_by_status = dict(db_session.execute(
        apply_ownership_filter(select(SalesOrder.status, func.count(SalesOrder.id)), SalesOrder, actor_employee_profile_id, all_permission_held=False)
        .group_by(SalesOrder.status)
    ).all())
    invoices_by_status = dict(db_session.execute(
        apply_ownership_filter(select(CommercialInvoice.status, func.count(CommercialInvoice.id)), CommercialInvoice, actor_employee_profile_id, all_permission_held=False)
        .group_by(CommercialInvoice.status)
    ).all())

    # AUDIT-owner-cross-screen: own_commission_sum() previously ran one SUM
    # per lifecycle status with NO currency filter at all, so an employee
    # holding a USD earning and a JOD earning saw the two added together --
    # a number in no currency, on the one screen ("My commission earnings")
    # the employee is most likely to check against their own payslip, while
    # every finance screen beside it is strictly single-currency
    # (aggregation.py's module docstring: "currency is never blended").
    #
    # One GROUP BY (currency, status) now replaces the four scalar sums.
    # The per-currency breakdown is the real answer and is returned in
    # full; the four scalars the HTML screen still binds to are DERIVED
    # from that breakdown for the requested currency rather than re-queried,
    # so the detailed view and the headline figures are the same numbers by
    # construction and cannot drift.
    commission_totals_by_currency: dict[str, dict[str, Decimal]] = {}
    for entry_currency, status, total in db_session.execute(
        select(
            CommissionLedgerEntry.currency,
            CommissionLedgerEntry.status,
            func.coalesce(func.sum(CommissionLedgerEntry.commission_amount), Decimal("0.00")),
        )
        .where(CommissionLedgerEntry.employee_profile_id == actor_employee_profile_id)
        .group_by(CommissionLedgerEntry.currency, CommissionLedgerEntry.status)
    ).all():
        stage = _COMMISSION_STAGE_BY_STATUS.get(status)
        if stage is None:
            # CANCELLED/DISPUTED (and the unassignable PENDING default) are
            # not earnings stages this screen reports -- skipped rather than
            # silently folded into one of the four it does report.
            continue
        commission_totals_by_currency.setdefault(entry_currency, dict(_ZERO_COMMISSION_STAGES))[stage] = total

    # Sorted so the screen's per-currency table and its JSON twin list
    # currencies in the same, stable order on every request -- a GROUP BY's
    # row order is not ordered (same reasoning as
    # finance_commercial_dashboard()'s sorted(outstanding_currencies)).
    commission_totals_by_currency = dict(sorted(commission_totals_by_currency.items()))

    scoped = commission_totals_by_currency.get(currency, dict(_ZERO_COMMISSION_STAGES))

    return EmployeeCommercialDashboard(
        currency=currency,
        quotes_by_status=quotes_by_status,
        orders_by_status=orders_by_status,
        invoices_by_status=invoices_by_status,
        # Approvals this employee is waiting on someone else to decide --
        # informational only; SALES never holds an approve permission
        # (Milestone 16), so this is never an actionable queue for them.
        own_pending_approval_requests=db_session.execute(
            select(func.count(CommercialApproval.id)).where(
                CommercialApproval.status == "PENDING", CommercialApproval.requested_by_staff_user_id == actor_staff_user_id
            )
        ).scalar_one(),
        # Own commission earnings, split by lifecycle stage, scoped to one
        # currency -- never a peer's (Non-Negotiable: beneficiary-scoped by
        # design, commissions.view_own).
        commission_earned_unapproved=scoped["earned_unapproved"],
        commission_approved_unpaid=scoped["approved_unpaid"],
        commission_paid_total=scoped["paid_total"],
        commission_reversed_total=scoped["reversed_total"],
        commission_totals_by_currency=commission_totals_by_currency,
    ).to_payload()


def finance_commercial_dashboard() -> dict:
    """Aggregate, cross-employee view -- authorized for FINANCE/SUPER_ADMIN
    only (the roles holding commissions.view_all/refunds.approve/
    invoices.issue per Milestone 16's SoD matrix). Emphasizes actionable
    queues (what needs a FINANCE decision right now) over vanity totals,
    matching the CRM management dashboard's "reassignment_required"
    precedent."""
    quotes_by_status = dict(db_session.execute(select(Quote.status, func.count(Quote.id)).group_by(Quote.status)).all())
    orders_by_status = dict(db_session.execute(select(SalesOrder.status, func.count(SalesOrder.id)).group_by(SalesOrder.status)).all())
    invoices_by_status = dict(db_session.execute(select(CommercialInvoice.status, func.count(CommercialInvoice.id)).group_by(CommercialInvoice.status)).all())

    pending_approvals = db_session.execute(
        select(func.count(CommercialApproval.id)).where(CommercialApproval.status == "PENDING")
    ).scalar_one()
    refunds_pending_approval = db_session.execute(
        select(func.count(CommercialRefund.id)).where(CommercialRefund.status == "DRAFT")
    ).scalar_one()
    # Reads the shared constant (models/commissions.py) rather than repeating
    # the literal, so this figure, the Attention Center's badge count and the
    # deep link that badge points at can never name three different statuses.
    commissions_pending_approval = db_session.execute(
        select(func.count(CommissionLedgerEntry.id)).where(
            CommissionLedgerEntry.status == COMMISSION_AWAITING_APPROVAL_STATUS
        )
    ).scalar_one()
    commissions_approved_unpaid = db_session.execute(
        select(func.count(CommissionLedgerEntry.id)).where(CommissionLedgerEntry.status == "APPROVED")
    ).scalar_one()
    payout_batches_pending_approval = db_session.execute(
        select(func.count(CommissionPayoutBatch.id)).where(CommissionPayoutBatch.status == "DRAFT")
    ).scalar_one()

    # AUDIT: previously summed CommercialInvoice.total (the invoice's GROSS
    # face value) for status in (ISSUED, PARTIALLY_PAID), never subtracting
    # confirmed payment allocations already received against it, and with
    # no currency filter at all -- silently blending every currency present
    # into one meaningless figure. Reuses the existing, already-correct
    # helper (operational_reports/aggregation.py::outstanding_receivables_total)
    # instead of a second, divergent implementation: it nets out
    # confirmed_allocated_amount(invoice) and is explicitly single-currency
    # per its own docstring ("currency is never blended"). Returned
    # per-currency (never summed across currencies) -- every currency that
    # appears on at least one non-DRAFT/VOID invoice gets its own entry,
    # including a real 0.00 when nothing of that currency is outstanding.
    outstanding_currencies = db_session.execute(
        select(CommercialInvoice.currency).where(CommercialInvoice.status.notin_(("DRAFT", "VOID"))).distinct()
    ).scalars().all()
    # sorted() so the screen and the JSON twin list currencies in the same,
    # stable order on every request (a DISTINCT's row order is not ordered).
    outstanding_invoice_totals = {
        currency: outstanding_receivables_total(currency) for currency in sorted(outstanding_currencies)
    }

    return FinanceCommercialDashboard(
        quotes_by_status=quotes_by_status,
        orders_by_status=orders_by_status,
        invoices_by_status=invoices_by_status,
        quote_approvals_pending=pending_approvals,
        refunds_pending_approval=refunds_pending_approval,
        commissions_pending_approval=commissions_pending_approval,
        commissions_approved_unpaid=commissions_approved_unpaid,
        payout_batches_pending_approval=payout_batches_pending_approval,
        # Shape change from a single blended scalar ("outstanding_invoice_total")
        # to a per-currency dict -- see this block's own comment above for why.
        # Both consumers now render per currency (finance_dashboard.html and
        # api_operations/commercial_sales.py); the field set is pinned by
        # metrics_contracts.FinanceCommercialDashboard so the next rename
        # cannot leave one of them silently blank the way this one did.
        outstanding_invoice_totals=outstanding_invoice_totals,
    ).to_payload()
