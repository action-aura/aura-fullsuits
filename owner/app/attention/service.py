"""Attention Center -- derived, real-time, permission-aware aggregation.

Per docs/owner/ui-modernization/attention-center-contract.md (read that
file first): this module intentionally does NOT create a persistent
notification/read-state table. Every item returned here is computed
fresh, on every call, directly from the real tables that already govern
that data's lifecycle (LeadFollowup/CustomerFollowup, CommercialApproval,
CommercialInvoice, PaymentRecord/PaymentAllocation, ExpenseApproval,
CommissionLedgerEntry, License, InternalNotification,
DatabaseBackupRecord) -- never a duplicated, independently-drifting copy
of that state. There is no "mark as read" anywhere in this module: an
item stops appearing the moment its underlying real record no longer
qualifies (completed, approved, paid, reactivated, ...), which IS the
read/unread signal, and the only one that can never go stale.

Every category function below is independently gated on the exact real
permission code that already gates the underlying list/detail route for
that data (see the contract doc's per-category table) and, where the
real route ownership-scopes its data (`_own`/`_all` pair, or a
record-level assignee check), mirrors that EXACT filter -- reusing the
real helper (`apply_ownership_filter`, `list_own_lead_followups_overdue`)
wherever one already exists, rather than re-deriving the rule. This is
why `get_attention_items()` takes the caller's `codes`/`profile` and only
ever queries a category the caller actually holds the permission for
(never "query everything, hide in the template" -- see
role-dashboard-contract.md's own disclosed efficiency gap this
deliberately avoids for new code).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from flask import url_for
from flask_babel import gettext as _
from sqlalchemy import select

from app.commercial_sales.allocation import unallocated_payment_balance
from app.commercial_sales.invoices import confirmed_allocated_amount
from app.employees.queries import find_own_profile
from app.extensions import db_session
from app.leads.ownership import apply_ownership_filter
from app.leads.engagement import list_own_lead_followups_overdue
from app.models.audit import DatabaseBackupRecord
from app.models.commercial_ops import InternalNotification
from app.models.commercial_sales import CommercialApproval, CommercialInvoice, Quote, QuoteLine
from app.models.commissions import CommissionLedgerEntry
from app.models.customers import Customer
from app.models.employees import EmployeeProfile
from app.models.expenses import Expense, ExpenseApproval
from app.models.base import utcnow
from app.models.leads import CustomerFollowup, Lead, LeadFollowup
from app.models.licensing import License, LicenseStatusHistory
from app.models.subscriptions import PaymentRecord

PRIORITY_URGENT = "urgent"
PRIORITY_NORMAL = "normal"
PRIORITY_LOW = "low"

# Every permission code that gates at least one category below -- the
# single source of truth for "does this employee have any reason to see
# the Attention Center at all" (topbar indicator + sidebar link).
ATTENTION_CATEGORY_PERMISSIONS = frozenset(
    {
        "leads.view_own", "leads.view_all",
        "customers.view",
        "quotes.approve", "pricing.override",
        "invoices.create", "invoices.issue",
        "payments.view",
        "expenses.approve",
        "commissions.approve",
        "licenses.view",
        "subscriptions.view",
        "system.view",
    }
)

# Queue role codes InternalNotification.assigned_role_code uses -- mirrors
# app/commercial_ops/ui_routes.py::_staff_role_codes() and
# app/commercial_ops/queues.py::QUEUE_ROLE_CODES exactly (not
# reimplemented differently here).
_QUEUE_ROLE_CODES = ("SALES", "FINANCE", "SUPPORT", "SUPER_ADMIN", "VIEWER")


@dataclass(frozen=True)
class AttentionItem:
    category: str          # stable machine code, e.g. "overdue_followups"
    category_label: str    # human-readable group heading
    priority: str           # one of PRIORITY_URGENT/PRIORITY_NORMAL/PRIORITY_LOW
    title: str
    context: str            # one concise line of real data
    occurred_at: datetime   # the real timestamp the item's age is derived from
    action_label: str
    action_url: str
    accessible_label: str   # category + title + context, for screen readers


def _age_priority(delta: timedelta, *, urgent_after: timedelta, normal_after: timedelta) -> str:
    if delta >= urgent_after:
        return PRIORITY_URGENT
    if delta >= normal_after:
        return PRIORITY_NORMAL
    return PRIORITY_LOW


def _humanize_duration(delta: timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 0)
    days = seconds // 86400
    if days >= 1:
        return _("%(count)d day(s)", count=days)
    hours = seconds // 3600
    if hours >= 1:
        return _("%(count)d hour(s)", count=hours)
    minutes = max(seconds // 60, 1)
    return _("%(count)d minute(s)", count=minutes)


def _accessible_label(category_label: str, title: str, context: str) -> str:
    return f"{category_label}: {title} -- {context}"


def _staff_role_codes(staff) -> list[str]:
    if staff.is_super_admin:
        return ["SUPER_ADMIN"]
    return [ra.role.code for ra in staff.role_assignments if ra.role.code in _QUEUE_ROLE_CODES]


# --------------------------------------------------------- Follow-ups --

def _overdue_lead_followup_items(profile, codes) -> list[AttentionItem]:
    now = utcnow()
    if "leads.view_all" in codes:
        # Company-wide bypass -- does not depend on the actor having their
        # own EmployeeProfile (e.g. a VIEWER account with no assigned work
        # of its own still needs to see everything under this permission).
        rows = db_session.execute(
            select(LeadFollowup, Lead)
            .join(Lead, Lead.id == LeadFollowup.lead_id)
            .where(
                LeadFollowup.completed_at.is_(None), LeadFollowup.cancelled_at.is_(None),
                LeadFollowup.due_at < now,
            )
            .order_by(LeadFollowup.due_at)
            .limit(100)
        ).all()
    elif profile is None:
        return []
    else:
        # Reuses the exact real "own overdue" query already used by the
        # employee CRM dashboard -- see leads/engagement.py.
        page = list_own_lead_followups_overdue(profile.id, as_of=now, page=1, page_size=100)
        followups = page["rows"]
        lead_ids = [f.lead_id for f in followups]
        leads_by_id = {}
        if lead_ids:
            leads_by_id = {
                lead.id: lead
                for lead in db_session.execute(select(Lead).where(Lead.id.in_(lead_ids))).scalars().all()
            }
        rows = [(f, leads_by_id.get(f.lead_id)) for f in followups if leads_by_id.get(f.lead_id) is not None]

    items = []
    for followup, lead in rows:
        overdue_by = now - followup.due_at
        priority = _age_priority(overdue_by, urgent_after=timedelta(days=3), normal_after=timedelta(hours=1))
        title = _("Follow-up overdue: %(name)s", name=lead.organization_or_prospect_name)
        context = _("%(duration)s overdue", duration=_humanize_duration(overdue_by))
        items.append(
            AttentionItem(
                category="overdue_followups", category_label=_("Overdue Follow-ups"), priority=priority,
                title=title, context=context, occurred_at=followup.due_at,
                action_label=_("Open lead"), action_url=url_for("leads.detail", lead_id=lead.id),
                accessible_label=_accessible_label(_("Overdue Follow-ups"), title, context),
            )
        )
    return items


def _overdue_customer_followup_items(staff, codes) -> list[AttentionItem]:
    now = utcnow()
    stmt = (
        select(CustomerFollowup, Customer)
        .join(Customer, Customer.id == CustomerFollowup.customer_id)
        .where(
            CustomerFollowup.completed_at.is_(None), CustomerFollowup.cancelled_at.is_(None),
            CustomerFollowup.due_at < now,
        )
    )
    if "customers.view_all" not in codes:
        # Mirrors app/customers/services.py::customer_visible_to_actor()'s
        # exact ownership rule (assigned_sales_staff_id is a StaffUser id,
        # not an EmployeeProfile id -- unlike LeadFollowup).
        stmt = stmt.where(Customer.assigned_sales_staff_id == staff.id)
    rows = db_session.execute(stmt.order_by(CustomerFollowup.due_at).limit(100)).all()

    items = []
    for followup, customer in rows:
        overdue_by = now - followup.due_at
        priority = _age_priority(overdue_by, urgent_after=timedelta(days=3), normal_after=timedelta(hours=1))
        title = _("Follow-up overdue: %(name)s", name=customer.legal_name)
        context = _("%(duration)s overdue", duration=_humanize_duration(overdue_by))
        items.append(
            AttentionItem(
                category="overdue_followups", category_label=_("Overdue Follow-ups"), priority=priority,
                title=title, context=context, occurred_at=followup.due_at,
                action_label=_("Open customer"), action_url=url_for("customers.detail", customer_id=customer.id),
                accessible_label=_accessible_label(_("Overdue Follow-ups"), title, context),
            )
        )
    return items


# ------------------------------------------------------------- Quotes --

def _quotes_pending_approval_items() -> list[AttentionItem]:
    now = utcnow()
    approvals = db_session.execute(
        select(CommercialApproval)
        .where(CommercialApproval.target_type == "QUOTE_LINE", CommercialApproval.status == "PENDING")
        .order_by(CommercialApproval.requested_at)
        .limit(100)
    ).scalars().all()

    items = []
    for approval in approvals:
        line = db_session.get(QuoteLine, approval.target_id)
        if line is None:
            continue  # target no longer exists -- stale, nothing to act on (see approvals.py's own handling)
        quote = db_session.get(Quote, line.quote_id)
        if quote is None:
            continue
        age = now - approval.requested_at
        priority = _age_priority(age, urgent_after=timedelta(days=2), normal_after=timedelta(hours=4))
        title = _("Quote %(number)s awaiting approval", number=quote.quote_number)
        context = _("Price override requested, pending %(duration)s", duration=_humanize_duration(age))
        items.append(
            AttentionItem(
                category="quotes_pending_approval", category_label=_("Quotes Waiting for Approval"), priority=priority,
                title=title, context=context, occurred_at=approval.requested_at,
                action_label=_("Review quote"), action_url=url_for("commercial_sales_web.quote_detail", quote_id=quote.id),
                accessible_label=_accessible_label(_("Quotes Waiting for Approval"), title, context),
            )
        )
    return items


# ----------------------------------------------------------- Invoices --

def _overdue_invoice_items(profile, codes) -> list[AttentionItem]:
    today = date.today()
    all_held = "invoices.issue" in codes
    stmt = apply_ownership_filter(
        select(CommercialInvoice), CommercialInvoice, profile.id if profile else None, all_permission_held=all_held
    ).where(
        CommercialInvoice.status.in_(("ISSUED", "PARTIALLY_PAID")),
        CommercialInvoice.due_date.is_not(None),
        CommercialInvoice.due_date < today,
    )
    if not all_held and profile is None:
        return []
    invoices = db_session.execute(stmt.order_by(CommercialInvoice.due_date).limit(100)).scalars().all()

    items = []
    for invoice in invoices:
        days_overdue = (today - invoice.due_date).days
        priority = (
            PRIORITY_URGENT if days_overdue >= 14 else PRIORITY_NORMAL if days_overdue >= 3 else PRIORITY_LOW
        )
        outstanding = invoice.total - confirmed_allocated_amount(invoice)
        title = _("Invoice %(number)s overdue", number=invoice.invoice_number)
        context = _(
            "%(days)d day(s) overdue, %(amount)s %(currency)s outstanding",
            days=days_overdue, amount=outstanding, currency=invoice.currency,
        )
        items.append(
            AttentionItem(
                category="overdue_invoices", category_label=_("Overdue Invoices"), priority=priority,
                title=title, context=context, occurred_at=datetime.combine(invoice.due_date, datetime.min.time(), tzinfo=timezone.utc),
                action_label=_("Open invoice"), action_url=url_for("commercial_sales_web.invoice_detail", invoice_id=invoice.id),
                accessible_label=_accessible_label(_("Overdue Invoices"), title, context),
            )
        )
    return items


# ----------------------------------------------------------- Payments --

def _unallocated_payment_items() -> list[AttentionItem]:
    now = utcnow()
    payments = db_session.execute(
        select(PaymentRecord).where(PaymentRecord.status == "CONFIRMED").order_by(PaymentRecord.created_at.desc()).limit(200)
    ).scalars().all()

    items = []
    for payment in payments:
        balance = unallocated_payment_balance(payment)
        if balance <= 0:
            continue
        age = now - payment.created_at
        priority = _age_priority(age, urgent_after=timedelta(days=7), normal_after=timedelta(days=2))
        title = _("Unallocated payment from customer")
        context = _(
            "%(balance)s %(currency)s of %(amount)s %(currency)s unallocated, confirmed %(duration)s ago",
            balance=balance, amount=payment.amount, currency=payment.currency, duration=_humanize_duration(age),
        )
        items.append(
            AttentionItem(
                category="unallocated_payments", category_label=_("Unallocated Payments"), priority=priority,
                title=title, context=context, occurred_at=payment.created_at,
                action_label=_("Allocate payment"), action_url=url_for("commercial_sales_web.payment_detail", payment_id=payment.id),
                accessible_label=_accessible_label(_("Unallocated Payments"), title, context),
            )
        )
    return items


# ----------------------------------------------------------- Expenses --

def _pending_expense_items(profile) -> list[AttentionItem]:
    now = utcnow()
    rows = db_session.execute(
        select(ExpenseApproval, Expense)
        .join(Expense, Expense.id == ExpenseApproval.expense_id)
        .where(ExpenseApproval.status == "PENDING")
        .order_by(ExpenseApproval.requested_at)
        .limit(100)
    ).all()

    items = []
    for approval, expense in rows:
        # Self-approval and beneficiary-conflict are always forbidden
        # (app/expenses/approvals.py::check_approver_eligibility) -- an
        # item this actor could never actually act on is excluded rather
        # than linked to a decision that will just be rejected.
        if profile is not None and (
            approval.requested_by_employee_profile_id == profile.id
            or expense.beneficiary_employee_profile_id == profile.id
        ):
            continue
        age = now - approval.requested_at
        priority = _age_priority(age, urgent_after=timedelta(days=5), normal_after=timedelta(days=2))
        title = _("Expense %(number)s awaiting approval", number=expense.expense_number or str(expense.id)[:8])
        context = _(
            "%(amount)s %(currency)s requested, pending %(duration)s",
            amount=approval.requested_amount, currency=expense.currency, duration=_humanize_duration(age),
        )
        items.append(
            AttentionItem(
                category="pending_expenses", category_label=_("Pending Expenses"), priority=priority,
                title=title, context=context, occurred_at=approval.requested_at,
                action_label=_("Review expense"), action_url=url_for("operations_ui.expense_detail", expense_id=expense.id),
                accessible_label=_accessible_label(_("Pending Expenses"), title, context),
            )
        )
    return items


# --------------------------------------------------------- Commissions --

def _pending_commission_items() -> list[AttentionItem]:
    now = utcnow()
    rows = db_session.execute(
        select(CommissionLedgerEntry, EmployeeProfile)
        .join(EmployeeProfile, EmployeeProfile.id == CommissionLedgerEntry.employee_profile_id)
        .where(CommissionLedgerEntry.status == "PENDING")
        .order_by(CommissionLedgerEntry.created_at)
        .limit(100)
    ).all()

    items = []
    for entry, employee in rows:
        age = now - entry.created_at
        priority = _age_priority(age, urgent_after=timedelta(days=5), normal_after=timedelta(days=2))
        title = _("Commission for %(name)s awaiting approval", name=employee.full_name)
        context = _(
            "%(amount)s %(currency)s, earned %(duration)s ago",
            amount=entry.commission_amount, currency=entry.currency, duration=_humanize_duration(age),
        )
        items.append(
            AttentionItem(
                category="pending_commissions", category_label=_("Pending Commission Approvals"), priority=priority,
                title=title, context=context, occurred_at=entry.created_at,
                action_label=_("Review commissions"), action_url=url_for("commercial_sales_web.list_commissions", status="PENDING"),
                accessible_label=_accessible_label(_("Pending Commission Approvals"), title, context),
            )
        )
    return items


# ----------------------------------------------------------- Licenses --

_EXPIRING_LICENSE_WINDOW_DAYS = 30


def _expiring_license_items() -> list[AttentionItem]:
    today = date.today()
    window_end = today + timedelta(days=_EXPIRING_LICENSE_WINDOW_DAYS)
    rows = db_session.execute(
        select(License, Customer)
        .join(Customer, Customer.id == License.customer_id)
        .where(License.status == "ACTIVE", License.valid_until.is_not(None), License.valid_until >= today, License.valid_until <= window_end)
        .order_by(License.valid_until)
        .limit(100)
    ).all()

    items = []
    for license_row, customer in rows:
        days_left = (license_row.valid_until - today).days
        priority = PRIORITY_URGENT if days_left <= 7 else PRIORITY_NORMAL
        title = _("License expiring for %(name)s", name=customer.legal_name)
        context = _("Expires in %(days)d day(s)", days=days_left)
        items.append(
            AttentionItem(
                category="expiring_licenses", category_label=_("Expiring Licenses"), priority=priority,
                title=title, context=context, occurred_at=datetime.combine(license_row.valid_until, datetime.min.time(), tzinfo=timezone.utc),
                action_label=_("Open license"), action_url=url_for("licensing.detail", license_id=license_row.id),
                accessible_label=_accessible_label(_("Expiring Licenses"), title, context),
            )
        )
    return items


def _suspended_license_items() -> list[AttentionItem]:
    now = utcnow()
    rows = db_session.execute(
        select(License, Customer).join(Customer, Customer.id == License.customer_id).where(License.status == "SUSPENDED").limit(100)
    ).all()

    items = []
    for license_row, customer in rows:
        history = db_session.execute(
            select(LicenseStatusHistory)
            .where(LicenseStatusHistory.license_id == license_row.id, LicenseStatusHistory.to_status == "SUSPENDED")
            .order_by(LicenseStatusHistory.created_at.desc())
            .limit(1)
        ).scalars().first()
        suspended_at = history.created_at if history is not None else license_row.updated_at
        age = now - suspended_at
        priority = _age_priority(age, urgent_after=timedelta(days=30), normal_after=timedelta(days=7))
        title = _("License suspended for %(name)s", name=customer.legal_name)
        context = _("Suspended %(duration)s ago", duration=_humanize_duration(age))
        items.append(
            AttentionItem(
                category="suspended_licenses", category_label=_("Suspended Licenses"), priority=priority,
                title=title, context=context, occurred_at=suspended_at,
                action_label=_("Open license"), action_url=url_for("licensing.detail", license_id=license_row.id),
                accessible_label=_accessible_label(_("Suspended Licenses"), title, context),
            )
        )
    return items


# ------------------------------------------------------ Device events --

_NOTIFICATION_SEVERITY_PRIORITY = {"CRITICAL": PRIORITY_URGENT, "WARNING": PRIORITY_NORMAL, "INFO": PRIORITY_LOW}


def _device_limit_event_items(staff) -> list[AttentionItem]:
    now = utcnow()
    role_codes = _staff_role_codes(staff)
    stmt = select(InternalNotification).where(
        InternalNotification.notification_type == "DEVICE_LIMIT_EXCEEDED",
        InternalNotification.status.in_(("OPEN", "IN_PROGRESS")),
    )
    if "SUPER_ADMIN" not in role_codes:
        if not role_codes:
            return []
        stmt = stmt.where(InternalNotification.assigned_role_code.in_(role_codes))
    rows = db_session.execute(stmt.order_by(InternalNotification.created_at).limit(100)).scalars().all()

    items = []
    for notification in rows:
        age = now - notification.created_at
        priority = _NOTIFICATION_SEVERITY_PRIORITY.get(notification.severity, PRIORITY_NORMAL)
        context = _("Open %(duration)s -- %(message)s", duration=_humanize_duration(age), message=notification.message)
        items.append(
            AttentionItem(
                category="device_limit_events", category_label=_("Device-Limit Events"), priority=priority,
                title=notification.title, context=context, occurred_at=notification.created_at,
                action_label=_("Open notifications"), action_url=url_for("commercial_ops_ui.list_notifications"),
                accessible_label=_accessible_label(_("Device-Limit Events"), notification.title, context),
            )
        )
    return items


# ------------------------------------------------------------ Backups --

def _failed_backup_items() -> list[AttentionItem]:
    latest = db_session.execute(
        select(DatabaseBackupRecord).order_by(DatabaseBackupRecord.created_at.desc()).limit(1)
    ).scalars().first()
    if latest is None or latest.status != "FAILED":
        return []
    now = utcnow()
    age = now - latest.created_at
    title = _("Most recent database backup failed")
    context = _("Failed %(duration)s ago -- no successful backup since", duration=_humanize_duration(age))
    return [
        AttentionItem(
            category="failed_backups", category_label=_("Failed Backups"), priority=PRIORITY_URGENT,
            title=title, context=context, occurred_at=latest.created_at,
            action_label=_("Open backups"), action_url=url_for("system.list_backups"),
            accessible_label=_accessible_label(_("Failed Backups"), title, context),
        )
    ]


# -------------------------------------------------------------- Public --

_PRIORITY_ORDER = {PRIORITY_URGENT: 0, PRIORITY_NORMAL: 1, PRIORITY_LOW: 2}


def get_attention_items(staff) -> list[AttentionItem]:
    """Real-time, permission-gated aggregation for the given StaffUser.
    Every category below is only ever queried if `staff` actually holds
    the real permission that governs it -- never computed then discarded
    in the template. Sorted urgent-first, then oldest-first within a
    priority band (the longer something has waited, the sooner it's
    shown)."""
    if staff is None:
        return []

    from app.security.rbac import get_staff_permission_codes

    codes = get_staff_permission_codes(staff)
    profile = find_own_profile(staff.id)

    items: list[AttentionItem] = []

    if "leads.view_own" in codes or "leads.view_all" in codes:
        items += _overdue_lead_followup_items(profile, codes)
    if "customers.view" in codes:
        items += _overdue_customer_followup_items(staff, codes)
    if "quotes.approve" in codes or "pricing.override" in codes:
        items += _quotes_pending_approval_items()
    if "invoices.create" in codes or "invoices.issue" in codes:
        items += _overdue_invoice_items(profile, codes)
    if "payments.view" in codes:
        items += _unallocated_payment_items()
    if "expenses.approve" in codes:
        items += _pending_expense_items(profile)
    if "commissions.approve" in codes:
        items += _pending_commission_items()
    if "licenses.view" in codes:
        items += _expiring_license_items()
        items += _suspended_license_items()
    if "subscriptions.view" in codes:
        items += _device_limit_event_items(staff)
    if "system.view" in codes:
        items += _failed_backup_items()

    items.sort(key=lambda item: (_PRIORITY_ORDER.get(item.priority, 9), item.occurred_at))
    return items


def count_attention_items(staff) -> int:
    """Lightweight-by-permission (not lightweight-by-query) count for the
    topbar badge: reuses get_attention_items() so the badge count and the
    /attention page can never drift apart from two independent filter
    implementations -- see the contract doc's disclosed performance note
    for why this is an accepted trade-off, not an oversight."""
    return len(get_attention_items(staff))
