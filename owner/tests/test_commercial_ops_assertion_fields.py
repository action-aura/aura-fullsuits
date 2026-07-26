from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from tests.conftest import make_staff


def _subscription(app, staff_id, *, status="ACTIVE", end_date=date(2026, 9, 1)):
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
    customer = Customer(legal_name="Assertion Field Test Co")
    plan = Plan(plan_code=f"AF-{staff_id}-{end_date.isoformat()}", product_id=product.id, name="x", billing_model="MONTHLY", currency="USD")
    db_session.add_all([customer, plan])
    db_session.commit()
    sub = create_subscription(
        {"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id,
         "start_date": date(2026, 1, 1), "end_date": end_date}, staff_id,
    )
    if status != "DRAFT":
        transition_subscription(sub, "ACTIVE", staff_id)
        if status != "ACTIVE":
            transition_subscription(sub, status, staff_id, reason="test setup")
    return sub, plan


def test_basic_fields_reflect_subscription(app, seeded):
    staff_id = make_staff(app, "af1@example.com")
    with app.app_context():
        from app.commercial_ops.assertion_fields import resolve_commercial_assertion_fields

        sub, plan = _subscription(app, staff_id)
        fields = resolve_commercial_assertion_fields(sub)
        assert fields["plan_code"] == plan.plan_code
        assert fields["term_start"] == "2026-01-01"
        assert fields["term_end"] == "2026-09-01"
        assert fields["renewal_status"] == "NONE"
        assert fields["past_due_since"] is None
        assert fields["commercial_grace_end"] is None
        assert fields["pilot_status"] is None
        assert fields["emergency_extension_id"] is None


def test_renewal_status_reflects_latest_renewal_request(app, seeded):
    staff_id = make_staff(app, "af2@example.com")
    with app.app_context():
        from app.commercial_ops.assertion_fields import resolve_commercial_assertion_fields
        from app.commercial_ops.renewal_requests import create_renewal_request, transition_renewal_request

        sub, _plan = _subscription(app, staff_id)
        renewal = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 9, 1), proposed_term_end=date(2027, 9, 1),
            currency="USD", actor_staff_user_id=staff_id,
        )
        assert resolve_commercial_assertion_fields(sub)["renewal_status"] == "DRAFT"
        transition_renewal_request(renewal, "QUOTED", staff_id)
        assert resolve_commercial_assertion_fields(sub)["renewal_status"] == "QUOTED"


def test_past_due_since_and_commercial_grace_end(app, seeded):
    staff_id = make_staff(app, "af3@example.com")
    with app.app_context():
        from app.commercial_ops.assertion_fields import resolve_commercial_assertion_fields
        from app.commercial_ops.commercial_policy import create_commercial_policy

        create_commercial_policy(
            policy_code=f"af3-policy-{staff_id}", warning_offsets_days=[7, 0],
            notify_role_codes=["SUPPORT"], effective_date=date(2026, 1, 1),
            actor_staff_user_id=staff_id, payment_grace_days=10, policy_version=3,
        )
        sub, _plan = _subscription(app, staff_id, status="PAST_DUE", end_date=date(2026, 6, 1))

        fields = resolve_commercial_assertion_fields(sub)
        assert fields["commercial_policy_version"] == 3
        assert fields["past_due_since"] is not None
        past_due_since = datetime.fromisoformat(fields["past_due_since"])
        grace_end = datetime.fromisoformat(fields["commercial_grace_end"])
        assert grace_end - past_due_since == timedelta(days=10)


def test_pilot_status_reflects_pilot_record(app, seeded):
    staff_id = make_staff(app, "af4@example.com")
    with app.app_context():
        from app.commercial_ops.assertion_fields import resolve_commercial_assertion_fields
        from app.commercial_ops.pilot_lifecycle import approve_pilot, create_pilot_record
        from app.extensions import db_session
        from app.models.catalog import Plan, Product
        from app.models.customers import Customer
        from app.subscriptions.services import create_subscription, transition_subscription

        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        customer = Customer(legal_name="Pilot Assertion Co")
        plan = Plan(plan_code=f"AF4-{staff_id}", product_id=product.id, name="x", billing_model="PILOT", currency="USD")
        db_session.add_all([customer, plan])
        db_session.commit()
        sub = create_subscription(
            {"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": date(2026, 9, 1)}, staff_id,
        )
        transition_subscription(sub, "PILOT", staff_id)
        pilot = create_pilot_record(
            subscription=sub, pilot_start=date(2026, 7, 1), pilot_end=date(2026, 9, 1), actor_staff_user_id=staff_id,
        )
        assert resolve_commercial_assertion_fields(sub)["pilot_status"] == "DRAFT"
        approve_pilot(pilot, staff_id)
        assert resolve_commercial_assertion_fields(sub)["pilot_status"] == "APPROVED"


def test_emergency_extension_id_only_while_active_and_unexpired(app, seeded):
    staff_id = make_staff(app, "af5@example.com")
    with app.app_context():
        from app.commercial_ops.assertion_fields import resolve_commercial_assertion_fields
        from app.commercial_ops.emergency_extensions import create_emergency_extension, revoke_emergency_extension

        sub, _plan = _subscription(app, staff_id, status="EXPIRED", end_date=date(2026, 6, 1))
        assert resolve_commercial_assertion_fields(sub)["emergency_extension_id"] is None

        extension = create_emergency_extension(subscription=sub, reason="incident", duration_hours=12, actor_staff_user_id=staff_id)
        fields = resolve_commercial_assertion_fields(sub)
        assert fields["emergency_extension_id"] == str(extension.id)

        # Past its expiry -- must not be reported as active even though
        # status is still ACTIVE in the row (time window rules, not just status).
        future = datetime.now(timezone.utc) + timedelta(hours=13)
        assert resolve_commercial_assertion_fields(sub, as_of=future)["emergency_extension_id"] is None

        revoke_emergency_extension(extension, reason="resolved", actor_staff_user_id=staff_id)
        assert resolve_commercial_assertion_fields(sub)["emergency_extension_id"] is None
