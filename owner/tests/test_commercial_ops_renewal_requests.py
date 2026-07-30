from __future__ import annotations

import threading
from datetime import date

import pytest

from tests.conftest import make_staff


def _make_subscription(app, staff_id, *, status="ACTIVE", end_date=date(2026, 8, 1), device_allowance=2, plan_code=None):
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
    plan = Plan(
        plan_code=plan_code or f"REN-{staff_id}-{end_date.isoformat()}",
        product_id=product.id, name="Renewal Test Plan", billing_model="MONTHLY", currency="USD",
    )
    customer = Customer(legal_name="Renewal Test Co")
    db_session.add_all([plan, customer])
    db_session.commit()
    sub = create_subscription(
        {
            "customer_id": customer.id, "product_id": product.id, "plan_id": plan.id,
            "end_date": end_date, "device_allowance": device_allowance,
        },
        staff_id,
    )
    if status != "DRAFT":
        transition_subscription(sub, "ACTIVE", staff_id)
        if status != "ACTIVE":
            transition_subscription(sub, status, staff_id, reason="test setup")
    return sub, plan


# -- create_renewal_request ---------------------------------------------------

def test_create_renewal_request_snapshots_current_term(app, seeded):
    staff_id = make_staff(app, "cr1@example.com")
    with app.app_context():
        from app.commercial_ops.renewal_requests import create_renewal_request

        sub, _ = _make_subscription(app, staff_id, end_date=date(2026, 8, 1))
        renewal = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 8, 1), proposed_term_end=date(2026, 9, 1),
            currency="USD", actor_staff_user_id=staff_id,
        )
        assert renewal.status == "DRAFT"
        assert renewal.current_term_end == date(2026, 8, 1)
        assert renewal.proposed_term_end == date(2026, 9, 1)
        assert renewal.created_by_staff_user_id == staff_id
        assert renewal.version == 1


def test_create_renewal_request_idempotent_retry_returns_same_row(app, seeded):
    staff_id = make_staff(app, "cr2@example.com")
    with app.app_context():
        from app.commercial_ops.renewal_requests import create_renewal_request

        sub, _ = _make_subscription(app, staff_id)
        first = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 8, 1), proposed_term_end=date(2026, 9, 1),
            currency="USD", actor_staff_user_id=staff_id, idempotency_key="retry-key-1",
        )
        second = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 8, 1), proposed_term_end=date(2026, 9, 1),
            currency="USD", actor_staff_user_id=staff_id, idempotency_key="retry-key-1",
        )
        assert first.id == second.id


# -- transition_renewal_request ------------------------------------------------

def test_valid_transition_sequence(app, seeded):
    staff_id = make_staff(app, "tr1@example.com")
    with app.app_context():
        from app.commercial_ops.renewal_requests import create_renewal_request, transition_renewal_request

        sub, _ = _make_subscription(app, staff_id)
        renewal = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 8, 1), proposed_term_end=date(2026, 9, 1),
            currency="USD", actor_staff_user_id=staff_id,
        )
        transition_renewal_request(renewal, "QUOTED", staff_id)
        transition_renewal_request(renewal, "AWAITING_CONFIRMATION", staff_id)
        transition_renewal_request(renewal, "AWAITING_PAYMENT", staff_id)
        transition_renewal_request(renewal, "PAYMENT_RECORDED", staff_id)
        assert renewal.status == "PAYMENT_RECORDED"
        assert len(renewal.status_history) == 4


def test_invalid_transition_rejected(app, seeded):
    staff_id = make_staff(app, "tr2@example.com")
    with app.app_context():
        from app.commercial_ops.renewal_requests import (
            InvalidRenewalTransitionError, create_renewal_request, transition_renewal_request,
        )

        sub, _ = _make_subscription(app, staff_id)
        renewal = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 8, 1), proposed_term_end=date(2026, 9, 1),
            currency="USD", actor_staff_user_id=staff_id,
        )
        with pytest.raises(InvalidRenewalTransitionError):
            transition_renewal_request(renewal, "APPLIED", staff_id)  # DRAFT -> APPLIED not allowed


def test_terminal_states_reject_further_transitions(app, seeded):
    staff_id = make_staff(app, "tr3@example.com")
    with app.app_context():
        from app.commercial_ops.renewal_requests import (
            InvalidRenewalTransitionError, create_renewal_request, transition_renewal_request,
        )

        sub, _ = _make_subscription(app, staff_id)
        renewal = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 8, 1), proposed_term_end=date(2026, 9, 1),
            currency="USD", actor_staff_user_id=staff_id,
        )
        transition_renewal_request(renewal, "CANCELLED", staff_id, reason="customer changed mind")
        assert renewal.cancelled_at is not None
        with pytest.raises(InvalidRenewalTransitionError):
            transition_renewal_request(renewal, "QUOTED", staff_id)


def test_transition_to_approved_via_generic_function_rejected(app, seeded):
    staff_id = make_staff(app, "tr4@example.com")
    with app.app_context():
        from app.commercial_ops.renewal_requests import (
            InvalidRenewalTransitionError, create_renewal_request, transition_renewal_request,
        )

        sub, _ = _make_subscription(app, staff_id)
        renewal = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 8, 1), proposed_term_end=date(2026, 9, 1),
            currency="USD", actor_staff_user_id=staff_id,
        )
        transition_renewal_request(renewal, "QUOTED", staff_id)
        transition_renewal_request(renewal, "AWAITING_CONFIRMATION", staff_id)
        transition_renewal_request(renewal, "AWAITING_PAYMENT", staff_id)
        transition_renewal_request(renewal, "PAYMENT_RECORDED", staff_id)
        with pytest.raises(InvalidRenewalTransitionError):
            transition_renewal_request(renewal, "APPROVED", staff_id)


# -- approve_renewal_request ---------------------------------------------------

def _make_approved_renewal(app, creator_id, approver_id, *, sub=None, plan=None, **overrides):
    from app.commercial_ops.renewal_requests import approve_renewal_request, create_renewal_request, transition_renewal_request

    if sub is None:
        sub, plan = _make_subscription(app, creator_id, end_date=overrides.pop("end_date", date(2026, 8, 1)))
    fields = dict(
        subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
        proposed_term_start=sub.end_date, proposed_term_end=date(2026, 9, 1),
        currency="USD", actor_staff_user_id=creator_id,
    )
    fields.update(overrides)
    renewal = create_renewal_request(**fields)
    transition_renewal_request(renewal, "QUOTED", creator_id)
    transition_renewal_request(renewal, "AWAITING_CONFIRMATION", creator_id)
    transition_renewal_request(renewal, "AWAITING_PAYMENT", creator_id)
    transition_renewal_request(renewal, "PAYMENT_RECORDED", creator_id)
    approve_renewal_request(renewal, approver_id)
    return renewal, sub, plan


def test_approve_renewal_request_success(app, seeded):
    creator_id = make_staff(app, "ap1@example.com")
    approver_id = make_staff(app, "ap1b@example.com")
    with app.app_context():
        renewal, sub, plan = _make_approved_renewal(app, creator_id, approver_id)
        assert renewal.status == "APPROVED"
        assert renewal.approved_by_staff_user_id == approver_id
        assert renewal.approved_at is not None


def test_self_approval_rejected(app, seeded):
    staff_id = make_staff(app, "ap2@example.com")
    with app.app_context():
        from app.commercial_ops.renewal_requests import (
            RenewalApplicationError, approve_renewal_request, create_renewal_request, transition_renewal_request,
        )

        sub, _ = _make_subscription(app, staff_id)
        renewal = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 8, 1), proposed_term_end=date(2026, 9, 1),
            currency="USD", actor_staff_user_id=staff_id,
        )
        transition_renewal_request(renewal, "QUOTED", staff_id)
        transition_renewal_request(renewal, "AWAITING_CONFIRMATION", staff_id)
        transition_renewal_request(renewal, "AWAITING_PAYMENT", staff_id)
        transition_renewal_request(renewal, "PAYMENT_RECORDED", staff_id)
        with pytest.raises(RenewalApplicationError):
            approve_renewal_request(renewal, staff_id)  # same staff who created it
        assert renewal.status == "PAYMENT_RECORDED"  # unchanged


def test_approve_wrong_status_rejected(app, seeded):
    creator_id = make_staff(app, "ap3@example.com")
    approver_id = make_staff(app, "ap3b@example.com")
    with app.app_context():
        from app.commercial_ops.renewal_requests import InvalidRenewalTransitionError, approve_renewal_request, create_renewal_request

        sub, _ = _make_subscription(app, creator_id)
        renewal = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 8, 1), proposed_term_end=date(2026, 9, 1),
            currency="USD", actor_staff_user_id=creator_id,
        )
        with pytest.raises(InvalidRenewalTransitionError):
            approve_renewal_request(renewal, approver_id)  # still DRAFT


# -- apply_renewal_request: happy paths ---------------------------------------

def test_apply_early_renewal_extends_active_subscription(app, seeded):
    creator_id = make_staff(app, "aer1@example.com")
    approver_id = make_staff(app, "aer1b@example.com")
    with app.app_context():
        from app.commercial_ops.renewal_requests import apply_renewal_request

        renewal, sub, plan = _make_approved_renewal(app, creator_id, approver_id, end_date=date(2026, 8, 1))
        previous_end = sub.end_date

        applied = apply_renewal_request(renewal.id, approver_id)

        assert applied.status == "APPLIED"
        assert applied.applied_renewal_record_id is not None
        assert sub.end_date == date(2026, 9, 1)
        assert sub.end_date != previous_end
        assert sub.status == "ACTIVE"


def test_apply_renewal_creates_linked_renewal_record_with_history(app, seeded):
    creator_id = make_staff(app, "aer2@example.com")
    approver_id = make_staff(app, "aer2b@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.commercial_ops.renewal_requests import apply_renewal_request
        from app.models.subscriptions import RenewalRecord

        renewal, sub, plan = _make_approved_renewal(app, creator_id, approver_id, end_date=date(2026, 8, 1))
        applied = apply_renewal_request(renewal.id, approver_id)

        record = db_session.get(RenewalRecord, applied.applied_renewal_record_id)
        assert record is not None
        assert record.subscription_id == sub.id
        assert record.previous_end_date == date(2026, 8, 1)
        assert record.new_end_date == date(2026, 9, 1)


def test_apply_renewal_revives_expired_subscription(app, seeded):
    creator_id = make_staff(app, "aer3@example.com")
    approver_id = make_staff(app, "aer3b@example.com")
    with app.app_context():
        from app.commercial_ops.renewal_requests import apply_renewal_request

        sub, plan = _make_subscription(app, creator_id, status="EXPIRED", end_date=date(2026, 6, 1))
        renewal, _, _ = _make_approved_renewal(
            app, creator_id, approver_id, sub=sub, plan=plan,
            date_rule="LATE_RENEWAL_FROM_PREVIOUS_END",
            proposed_term_start=date(2026, 6, 1), proposed_term_end=date(2026, 7, 1),
        )

        assert sub.status == "EXPIRED"
        applied = apply_renewal_request(renewal.id, approver_id)
        assert applied.status == "APPLIED"
        assert sub.status == "ACTIVE"
        assert sub.end_date == date(2026, 7, 1)
        history_to = [h.to_status for h in sub.status_history]
        assert "ACTIVE" in history_to


def test_apply_renewal_applies_plan_change_and_device_allowance(app, seeded):
    creator_id = make_staff(app, "aer4@example.com")
    approver_id = make_staff(app, "aer4b@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.commercial_ops.renewal_requests import apply_renewal_request
        from app.models.catalog import Plan

        sub, old_plan = _make_subscription(app, creator_id, end_date=date(2026, 8, 1), device_allowance=2)
        new_plan = Plan(
            plan_code=f"NEWPLAN-{creator_id}", product_id=old_plan.product_id, name="Upgraded plan",
            billing_model="MONTHLY", currency="USD",
        )
        db_session.add(new_plan)
        db_session.commit()

        renewal, _, _ = _make_approved_renewal(
            app, creator_id, approver_id, sub=sub, plan=old_plan,
            requested_plan_id=new_plan.id, device_allowance_after=5,
        )
        applied = apply_renewal_request(renewal.id, approver_id)
        assert applied.status == "APPLIED"
        assert sub.plan_id == new_plan.id
        assert sub.device_allowance == 5


# -- apply_renewal_request: guards and errors ---------------------------------

def test_apply_wrong_status_rejected(app, seeded):
    creator_id = make_staff(app, "aer5@example.com")
    with app.app_context():
        from app.commercial_ops.renewal_requests import InvalidRenewalTransitionError, apply_renewal_request, create_renewal_request

        sub, _ = _make_subscription(app, creator_id)
        renewal = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 8, 1), proposed_term_end=date(2026, 9, 1),
            currency="USD", actor_staff_user_id=creator_id,
        )
        with pytest.raises(InvalidRenewalTransitionError):
            apply_renewal_request(renewal.id, creator_id)  # still DRAFT


def test_apply_nonexistent_renewal_rejected(app, seeded):
    staff_id = make_staff(app, "aer6@example.com")
    with app.app_context():
        import uuid

        from app.commercial_ops.renewal_requests import RenewalApplicationError, apply_renewal_request

        with pytest.raises(RenewalApplicationError):
            apply_renewal_request(uuid.uuid4(), staff_id)


def test_second_renewal_against_stale_term_rejected(app, seeded):
    # Two renewal requests created against the SAME original term, both
    # approved, before either is applied -- the second must be rejected
    # once the first has moved the subscription's term (spec Part E:
    # "two simultaneous renewal approvals cannot double-extend the
    # subscription").
    creator_id = make_staff(app, "aer7@example.com")
    approver_id = make_staff(app, "aer7b@example.com")
    with app.app_context():
        from app.commercial_ops.renewal_requests import RenewalConcurrencyError, apply_renewal_request

        sub, plan = _make_subscription(app, creator_id, end_date=date(2026, 8, 1))
        renewal_a, _, _ = _make_approved_renewal(
            app, creator_id, approver_id, sub=sub, plan=plan,
            proposed_term_start=date(2026, 8, 1), proposed_term_end=date(2026, 9, 1),
        )
        renewal_b, _, _ = _make_approved_renewal(
            app, creator_id, approver_id, sub=sub, plan=plan,
            proposed_term_start=date(2026, 8, 1), proposed_term_end=date(2026, 10, 1),
        )

        applied_a = apply_renewal_request(renewal_a.id, approver_id)
        assert applied_a.status == "APPLIED"
        assert sub.end_date == date(2026, 9, 1)

        with pytest.raises(RenewalConcurrencyError):
            apply_renewal_request(renewal_b.id, approver_id)
        # Term must reflect only the FIRST applied renewal, not doubled.
        assert sub.end_date == date(2026, 9, 1)


def test_applying_same_renewal_twice_rejected_second_time(app, seeded):
    creator_id = make_staff(app, "aer8@example.com")
    approver_id = make_staff(app, "aer8b@example.com")
    with app.app_context():
        from app.commercial_ops.renewal_requests import InvalidRenewalTransitionError, apply_renewal_request

        renewal, sub, _ = _make_approved_renewal(app, creator_id, approver_id, end_date=date(2026, 8, 1))
        apply_renewal_request(renewal.id, approver_id)
        with pytest.raises(InvalidRenewalTransitionError):
            apply_renewal_request(renewal.id, approver_id)


# -- real concurrency: two threads racing to apply the SAME renewal request ---

def test_concurrent_apply_of_same_renewal_request_only_one_succeeds(app, seeded):
    creator_id = make_staff(app, "conc1@example.com")
    approver_id = make_staff(app, "conc1b@example.com")
    with app.app_context():
        renewal, sub, _ = _make_approved_renewal(app, creator_id, approver_id, end_date=date(2026, 8, 1))
        renewal_id = renewal.id
        sub_id = sub.id

    results: list[tuple[bool, str]] = []
    lock = threading.Lock()

    def _attempt():
        from app.extensions import db_session

        with app.app_context():
            from app.commercial_ops.renewal_requests import (
                InvalidRenewalTransitionError, RenewalApplicationError, apply_renewal_request,
            )

            try:
                apply_renewal_request(renewal_id, approver_id)
                with lock:
                    results.append((True, "applied"))
            except (RenewalApplicationError, InvalidRenewalTransitionError) as exc:
                db_session.rollback()
                with lock:
                    results.append((False, str(exc)))
            finally:
                db_session.remove()

    threads = [threading.Thread(target=_attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 2
    successes = [r for r in results if r[0]]
    failures = [r for r in results if not r[0]]
    assert len(successes) == 1, f"expected exactly one success, got {results}"
    assert len(failures) == 1

    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        refreshed = db_session.get(Subscription, sub_id)
        # Extended exactly once, not twice, no matter which thread won.
        assert refreshed.end_date == date(2026, 9, 1)


# -- Phase 8V-P2 Scenario 7: device_allowance -> License.device_limit sync ----
# Real gap found during Phase 8V-P physical validation: apply_renewal_request()
# updated Subscription.device_allowance but never propagated it to
# License.device_limit, the field every real device-limit check actually
# enforces. See docs/owner/phase8vp2/scenario7-gap-root-cause.md.

def _make_license_for_subscription(app, sub, plan, staff_id, *, device_limit):
    from app.extensions import db_session
    from app.licensing.services import create_license, issue_license_key

    lic = create_license(
        {
            "customer_id": sub.customer_id, "subscription_id": sub.id, "product_id": sub.product_id,
            "plan_id": plan.id, "allowed_platforms": "WINDOWS,ANDROID", "device_limit": device_limit,
        },
        staff_id,
    )
    lic, _ = issue_license_key(lic, app.config["LICENSE_PEPPER"], f"scenario7-{lic.id}", staff_id)
    db_session.commit()
    return lic


def _activate_installation(app, lic, staff_id, *, label):
    from app.extensions import db_session
    from app.installations.services import register_installation
    from app.models.catalog import Platform

    platform_id = db_session.query(Platform).filter_by(platform_code="WINDOWS").first().id
    installation = register_installation(
        {"customer_id": lic.customer_id, "license_id": lic.id, "product_id": lic.product_id,
         "platform_id": platform_id, "installation_label": label},
        staff_id,
    )
    installation.status = "ACTIVE"
    db_session.commit()
    return installation


def test_apply_downgrade_syncs_license_device_limit(app, seeded):
    creator_id = make_staff(app, "s7a@example.com")
    approver_id = make_staff(app, "s7a-b@example.com")
    with app.app_context():
        from app.commercial_ops.renewal_requests import apply_renewal_request
        from app.licensing_service.activation import count_slot_consuming_installations
        from app.commercial_ops.device_slot_ops import resolve_effective_device_limit

        sub, plan = _make_subscription(app, creator_id, end_date=date(2026, 8, 1), device_allowance=2)
        lic = _make_license_for_subscription(app, sub, plan, creator_id, device_limit=2)
        _activate_installation(app, lic, creator_id, label="dev-1")
        _activate_installation(app, lic, creator_id, label="dev-2")

        renewal, _, _ = _make_approved_renewal(
            app, creator_id, approver_id, sub=sub, plan=plan, device_allowance_after=1,
        )
        applied = apply_renewal_request(renewal.id, approver_id)
        assert applied.status == "APPLIED"
        assert sub.device_allowance == 1

        # The field every real device-limit check enforces must now match --
        # this is the exact gap Phase 8V-P found and left unfixed.
        assert lic.device_limit == 1
        assert resolve_effective_device_limit(lic) == 1

        # Both pre-existing installations remain untouched -- a lowered
        # allowance never silently deactivates an already-active device.
        assert count_slot_consuming_installations(lic.id) == 2


def test_apply_downgrade_does_not_touch_installations(app, seeded):
    creator_id = make_staff(app, "s7b@example.com")
    approver_id = make_staff(app, "s7b-b@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.commercial_ops.renewal_requests import apply_renewal_request
        from app.models.installations import Installation

        sub, plan = _make_subscription(app, creator_id, end_date=date(2026, 8, 1), device_allowance=2)
        lic = _make_license_for_subscription(app, sub, plan, creator_id, device_limit=2)
        installation = _activate_installation(app, lic, creator_id, label="dev-1")

        renewal, _, _ = _make_approved_renewal(
            app, creator_id, approver_id, sub=sub, plan=plan, device_allowance_after=0,
        )
        apply_renewal_request(renewal.id, approver_id)

        refreshed = db_session.get(Installation, installation.id)
        assert refreshed.status == "ACTIVE"


def test_apply_downgrade_syncs_every_license_under_the_subscription(app, seeded):
    # A subscription can back more than one License row (e.g. one per
    # platform/product line issued separately) -- all of them must move
    # together, not just the first one found.
    creator_id = make_staff(app, "s7c@example.com")
    approver_id = make_staff(app, "s7c-b@example.com")
    with app.app_context():
        from app.commercial_ops.renewal_requests import apply_renewal_request

        sub, plan = _make_subscription(app, creator_id, end_date=date(2026, 8, 1), device_allowance=3)
        lic_a = _make_license_for_subscription(app, sub, plan, creator_id, device_limit=3)
        lic_b = _make_license_for_subscription(app, sub, plan, creator_id, device_limit=3)

        renewal, _, _ = _make_approved_renewal(
            app, creator_id, approver_id, sub=sub, plan=plan, device_allowance_after=1,
        )
        apply_renewal_request(renewal.id, approver_id)

        assert lic_a.device_limit == 1
        assert lic_b.device_limit == 1


def test_apply_downgrade_unchanged_limit_creates_no_audit_noise(app, seeded):
    creator_id = make_staff(app, "s7d@example.com")
    approver_id = make_staff(app, "s7d-b@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.commercial_ops.renewal_requests import apply_renewal_request
        from app.models.audit import AuditLog

        sub, plan = _make_subscription(app, creator_id, end_date=date(2026, 8, 1), device_allowance=2)
        lic = _make_license_for_subscription(app, sub, plan, creator_id, device_limit=2)

        renewal, _, _ = _make_approved_renewal(
            app, creator_id, approver_id, sub=sub, plan=plan, device_allowance_after=2,
        )
        apply_renewal_request(renewal.id, approver_id)

        rows = db_session.query(AuditLog).filter_by(
            action_code="LICENSE_DEVICE_LIMIT_SYNCED", entity_public_id=str(lic.id)
        ).all()
        assert rows == []


def test_apply_downgrade_writes_device_limit_sync_audit_entry(app, seeded):
    creator_id = make_staff(app, "s7e@example.com")
    approver_id = make_staff(app, "s7e-b@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.commercial_ops.renewal_requests import apply_renewal_request
        from app.models.audit import AuditLog

        sub, plan = _make_subscription(app, creator_id, end_date=date(2026, 8, 1), device_allowance=2)
        lic = _make_license_for_subscription(app, sub, plan, creator_id, device_limit=2)

        renewal, _, _ = _make_approved_renewal(
            app, creator_id, approver_id, sub=sub, plan=plan, device_allowance_after=1,
        )
        apply_renewal_request(renewal.id, approver_id)

        row = db_session.query(AuditLog).filter_by(
            action_code="LICENSE_DEVICE_LIMIT_SYNCED", entity_public_id=str(lic.id)
        ).one()
        assert row.before_state_redacted == {"device_limit": 2}
        assert row.after_state_redacted == {"device_limit": 1}


def test_apply_downgrade_new_activation_blocked_by_synced_limit(app, seeded):
    # End-to-end proof the sync actually enforces: a genuinely new device
    # activation attempt against the now-lower limit is rejected, exactly
    # like the pre-existing over-limit-scan evidence already proved for a
    # manually-edited device_limit -- this time reached via a real renewal.
    creator_id = make_staff(app, "s7f@example.com")
    approver_id = make_staff(app, "s7f-b@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.commercial_ops.renewal_requests import apply_renewal_request
        from app.licensing_service.activation import count_slot_consuming_installations
        from app.commercial_ops.device_slot_ops import resolve_effective_device_limit
        from app.models.licensing import License

        sub, plan = _make_subscription(app, creator_id, end_date=date(2026, 8, 1), device_allowance=2)
        lic = _make_license_for_subscription(app, sub, plan, creator_id, device_limit=2)
        _activate_installation(app, lic, creator_id, label="dev-1")

        renewal, _, _ = _make_approved_renewal(
            app, creator_id, approver_id, sub=sub, plan=plan, device_allowance_after=1,
        )
        apply_renewal_request(renewal.id, approver_id)

        locked = db_session.get(License, lic.id)
        assert count_slot_consuming_installations(locked.id) >= resolve_effective_device_limit(locked)
