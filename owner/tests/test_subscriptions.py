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


def test_correct_payment_writes_correction_history_row(app, seeded):
    # Phase 8 Part F: correcting a payment must create a correction record,
    # not just overwrite the live row -- see docs/owner/phase8/
    # phase8-scope-and-baseline.md's Milestone 1 gap note.
    staff_id = make_staff(app, "x6@example.com")
    with app.app_context():
        from decimal import Decimal
        from datetime import date

        from app.subscriptions.services import correct_payment, record_payment

        sub = _make_subscription(app, staff_id)
        payment = record_payment(
            {
                "customer_id": sub.customer_id, "subscription_id": sub.id, "amount": Decimal("10"),
                "currency": "USD", "payment_date": date.today(), "status": "PENDING",
                "internal_note": "awaiting bank confirmation",
            },
            staff_id,
        )

        correct_payment(payment, "CONFIRMED", "bank confirmed transfer", staff_id)

        assert payment.status == "CONFIRMED"
        assert payment.internal_note == "bank confirmed transfer"
        assert payment.verified_by_staff_user_id == staff_id

        assert len(payment.correction_history) == 1
        entry = payment.correction_history[0]
        assert entry.previous_status == "PENDING"
        assert entry.new_status == "CONFIRMED"
        assert entry.previous_internal_note == "awaiting bank confirmation"
        assert entry.correction_note == "bank confirmed transfer"
        assert entry.corrected_by_staff_user_id == staff_id


def test_correct_payment_twice_preserves_full_history_not_just_latest(app, seeded):
    staff_id = make_staff(app, "x7@example.com")
    with app.app_context():
        from decimal import Decimal
        from datetime import date

        from app.subscriptions.services import correct_payment, record_payment

        sub = _make_subscription(app, staff_id)
        payment = record_payment(
            {
                "customer_id": sub.customer_id, "subscription_id": sub.id, "amount": Decimal("10"),
                "currency": "USD", "payment_date": date.today(), "status": "PENDING",
            },
            staff_id,
        )

        correct_payment(payment, "CONFIRMED", "first correction", staff_id)
        correct_payment(payment, "REFUNDED", "customer requested refund", staff_id)

        assert payment.status == "REFUNDED"
        assert len(payment.correction_history) == 2
        assert payment.correction_history[0].previous_status == "PENDING"
        assert payment.correction_history[0].new_status == "CONFIRMED"
        assert payment.correction_history[1].previous_status == "CONFIRMED"
        assert payment.correction_history[1].new_status == "REFUNDED"


def test_correct_payment_invalid_status_still_rejected(app, seeded):
    staff_id = make_staff(app, "x8@example.com")
    with app.app_context():
        from decimal import Decimal
        from datetime import date

        from app.subscriptions.services import correct_payment, record_payment

        sub = _make_subscription(app, staff_id)
        payment = record_payment(
            {
                "customer_id": sub.customer_id, "subscription_id": sub.id, "amount": Decimal("10"),
                "currency": "USD", "payment_date": date.today(), "status": "PENDING",
            },
            staff_id,
        )
        with pytest.raises(ValueError):
            correct_payment(payment, "NOT_A_REAL_STATUS", "oops", staff_id)
        # No correction row written for a rejected correction attempt.
        assert len(payment.correction_history) == 0
