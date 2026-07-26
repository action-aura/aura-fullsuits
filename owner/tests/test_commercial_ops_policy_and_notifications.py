from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import make_staff


def test_create_commercial_policy_rejects_unsorted_offsets(app, seeded):
    staff_id = make_staff(app, "cp1@example.com")
    with app.app_context():
        from app.commercial_ops.commercial_policy import CommercialPolicyError, create_commercial_policy

        with pytest.raises(CommercialPolicyError):
            create_commercial_policy(
                policy_code="bad1", warning_offsets_days=[7, 30, 3], notify_role_codes=["SALES"],
                effective_date=date(2026, 1, 1), actor_staff_user_id=staff_id,
            )


def test_create_commercial_policy_rejects_negative_offsets(app, seeded):
    staff_id = make_staff(app, "cp2@example.com")
    with app.app_context():
        from app.commercial_ops.commercial_policy import CommercialPolicyError, create_commercial_policy

        with pytest.raises(CommercialPolicyError):
            create_commercial_policy(
                policy_code="bad2", warning_offsets_days=[30, -1], notify_role_codes=["SALES"],
                effective_date=date(2026, 1, 1), actor_staff_user_id=staff_id,
            )


def test_resolve_policy_prefers_product_specific_over_default(app, seeded):
    staff_id = make_staff(app, "cp3@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.commercial_ops.commercial_policy import create_commercial_policy, resolve_policy_for_subscription
        from app.models.catalog import Product
        from app.models.customers import Customer
        from app.models.catalog import Plan
        from app.subscriptions.services import create_subscription

        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        create_commercial_policy(
            policy_code="global-default-1", warning_offsets_days=[30, 7], notify_role_codes=["SALES"],
            effective_date=date(2026, 1, 1), actor_staff_user_id=staff_id,
        )
        specific = create_commercial_policy(
            policy_code="clinic-specific-1", warning_offsets_days=[14, 1], notify_role_codes=["SUPPORT"],
            effective_date=date(2026, 1, 1), actor_staff_user_id=staff_id, product_id=product.id,
        )
        plan = Plan(plan_code="CPTEST1", product_id=product.id, name="p", billing_model="MONTHLY", currency="USD")
        customer = Customer(legal_name="CP Test Co")
        db_session.add_all([plan, customer])
        db_session.commit()
        sub = create_subscription(
            {"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": date(2026, 8, 1)},
            staff_id,
        )
        resolved = resolve_policy_for_subscription(sub, as_of=date(2026, 7, 1))
        assert resolved.id == specific.id


def test_resolve_policy_falls_back_to_global_default(app, seeded):
    staff_id = make_staff(app, "cp4@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.commercial_ops.commercial_policy import create_commercial_policy, resolve_policy_for_subscription
        from app.models.catalog import Plan, Product
        from app.models.customers import Customer
        from app.subscriptions.services import create_subscription

        product = db_session.query(Product).filter_by(product_code="AURA_RETAIL").first()
        default_policy = create_commercial_policy(
            policy_code="global-default-2", warning_offsets_days=[30, 7], notify_role_codes=["SALES"],
            effective_date=date(2026, 1, 1), actor_staff_user_id=staff_id,
        )
        plan = Plan(plan_code="CPTEST2", product_id=product.id, name="p", billing_model="MONTHLY", currency="USD")
        customer = Customer(legal_name="CP Test Co 2")
        db_session.add_all([plan, customer])
        db_session.commit()
        sub = create_subscription(
            {"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": date(2026, 8, 1)},
            staff_id,
        )
        resolved = resolve_policy_for_subscription(sub, as_of=date(2026, 7, 1))
        assert resolved.id == default_policy.id


def test_resolve_policy_returns_none_when_no_policy_exists(app, seeded):
    staff_id = make_staff(app, "cp5@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.commercial_ops.commercial_policy import resolve_policy_for_subscription
        from app.models.catalog import Plan, Product
        from app.models.customers import Customer
        from app.subscriptions.services import create_subscription

        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        plan = Plan(plan_code="CPTEST3", product_id=product.id, name="p", billing_model="MONTHLY", currency="USD")
        customer = Customer(legal_name="CP Test Co 3")
        db_session.add_all([plan, customer])
        db_session.commit()
        sub = create_subscription(
            {"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": date(2026, 8, 1)},
            staff_id,
        )
        assert resolve_policy_for_subscription(sub, as_of=date(2026, 7, 1)) is None


def test_create_notification_idempotent_on_dedup_key(app, seeded):
    with app.app_context():
        from app.commercial_ops.commercial_policy import create_notification

        first, created1 = create_notification(
            notification_type="SUBSCRIPTION_EXPIRING_7_DAYS", severity="WARNING", title="t", message="m",
            dedup_key="dedupe-test-1",
        )
        second, created2 = create_notification(
            notification_type="SUBSCRIPTION_EXPIRING_7_DAYS", severity="WARNING", title="t", message="m",
            dedup_key="dedupe-test-1",
        )
        assert created1 is True
        assert created2 is False
        assert first.id == second.id


def test_acknowledge_and_resolve_notification(app, seeded):
    staff_id = make_staff(app, "cp6@example.com")
    with app.app_context():
        from app.commercial_ops.commercial_policy import (
            acknowledge_notification, create_notification, resolve_notification,
        )

        notification, _ = create_notification(
            notification_type="SUBSCRIPTION_EXPIRING_7_DAYS", severity="WARNING", title="t", message="m",
            dedup_key="ack-resolve-test",
        )
        acknowledge_notification(notification, staff_id)
        assert notification.status == "ACKNOWLEDGED"
        assert notification.acknowledged_by_staff_user_id == staff_id

        resolve_notification(notification, staff_id, "customer renewed")
        assert notification.status == "RESOLVED"
        assert notification.resolution == "customer renewed"


def test_resolve_already_resolved_notification_rejected(app, seeded):
    staff_id = make_staff(app, "cp7@example.com")
    with app.app_context():
        from app.commercial_ops.commercial_policy import NotificationError, create_notification, resolve_notification

        notification, _ = create_notification(
            notification_type="SUBSCRIPTION_EXPIRING_7_DAYS", severity="WARNING", title="t", message="m",
            dedup_key="double-resolve-test",
        )
        resolve_notification(notification, staff_id, "done")
        with pytest.raises(NotificationError):
            resolve_notification(notification, staff_id, "done again")


def test_assign_notification_moves_open_to_in_progress(app, seeded):
    staff_id = make_staff(app, "cp8@example.com")
    assignee_id = make_staff(app, "cp8b@example.com")
    with app.app_context():
        from app.commercial_ops.commercial_policy import assign_notification, create_notification

        notification, _ = create_notification(
            notification_type="SUBSCRIPTION_EXPIRING_7_DAYS", severity="WARNING", title="t", message="m",
            dedup_key="assign-test",
        )
        assert notification.status == "OPEN"
        assign_notification(notification, staff_id, assignee_id)
        assert notification.status == "IN_PROGRESS"
        assert notification.assigned_staff_user_id == assignee_id
