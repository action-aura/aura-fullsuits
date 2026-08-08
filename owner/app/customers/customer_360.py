"""Customer 360 profile -- real, bounded, permission-gated cross-domain
queries for the redesigned Customer detail screen's new tabs (Quotes/
Orders/Invoices/Payments/Subscriptions/Licenses/Installations) and Timeline.

See docs/owner/ui-modernization/customer-360-contract.md for the full
contract (header data sources, per-tab permission/ownership mapping,
Timeline construction, Location/Assignment-history placement).

Every query here is READ-ONLY, filtered to exactly one customer_id, and
gated by the caller (owner/app/customers/routes.py::detail) reusing the
EXACT real permission code that already guards that entity's own real
list/detail route -- verified against the real @require_permission/
@require_any_permission decorators (owner/app/commercial_sales/routes.py,
owner/app/subscriptions/routes.py, owner/app/licensing/routes.py,
owner/app/installations/routes.py), the same discipline
app.command_palette.service already established for cross-domain search.
Never a new permission code, never a route-path change.

Ownership scoping reuses app.leads.ownership.apply_ownership_filter --
never a second, independently written filter -- for the three entities
that have a real ownership dimension (Quote/SalesOrder/CommercialInvoice,
creator-only, "_all"-shaped bypass = quotes.approve/orders.approve/
invoices.issue, mirroring commercial_sales/routes.py::list_quotes/
list_orders/list_invoices's own exact `_own_or_all()` rule). Subscription/
License/Installation/PaymentRecord/CommercialRefund have NO ownership
dimension in their own real routes either (verified: none of
subscriptions.routes.list_subscriptions, licensing.routes.list_licenses,
installations.routes.list_installations,
commercial_sales.routes.list_payments/list_refunds ever call
apply_ownership_filter) -- company-wide once the permission is held, the
exact same shape is reproduced here, not invented.

The Timeline tab is built purely from these same domain-event rows --
never AuditLog/SecurityEvent (the governing spec explicitly warns against
duplicating the audit log as a customer-friendly timeline without proper
filtering/translation). Each event only appears if the viewer holds the
permission for the entity type it came from, exactly mirroring that tab's
own gate.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from flask import url_for
from flask_babel import gettext as _
from sqlalchemy import select

from app.extensions import db_session
from app.i18n_format import format_owner_number
from app.i18n_labels import (
    commercial_invoice_status_label,
    quote_status_label,
    sales_order_status_label,
)
from app.leads.ownership import apply_ownership_filter
from app.models.commercial_sales import CommercialApproval, CommercialInvoice, CommercialRefund, Quote, SalesOrder
from app.models.customers import Customer
from app.models.installations import Installation, InstallationStatusHistory
from app.models.licensing import License
from app.models.subscriptions import PaymentRecord, Subscription

# Bounded per-tab/per-feed row count -- same "no full unfiltered table scan"
# discipline as command_palette.service.PER_TYPE_LIMIT. A single customer is
# not expected to accumulate more than a few dozen of any one document type;
# if that assumption is ever wrong for a real account, the fix is real
# pagination on the affected tab (documented as a follow-up in
# customer-360-contract.md), not silently raising this bound.
PER_TAB_LIMIT = 50
TIMELINE_LIMIT = 50


def _quotes_all_held(codes: set[str]) -> bool:
    """Mirrors commercial_sales/routes.py::list_quotes's own exact rule:
    only quotes.approve bypasses ownership, quotes.create alone does not."""
    return "quotes.approve" in codes


def _orders_all_held(codes: set[str]) -> bool:
    return "orders.approve" in codes


def _invoices_all_held(codes: set[str]) -> bool:
    return "invoices.issue" in codes


def get_quotes(customer_id: uuid.UUID, *, profile_id, codes: set[str]) -> list[Quote]:
    if not (codes & {"quotes.create", "quotes.approve"}):
        return []
    stmt = select(Quote).where(Quote.customer_id == customer_id)
    stmt = apply_ownership_filter(stmt, Quote, profile_id, all_permission_held=_quotes_all_held(codes))
    return list(db_session.execute(stmt.order_by(Quote.created_at.desc()).limit(PER_TAB_LIMIT)).scalars().all())


def get_orders(customer_id: uuid.UUID, *, profile_id, codes: set[str]) -> list[SalesOrder]:
    if not (codes & {"orders.create", "orders.approve"}):
        return []
    stmt = select(SalesOrder).where(SalesOrder.customer_id == customer_id)
    stmt = apply_ownership_filter(stmt, SalesOrder, profile_id, all_permission_held=_orders_all_held(codes))
    return list(db_session.execute(stmt.order_by(SalesOrder.created_at.desc()).limit(PER_TAB_LIMIT)).scalars().all())


def get_invoices(customer_id: uuid.UUID, *, profile_id, codes: set[str]) -> list[CommercialInvoice]:
    if not (codes & {"invoices.create", "invoices.issue"}):
        return []
    stmt = select(CommercialInvoice).where(CommercialInvoice.customer_id == customer_id)
    stmt = apply_ownership_filter(stmt, CommercialInvoice, profile_id, all_permission_held=_invoices_all_held(codes))
    return list(db_session.execute(stmt.order_by(CommercialInvoice.created_at.desc()).limit(PER_TAB_LIMIT)).scalars().all())


def get_payments(customer_id: uuid.UUID, *, codes: set[str]) -> list[PaymentRecord]:
    if "payments.view" not in codes:
        return []
    stmt = select(PaymentRecord).where(PaymentRecord.customer_id == customer_id)
    return list(db_session.execute(stmt.order_by(PaymentRecord.created_at.desc()).limit(PER_TAB_LIMIT)).scalars().all())


def get_subscriptions(customer_id: uuid.UUID, *, codes: set[str]) -> list[Subscription]:
    if "subscriptions.view" not in codes:
        return []
    stmt = select(Subscription).where(Subscription.customer_id == customer_id)
    return list(db_session.execute(stmt.order_by(Subscription.created_at.desc()).limit(PER_TAB_LIMIT)).scalars().all())


def get_licenses(customer_id: uuid.UUID, *, codes: set[str]) -> list[License]:
    if "licenses.view" not in codes:
        return []
    stmt = select(License).where(License.customer_id == customer_id)
    return list(db_session.execute(stmt.order_by(License.created_at.desc()).limit(PER_TAB_LIMIT)).scalars().all())


def get_installations(customer_id: uuid.UUID, *, codes: set[str]) -> list[Installation]:
    if "installations.view" not in codes:
        return []
    stmt = select(Installation).where(Installation.customer_id == customer_id)
    return list(db_session.execute(stmt.order_by(Installation.created_at.desc()).limit(PER_TAB_LIMIT)).scalars().all())


def get_refunds(customer_id: uuid.UUID, *, codes: set[str]) -> list[CommercialRefund]:
    """Timeline-only feed (spec's required "Refund processed" event type)
    -- Refunds has no dedicated tab in the governing spec's 13-tab list,
    but the same real permission gate commercial_sales/routes.py::
    list_refunds enforces (refunds.create/refunds.approve, no ownership
    filter -- verified against the real route) applies here too."""
    if not (codes & {"refunds.create", "refunds.approve"}):
        return []
    stmt = (
        select(CommercialRefund)
        .join(CommercialInvoice, CommercialInvoice.id == CommercialRefund.commercial_invoice_id)
        .where(CommercialInvoice.customer_id == customer_id)
    )
    return list(db_session.execute(stmt.order_by(CommercialRefund.created_at.desc()).limit(PER_TAB_LIMIT)).scalars().all())


def get_approved_quote_approvals(quote_ids: list[uuid.UUID], *, codes: set[str]) -> list[CommercialApproval]:
    """Real "Quote approved" source: Quote itself has no APPROVED status
    (QUOTE_STATUSES is DRAFT/SENT/ACCEPTED/REJECTED/EXPIRED/CANCELLED --
    verified against app.models.commercial_sales) -- the real, distinct
    approval event is a CommercialApproval row (pricing-exception/discount
    approval workflow) reaching status APPROVED. Scoped to the caller's
    own already-permission-and-ownership-filtered `quotes` list's ids, so
    an actor never sees an approval for a quote they couldn't otherwise
    see via the Quotes tab."""
    if not quote_ids or not (codes & {"quotes.create", "quotes.approve"}):
        return []
    stmt = select(CommercialApproval).where(
        CommercialApproval.target_type == "QUOTE",
        CommercialApproval.target_id.in_(quote_ids),
        CommercialApproval.status == "APPROVED",
    )
    return list(db_session.execute(stmt.order_by(CommercialApproval.decided_at.desc())).scalars().all())


def get_installation_activations(installation_ids: list[uuid.UUID], *, codes: set[str]) -> list[InstallationStatusHistory]:
    """Real "Installation activated" source: InstallationStatusHistory rows
    with to_status == "ACTIVE" (written by
    app.installations.services.transition_installation, the one real place
    an Installation reaches ACTIVE) -- scoped to the caller's own
    already-permission-filtered `installations` list's ids."""
    if not installation_ids or "installations.view" not in codes:
        return []
    stmt = select(InstallationStatusHistory).where(
        InstallationStatusHistory.installation_id.in_(installation_ids),
        InstallationStatusHistory.to_status == "ACTIVE",
    )
    return list(db_session.execute(stmt.order_by(InstallationStatusHistory.created_at.desc()).limit(PER_TAB_LIMIT)).scalars().all())


def build_commercial_summary(
    *, quotes: list[Quote], orders: list[SalesOrder], invoices: list[CommercialInvoice],
    subscriptions: list[Subscription], licenses: list[License], installations: list[Installation],
) -> dict:
    """Real, bounded aggregate numbers for the header's "commercial summary"
    -- reuses the exact rows already fetched for each tab (never a second,
    separately-computed query). An entity the actor lacks permission for
    contributes an empty list (see the get_*() functions above), so its
    key here is simply absent -- the header only ever shows what the real
    data supports for THIS viewer, never a fabricated zero standing in for
    "not permitted to know"."""
    summary: dict = {}
    if quotes:
        summary["quotes_count"] = len(quotes)
    if orders:
        summary["orders_count"] = len(orders)
    if invoices:
        open_invoices = [inv for inv in invoices if inv.status in ("ISSUED", "PARTIALLY_PAID")]
        if open_invoices:
            summary["open_invoices_count"] = len(open_invoices)
            summary["open_invoices_total"] = sum((inv.total for inv in open_invoices), Decimal("0"))
            summary["open_invoices_currency"] = open_invoices[0].currency
    if subscriptions:
        active = [s for s in subscriptions if s.status == "ACTIVE"]
        summary["active_subscriptions_count"] = len(active)
    if licenses:
        active = [lic for lic in licenses if lic.status == "ACTIVE"]
        summary["active_licenses_count"] = len(active)
    if installations:
        active = [i for i in installations if i.status == "ACTIVE"]
        summary["active_installations_count"] = len(active)
    return summary


@dataclass(frozen=True)
class TimelineEvent:
    timestamp: datetime
    label: str
    url: str | None


def build_timeline(
    customer: Customer, *, contacts: list, notes: list,
    quotes: list[Quote], quote_approvals: list[CommercialApproval],
    orders: list[SalesOrder], invoices: list[CommercialInvoice], payments: list[PaymentRecord],
    subscriptions: list[Subscription], licenses: list[License],
    installation_activations: list[InstallationStatusHistory], refunds: list[CommercialRefund],
) -> list[TimelineEvent]:
    """Merges every domain-event source already fetched for the other tabs
    into one chronological feed, most-recent-first, bounded to
    TIMELINE_LIMIT. Deliberately does NOT query AuditLog/SecurityEvent --
    every event below comes from a real, typed domain row this same actor
    is already independently permitted to see via its own tab's gate (each
    get_*() function above already returned an empty list when the
    permission is missing, so an ungated caller simply contributes no
    events -- no second permission check needed here)."""
    events: list[TimelineEvent] = []

    if customer.converted_from_lead_id:
        events.append(TimelineEvent(
            timestamp=customer.created_at,
            label=_("Lead converted to this customer"),
            url=url_for("leads.detail", lead_id=customer.converted_from_lead_id),
        ))

    for contact in contacts:
        events.append(TimelineEvent(
            timestamp=contact.created_at,
            label=_("Contact added: %(name)s", name=contact.name),
            url=None,
        ))

    for note in notes:
        events.append(TimelineEvent(timestamp=note.created_at, label=_("Note added"), url=None))

    for quote in quotes:
        events.append(TimelineEvent(
            timestamp=quote.created_at,
            label=_("Quote created: %(number)s (%(status)s)", number=quote.quote_number, status=quote_status_label(quote.status)),
            url=url_for("commercial_sales_web.quote_detail", quote_id=quote.id),
        ))
    quotes_by_id = {quote.id: quote for quote in quotes}
    for approval in quote_approvals:
        quote = quotes_by_id.get(approval.target_id)
        if quote is None or approval.decided_at is None:
            continue
        events.append(TimelineEvent(
            timestamp=approval.decided_at,
            label=_("Quote approved: %(number)s", number=quote.quote_number),
            url=url_for("commercial_sales_web.quote_detail", quote_id=quote.id),
        ))

    for order in orders:
        events.append(TimelineEvent(
            timestamp=order.created_at,
            label=_("Order created: %(number)s (%(status)s)", number=order.order_number, status=sales_order_status_label(order.status)),
            url=url_for("commercial_sales_web.order_detail", order_id=order.id),
        ))

    for invoice in invoices:
        if invoice.issued_at is None:
            continue
        events.append(TimelineEvent(
            timestamp=invoice.issued_at,
            label=_(
                "Invoice issued: %(number)s (%(status)s)",
                number=invoice.invoice_number, status=commercial_invoice_status_label(invoice.status),
            ),
            url=url_for("commercial_sales_web.invoice_detail", invoice_id=invoice.id),
        ))

    for payment in payments:
        events.append(TimelineEvent(
            timestamp=payment.created_at,
            label=_(
                "Payment recorded: %(amount)s %(currency)s",
                amount=format_owner_number(payment.amount), currency=payment.currency,
            ),
            url=url_for("commercial_sales_web.payment_detail", payment_id=payment.id),
        ))

    for subscription in subscriptions:
        events.append(TimelineEvent(
            timestamp=subscription.created_at,
            label=_("Subscription created"),
            url=url_for("subscriptions.detail", subscription_id=subscription.id),
        ))

    for license_row in licenses:
        if license_row.issued_at is None:
            continue
        events.append(TimelineEvent(
            timestamp=license_row.issued_at,
            label=_("License issued: %(prefix)s", prefix=license_row.key_prefix or _("(unissued)")),
            url=url_for("licensing.detail", license_id=license_row.id),
        ))

    for history in installation_activations:
        events.append(TimelineEvent(
            timestamp=history.created_at,
            label=_("Installation activated"),
            url=url_for("installations.detail", installation_id=history.installation_id),
        ))

    for refund in refunds:
        if refund.paid_at is None:
            continue
        events.append(TimelineEvent(
            timestamp=refund.paid_at,
            label=_(
                "Refund processed: %(amount)s %(currency)s",
                amount=format_owner_number(refund.amount), currency=refund.currency,
            ),
            url=url_for("commercial_sales_web.refund_detail", refund_id=refund.id),
        ))

    events.sort(key=lambda e: e.timestamp, reverse=True)
    return events[:TIMELINE_LIMIT]
