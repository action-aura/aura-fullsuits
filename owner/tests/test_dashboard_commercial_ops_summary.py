from __future__ import annotations

from datetime import date

from tests.conftest import make_staff


def test_dashboard_summary_includes_commercial_ops_counts(app, seeded):
    staff_id = make_staff(app, "dash1@example.com")
    with app.app_context():
        from app.dashboard.services import get_dashboard_summary

        summary = get_dashboard_summary()
        for key in (
            "open_notifications", "renewals_awaiting_approval", "pilots_needing_action",
            "pending_activations", "active_emergency_extensions", "active_device_slot_exceptions",
        ):
            assert key in summary
            assert isinstance(summary[key], int)


def test_dashboard_summary_reflects_payment_recorded_renewal(app, seeded):
    creator_id = make_staff(app, "dash2@example.com")
    with app.app_context():
        from app.commercial_ops.renewal_requests import create_renewal_request, transition_renewal_request
        from app.dashboard.services import get_dashboard_summary
        from app.extensions import db_session
        from app.models.catalog import Plan, Product
        from app.models.customers import Customer
        from app.subscriptions.services import create_subscription, transition_subscription

        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        customer = Customer(legal_name="Dashboard Test Co")
        plan = Plan(plan_code=f"DASH-{creator_id}", product_id=product.id, name="x", billing_model="MONTHLY", currency="USD")
        db_session.add_all([customer, plan])
        db_session.commit()
        sub = create_subscription(
            {"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": date(2026, 9, 1)}, creator_id,
        )
        transition_subscription(sub, "ACTIVE", creator_id)

        before = get_dashboard_summary()["renewals_awaiting_approval"]

        renewal = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 9, 1), proposed_term_end=date(2027, 9, 1),
            currency="USD", actor_staff_user_id=creator_id,
        )
        transition_renewal_request(renewal, "QUOTED", creator_id)
        transition_renewal_request(renewal, "AWAITING_CONFIRMATION", creator_id)
        transition_renewal_request(renewal, "AWAITING_PAYMENT", creator_id)
        transition_renewal_request(renewal, "PAYMENT_RECORDED", creator_id)

        after = get_dashboard_summary()["renewals_awaiting_approval"]
        assert after == before + 1
