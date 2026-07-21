"""Internal dashboard aggregation queries (Part U). Commercial-metadata only --
never Retail sales/inventory or Clinic patient/medical data (Part W)."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

from app.extensions import db_session
from app.models.audit import AuditLog, DatabaseBackupRecord, SecurityEvent
from app.models.catalog import Product
from app.models.customers import Customer
from app.models.installations import Installation
from app.models.licensing import License
from app.models.subscriptions import Subscription


def get_dashboard_summary() -> dict:
    today = date.today()

    def count(stmt):
        return db_session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()

    total_customers = count(select(Customer).where(Customer.archived_at.is_(None)))
    pilot_customers = count(select(Customer).where(Customer.lifecycle_status == "PILOT"))
    active_customers = count(select(Customer).where(Customer.lifecycle_status == "ACTIVE"))
    active_subscriptions = count(select(Subscription).where(Subscription.status == "ACTIVE"))

    expiring_7 = count(
        select(Subscription).where(
            Subscription.status == "ACTIVE", Subscription.end_date.is_not(None),
            Subscription.end_date <= today + timedelta(days=7), Subscription.end_date >= today,
        )
    )
    expiring_30 = count(
        select(Subscription).where(
            Subscription.status == "ACTIVE", Subscription.end_date.is_not(None),
            Subscription.end_date <= today + timedelta(days=30), Subscription.end_date >= today,
        )
    )

    subs_by_product = dict(
        db_session.execute(
            select(Product.name, func.count(Subscription.id))
            .join(Subscription, Subscription.product_id == Product.id)
            .group_by(Product.name)
        ).all()
    )

    licenses_by_status = dict(
        db_session.execute(select(License.status, func.count(License.id)).group_by(License.status)).all()
    )

    active_installations_by_product = dict(
        db_session.execute(
            select(Product.name, func.count(Installation.id))
            .join(Installation, Installation.product_id == Product.id)
            .where(Installation.status == "ACTIVE")
            .group_by(Product.name)
        ).all()
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

    return {
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
    }
