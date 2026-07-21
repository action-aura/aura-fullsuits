from __future__ import annotations

import uuid

import pytest

from tests.conftest import make_staff


def _make_license(app, staff_id):
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.licensing.services import create_license, issue_license_key
    from app.subscriptions.services import create_subscription

    product = db_session.query(Product).filter_by(product_code="AURA_RETAIL").first()
    plan = Plan(plan_code="INST1", product_id=product.id, name="INST1", billing_model="MONTHLY", currency="USD")
    customer = Customer(legal_name="Installation Test Co")
    db_session.add_all([plan, customer])
    db_session.commit()
    sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id}, staff_id)
    license_row = create_license(
        {"customer_id": customer.id, "subscription_id": sub.id, "product_id": product.id, "plan_id": plan.id,
         "allowed_platforms": "WINDOWS", "device_limit": 1},
        staff_id,
    )
    issue_license_key(license_row, "test-pepper", str(uuid.uuid4()), staff_id)
    return license_row, product


def test_register_installation_no_raw_hardware_id_columns(app, seeded):
    staff_id = make_staff(app, "i1@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.installations.services import register_installation
        from app.models.catalog import Platform

        license_row, product = _make_license(app, staff_id)
        platform = db_session.query(Platform).filter_by(platform_code="WINDOWS").first()
        installation = register_installation(
            {
                "customer_id": license_row.customer_id, "subscription_id": license_row.subscription_id,
                "license_id": license_row.id, "product_id": product.id, "platform_id": platform.id,
                "installation_label": "Test PC",
            },
            staff_id,
        )
        assert installation.status == "REGISTERED"
        from app.models.installations import Installation

        column_names = {c.name for c in Installation.__table__.columns}
        assert "imei" not in column_names
        assert "mac_address" not in column_names
        assert "geolocation" not in column_names


def test_valid_and_invalid_transitions(app, seeded):
    from app.installations.services import (
        InvalidInstallationTransitionError,
        register_installation,
        transition_installation,
    )

    staff_id = make_staff(app, "i2@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.models.catalog import Platform

        license_row, product = _make_license(app, staff_id)
        platform = db_session.query(Platform).filter_by(platform_code="WINDOWS").first()
        installation = register_installation(
            {"customer_id": license_row.customer_id, "subscription_id": license_row.subscription_id,
             "license_id": license_row.id, "product_id": product.id, "platform_id": platform.id},
            staff_id,
        )
        transition_installation(installation, "ACTIVE", staff_id)
        assert installation.status == "ACTIVE"
        with pytest.raises(InvalidInstallationTransitionError):
            transition_installation(installation, "REGISTERED", staff_id)  # cannot go backwards


def test_activation_event_recorded_on_registration(app, seeded):
    staff_id = make_staff(app, "i3@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.installations.services import register_installation
        from app.models.catalog import Platform
        from app.models.installations import ActivationEvent

        license_row, product = _make_license(app, staff_id)
        platform = db_session.query(Platform).filter_by(platform_code="WINDOWS").first()
        installation = register_installation(
            {"customer_id": license_row.customer_id, "subscription_id": license_row.subscription_id,
             "license_id": license_row.id, "product_id": product.id, "platform_id": platform.id},
            staff_id,
        )
        events = db_session.query(ActivationEvent).filter_by(installation_id=installation.id).all()
        assert len(events) == 1
        assert events[0].event_type == "DEVICE_REGISTERED"
