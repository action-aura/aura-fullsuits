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
from app.models.commercial_sales import (
    CommercialApproval,
    CommercialInvoice,
    CommercialRefund,
    Quote,
    SalesOrder,
)
from app.models.commissions import CommissionLedgerEntry, CommissionPayoutBatch


def employee_commercial_dashboard(actor_employee_profile_id: uuid.UUID, actor_staff_user_id: uuid.UUID) -> dict:
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

    def own_commission_sum(*extra_where):
        stmt = select(func.coalesce(func.sum(CommissionLedgerEntry.commission_amount), Decimal("0.00"))).where(
            CommissionLedgerEntry.employee_profile_id == actor_employee_profile_id
        )
        for clause in extra_where:
            stmt = stmt.where(clause)
        return db_session.execute(stmt).scalar_one()

    return {
        "quotes_by_status": quotes_by_status,
        "orders_by_status": orders_by_status,
        "invoices_by_status": invoices_by_status,
        # Approvals this employee is waiting on someone else to decide --
        # informational only; SALES never holds an approve permission
        # (Milestone 16), so this is never an actionable queue for them.
        "own_pending_approval_requests": db_session.execute(
            select(func.count(CommercialApproval.id)).where(
                CommercialApproval.status == "PENDING", CommercialApproval.requested_by_staff_user_id == actor_staff_user_id
            )
        ).scalar_one(),
        # Own commission earnings, split by lifecycle stage -- never a
        # peer's (Non-Negotiable: beneficiary-scoped by design, commissions.view_own).
        "commission_earned_unapproved": own_commission_sum(CommissionLedgerEntry.status == "EARNED"),
        "commission_approved_unpaid": own_commission_sum(CommissionLedgerEntry.status == "APPROVED"),
        "commission_paid_total": own_commission_sum(CommissionLedgerEntry.status == "PAID"),
        "commission_reversed_total": own_commission_sum(CommissionLedgerEntry.status == "REVERSED"),
    }


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
    commissions_pending_approval = db_session.execute(
        select(func.count(CommissionLedgerEntry.id)).where(CommissionLedgerEntry.status == "EARNED")
    ).scalar_one()
    commissions_approved_unpaid = db_session.execute(
        select(func.count(CommissionLedgerEntry.id)).where(CommissionLedgerEntry.status == "APPROVED")
    ).scalar_one()
    payout_batches_pending_approval = db_session.execute(
        select(func.count(CommissionPayoutBatch.id)).where(CommissionPayoutBatch.status == "DRAFT")
    ).scalar_one()

    outstanding_invoice_total = db_session.execute(
        select(func.coalesce(func.sum(CommercialInvoice.total), Decimal("0.00"))).where(
            CommercialInvoice.status.in_(("ISSUED", "PARTIALLY_PAID"))
        )
    ).scalar_one()

    return {
        "quotes_by_status": quotes_by_status,
        "orders_by_status": orders_by_status,
        "invoices_by_status": invoices_by_status,
        "quote_approvals_pending": pending_approvals,
        "refunds_pending_approval": refunds_pending_approval,
        "commissions_pending_approval": commissions_pending_approval,
        "commissions_approved_unpaid": commissions_approved_unpaid,
        "payout_batches_pending_approval": payout_batches_pending_approval,
        "outstanding_invoice_total": outstanding_invoice_total,
    }
