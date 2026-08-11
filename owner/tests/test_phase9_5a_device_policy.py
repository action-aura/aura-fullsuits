from __future__ import annotations

import uuid as uuid_mod
from datetime import date, datetime, timedelta

from tests.conftest import make_staff


def _make_plan_and_subscription(app, staff_id):
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription

    product = db_session.execute(
        __import__("sqlalchemy").select(Product).where(Product.product_code == "AURA_CLINIC")
    ).scalars().first()
    plan = Plan(
        plan_code=f"DP-PLAN-{uuid_mod.uuid4().hex[:8]}", product_id=product.id, name="Device Policy Test Plan",
        billing_model="PILOT", currency="USD",
    )
    customer = Customer(legal_name=f"Device Policy Co {uuid_mod.uuid4().hex[:8]}")
    db_session.add_all([plan, customer])
    db_session.commit()

    sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id}, staff_id)
    return plan, sub


def test_resolve_device_policy_returns_open_policy_when_no_row_exists(app, seeded):
    staff_id = make_staff(app, "dp1@example.com")
    with app.app_context():
        from app.commercial_sales.device_policy import resolve_device_policy

        _, sub = _make_plan_and_subscription(app, staff_id)
        policy = resolve_device_policy(sub)
        assert policy.max_total_devices is None
        assert policy.platform_rule_map == {}


def test_resolve_device_policy_applies_plan_profile_and_platform_rules(app, seeded):
    staff_id = make_staff(app, "dp2@example.com")
    with app.app_context():
        from app.commercial_sales.device_policy import resolve_device_policy
        from app.extensions import db_session
        from app.models.activation_governance import DevicePolicyPlatformRule, DevicePolicyProfile

        plan, sub = _make_plan_and_subscription(app, staff_id)
        profile = DevicePolicyProfile(
            profile_code=f"DPP-{uuid_mod.uuid4().hex[:8]}", plan_id=plan.id, max_total_devices=2,
            effective_date=date(2026, 1, 1), created_by_staff_user_id=staff_id,
        )
        db_session.add(profile)
        db_session.flush()
        db_session.add_all(
            [
                DevicePolicyPlatformRule(device_policy_profile_id=profile.id, platform_category="WINDOWS", max_devices=1),
                DevicePolicyPlatformRule(device_policy_profile_id=profile.id, platform_category="MOBILE", max_devices=1),
            ]
        )
        db_session.commit()

        policy = resolve_device_policy(sub, as_of=datetime(2026, 6, 1))
        assert policy.max_total_devices == 2
        assert policy.platform_rule_map == {"WINDOWS": 1, "MOBILE": 1}


def test_resolve_device_policy_active_override_wins_over_plan_default(app, seeded):
    staff_id = make_staff(app, "dp3@example.com")
    with app.app_context():
        from app.commercial_sales.device_policy import resolve_device_policy
        from app.extensions import db_session
        from app.models.activation_governance import DevicePolicyProfile, SubscriptionDevicePolicyOverride

        plan, sub = _make_plan_and_subscription(app, staff_id)
        profile = DevicePolicyProfile(
            profile_code=f"DPP-{uuid_mod.uuid4().hex[:8]}", plan_id=plan.id, max_total_devices=2,
            effective_date=date(2026, 1, 1), created_by_staff_user_id=staff_id,
        )
        db_session.add(profile)
        db_session.commit()

        now = datetime(2026, 6, 1)
        override = SubscriptionDevicePolicyOverride(
            subscription_id=sub.id, max_total_devices=5, reason="fleet expansion approved",
            approved_by_staff_user_id=staff_id, effective_from=now - timedelta(days=1),
            effective_until=now + timedelta(days=30),
        )
        db_session.add(override)
        db_session.commit()

        assert resolve_device_policy(sub, as_of=now).max_total_devices == 5
        # Outside the override window -- falls back to the plan default.
        assert resolve_device_policy(sub, as_of=now + timedelta(days=60)).max_total_devices == 2
