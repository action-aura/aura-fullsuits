from __future__ import annotations

import pytest

from tests.conftest import make_staff


def _make_subscription(app, staff_id):
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription

    product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
    plan = Plan(plan_code="SUB1", product_id=product.id, name="SUB1", billing_model="MONTHLY", currency="USD")
    customer = Customer(legal_name="Sub Test Co")
    db_session.add_all([plan, customer])
    db_session.commit()
    return create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id}, staff_id)


def test_valid_transition_succeeds(app, seeded):
    staff_id = make_staff(app, "x@example.com")
    with app.app_context():
        from app.subscriptions.services import transition_subscription

        sub = _make_subscription(app, staff_id)
        transition_subscription(sub, "ACTIVE", staff_id)
        assert sub.status == "ACTIVE"


def test_invalid_transition_rejected(app, seeded):
    staff_id = make_staff(app, "x2@example.com")
    with app.app_context():
        from app.subscriptions.services import InvalidTransitionError, transition_subscription

        sub = _make_subscription(app, staff_id)
        with pytest.raises(InvalidTransitionError):
            transition_subscription(sub, "EXPIRED", staff_id)  # DRAFT -> EXPIRED is not allowed


def test_status_history_recorded(app, seeded):
    staff_id = make_staff(app, "x3@example.com")
    with app.app_context():
        from app.subscriptions.services import transition_subscription

        sub = _make_subscription(app, staff_id)
        transition_subscription(sub, "ACTIVE", staff_id, reason="pilot approved")
        assert len(sub.status_history) == 1
        assert sub.status_history[0].from_status == "DRAFT"
        assert sub.status_history[0].to_status == "ACTIVE"
        assert sub.status_history[0].reason == "pilot approved"


def test_cancellation_from_terminal_state_rejected(app, seeded):
    staff_id = make_staff(app, "x4@example.com")
    with app.app_context():
        from app.subscriptions.services import InvalidTransitionError, transition_subscription

        sub = _make_subscription(app, staff_id)
        transition_subscription(sub, "ACTIVE", staff_id)
        transition_subscription(sub, "CANCELLED", staff_id, reason="customer churned")
        with pytest.raises(InvalidTransitionError):
            transition_subscription(sub, "ACTIVE", staff_id)  # CANCELLED is terminal


def test_payment_status_validated(app, seeded):
    staff_id = make_staff(app, "x5@example.com")
    with app.app_context():
        from decimal import Decimal
        from datetime import date

        from app.subscriptions.services import record_payment

        sub = _make_subscription(app, staff_id)
        try:
            record_payment(
                {
                    "customer_id": sub.customer_id, "subscription_id": sub.id, "amount": Decimal("10"),
                    "currency": "USD", "payment_date": date.today(), "status": "NOT_A_STATUS",
                },
                staff_id,
            )
            assert False, "should have raised"
        except ValueError:
            pass
