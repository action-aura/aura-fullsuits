from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import make_staff


def _make_pilot_subscription(app, staff_id, *, pilot_end=date(2026, 9, 1), device_allowance=1):
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
    plan = Plan(
        plan_code=f"PILOT-{staff_id}-{pilot_end.isoformat()}",
        product_id=product.id, name="Pilot Test Plan", billing_model="PILOT", currency="USD",
    )
    customer = Customer(legal_name="Pilot Test Co")
    db_session.add_all([plan, customer])
    db_session.commit()
    sub = create_subscription(
        {
            "customer_id": customer.id, "product_id": product.id, "plan_id": plan.id,
            "end_date": pilot_end, "device_allowance": device_allowance,
        },
        staff_id,
    )
    transition_subscription(sub, "PILOT", staff_id)
    return sub, plan, customer, product


# -- create_pilot_record -------------------------------------------------

def test_create_pilot_record_requires_pilot_subscription(app, seeded):
    staff_id = make_staff(app, "pil1@example.com")
    with app.app_context():
        from app.commercial_ops.pilot_lifecycle import PilotLifecycleError, create_pilot_record
        from app.extensions import db_session
        from app.models.catalog import Plan, Product
        from app.models.customers import Customer
        from app.subscriptions.services import create_subscription

        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        plan = Plan(plan_code=f"NOTPILOT-{staff_id}", product_id=product.id, name="x", billing_model="MONTHLY", currency="USD")
        customer = Customer(legal_name="Not Pilot Co")
        db_session.add_all([plan, customer])
        db_session.commit()
        sub = create_subscription(
            {"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": date(2026, 9, 1)},
            staff_id,
        )
        with pytest.raises(PilotLifecycleError):
            create_pilot_record(
                subscription=sub, pilot_start=date(2026, 8, 1), pilot_end=date(2026, 9, 1), actor_staff_user_id=staff_id
            )


def test_create_pilot_record_rejects_bad_dates(app, seeded):
    staff_id = make_staff(app, "pil2@example.com")
    with app.app_context():
        from app.commercial_ops.pilot_lifecycle import PilotLifecycleError, create_pilot_record

        sub, *_ = _make_pilot_subscription(app, staff_id)
        with pytest.raises(PilotLifecycleError):
            create_pilot_record(
                subscription=sub, pilot_start=date(2026, 9, 1), pilot_end=date(2026, 8, 1), actor_staff_user_id=staff_id
            )


def test_create_pilot_record_success(app, seeded):
    staff_id = make_staff(app, "pil3@example.com")
    with app.app_context():
        from app.commercial_ops.pilot_lifecycle import create_pilot_record

        sub, *_ = _make_pilot_subscription(app, staff_id)
        pilot = create_pilot_record(
            subscription=sub, pilot_start=date(2026, 7, 1), pilot_end=date(2026, 9, 1), actor_staff_user_id=staff_id,
        )
        assert pilot.status == "DRAFT"
        assert pilot.subscription_id == sub.id
        assert pilot.max_extensions_allowed == 2
        assert pilot.extension_count == 0


# -- transitions -----------------------------------------------------------

def test_approve_then_activate_pilot(app, seeded):
    staff_id = make_staff(app, "pil4@example.com")
    with app.app_context():
        from app.commercial_ops.pilot_lifecycle import activate_pilot, approve_pilot, create_pilot_record

        sub, *_ = _make_pilot_subscription(app, staff_id)
        pilot = create_pilot_record(
            subscription=sub, pilot_start=date(2026, 7, 1), pilot_end=date(2026, 9, 1), actor_staff_user_id=staff_id,
        )
        approve_pilot(pilot, staff_id)
        assert pilot.status == "APPROVED"
        assert pilot.approved_by_staff_user_id == staff_id
        activate_pilot(pilot, staff_id)
        assert pilot.status == "ACTIVE"


def test_invalid_transition_rejected(app, seeded):
    staff_id = make_staff(app, "pil5@example.com")
    with app.app_context():
        from app.commercial_ops.pilot_lifecycle import InvalidPilotTransitionError, activate_pilot, create_pilot_record

        sub, *_ = _make_pilot_subscription(app, staff_id)
        pilot = create_pilot_record(
            subscription=sub, pilot_start=date(2026, 7, 1), pilot_end=date(2026, 9, 1), actor_staff_user_id=staff_id,
        )
        with pytest.raises(InvalidPilotTransitionError):
            activate_pilot(pilot, staff_id)  # still DRAFT, must be APPROVED first


# -- extend_pilot ------------------------------------------------------------

def test_extend_pilot_requires_reason(app, seeded):
    staff_id = make_staff(app, "pil6@example.com")
    with app.app_context():
        from app.commercial_ops.pilot_lifecycle import PilotLifecycleError, activate_pilot, approve_pilot, create_pilot_record, extend_pilot

        sub, *_ = _make_pilot_subscription(app, staff_id, pilot_end=date(2026, 9, 1))
        pilot = create_pilot_record(
            subscription=sub, pilot_start=date(2026, 7, 1), pilot_end=date(2026, 9, 1), actor_staff_user_id=staff_id,
        )
        approve_pilot(pilot, staff_id)
        activate_pilot(pilot, staff_id)
        with pytest.raises(PilotLifecycleError):
            extend_pilot(pilot, new_end_date=date(2026, 10, 1), reason="   ", actor_staff_user_id=staff_id)


def test_extend_pilot_enforces_max_extensions(app, seeded):
    staff_id = make_staff(app, "pil7@example.com")
    with app.app_context():
        from app.commercial_ops.pilot_lifecycle import PilotLifecycleError, activate_pilot, approve_pilot, create_pilot_record, extend_pilot

        sub, *_ = _make_pilot_subscription(app, staff_id, pilot_end=date(2026, 9, 1))
        pilot = create_pilot_record(
            subscription=sub, pilot_start=date(2026, 7, 1), pilot_end=date(2026, 9, 1), actor_staff_user_id=staff_id,
            max_extensions_allowed=1,
        )
        approve_pilot(pilot, staff_id)
        activate_pilot(pilot, staff_id)
        extend_pilot(pilot, new_end_date=date(2026, 10, 1), reason="Customer needs more eval time", actor_staff_user_id=staff_id)
        assert pilot.extension_count == 1
        assert pilot.status == "EXTENDED"
        assert pilot.pilot_end == date(2026, 10, 1)
        with pytest.raises(PilotLifecycleError):
            extend_pilot(pilot, new_end_date=date(2026, 11, 1), reason="Again", actor_staff_user_id=staff_id)


def test_extend_pilot_rejects_earlier_or_equal_date(app, seeded):
    staff_id = make_staff(app, "pil8@example.com")
    with app.app_context():
        from app.commercial_ops.pilot_lifecycle import PilotLifecycleError, activate_pilot, approve_pilot, create_pilot_record, extend_pilot

        sub, *_ = _make_pilot_subscription(app, staff_id, pilot_end=date(2026, 9, 1))
        pilot = create_pilot_record(
            subscription=sub, pilot_start=date(2026, 7, 1), pilot_end=date(2026, 9, 1), actor_staff_user_id=staff_id,
        )
        approve_pilot(pilot, staff_id)
        activate_pilot(pilot, staff_id)
        with pytest.raises(PilotLifecycleError):
            extend_pilot(pilot, new_end_date=date(2026, 9, 1), reason="no-op", actor_staff_user_id=staff_id)


# -- complete / cancel also flip the underlying subscription ------------------

def test_complete_pilot_without_conversion_flips_subscription(app, seeded):
    staff_id = make_staff(app, "pil9@example.com")
    with app.app_context():
        from app.commercial_ops.pilot_lifecycle import activate_pilot, approve_pilot, complete_pilot, create_pilot_record

        sub, *_ = _make_pilot_subscription(app, staff_id)
        pilot = create_pilot_record(
            subscription=sub, pilot_start=date(2026, 7, 1), pilot_end=date(2026, 9, 1), actor_staff_user_id=staff_id,
        )
        approve_pilot(pilot, staff_id)
        activate_pilot(pilot, staff_id)
        complete_pilot(pilot, actor_staff_user_id=staff_id, reason="Trial ended, no conversion")
        assert pilot.status == "COMPLETED"
        assert pilot.conversion_decision == "DO_NOT_CONVERT"
        assert sub.status == "COMPLETED"


def test_cancel_pilot_requires_reason_and_flips_subscription(app, seeded):
    staff_id = make_staff(app, "pil10@example.com")
    with app.app_context():
        from app.commercial_ops.pilot_lifecycle import PilotLifecycleError, activate_pilot, approve_pilot, cancel_pilot, create_pilot_record

        sub, *_ = _make_pilot_subscription(app, staff_id)
        pilot = create_pilot_record(
            subscription=sub, pilot_start=date(2026, 7, 1), pilot_end=date(2026, 9, 1), actor_staff_user_id=staff_id,
        )
        approve_pilot(pilot, staff_id)
        activate_pilot(pilot, staff_id)
        with pytest.raises(PilotLifecycleError):
            cancel_pilot(pilot, reason="", actor_staff_user_id=staff_id)
        cancel_pilot(pilot, reason="Customer withdrew", actor_staff_user_id=staff_id)
        assert pilot.status == "CANCELLED"
        assert sub.status == "CANCELLED"


# -- mark_pilot_converted: full conversion via the real renewal pipeline -----

def test_pilot_conversion_via_renewal_pipeline(app, seeded):
    creator_id = make_staff(app, "pilconv-creator@example.com")
    approver_id = make_staff(app, "pilconv-approver@example.com")
    with app.app_context():
        from app.commercial_ops.pilot_lifecycle import activate_pilot, approve_pilot, create_pilot_record, mark_pilot_converted
        from app.commercial_ops.renewal_requests import (
            apply_renewal_request,
            approve_renewal_request,
            create_renewal_request,
            transition_renewal_request,
        )

        sub, *_ = _make_pilot_subscription(app, creator_id, pilot_end=date(2026, 9, 1))
        pilot = create_pilot_record(
            subscription=sub, pilot_start=date(2026, 7, 1), pilot_end=date(2026, 9, 1), actor_staff_user_id=creator_id,
        )
        approve_pilot(pilot, approver_id)
        activate_pilot(pilot, creator_id)

        renewal = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 9, 1), proposed_term_end=date(2027, 9, 1),
            currency="USD", actor_staff_user_id=creator_id,
        )
        transition_renewal_request(renewal, "QUOTED", creator_id)
        transition_renewal_request(renewal, "AWAITING_CONFIRMATION", creator_id)
        transition_renewal_request(renewal, "AWAITING_PAYMENT", creator_id)
        transition_renewal_request(renewal, "PAYMENT_RECORDED", creator_id)
        approve_renewal_request(renewal, approver_id)
        apply_renewal_request(renewal.id, approver_id)

        assert sub.status == "ACTIVE"
        assert sub.end_date == date(2027, 9, 1)

        mark_pilot_converted(pilot, renewal, approver_id)
        assert pilot.status == "CONVERTED"
        assert pilot.conversion_decision == "CONVERT"
        assert pilot.converted_renewal_request_id == renewal.id


def test_mark_pilot_converted_rejects_unapplied_renewal(app, seeded):
    staff_id = make_staff(app, "pilconv2@example.com")
    with app.app_context():
        from app.commercial_ops.pilot_lifecycle import PilotLifecycleError, activate_pilot, approve_pilot, create_pilot_record, mark_pilot_converted
        from app.commercial_ops.renewal_requests import create_renewal_request

        sub, *_ = _make_pilot_subscription(app, staff_id, pilot_end=date(2026, 9, 1))
        pilot = create_pilot_record(
            subscription=sub, pilot_start=date(2026, 7, 1), pilot_end=date(2026, 9, 1), actor_staff_user_id=staff_id,
        )
        approve_pilot(pilot, staff_id)
        activate_pilot(pilot, staff_id)
        renewal = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 9, 1), proposed_term_end=date(2027, 9, 1),
            currency="USD", actor_staff_user_id=staff_id,
        )
        with pytest.raises(PilotLifecycleError):
            mark_pilot_converted(pilot, renewal, staff_id)
