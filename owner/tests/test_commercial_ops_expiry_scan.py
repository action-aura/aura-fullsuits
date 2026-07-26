from __future__ import annotations

from datetime import date

from tests.conftest import make_staff


def _make_active_subscription(app, staff_id, *, end_date, product_code="AURA_CLINIC", plan_code=None):
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    product = db_session.query(Product).filter_by(product_code=product_code).first()
    plan = Plan(
        plan_code=plan_code or f"SCAN-{staff_id}-{end_date.isoformat()}", product_id=product.id,
        name="Scan Test Plan", billing_model="MONTHLY", currency="USD",
    )
    customer = Customer(legal_name="Scan Test Co")
    db_session.add_all([plan, customer])
    db_session.commit()
    sub = create_subscription(
        {"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": end_date},
        staff_id,
    )
    transition_subscription(sub, "ACTIVE", staff_id)
    return sub


def _make_policy(app, staff_id, *, product_id=None, offsets=(30, 14, 7, 3, 1, 0), past_due_start_days=0,
                  payment_grace_days=0, auto_expire_after_grace=True, policy_code=None):
    from app.commercial_ops.commercial_policy import create_commercial_policy

    return create_commercial_policy(
        policy_code=policy_code or f"scan-policy-{staff_id}-{product_id}",
        warning_offsets_days=list(offsets), notify_role_codes=["SALES"],
        effective_date=date(2026, 1, 1), actor_staff_user_id=staff_id, product_id=product_id,
        past_due_start_days=past_due_start_days, payment_grace_days=payment_grace_days,
        auto_expire_after_grace=auto_expire_after_grace,
    )


def test_dry_run_writes_nothing(app, seeded):
    staff_id = make_staff(app, "es1@example.com")
    with app.app_context():
        from app.commercial_ops.expiry_scan import run_expiry_scan
        from app.extensions import db_session
        from app.models.commercial_ops import InternalNotification

        _make_policy(app, staff_id)
        sub = _make_active_subscription(app, staff_id, end_date=date(2026, 7, 3))  # 3 days out from as_of below

        result = run_expiry_scan(as_of=date(2026, 6, 30), dry_run=True)
        assert result.dry_run is True
        assert result.notifications_created == 0
        assert len(result.findings) > 0
        assert db_session.query(InternalNotification).count() == 0
        # subscription untouched
        assert sub.status == "ACTIVE"


def test_apply_creates_warning_notification_for_crossed_offset(app, seeded):
    staff_id = make_staff(app, "es2@example.com")
    with app.app_context():
        from app.commercial_ops.expiry_scan import run_expiry_scan

        _make_policy(app, staff_id, offsets=(30, 14, 7, 3, 1, 0))
        _make_active_subscription(app, staff_id, end_date=date(2026, 7, 7))  # exactly 7 days from as_of

        result = run_expiry_scan(as_of=date(2026, 6, 30), dry_run=False)
        assert result.notifications_created >= 1
        # 7-day offset and everything looser than it (14, 30) should fire
        # since days_until_end(7) <= offset for 7/14/30.


def test_apply_second_run_dedupes_no_duplicate_notifications(app, seeded):
    staff_id = make_staff(app, "es3@example.com")
    with app.app_context():
        from app.commercial_ops.expiry_scan import run_expiry_scan

        _make_policy(app, staff_id, offsets=(7,))
        _make_active_subscription(app, staff_id, end_date=date(2026, 7, 7))

        first = run_expiry_scan(as_of=date(2026, 6, 30), dry_run=False)
        second = run_expiry_scan(as_of=date(2026, 6, 30), dry_run=False)

        assert first.notifications_created == 1
        assert second.notifications_created == 0
        assert second.notifications_deduped == 1


def test_apply_transitions_active_to_expired_when_immediate_policy(app, seeded):
    staff_id = make_staff(app, "es4@example.com")
    with app.app_context():
        from app.commercial_ops.expiry_scan import run_expiry_scan

        _make_policy(app, staff_id, offsets=(), past_due_start_days=0)
        sub = _make_active_subscription(app, staff_id, end_date=date(2026, 6, 25))  # already past as_of

        result = run_expiry_scan(as_of=date(2026, 6, 30), dry_run=False)
        assert result.transitioned_to_expired == 1
        assert sub.status == "EXPIRED"


def test_apply_transitions_active_to_past_due_then_expired_after_grace(app, seeded):
    staff_id = make_staff(app, "es5@example.com")
    with app.app_context():
        from app.commercial_ops.expiry_scan import run_expiry_scan

        _make_policy(app, staff_id, offsets=(), past_due_start_days=5, payment_grace_days=10, auto_expire_after_grace=True)
        sub = _make_active_subscription(app, staff_id, end_date=date(2026, 6, 20))

        # 10 days past end -- past_due_start_days(5) reached, but grace(10) not yet
        result1 = run_expiry_scan(as_of=date(2026, 6, 30), dry_run=False)
        assert sub.status == "PAST_DUE"
        assert result1.transitioned_to_past_due == 1
        assert result1.transitioned_to_expired == 0

        # 20 days past end -- days_past_due = 20 - 5 = 15 >= grace(10)
        result2 = run_expiry_scan(as_of=date(2026, 7, 10), dry_run=False)
        assert sub.status == "EXPIRED"
        assert result2.transitioned_to_expired == 1


def test_apply_does_not_auto_expire_when_policy_disables_it(app, seeded):
    staff_id = make_staff(app, "es6@example.com")
    with app.app_context():
        from app.commercial_ops.expiry_scan import run_expiry_scan

        _make_policy(
            app, staff_id, offsets=(), past_due_start_days=1, payment_grace_days=5, auto_expire_after_grace=False,
        )
        sub = _make_active_subscription(app, staff_id, end_date=date(2026, 6, 1))

        run_expiry_scan(as_of=date(2026, 6, 3), dry_run=False)  # 2 days past end -- crosses past_due_start_days(1)
        assert sub.status == "PAST_DUE"

        # Far past grace, but auto_expire_after_grace=False -- must stay PAST_DUE.
        run_expiry_scan(as_of=date(2026, 7, 1), dry_run=False)
        assert sub.status == "PAST_DUE"


def test_scan_skips_subscription_with_no_resolvable_policy(app, seeded):
    staff_id = make_staff(app, "es7@example.com")
    with app.app_context():
        from app.commercial_ops.expiry_scan import run_expiry_scan

        # No policy created at all.
        sub = _make_active_subscription(app, staff_id, end_date=date(2026, 6, 1))
        result = run_expiry_scan(as_of=date(2026, 7, 1), dry_run=False)
        assert any(f.action == "SKIPPED_NO_POLICY" for f in result.findings)
        assert sub.status == "ACTIVE"  # untouched


def test_idempotent_rerun_does_not_double_transition(app, seeded):
    staff_id = make_staff(app, "es8@example.com")
    with app.app_context():
        from app.commercial_ops.expiry_scan import run_expiry_scan

        _make_policy(app, staff_id, offsets=(), past_due_start_days=0)
        sub = _make_active_subscription(app, staff_id, end_date=date(2026, 6, 1))

        run_expiry_scan(as_of=date(2026, 7, 1), dry_run=False)
        assert sub.status == "EXPIRED"

        # Second run: subscription no longer in the eligible-status scan
        # scope (ACTIVE, PAST_DUE) -- must not error, must not re-transition.
        result = run_expiry_scan(as_of=date(2026, 7, 2), dry_run=False)
        assert sub.status == "EXPIRED"
        assert result.transitioned_to_expired == 0
