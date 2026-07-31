from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from tests.conftest import make_license, make_staff


def _make_subscription(app, staff_id, *, status="EXPIRED", end_date=date(2026, 7, 1)):
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
    plan = Plan(
        plan_code=f"EMERG-{staff_id}-{end_date.isoformat()}",
        product_id=product.id, name="Emergency Test Plan", billing_model="MONTHLY", currency="USD",
    )
    customer = Customer(legal_name="Emergency Test Co")
    db_session.add_all([plan, customer])
    db_session.commit()
    sub = create_subscription(
        {"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": end_date},
        staff_id,
    )
    transition_subscription(sub, "ACTIVE", staff_id)
    if status != "ACTIVE":
        transition_subscription(sub, status, staff_id, reason="test setup")
    return sub


# -- create_emergency_extension ----------------------------------------------

def test_create_emergency_extension_success(app, seeded):
    staff_id = make_staff(app, "ee1@example.com")
    with app.app_context():
        from app.commercial_ops.emergency_extensions import create_emergency_extension

        sub = _make_subscription(app, staff_id)
        ext = create_emergency_extension(
            subscription=sub, reason="Customer site outage, awaiting payment confirmation",
            duration_hours=24, actor_staff_user_id=staff_id, incident_reference="INC-1001",
        )
        assert ext.status == "ACTIVE"
        assert ext.subscription_id == sub.id
        assert ext.expires_at - ext.starts_at == timedelta(hours=24)


def test_create_emergency_extension_default_now_is_real_utc_not_shifted(app, seeded):
    # Phase 8V-P5: found by real physical validation against a real
    # non-UTC-session Postgres (Asia/Amman, +3) -- create_emergency_extension()
    # used to default `now` to the naive, deprecated datetime.utcnow(), which
    # a DateTime(timezone=True) column silently re-localizes to the DB
    # session's own timezone on write, shifting the real stored instant by
    # the session's UTC offset. A 3-minute extension created this way could
    # already be expired by the time a caller read it back. This regression
    # exercises the actual default-`now` code path (never passing `now=`
    # explicitly, since that's what a real caller does) and asserts the
    # stored starts_at is genuinely close to real UTC now, not hours off.
    from datetime import timezone

    staff_id = make_staff(app, "ee-tz@example.com")
    with app.app_context():
        from app.commercial_ops.emergency_extensions import create_emergency_extension

        sub = _make_subscription(app, staff_id)
        real_utc_before = datetime.now(timezone.utc)
        ext = create_emergency_extension(
            subscription=sub, reason="Timezone regression check",
            duration_hours=1, actor_staff_user_id=staff_id, incident_reference="INC-TZ-1",
        )
        real_utc_after = datetime.now(timezone.utc)

        assert ext.starts_at.tzinfo is not None
        # The stored instant must fall within the real wall-clock window this
        # test actually ran in -- a multi-hour timezone-shift bug would place
        # it far outside this window.
        assert real_utc_before <= ext.starts_at <= real_utc_after
        assert real_utc_before + timedelta(hours=1) <= ext.expires_at <= real_utc_after + timedelta(hours=1)


def test_create_emergency_extension_requires_reason(app, seeded):
    staff_id = make_staff(app, "ee2@example.com")
    with app.app_context():
        from app.commercial_ops.emergency_extensions import EmergencyExtensionError, create_emergency_extension

        sub = _make_subscription(app, staff_id)
        with pytest.raises(EmergencyExtensionError):
            create_emergency_extension(subscription=sub, reason="  ", duration_hours=12, actor_staff_user_id=staff_id)


def test_create_emergency_extension_enforces_hard_cap(app, seeded):
    staff_id = make_staff(app, "ee3@example.com")
    with app.app_context():
        from app.commercial_ops.emergency_extensions import (
            MAX_EMERGENCY_EXTENSION_HOURS,
            EmergencyExtensionError,
            create_emergency_extension,
        )

        sub = _make_subscription(app, staff_id)
        with pytest.raises(EmergencyExtensionError):
            create_emergency_extension(
                subscription=sub, reason="too long", duration_hours=MAX_EMERGENCY_EXTENSION_HOURS + 1,
                actor_staff_user_id=staff_id,
            )
        with pytest.raises(EmergencyExtensionError):
            create_emergency_extension(subscription=sub, reason="zero", duration_hours=0, actor_staff_user_id=staff_id)


def test_create_emergency_extension_rejects_revoked_license(app, seeded):
    staff_id = make_staff(app, "ee4@example.com")
    with app.app_context():
        from app.commercial_ops.emergency_extensions import EmergencyExtensionError, create_emergency_extension
        from app.extensions import db_session
        from app.licensing.services import transition_license
        from app.models.licensing import License

        sub = _make_subscription(app, staff_id)
        lic_id, _ = make_license(app, staff_id)
        lic = db_session.get(License, lic_id)
        transition_license(lic, "ACTIVE", staff_id)
        transition_license(lic, "REVOKED", staff_id, reason="fraud")
        with pytest.raises(EmergencyExtensionError):
            create_emergency_extension(
                subscription=sub, reason="attempt override", duration_hours=12, actor_staff_user_id=staff_id, license=lic,
            )


def test_create_emergency_extension_no_stacking(app, seeded):
    staff_id = make_staff(app, "ee5@example.com")
    with app.app_context():
        from app.commercial_ops.emergency_extensions import EmergencyExtensionError, create_emergency_extension

        sub = _make_subscription(app, staff_id)
        create_emergency_extension(subscription=sub, reason="first", duration_hours=12, actor_staff_user_id=staff_id)
        with pytest.raises(EmergencyExtensionError):
            create_emergency_extension(subscription=sub, reason="second", duration_hours=12, actor_staff_user_id=staff_id)


def test_new_extension_allowed_after_previous_revoked(app, seeded):
    staff_id = make_staff(app, "ee6@example.com")
    with app.app_context():
        from app.commercial_ops.emergency_extensions import create_emergency_extension, revoke_emergency_extension

        sub = _make_subscription(app, staff_id)
        first = create_emergency_extension(subscription=sub, reason="first", duration_hours=12, actor_staff_user_id=staff_id)
        revoke_emergency_extension(first, reason="resolved early", actor_staff_user_id=staff_id)
        second = create_emergency_extension(subscription=sub, reason="second", duration_hours=12, actor_staff_user_id=staff_id)
        assert second.status == "ACTIVE"
        assert second.id != first.id


# -- revoke_emergency_extension ------------------------------------------------

def test_revoke_emergency_extension_requires_reason(app, seeded):
    staff_id = make_staff(app, "ee7@example.com")
    with app.app_context():
        from app.commercial_ops.emergency_extensions import EmergencyExtensionError, create_emergency_extension, revoke_emergency_extension

        sub = _make_subscription(app, staff_id)
        ext = create_emergency_extension(subscription=sub, reason="first", duration_hours=12, actor_staff_user_id=staff_id)
        with pytest.raises(EmergencyExtensionError):
            revoke_emergency_extension(ext, reason="", actor_staff_user_id=staff_id)


def test_revoke_emergency_extension_rejects_already_revoked(app, seeded):
    staff_id = make_staff(app, "ee8@example.com")
    with app.app_context():
        from app.commercial_ops.emergency_extensions import EmergencyExtensionError, create_emergency_extension, revoke_emergency_extension

        sub = _make_subscription(app, staff_id)
        ext = create_emergency_extension(subscription=sub, reason="first", duration_hours=12, actor_staff_user_id=staff_id)
        revoke_emergency_extension(ext, reason="done", actor_staff_user_id=staff_id)
        with pytest.raises(EmergencyExtensionError):
            revoke_emergency_extension(ext, reason="again", actor_staff_user_id=staff_id)


# -- is_emergency_extension_active + resolve_commercial_state override -------

def test_is_emergency_extension_active_reflects_status_and_expiry(app, seeded):
    staff_id = make_staff(app, "ee9@example.com")
    with app.app_context():
        from app.commercial_ops.emergency_extensions import create_emergency_extension, is_emergency_extension_active

        sub = _make_subscription(app, staff_id)
        assert is_emergency_extension_active(sub.id) is False
        create_emergency_extension(subscription=sub, reason="first", duration_hours=1, actor_staff_user_id=staff_id)
        assert is_emergency_extension_active(sub.id) is True
        # Phase 8V-P5: must be timezone-aware -- a naive value compared
        # against the (correctly timezone-aware, post-fix) expires_at column
        # gets silently re-localized to the DB session's own timezone instead
        # of being treated as UTC, which on a non-UTC session previously made
        # this assertion pass for the wrong reason (or fail outright).
        from datetime import timezone

        future = datetime.now(timezone.utc) + timedelta(hours=2)
        assert is_emergency_extension_active(sub.id, now=future) is False


def test_resolve_commercial_state_override_never_applies_to_revoked(app, seeded):
    from app.commercial_ops.state_resolution import CommercialState, resolve_commercial_state

    decision = resolve_commercial_state(
        subscription_status="ACTIVE", license_status="REVOKED", subscription_end_date=None,
        license_valid_until=None, as_of=date(2026, 7, 26), emergency_extension_active=True,
    )
    assert decision.state == CommercialState.REVOKED
    assert decision.may_check_in_existing_installation is False


def test_resolve_commercial_state_override_permits_expired_check_in(app, seeded):
    from app.commercial_ops.state_resolution import CommercialState, resolve_commercial_state

    without_override = resolve_commercial_state(
        subscription_status="EXPIRED", license_status="EXPIRED", subscription_end_date=date(2026, 7, 1),
        license_valid_until=date(2026, 7, 1), as_of=date(2026, 7, 26),
    )
    assert without_override.may_issue_assertion is False

    with_override = resolve_commercial_state(
        subscription_status="EXPIRED", license_status="EXPIRED", subscription_end_date=date(2026, 7, 1),
        license_valid_until=date(2026, 7, 1), as_of=date(2026, 7, 26), emergency_extension_active=True,
    )
    assert with_override.state == CommercialState.EXPIRED
    assert with_override.may_issue_assertion is True
    assert with_override.may_check_in_existing_installation is True
    assert with_override.required_action is None
