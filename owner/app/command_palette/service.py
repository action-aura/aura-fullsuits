"""Command Palette (Ctrl+K) -- server-side, permission-scoped entity search
+ a small, fixed-size static navigation/quick-create command list.

See docs/owner/ui-modernization/command-palette-contract.md (read that
file first). Two halves, deliberately different shapes:

1. `search_entities()` -- real, bounded, server-side search across
   Customers/Leads/Quotes/Sales Orders/Invoices/Licenses/Installations.
   Every entity function below is gated on the EXACT real permission
   (`@require_permission`/`@require_any_permission`) that already guards
   that entity's real `list_*` route -- grepped from the actual route
   file, never guessed -- and, where the real route ownership-scopes its
   query, reuses the exact same mechanism (`apply_ownership_filter()`,
   the same shared helper `commercial_sales_web.list_quotes`/
   `list_orders`/`list_invoices` and `leads.list_leads`/
   `customers.list_customers` already call). Never a second,
   independently-derived filter -- see attention-center-contract.md's
   own precedent for why, and its two disclosed real bugs this module
   was written to avoid repeating: (a) every parameterized translated
   string uses `gettext()`'s own kwarg form, never `% {...}` after the
   fact; (b) an `_all`-permission bypass is always checked BEFORE any
   "no EmployeeProfile" guard, never after, so a VIEWER-shaped account
   with no profile of its own still gets the company-wide result.

2. `get_static_commands()` -- a small, fixed-size (~40 entries, the same
   order of magnitude as the sidebar's own real link count) server-
   rendered list of "go to X" and "quick create X" entries, mirroring
   `layout/_sidebar.html`'s own real destinations and permission checks
   line-for-line (same endpoints, same permission codes -- see the
   function's own docstring for the disclosed trade-off of keeping two
   files in sync by hand rather than one deriving from the other).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from flask import url_for
from flask_babel import gettext as _
from sqlalchemy import or_, select

from app.employees.queries import find_own_profile
from app.extensions import db_session
from app.i18n_labels import (
    commercial_invoice_status_label,
    customer_status_label,
    installation_status_label,
    lead_status_label,
    license_status_label,
    quote_status_label,
    sales_order_status_label,
)
from app.leads.ownership import apply_ownership_filter
from app.models.commercial_sales import CommercialInvoice, Quote, SalesOrder
from app.models.customers import Customer
from app.models.installations import Installation
from app.models.leads import Lead
from app.models.licensing import License

# Bounded, per this task's explicit "no full unfiltered table scan" and
# "no full dataset to the browser" requirements -- never unbounded.
PER_TYPE_LIMIT = 6
TOTAL_LIMIT = 36
MIN_QUERY_LENGTH = 2


@dataclass(frozen=True)
class SearchResult:
    type: str          # stable machine code, e.g. "customer"
    type_label: str    # human-readable, localized
    id: str
    label: str          # the real display field (name/number/identifier)
    context: str         # one short, real line of supporting data
    url: str             # a route this same staff member can already reach


def _any_code(codes: set[str], *wanted: str) -> bool:
    return any(code in codes for code in wanted)


def _escaped_term(query: str) -> str:
    """Escapes literal ILIKE wildcard characters in the user's own input
    so a typed '%' or '_' is matched literally rather than treated as a
    SQL wildcard (defense-in-depth, not itself an injection risk since
    the value is already bound as a parameter)."""
    escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# ---------------------------------------------------------- Customers --

def _search_customers(term: str, escaped_term: str, profile, codes: set[str]) -> list[SearchResult]:
    if "customers.view" not in codes:
        return []
    all_held = "customers.view_all" in codes
    # The `_all` bypass must be checked before any "no profile" guard --
    # see this module's own docstring (bug (b)) and attention-center-
    # contract.md's real precedent for why the order matters.
    if not all_held and profile is None:
        return []
    stmt = select(Customer).where(Customer.legal_name.ilike(escaped_term, escape="\\"))
    stmt = apply_ownership_filter(stmt, Customer, profile.id if profile else None, all_permission_held=all_held)
    rows = db_session.execute(stmt.order_by(Customer.legal_name).limit(PER_TYPE_LIMIT)).scalars().all()
    return [
        SearchResult(
            type="customer", type_label=_("Customer"), id=str(row.id), label=row.legal_name,
            context=customer_status_label(row.lifecycle_status),
            url=url_for("customers.detail", customer_id=row.id),
        )
        for row in rows
    ]


# --------------------------------------------------------------- Leads --

def _search_leads(term: str, escaped_term: str, profile, codes: set[str]) -> list[SearchResult]:
    if not _any_code(codes, "leads.view_own", "leads.view_all"):
        return []
    all_held = "leads.view_all" in codes
    if not all_held and profile is None:
        return []
    stmt = select(Lead).where(Lead.organization_or_prospect_name.ilike(escaped_term, escape="\\"))
    stmt = apply_ownership_filter(stmt, Lead, profile.id if profile else None, all_permission_held=all_held)
    rows = db_session.execute(stmt.order_by(Lead.organization_or_prospect_name).limit(PER_TYPE_LIMIT)).scalars().all()
    return [
        SearchResult(
            type="lead", type_label=_("Lead"), id=str(row.id), label=row.organization_or_prospect_name,
            context=lead_status_label(row.status), url=url_for("leads.detail", lead_id=row.id),
        )
        for row in rows
    ]


# ------------------------------------------------------------- Quotes --

def _search_quotes(term: str, escaped_term: str, profile, codes: set[str]) -> list[SearchResult]:
    if not _any_code(codes, "quotes.create", "quotes.approve"):
        return []
    # Mirrors commercial_sales/routes.py::_own_or_all()'s exact real rule
    # for this entity: only the approval-shaped permission bypasses
    # ownership, "create" alone does not.
    all_held = "quotes.approve" in codes
    if not all_held and profile is None:
        return []
    stmt = (
        select(Quote, Customer, Lead)
        .outerjoin(Customer, Customer.id == Quote.customer_id)
        .outerjoin(Lead, Lead.id == Quote.lead_id)
        .where(Quote.quote_number.ilike(escaped_term, escape="\\"))
    )
    stmt = apply_ownership_filter(stmt, Quote, profile.id if profile else None, all_permission_held=all_held)
    rows = db_session.execute(stmt.order_by(Quote.created_at.desc()).limit(PER_TYPE_LIMIT)).all()
    results = []
    for quote, customer, lead in rows:
        name = customer.legal_name if customer is not None else (lead.organization_or_prospect_name if lead is not None else "")
        context = _("%(name)s -- %(status)s", name=name, status=quote_status_label(quote.status))
        results.append(
            SearchResult(
                type="quote", type_label=_("Quote"), id=str(quote.id), label=quote.quote_number,
                context=context, url=url_for("commercial_sales_web.quote_detail", quote_id=quote.id),
            )
        )
    return results


# ------------------------------------------------------------- Orders --

def _search_orders(term: str, escaped_term: str, profile, codes: set[str]) -> list[SearchResult]:
    if not _any_code(codes, "orders.create", "orders.approve"):
        return []
    all_held = "orders.approve" in codes
    if not all_held and profile is None:
        return []
    stmt = (
        select(SalesOrder, Customer)
        .join(Customer, Customer.id == SalesOrder.customer_id)
        .where(SalesOrder.order_number.ilike(escaped_term, escape="\\"))
    )
    stmt = apply_ownership_filter(stmt, SalesOrder, profile.id if profile else None, all_permission_held=all_held)
    rows = db_session.execute(stmt.order_by(SalesOrder.created_at.desc()).limit(PER_TYPE_LIMIT)).all()
    return [
        SearchResult(
            type="order", type_label=_("Sales Order"), id=str(order.id), label=order.order_number,
            context=_("%(name)s -- %(status)s", name=customer.legal_name, status=sales_order_status_label(order.status)),
            url=url_for("commercial_sales_web.order_detail", order_id=order.id),
        )
        for order, customer in rows
    ]


# ----------------------------------------------------------- Invoices --

def _search_invoices(term: str, escaped_term: str, profile, codes: set[str]) -> list[SearchResult]:
    if not _any_code(codes, "invoices.create", "invoices.issue"):
        return []
    all_held = "invoices.issue" in codes
    if not all_held and profile is None:
        return []
    stmt = (
        select(CommercialInvoice, Customer)
        .join(Customer, Customer.id == CommercialInvoice.customer_id)
        .where(CommercialInvoice.invoice_number.ilike(escaped_term, escape="\\"))
    )
    stmt = apply_ownership_filter(stmt, CommercialInvoice, profile.id if profile else None, all_permission_held=all_held)
    rows = db_session.execute(stmt.order_by(CommercialInvoice.created_at.desc()).limit(PER_TYPE_LIMIT)).all()
    return [
        SearchResult(
            type="invoice", type_label=_("Invoice"), id=str(invoice.id), label=invoice.invoice_number,
            context=_(
                "%(name)s -- %(status)s", name=customer.legal_name, status=commercial_invoice_status_label(invoice.status)
            ),
            url=url_for("commercial_sales_web.invoice_detail", invoice_id=invoice.id),
        )
        for invoice, customer in rows
    ]


# ----------------------------------------------------------- Licenses --

def _search_licenses(term: str, escaped_term: str, codes: set[str]) -> list[SearchResult]:
    if "licenses.view" not in codes:
        return []
    # licensing.list_licenses has no ownership scoping at all (verified in
    # the real route) -- company-wide once the single permission is held,
    # so no apply_ownership_filter() call here, matching the real route.
    stmt = (
        select(License, Customer)
        .join(Customer, Customer.id == License.customer_id)
        .where(
            or_(
                Customer.legal_name.ilike(escaped_term, escape="\\"),
                License.key_prefix.ilike(escaped_term, escape="\\"),
            )
        )
    )
    rows = db_session.execute(stmt.order_by(License.created_at.desc()).limit(PER_TYPE_LIMIT)).all()
    return [
        SearchResult(
            type="license", type_label=_("License"), id=str(license_row.id),
            label=_("License for %(name)s", name=customer.legal_name),
            context=_(
                "%(prefix)s -- %(status)s",
                prefix=license_row.key_prefix or _("(unissued)"), status=license_status_label(license_row.status),
            ),
            url=url_for("licensing.detail", license_id=license_row.id),
        )
        for license_row, customer in rows
    ]


# ------------------------------------------------------- Installations --

def _search_installations(term: str, escaped_term: str, codes: set[str]) -> list[SearchResult]:
    if "installations.view" not in codes:
        return []
    # installations.list_installations has no ownership scoping either
    # (verified in the real route) -- same company-wide-once-permitted
    # shape as licenses.
    stmt = (
        select(Installation, Customer)
        .join(Customer, Customer.id == Installation.customer_id)
        .where(
            or_(
                Installation.installation_label.ilike(escaped_term, escape="\\"),
                Installation.device_label.ilike(escaped_term, escape="\\"),
                Customer.legal_name.ilike(escaped_term, escape="\\"),
            )
        )
    )
    rows = db_session.execute(stmt.order_by(Installation.created_at.desc()).limit(PER_TYPE_LIMIT)).all()
    results = []
    for installation, customer in rows:
        label = installation.installation_label or installation.device_label or _("Installation for %(name)s", name=customer.legal_name)
        results.append(
            SearchResult(
                type="installation", type_label=_("Installation"), id=str(installation.id), label=label,
                context=_(
                    "%(name)s -- %(status)s", name=customer.legal_name, status=installation_status_label(installation.status)
                ),
                url=url_for("installations.detail", installation_id=installation.id),
            )
        )
    return results


# -------------------------------------------------------------- Public --

def search_entities(staff, query: str) -> list[SearchResult]:
    """Real, bounded, permission- and ownership-scoped search across every
    entity type this stage covers. A short/empty query returns no entity
    results at all (avoids a full unfiltered table scan-shaped query) --
    static navigation matches are a separate, client-side-only concern
    (get_static_commands()), not affected by this bound."""
    if staff is None:
        return []
    term = (query or "").strip()
    if len(term) < MIN_QUERY_LENGTH:
        return []

    from app.security.rbac import get_staff_permission_codes

    codes = get_staff_permission_codes(staff)
    profile = find_own_profile(staff.id)
    escaped_term = _escaped_term(term)

    results: list[SearchResult] = []
    results += _search_customers(term, escaped_term, profile, codes)
    results += _search_leads(term, escaped_term, profile, codes)
    results += _search_quotes(term, escaped_term, profile, codes)
    results += _search_orders(term, escaped_term, profile, codes)
    results += _search_invoices(term, escaped_term, profile, codes)
    results += _search_licenses(term, escaped_term, codes)
    results += _search_installations(term, escaped_term, codes)
    return results[:TOTAL_LIMIT]


def search_results_as_dicts(results: list[SearchResult]) -> list[dict]:
    return [asdict(result) for result in results]


# --------------------------------------------------- Static commands --

def get_static_commands(codes: set[str]) -> dict:
    """A small, fixed-size (~40 entries) server-rendered command list:
    "go to X" navigation entries mirroring layout/_sidebar.html's own
    real destinations/permission checks line-for-line, plus "quick
    create X" entries gated on each real create route's own real
    permission. This is a second, hand-kept-in-sync rendering of the
    same permission source as _sidebar.html, not derived from it (Jinja
    templates cannot be introspected from Python) -- a disclosed,
    accepted duplication (see command-palette-contract.md): if a nav
    destination is added to the sidebar, it should also be added here.
    Every permission code and endpoint below was copied from the real,
    current _sidebar.html at the time this was written, not invented."""
    navigate: list[dict] = []
    create: list[dict] = []

    def nav(endpoint: str, label: str, group: str) -> None:
        navigate.append({"label": label, "group": group, "url": url_for(endpoint)})

    def quick_create(endpoint: str, label: str, key: str) -> None:
        create.append({"label": label, "group": str(_("Quick Create")), "url": url_for(endpoint), "key": key})

    overview = str(_("Overview"))
    crm = str(_("CRM"))
    sales = str(_("Sales"))
    finance_ops = str(_("Finance / Operations"))
    licensing_group = str(_("Licensing"))
    management = str(_("Management"))
    system_group = str(_("System"))

    # ---------- Overview ----------
    nav("dashboard.index", str(_("Dashboard")), overview)
    from app.attention.service import ATTENTION_CATEGORY_PERMISSIONS

    if codes & ATTENTION_CATEGORY_PERMISSIONS:
        nav("attention.index", str(_("Attention Center")), overview)

    # ---------- CRM ----------
    if _any_code(codes, "leads.view_own", "leads.view_all"):
        nav("leads.list_leads", str(_("Leads")), crm)
        nav("leads.crm_dashboard", str(_("CRM Dashboard")), crm)
    if "customers.view" in codes:
        nav("customers.list_customers", str(_("Customers")), crm)

    # ---------- Sales ----------
    if _any_code(codes, "quotes.create", "quotes.approve"):
        nav("commercial_sales_web.list_quotes", str(_("Quotes")), sales)
    if _any_code(codes, "orders.create", "orders.approve"):
        nav("commercial_sales_web.list_orders", str(_("Sales Orders")), sales)
    if _any_code(codes, "invoices.create", "invoices.issue"):
        nav("commercial_sales_web.list_invoices", str(_("Invoices")), sales)
    if "payments.view" in codes:
        nav("commercial_sales_web.list_payments", str(_("Payments")), sales)
    if _any_code(codes, "refunds.create", "refunds.approve"):
        nav("commercial_sales_web.list_refunds", str(_("Refunds")), sales)
    if _any_code(codes, "commissions.view_own", "commissions.view_all"):
        nav("commercial_sales_web.list_commissions", str(_("Commissions")), sales)
    if "commissions.pay" in codes:
        nav("commercial_sales_web.list_payout_batches", str(_("Commission Payouts")), sales)
    if _any_code(codes, "quotes.create", "orders.create", "invoices.create"):
        nav("commercial_sales_web.employee_dashboard_view", str(_("Commercial Dashboard")), sales)
    if "commissions.view_all" in codes:
        nav("commercial_sales_web.finance_dashboard_view", str(_("Finance Dashboard")), sales)

    # ---------- Finance / Operations ----------
    if _any_code(codes, "expenses.view_own", "expenses.view_all"):
        nav("operations_ui.list_expenses", str(_("Expenses")), finance_ops)
    if _any_code(codes, "expenses.create", "expenses.manage_payees"):
        nav("operations_ui.list_payees", str(_("Expense Payees")), finance_ops)
    if _any_code(codes, "cash_closing.view_own", "cash_closing.view_all", "cash_closing.prepare"):
        nav("operations_ui.list_closings", str(_("Cash Closing")), finance_ops)
    if "report_snapshots.view" in codes:
        nav("operations_ui.list_snapshots", str(_("Operational Reports")), finance_ops)
    if "management_notes.view" in codes:
        nav("operations_ui.list_notes", str(_("Management Notes")), finance_ops)
    if "dashboard.view_own" in codes:
        nav("operations_ui.employee_expense_dashboard_view", str(_("My Expense Dashboard")), finance_ops)
    if "dashboard.view_all" in codes:
        nav("operations_ui.management_operational_dashboard_view", str(_("Operational Management Dashboard")), finance_ops)
        nav("operations_ui.finance_operational_dashboard_view", str(_("Operational Finance Dashboard")), finance_ops)

    # ---------- Licensing ----------
    if "catalog.view" in codes:
        nav("catalog.index", str(_("Catalog")), licensing_group)
    if "subscriptions.view" in codes:
        nav("subscriptions.list_subscriptions", str(_("Subscriptions")), licensing_group)
    if "licenses.view" in codes:
        nav("licensing.list_licenses", str(_("Licenses")), licensing_group)
    if "installations.view" in codes:
        nav("installations.list_installations", str(_("Installations")), licensing_group)
    if "subscriptions.view" in codes:
        nav("commercial_ops_ui.list_renewals", str(_("Renewals")), licensing_group)
    if "pilots.view" in codes:
        nav("commercial_ops_ui.list_pilots", str(_("Pilots")), licensing_group)
    if "emergency_extensions.view" in codes:
        nav("commercial_ops_ui.list_emergency_extensions", str(_("Emergency Ext.")), licensing_group)
    if "pending_activations.view" in codes:
        nav("commercial_ops_ui.list_pending_activations", str(_("Activation Reviews")), licensing_group)
    if "subscriptions.view" in codes:
        nav("commercial_ops_ui.list_notifications", str(_("Notifications")), licensing_group)
        nav("commercial_ops_ui.queue_view", str(_("My Queue")), licensing_group)
    if "system.view" in codes:
        nav("commercial_ops_ui.reconciliation_view", str(_("Reconciliation")), licensing_group)
    if "activation_service.view" in codes:
        nav("licensing_admin.status", str(_("Activation Service")), licensing_group)

    # ---------- Management ----------
    if "staff.view" in codes:
        nav("staff.list_staff", str(_("Staff")), management)
    if "employees.view_all" in codes:
        nav("employees.list_view", str(_("Employees")), management)
        nav("employees.dashboard", str(_("Employee Dashboard")), management)

    # ---------- System ----------
    if "audit.view" in codes:
        nav("audit.list_audit", str(_("Audit Log")), system_group)
        nav("audit.security_events", str(_("Security Events")), system_group)
    if "system.view" in codes:
        nav("system.list_backups", str(_("Backups")), system_group)

    # ---------- Quick create ----------
    if "leads.create" in codes:
        quick_create("leads.new_form", str(_("New Lead")), "new_lead")
    if "customers.create" in codes:
        quick_create("customers.new_form", str(_("New Customer")), "new_customer")
    if "quotes.create" in codes:
        quick_create("commercial_sales_web.new_quote_form", str(_("New Quote")), "new_quote")
    if "licenses.create" in codes:
        quick_create("licensing.new_form", str(_("New License")), "new_license")
    if "installations.register" in codes:
        quick_create("installations.new_form", str(_("New Installation")), "new_installation")
    if "payments.create" in codes:
        quick_create("commercial_sales_web.new_payment_form", str(_("New Payment")), "new_payment")
    if "expenses.create" in codes:
        quick_create("operations_ui.new_expense_form", str(_("New Expense")), "new_expense")
    if "cash_closing.prepare" in codes:
        quick_create("operations_ui.new_closing_form", str(_("New Cash Closing")), "new_cash_closing")

    return {"navigate": navigate, "create": create}
