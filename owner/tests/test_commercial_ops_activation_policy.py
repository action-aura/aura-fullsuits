from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import make_staff


def _product_id(app, code="AURA_CLINIC"):
    from app.extensions import db_session
    from app.models.catalog import Product

    return db_session.query(Product).filter_by(product_code=code).first().id


# -- resolve_activation_mode --------------------------------------------------

def test_resolve_activation_mode_defaults_to_automatic_when_unconfigured(app, seeded):
    with app.app_context():
        from app.commercial_ops.activation_policy import resolve_activation_mode

        assert resolve_activation_mode(_product_id(app)) == "AUTOMATIC"


def test_resolve_activation_mode_product_specific_overrides_global_default(app, seeded):
    staff_id = make_staff(app, "ap1@example.com")
    with app.app_context():
        from app.commercial_ops.activation_policy import create_activation_policy, resolve_activation_mode

        create_activation_policy(
            policy_code="global-manual", mode="MANUAL_APPROVAL", effective_date=date(2026, 1, 1), actor_staff_user_id=staff_id,
        )
        pid = _product_id(app)
        create_activation_policy(
            policy_code="clinic-automatic", mode="AUTOMATIC", effective_date=date(2026, 1, 1),
            actor_staff_user_id=staff_id, product_id=pid,
        )
        assert resolve_activation_mode(pid) == "AUTOMATIC"
        assert resolve_activation_mode(_product_id(app, "AURA_RETAIL")) == "MANUAL_APPROVAL"


def test_create_activation_policy_rejects_unknown_mode(app, seeded):
    staff_id = make_staff(app, "ap2@example.com")
    with app.app_context():
        from app.commercial_ops.activation_policy import ActivationPolicyError, create_activation_policy

        with pytest.raises(ActivationPolicyError):
            create_activation_policy(
                policy_code="bad-mode", mode="SOMETHING_ELSE", effective_date=date(2026, 1, 1), actor_staff_user_id=staff_id,
            )


# -- pending activation approve/reject ----------------------------------------

def _pending_installation(app, staff_id, *, device_limit=1):
    from app.extensions import db_session
    from app.installations.services import register_installation
    from app.licensing.services import create_license, transition_license
    from app.models.catalog import Plan, Platform, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
    platform = db_session.query(Platform).filter_by(platform_code="WINDOWS").first()
    customer = Customer(legal_name="Pending Activation Co")
    plan = Plan(plan_code=f"PA-{staff_id}", product_id=product.id, name="x", billing_model="MONTHLY", currency="USD")
    db_session.add_all([customer, plan])
    db_session.commit()

    sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id}, staff_id)
    transition_subscription(sub, "ACTIVE", staff_id)
    lic = create_license(
        {"customer_id": customer.id, "subscription_id": sub.id, "product_id": product.id, "plan_id": plan.id,
         "allowed_platforms": "WINDOWS,ANDROID", "device_limit": device_limit}, staff_id,
    )
    transition_license(lic, "ISSUED", staff_id)
    transition_license(lic, "ACTIVE", staff_id)
    installation = register_installation(
        {"customer_id": customer.id, "subscription_id": sub.id, "license_id": lic.id, "product_id": product.id,
         "platform_id": platform.id, "installation_label": "pending-dev-1"},
        staff_id,
    )
    installation.status = "PENDING_ACTIVATION"
    db_session.commit()
    return installation, lic


def test_approve_pending_activation_flips_installation_active(app, seeded):
    creator_id = make_staff(app, "papp1-creator@example.com")
    approver_id = make_staff(app, "papp1-approver@example.com")
    with app.app_context():
        from app.commercial_ops.activation_policy import approve_pending_activation, create_pending_activation

        installation, lic = _pending_installation(app, creator_id)
        pending = create_pending_activation(
            installation=installation, license_id=lic.id, product_id=installation.product_id,
            platform_id=installation.platform_id, mode="MANUAL_APPROVAL",
        )
        approve_pending_activation(pending, approver_id)
        assert installation.status == "ACTIVE"
        assert pending.status == "APPROVED"
        assert pending.decided_by_staff_user_id == approver_id


def test_approve_pending_activation_rechecks_device_limit(app, seeded):
    staff_id = make_staff(app, "papp2@example.com")
    with app.app_context():
        from app.commercial_ops.activation_policy import PendingActivationError, approve_pending_activation, create_pending_activation
        from app.extensions import db_session
        from app.installations.services import register_installation

        installation, lic = _pending_installation(app, staff_id)
        pending = create_pending_activation(
            installation=installation, license_id=lic.id, product_id=installation.product_id,
            platform_id=installation.platform_id, mode="MANUAL_APPROVAL",
        )
        # Consume the license's one and only device slot with a second,
        # already-active installation before the review lands.
        other = register_installation(
            {"customer_id": lic.customer_id, "license_id": lic.id, "product_id": installation.product_id,
             "platform_id": installation.platform_id, "installation_label": "other-dev"},
            staff_id,
        )
        other.status = "ACTIVE"
        db_session.commit()

        with pytest.raises(PendingActivationError):
            approve_pending_activation(pending, staff_id)


def test_reject_pending_activation_requires_reason_and_frees_slot(app, seeded):
    staff_id = make_staff(app, "prej1@example.com")
    with app.app_context():
        from app.commercial_ops.activation_policy import PendingActivationError, create_pending_activation, reject_pending_activation

        installation, lic = _pending_installation(app, staff_id)
        pending = create_pending_activation(
            installation=installation, license_id=lic.id, product_id=installation.product_id,
            platform_id=installation.platform_id, mode="MANUAL_APPROVAL",
        )
        with pytest.raises(PendingActivationError):
            reject_pending_activation(pending, staff_id, reason="")
        reject_pending_activation(pending, staff_id, reason="Suspicious device fingerprint")
        assert pending.status == "REJECTED"
        assert installation.status == "DEACTIVATED"


def test_create_pending_activation_is_idempotent_per_installation(app, seeded):
    staff_id = make_staff(app, "pidem1@example.com")
    with app.app_context():
        from app.commercial_ops.activation_policy import create_pending_activation

        installation, lic = _pending_installation(app, staff_id)
        first = create_pending_activation(
            installation=installation, license_id=lic.id, product_id=installation.product_id,
            platform_id=installation.platform_id, mode="MANUAL_APPROVAL",
        )
        second = create_pending_activation(
            installation=installation, license_id=lic.id, product_id=installation.product_id,
            platform_id=installation.platform_id, mode="MANUAL_APPROVAL",
        )
        assert first.id == second.id
