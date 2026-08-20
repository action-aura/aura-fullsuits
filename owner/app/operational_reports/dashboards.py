"""Phase 9.5E Milestone 12 -- Employee/Management/Finance operational-finance
dashboards. Pure read-only aggregation over existing domain tables, reusing
the exact same service functions (app/operational_reports/aggregation.py,
app/expenses/payments.py, app/commercial_sales/allocation.py) every other
milestone already built and proved -- no financial calculation is
duplicated here, only COUNT/SUM/GROUP BY queries and calls to those
functions. Every count is scope-filtered by the caller's own ownership/
permission (never a global count leaked to an unauthorized caller) --
matches the exact reasoning app/commercial_sales/dashboards.py already
documents for its own two scopes."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.commercial_sales.allocation import unallocated_payment_balance
from app.commercial_sales.invoices import confirmed_allocated_amount
from app.expenses.duplicates import NON_ACTIVE_FOR_DUPLICATE_CHECK
from app.expenses.payments import outstanding_amount
from app.extensions import db_session
from app.metrics_contracts import FinanceOperationalDashboard, ManagementOperationalDashboard
from app.models.cash_closing import CashClosing
from app.models.commercial_sales import CommercialInvoice, CommercialRefund
from app.models.commissions import CommissionLedgerEntry
from app.models.expenses import Expense, ExpenseApproval, ExpenseCategory
from app.models.management_notes import SharedManagementNote
from app.models.subscriptions import PaymentRecord, Subscription
from app.operational_reports.aggregation import (
    confirmed_payments_total,
    confirmed_refunds_total,
    net_operational_cash_movement,
    outstanding_receivables_total,
    paid_expenses_total,
    recorded_commission_payouts_total,
)


# AUDIT: single shared definition of "overdue invoice", reused by both the
# Attention Center (app/attention/service.py, own-scoped) and this module's
# company-wide Management dashboard. Before this, the two screens used two
# independently-drifted definitions: the Attention Center only checked
# status.in_(("ISSUED", "PARTIALLY_PAID")) and never checked the remaining
# balance, so it silently missed PARTIALLY_REFUNDED invoices (a real,
# reachable INVOICE_STATUSES value, see app/models/commercial_sales.py)
# that this module already counted correctly. Extracted here (this module
# already owned the correct definition) so both screens read the same
# predicate and cannot drift apart again -- not DRAFT/VOID/PAID/REFUNDED,
# due date strictly in the past, and a strictly positive remaining balance
# (invoice.total minus confirmed_allocated_amount).
OVERDUE_INVOICE_EXCLUDED_STATUSES = ("DRAFT", "VOID", "PAID", "REFUNDED")


# AUDIT-owner-cross-screen: which Expense statuses belong in a Management
# "expense totals" figure. The filter here used to be status.notin_(("VOID",))
# -- i.e. everything except VOID -- so a DRAFT nobody has even submitted yet,
# a SUBMITTED request nobody has approved, a RETURNED one sent back for
# correction, and an outright REJECTED one all inflated the company's
# category and per-employee totals.
#
# The correct set is authorized spend and nothing else: APPROVED (a real
# approval decision was recorded), PARTIALLY_PAID and PAID (that same
# approved expense, further along the payment lifecycle). DRAFT/SUBMITTED/
# RETURNED are requests, not spend -- an employee's typo in a DRAFT must not
# move a management total. REJECTED/VOID never become spend at all.
#
# What this does NOT buy, stated plainly because an earlier draft of this
# comment claimed it did: it does not make "Paid Expenses" on the same
# screen a subset of these totals, and the two are not expected to
# reconcile arithmetically. Three real reasons, all still true:
#   * different window -- these totals are all-time, while paid_expenses is
#     month-to-date (aggregation.py::paid_expenses_total takes a period);
#   * different subject -- paid_expenses sums RECORDED ExpensePayment rows
#     and never joins Expense at all, so a payment against an expense that
#     was VOIDed afterwards still counts there and no longer counts here;
#   * different amount -- these totals sum the REQUESTED Expense.amount,
#     while a payment is made against the approved amount.
# Additionally expense_totals_by_category INNER JOINs ExpenseCategory, so an
# expense with no category is absent from that breakdown (though present in
# expense_totals_by_employee) -- the two breakdowns need not sum equal
# either. The value of this status set is narrower and real: a figure
# labelled "expense totals" now counts only spend somebody actually
# authorized. Reconciling it against cash out is a reporting question
# nobody has specified, and this constant does not answer it.
#
# Deliberately NOT changed in this pass: the sum is still over the requested
# Expense.amount rather than coalesce(approved_amount, amount). Where an
# approver cut the amount, that difference is a separate, real question
# (it changes what "expense total" means, not merely which rows count) and
# is left to whoever owns that decision.
MANAGEMENT_EXPENSE_TOTAL_STATUSES = ("APPROVED", "PARTIALLY_PAID", "PAID")

# Imported, never restated: the duplicate-review queue below must call an
# expense "active" by exactly the rule the per-expense duplicate check uses.
DUPLICATE_REVIEW_EXCLUDED_STATUSES = NON_ACTIVE_FOR_DUPLICATE_CHECK


def is_invoice_overdue(invoice: CommercialInvoice, *, as_of: date | None = None) -> bool:
    as_of = as_of or date.today()
    if invoice.status in OVERDUE_INVOICE_EXCLUDED_STATUSES:
        return False
    if invoice.due_date is None or invoice.due_date >= as_of:
        return False
    return (invoice.total - confirmed_allocated_amount(invoice)) > 0


def _pending_expense_approval_count(currency: str) -> int:
    """One definition of "expense approvals pending, in this currency",
    read by both the Management and the Finance dashboard -- the two
    screens showed the same label with two different (both un-scoped)
    counts before, and a single helper is what stops them drifting apart
    again."""
    return db_session.execute(
        select(func.count(ExpenseApproval.id))
        .join(Expense, Expense.id == ExpenseApproval.expense_id)
        .where(ExpenseApproval.status == "PENDING", Expense.currency == currency)
    ).scalar_one()


def employee_expense_dashboard(employee_profile_id: uuid.UUID) -> dict:
    """Own records only -- filtered by entered_by_employee_profile_id at the
    query itself, never fetched broader and filtered after."""
    by_status = dict(db_session.execute(
        select(Expense.status, func.count(Expense.id))
        .where(Expense.entered_by_employee_profile_id == employee_profile_id)
        .group_by(Expense.status)
    ).all())

    return {
        "draft": by_status.get("DRAFT", 0),
        "submitted": by_status.get("SUBMITTED", 0),
        "pending_approval": by_status.get("SUBMITTED", 0),
        "returned_for_correction": by_status.get("RETURNED", 0),
        "approved_unpaid": by_status.get("APPROVED", 0),
        "partially_paid": by_status.get("PARTIALLY_PAID", 0),
        "paid": by_status.get("PAID", 0),
        "rejected": by_status.get("REJECTED", 0),
        "required_actions": by_status.get("RETURNED", 0),
    }


def management_operational_dashboard(currency: str) -> dict:
    """Management scope -- currency-separated (caller supplies exactly one
    currency; never blended). Every figure is either a scoped COUNT/GROUP BY
    or a direct call into the already-proven aggregation/service functions."""
    today = date.today()
    month_start = today.replace(day=1)

    expense_totals_by_category = dict(db_session.execute(
        select(ExpenseCategory.name, func.coalesce(func.sum(Expense.amount), Decimal("0.00")))
        .join(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
        .where(Expense.currency == currency, Expense.status.in_(MANAGEMENT_EXPENSE_TOTAL_STATUSES))
        .group_by(ExpenseCategory.name)
    ).all())

    expense_totals_by_employee = dict(db_session.execute(
        select(Expense.entered_by_employee_profile_id, func.coalesce(func.sum(Expense.amount), Decimal("0.00")))
        .where(Expense.currency == currency, Expense.status.in_(MANAGEMENT_EXPENSE_TOTAL_STATUSES))
        .group_by(Expense.entered_by_employee_profile_id)
    ).all())

    # AUDIT-owner-cross-screen: currency-scoped like every other figure on
    # this screen. ExpenseApproval carries no currency of its own, so the
    # scope comes from the Expense it decides -- without this join a
    # Management viewer looking at the USD dashboard was shown a queue count
    # that also included JOD requests they could not see anywhere else on
    # the page.
    approval_queue_count = _pending_expense_approval_count(currency)

    # Duplicate-review queue: active expenses sharing an external_reference
    # with at least one other active expense -- the same signal
    # app.expenses.duplicates.find_duplicate_signals() checks per-expense,
    # aggregated here as a queue count rather than re-run per row.
    #
    # AUDIT-owner-cross-screen: currency-scoped like its neighbours, but in
    # TWO steps, because this figure is a GROUP BY and not a flat COUNT.
    # Scoping a flat COUNT to one currency only ever removes rows. Putting
    # `currency == :currency` INSIDE this GROUP BY changes the grouping
    # itself: a USD expense and a JOD expense sharing one external reference
    # split into two groups of one, neither cleared HAVING count > 1, and a
    # real duplicate pair DISAPPEARED from every currency's screen rather
    # than showing up on one. On a fraud/error control, a silent drop is the
    # worst available failure mode.
    #
    # So detection stays currency-agnostic (step 1 -- the same rule
    # find_duplicate_signals() applies: same external_reference, both rows
    # still active), and only ATTRIBUTION to a screen is scoped (step 2 --
    # this currency has at least one active row under that reference). A
    # cross-currency pair is therefore counted on BOTH currencies' screens:
    # over-reporting a duplicate to a second reviewer is recoverable,
    # showing it to nobody is not.
    duplicate_reference_groups = (
        select(Expense.external_reference)
        .where(
            Expense.external_reference.is_not(None),
            Expense.status.notin_(DUPLICATE_REVIEW_EXCLUDED_STATUSES),
        )
        .group_by(Expense.external_reference)
        .having(func.count(Expense.id) > 1)
    )
    duplicate_review_queue_count = db_session.execute(
        select(func.count(func.distinct(Expense.external_reference))).where(
            Expense.currency == currency,
            Expense.status.notin_(DUPLICATE_REVIEW_EXCLUDED_STATUSES),
            Expense.external_reference.in_(duplicate_reference_groups),
        )
    ).scalar_one()

    approved_unpaid_count = db_session.execute(
        select(func.count(Expense.id)).where(Expense.currency == currency, Expense.status == "APPROVED")
    ).scalar_one()
    partially_paid_count = db_session.execute(
        select(func.count(Expense.id)).where(Expense.currency == currency, Expense.status == "PARTIALLY_PAID")
    ).scalar_one()

    closing_variance_queue_count = db_session.execute(
        select(func.count(CashClosing.id)).where(CashClosing.currency == currency, CashClosing.status == "REVIEW_REQUIRED")
    ).scalar_one()

    overdue_invoices = sum(
        1
        for invoice in db_session.execute(
            select(CommercialInvoice).where(
                CommercialInvoice.currency == currency, CommercialInvoice.status.notin_(OVERDUE_INVOICE_EXCLUDED_STATUSES)
            )
        ).scalars().all()
        if is_invoice_overdue(invoice, as_of=today)
    )

    # Fulfillment exceptions: a confirmed order whose invoice is fully paid
    # but no Subscription was ever created for it (Subscription.sales_order_id
    # is the real, existing link -- app/models/subscriptions.py).
    paid_invoice_order_ids = set(db_session.execute(
        select(CommercialInvoice.sales_order_id).where(CommercialInvoice.status == "PAID", CommercialInvoice.sales_order_id.is_not(None))
    ).scalars().all())
    fulfilled_order_ids = set(db_session.execute(
        select(Subscription.sales_order_id).where(Subscription.sales_order_id.is_not(None))
    ).scalars().all())
    fulfillment_exceptions = len(paid_invoice_order_ids - fulfilled_order_ids)

    management_notes_requiring_action = db_session.execute(
        select(func.count(SharedManagementNote.id)).where(SharedManagementNote.status.in_(("OPEN", "IN_PROGRESS")))
    ).scalar_one()

    return ManagementOperationalDashboard(
        currency=currency,
        expense_totals_by_category={k: str(v) for k, v in expense_totals_by_category.items()},
        expense_totals_by_employee={str(k): str(v) for k, v in expense_totals_by_employee.items()},
        approval_queue_count=approval_queue_count,
        duplicate_review_queue_count=duplicate_review_queue_count,
        approved_unpaid_count=approved_unpaid_count,
        partially_paid_count=partially_paid_count,
        closing_variance_queue_count=closing_variance_queue_count,
        confirmed_collections=str(confirmed_payments_total(month_start, today, currency)),
        confirmed_refunds=str(confirmed_refunds_total(month_start, today, currency)),
        recorded_commission_payouts=str(recorded_commission_payouts_total(month_start, today, currency)),
        paid_expenses=str(paid_expenses_total(month_start, today, currency)),
        net_operational_cash_movement=str(net_operational_cash_movement(month_start, today, currency)),
        outstanding_receivables=str(outstanding_receivables_total(currency)),
        overdue_invoices=overdue_invoices,
        fulfillment_exceptions=fulfillment_exceptions,
        management_notes_requiring_action=management_notes_requiring_action,
    ).to_payload()


def finance_operational_dashboard(currency: str) -> dict:
    """Finance scope -- money-authorization queues + cash position, matching
    the exact FINANCE role shape already established (commercial_sales
    dashboards, RBAC SoD matrix)."""
    today = date.today()

    # AUDIT-owner-cross-screen: shared with the Management dashboard, and
    # currency-scoped -- "Expense approvals pending" sat directly above
    # "Approved unpaid Expenses" (already currency-scoped) and quietly
    # counted every other currency's requests too.
    approval_queue_count = _pending_expense_approval_count(currency)
    approved_unpaid_count = db_session.execute(
        select(func.count(Expense.id)).where(Expense.currency == currency, Expense.status == "APPROVED")
    ).scalar_one()
    partially_paid_count = db_session.execute(
        select(func.count(Expense.id)).where(Expense.currency == currency, Expense.status == "PARTIALLY_PAID")
    ).scalar_one()

    todays_closing = db_session.execute(
        select(CashClosing).where(CashClosing.business_date == today, CashClosing.currency == currency)
    ).scalars().first()
    daily_cash_closing = None
    if todays_closing is not None:
        daily_cash_closing = {
            "id": str(todays_closing.id), "status": todays_closing.status,
            "expected_closing_cash": str(todays_closing.expected_closing_cash),
            "variance": str(todays_closing.variance) if todays_closing.variance is not None else None,
        }
    closing_variance_queue_count = db_session.execute(
        select(func.count(CashClosing.id)).where(CashClosing.currency == currency, CashClosing.status == "REVIEW_REQUIRED")
    ).scalar_one()

    payment_confirmations_pending = db_session.execute(
        select(func.count(PaymentRecord.id)).where(PaymentRecord.currency == currency, PaymentRecord.status == "PENDING")
    ).scalar_one()

    unallocated_total = Decimal("0.00")
    for payment in db_session.execute(
        select(PaymentRecord).where(PaymentRecord.currency == currency, PaymentRecord.status == "CONFIRMED")
    ).scalars().all():
        balance = unallocated_payment_balance(payment)
        if balance > 0:
            unallocated_total += balance

    refund_queue_count = db_session.execute(
        select(func.count(CommercialRefund.id)).where(CommercialRefund.currency == currency, CommercialRefund.status.in_(("DRAFT", "APPROVED")))
    ).scalar_one()
    commission_payout_queue_count = db_session.execute(
        select(func.count(CommissionLedgerEntry.id)).where(CommissionLedgerEntry.currency == currency, CommissionLedgerEntry.status == "APPROVED")
    ).scalar_one()

    return FinanceOperationalDashboard(
        currency=currency,
        expense_approvals_pending=approval_queue_count,
        approved_unpaid_expenses=approved_unpaid_count,
        expense_payment_queue=approved_unpaid_count + partially_paid_count,
        partial_settlements=partially_paid_count,
        daily_cash_closing=daily_cash_closing,
        closing_variance_queue_count=closing_variance_queue_count,
        payment_confirmations_pending=payment_confirmations_pending,
        unallocated_payments_total=str(unallocated_total),
        refund_queue_count=refund_queue_count,
        commission_payout_queue_count=commission_payout_queue_count,
        net_operational_cash_movement=str(net_operational_cash_movement(today.replace(day=1), today, currency)),
    ).to_payload()
