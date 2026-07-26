from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import make_staff


def _subscription(app, staff_id, *, end_date=date(2026, 9, 1)):
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
    customer = Customer(legal_name="Queue Test Co")
    plan = Plan(plan_code=f"Q-{staff_id}-{end_date.isoformat()}", product_id=product.id, name="x", billing_model="MONTHLY", currency="USD")
    db_session.add_all([customer, plan])
    db_session.commit()
    sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": end_date}, staff_id)
    transition_subscription(sub, "ACTIVE", staff_id)
    return sub


def test_unknown_role_raises(app, seeded):
    with app.app_context():
        from app.commercial_ops.queues import UnknownQueueRoleError, get_queue_for_role

        with pytest.raises(UnknownQueueRoleError):
            get_queue_for_role("NOT_A_ROLE")


def test_sales_queue_shows_early_pipeline_renewal_not_finance_stage(app, seeded):
    creator_id = make_staff(app, "q1@example.com")
    with app.app_context():
        from app.commercial_ops.queues import get_queue_for_role
        from app.commercial_ops.renewal_requests import create_renewal_request

        sub = _subscription(app, creator_id)
        renewal = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 9, 1), proposed_term_end=date(2027, 9, 1),
            currency="USD", actor_staff_user_id=creator_id,
        )
        sales_snapshot = get_queue_for_role("SALES")
        finance_snapshot = get_queue_for_role("FINANCE")

        sales_ids = [item["id"] for item in sales_snapshot.items["renewals_in_sales_pipeline"]]
        assert str(renewal.id) in sales_ids
        finance_ids = [item["id"] for item in finance_snapshot.items["renewals_awaiting_finance_approval"]]
        assert str(renewal.id) not in finance_ids


def test_finance_queue_shows_payment_recorded_renewal(app, seeded):
    creator_id = make_staff(app, "q2@example.com")
    with app.app_context():
        from app.commercial_ops.queues import get_queue_for_role
        from app.commercial_ops.renewal_requests import create_renewal_request, transition_renewal_request

        sub = _subscription(app, creator_id)
        renewal = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 9, 1), proposed_term_end=date(2027, 9, 1),
            currency="USD", actor_staff_user_id=creator_id,
        )
        transition_renewal_request(renewal, "QUOTED", creator_id)
        transition_renewal_request(renewal, "AWAITING_CONFIRMATION", creator_id)
        transition_renewal_request(renewal, "AWAITING_PAYMENT", creator_id)
        transition_renewal_request(renewal, "PAYMENT_RECORDED", creator_id)

        finance_snapshot = get_queue_for_role("FINANCE")
        finance_ids = [item["id"] for item in finance_snapshot.items["renewals_awaiting_finance_approval"]]
        assert str(renewal.id) in finance_ids
        sales_snapshot = get_queue_for_role("SALES")
        sales_ids = [item["id"] for item in sales_snapshot.items["renewals_in_sales_pipeline"]]
        assert str(renewal.id) not in sales_ids


def test_super_admin_queue_is_the_union(app, seeded):
    creator_id = make_staff(app, "q3@example.com")
    with app.app_context():
        from app.commercial_ops.queues import get_queue_for_role
        from app.commercial_ops.renewal_requests import create_renewal_request

        sub = _subscription(app, creator_id)
        renewal = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 9, 1), proposed_term_end=date(2027, 9, 1),
            currency="USD", actor_staff_user_id=creator_id,
        )
        super_admin_snapshot = get_queue_for_role("SUPER_ADMIN")
        ids = [item["id"] for item in super_admin_snapshot.items["renewals_in_sales_pipeline"]]
        assert str(renewal.id) in ids
        assert set(super_admin_snapshot.items.keys()) >= {
            "notifications", "renewals_in_sales_pipeline", "renewals_awaiting_finance_approval",
            "pilots_needing_action", "pending_activations", "active_emergency_extensions",
            "active_device_slot_exceptions",
        }


def test_viewer_queue_has_counts_but_no_items(app, seeded):
    with app.app_context():
        from app.commercial_ops.queues import get_queue_for_role

        snapshot = get_queue_for_role("VIEWER")
        assert snapshot.items == {}
        assert "renewals_in_sales_pipeline" in snapshot.counts
        assert isinstance(snapshot.counts["renewals_in_sales_pipeline"], int)


def test_support_queue_shows_pending_activation(app, seeded):
    staff_id = make_staff(app, "q4@example.com")
    with app.app_context():
        from app.commercial_ops.activation_policy import create_pending_activation
        from app.commercial_ops.queues import get_queue_for_role
        from app.extensions import db_session
        from app.installations.services import register_installation
        from app.licensing.services import create_license, transition_license
        from app.models.catalog import Plan, Platform, Product
        from app.models.customers import Customer
        from app.subscriptions.services import create_subscription, transition_subscription

        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        platform = db_session.query(Platform).filter_by(platform_code="WINDOWS").first()
        customer = Customer(legal_name="Support Queue Co")
        plan = Plan(plan_code=f"Q4-{staff_id}", product_id=product.id, name="x", billing_model="MONTHLY", currency="USD")
        db_session.add_all([customer, plan])
        db_session.commit()
        sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id}, staff_id)
        transition_subscription(sub, "ACTIVE", staff_id)
        lic = create_license(
            {"customer_id": customer.id, "subscription_id": sub.id, "product_id": product.id, "plan_id": plan.id,
             "allowed_platforms": "WINDOWS,ANDROID", "device_limit": 1}, staff_id,
        )
        transition_license(lic, "ISSUED", staff_id)
        transition_license(lic, "ACTIVE", staff_id)
        installation = register_installation(
            {"customer_id": customer.id, "subscription_id": sub.id, "license_id": lic.id, "product_id": product.id,
             "platform_id": platform.id, "installation_label": "q4-dev"},
            staff_id,
        )
        installation.status = "PENDING_ACTIVATION"
        db_session.commit()
        pending = create_pending_activation(
            installation=installation, license_id=lic.id, product_id=product.id, platform_id=platform.id, mode="MANUAL_APPROVAL",
        )

        support_snapshot = get_queue_for_role("SUPPORT")
        ids = [item["id"] for item in support_snapshot.items["pending_activations"]]
        assert str(pending.id) in ids
