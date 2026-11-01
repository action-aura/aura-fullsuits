"""Internal dashboard aggregation queries (Part U). Commercial-metadata only --
never Retail sales/inventory or Clinic patient/medical data (Part W)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.extensions import db_session
from app.models.activation_governance import DeviceSlotException, PendingActivation
from app.models.audit import AuditLog, DatabaseBackupRecord, SecurityEvent
from app.models.catalog import Product
from app.models.commercial_ops import EmergencyExtension, InternalNotification, PilotRecord, RenewalRequest
from app.models.customers import Customer
from app.models.installations import Installation
from app.models.licensing import License
from app.models.subscriptions import Subscription
from app.operational_reports.periods import jordan_today


def sees_own_customers_only(actor, codes) -> bool:
    """A seller (customers.view_own without customers.view_all) sees the dashboard
    for THEIR customers, not the company. The same ownership rule as the customer
    list (customers.services.customer_visible_to_actor): the customer is assigned
    to them. Rehearsal 2026-10-31: a seller's dashboard showed the whole company's
    customer, subscription, licence and installation totals."""
    return (
        actor is not None and codes is not None
        and "customers.view_own" in codes and "customers.view_all" not in codes
    )


def get_dashboard_summary(actor=None, codes=None) -> dict:
    today = jordan_today()  # Jordan's day (Step 0 fix round): was the server's calendar
    scoped = sees_own_customers_only(actor, codes)
    # The customers this viewer may count; None = the whole company (managers, finance, ...).
    own_ids = (
        select(Customer.id).where(Customer.assigned_sales_staff_id == actor.id, Customer.archived_at.is_(None))
        if scoped else None
    )

    def own(stmt, customer_column):
        return stmt if own_ids is None else stmt.where(customer_column.in_(own_ids))

    def count(stmt):
        return db_session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()

    total_customers = count(own(select(Customer).where(Customer.archived_at.is_(None)), Customer.id))
    pilot_customers = count(own(select(Customer).where(Customer.lifecycle_status == "PILOT"), Customer.id))
    active_customers = count(own(select(Customer).where(Customer.lifecycle_status == "ACTIVE"), Customer.id))
    active_subscriptions = count(own(select(Subscription).where(Subscription.status == "ACTIVE"), Subscription.customer_id))

    expiring_7 = count(own(
        select(Subscription).where(
            Subscription.status == "ACTIVE", Subscription.end_date.is_not(None),
            Subscription.end_date <= today + timedelta(days=7), Subscription.end_date >= today,
        ), Subscription.customer_id,
    ))
    expiring_30 = count(own(
        select(Subscription).where(
            Subscription.status == "ACTIVE", Subscription.end_date.is_not(None),
            Subscription.end_date <= today + timedelta(days=30), Subscription.end_date >= today,
        ), Subscription.customer_id,
    ))

    subs_by_product = dict(
        db_session.execute(own(
            select(Product.name, func.count(Subscription.id))
            .join(Subscription, Subscription.product_id == Product.id), Subscription.customer_id,
        ).group_by(Product.name)).all()
    )

    licenses_by_status = dict(
        db_session.execute(own(select(License.status, func.count(License.id)), License.customer_id).group_by(License.status)).all()
    )

    active_installations_by_product = dict(
        db_session.execute(own(
            select(Product.name, func.count(Installation.id))
            .join(Installation, Installation.product_id == Product.id)
            .where(Installation.status == "ACTIVE"), Installation.customer_id,
        ).group_by(Product.name)).all()
    )

    recent_security_events = db_session.execute(
        select(SecurityEvent).order_by(SecurityEvent.created_at.desc()).limit(10)
    ).scalars().all()

    recent_staff_actions = db_session.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(10)
    ).scalars().all()

    latest_backup = db_session.execute(
        select(DatabaseBackupRecord).order_by(DatabaseBackupRecord.created_at.desc()).limit(1)
    ).scalars().first()

    # Phase 8 -- commercial-operations metadata only (counts, never
    # per-customer financial detail or PII beyond what the rest of this
    # summary already surfaces): open notifications, workflow items
    # awaiting a human decision, and active exceptional-override counts.
    #
    # The counts that belong to a customer follow the same scope as above. The ones that
    # do not (notifications, manual-review activations, emergency extensions, device-slot
    # exceptions) are company-wide queues a seller has no share of: they are left at 0
    # and `scoped` tells the template not to draw those tiles at all.
    if scoped:
        open_notifications = pending_activations = active_emergency_extensions = active_device_slot_exceptions = 0
    else:
        open_notifications = count(select(InternalNotification).where(InternalNotification.status.in_(("OPEN", "IN_PROGRESS"))))
        pending_activations = count(select(PendingActivation).where(PendingActivation.status == "PENDING_REVIEW"))
        active_emergency_extensions = count(select(EmergencyExtension).where(EmergencyExtension.status == "ACTIVE"))
        active_device_slot_exceptions = count(select(DeviceSlotException).where(DeviceSlotException.status == "ACTIVE"))
    renewals_awaiting_approval = count(own(select(RenewalRequest).where(RenewalRequest.status == "PAYMENT_RECORDED"), RenewalRequest.customer_id))
    pilots_needing_action = count(own(select(PilotRecord).where(PilotRecord.status.in_(("DRAFT", "APPROVED"))), PilotRecord.customer_id))
    # Seller agreement clause 5: sellers' objections to their statements waiting for a reply,
    # and the ones past their 2-working-day deadline (flagged in red on the manager dashboard).
    from app.commissions.statements import open_and_overdue_counts

    # A company-wide queue like the ones above: a seller sees only their own numbers
    # (review r33, 2026-11-01 -- a seller's dashboard still carried every seller's count).
    open_objections, overdue_objections = (0, 0) if scoped else open_and_overdue_counts()

    # Seller agreement clause 7: a seller sees THEIR OWN cash to hand in; a payments.confirm holder sees
    # the company-wide numbers (still owed, overdue). Nobody sees a number they have no use for.
    from app.sellers import cash_handin

    cash_mine = cash_handin.tile_for_seller(actor.id) if actor is not None and codes and "sellers.self_service" in codes else None
    cash_owed, cash_overdue = (
        cash_handin.counts_for_managers() if actor is not None and codes and "payments.confirm" in codes else (None, None)
    )

    # Clause 8: a seller's own customers whose 15 days end within 3 days (soonest first).
    from app.sellers import ownership

    expiring_soon = (
        [{"id": c.id, "name": c.legal_name, "ends": e} for c, e in ownership.expiring_soon_for(actor.id)]
        if actor is not None and codes and "sellers.self_service" in codes else None
    )

    return {
        "scoped": scoped,
        "expiring_soon": expiring_soon,
        "cash_mine": cash_mine,
        "cash_owed_all": cash_owed,
        "cash_overdue_all": cash_overdue,
        "total_customers": total_customers,
        "pilot_customers": pilot_customers,
        "active_customers": active_customers,
        "active_subscriptions": active_subscriptions,
        "expiring_7": expiring_7,
        "expiring_30": expiring_30,
        "subs_by_product": subs_by_product,
        "licenses_by_status": licenses_by_status,
        "active_installations_by_product": active_installations_by_product,
        "recent_security_events": recent_security_events,
        "recent_staff_actions": recent_staff_actions,
        "latest_backup": latest_backup,
        "open_notifications": open_notifications,
        "renewals_awaiting_approval": renewals_awaiting_approval,
        "pilots_needing_action": pilots_needing_action,
        "pending_activations": pending_activations,
        "active_emergency_extensions": active_emergency_extensions,
        "active_device_slot_exceptions": active_device_slot_exceptions,
        "open_statement_objections": open_objections,
        "overdue_statement_objections": overdue_objections,
    }
