from __future__ import annotations

from datetime import date, datetime, timedelta

from tests.conftest import make_staff


def _subscription_and_license(app, staff_id, *, sub_status="ACTIVE", license_status="ACTIVE", end_date=date(2026, 9, 1)):
    from app.extensions import db_session
    from app.licensing.services import create_license, transition_license
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
    customer = Customer(legal_name="Reconcile Test Co")
    plan = Plan(plan_code=f"REC-{staff_id}-{end_date.isoformat()}", product_id=product.id, name="x", billing_model="MONTHLY", currency="USD")
    db_session.add_all([customer, plan])
    db_session.commit()

    sub = create_subscription(
        {"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": end_date}, staff_id,
    )
    if sub_status != "DRAFT":
        transition_subscription(sub, "ACTIVE", staff_id)
        if sub_status != "ACTIVE":
            transition_subscription(sub, sub_status, staff_id, reason="test setup")

    lic = create_license(
        {"customer_id": customer.id, "subscription_id": sub.id, "product_id": product.id, "plan_id": plan.id,
         "allowed_platforms": "WINDOWS,ANDROID", "device_limit": 1}, staff_id,
    )
    if license_status != "DRAFT":
        transition_license(lic, "ISSUED", staff_id)
        if license_status != "ISSUED":
            transition_license(lic, "ACTIVE", staff_id)
            if license_status != "ACTIVE":
                transition_license(lic, license_status, staff_id, reason="test setup")
    return sub, lic


# -- state inconsistency ------------------------------------------------------

def test_active_subscription_with_draft_license_flagged(app, seeded):
    staff_id = make_staff(app, "rec1@example.com")
    with app.app_context():
        from app.commercial_ops.reconciliation import run_reconciliation

        sub, lic = _subscription_and_license(app, staff_id, sub_status="ACTIVE", license_status="DRAFT")
        result = run_reconciliation(as_of=date(2026, 7, 26), dry_run=True)
        matches = [f for f in result.findings if f.check == "STATE_INCONSISTENT" and f.entity_id == str(lic.id)]
        assert len(matches) == 1


def test_consistent_pair_not_flagged(app, seeded):
    staff_id = make_staff(app, "rec2@example.com")
    with app.app_context():
        from app.commercial_ops.reconciliation import run_reconciliation

        sub, lic = _subscription_and_license(app, staff_id, sub_status="ACTIVE", license_status="ACTIVE")
        result = run_reconciliation(as_of=date(2026, 7, 26), dry_run=True)
        matches = [f for f in result.findings if f.entity_id == str(lic.id)]
        assert matches == []


def test_replaced_license_excluded_from_state_check(app, seeded):
    staff_id = make_staff(app, "rec3@example.com")
    with app.app_context():
        from app.commercial_ops.reconciliation import run_reconciliation
        from app.extensions import db_session
        from app.licensing.services import transition_license

        sub, lic = _subscription_and_license(app, staff_id, sub_status="ACTIVE", license_status="EXPIRED")
        transition_license(lic, "REPLACED", staff_id)
        db_session.commit()

        result = run_reconciliation(as_of=date(2026, 7, 26), dry_run=True)
        matches = [f for f in result.findings if f.entity_id == str(lic.id)]
        assert matches == []


def test_revoked_license_not_flagged_as_inconsistent(app, seeded):
    """REVOKED has its own dedicated CommercialState branch (always wins) --
    not INVALID, so reconciliation must not flag it."""
    staff_id = make_staff(app, "rec4@example.com")
    with app.app_context():
        from app.commercial_ops.reconciliation import run_reconciliation

        sub, lic = _subscription_and_license(app, staff_id, sub_status="ACTIVE", license_status="REVOKED")
        result = run_reconciliation(as_of=date(2026, 7, 26), dry_run=True)
        matches = [f for f in result.findings if f.entity_id == str(lic.id)]
        assert matches == []


# -- stale approved renewals ---------------------------------------------------

def test_stale_approved_renewal_flagged(app, seeded):
    creator_id = make_staff(app, "rec5-creator@example.com")
    approver_id = make_staff(app, "rec5-approver@example.com")
    with app.app_context():
        from app.commercial_ops.reconciliation import run_reconciliation
        from app.commercial_ops.renewal_requests import approve_renewal_request, create_renewal_request, transition_renewal_request

        sub, _lic = _subscription_and_license(app, creator_id, sub_status="ACTIVE", license_status="ACTIVE")
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

        result_fresh = run_reconciliation(now=renewal.approved_at + timedelta(hours=1), dry_run=True)
        assert not any(f.entity_id == str(renewal.id) for f in result_fresh.findings)

        result_stale = run_reconciliation(now=renewal.approved_at + timedelta(days=8), dry_run=True)
        assert any(f.check == "STALE_APPROVED_RENEWAL" and f.entity_id == str(renewal.id) for f in result_stale.findings)


# -- stale pending activations -------------------------------------------------

def test_stale_pending_activation_flagged(app, seeded):
    staff_id = make_staff(app, "rec6@example.com")
    with app.app_context():
        from app.commercial_ops.activation_policy import create_pending_activation
        from app.commercial_ops.reconciliation import run_reconciliation
        from app.extensions import db_session
        from app.installations.services import register_installation
        from app.licensing.services import transition_license

        sub, lic = _subscription_and_license(app, staff_id, sub_status="ACTIVE", license_status="ACTIVE")
        from app.models.catalog import Platform

        platform = db_session.query(Platform).filter_by(platform_code="WINDOWS").first()
        installation = register_installation(
            {"customer_id": sub.customer_id, "subscription_id": sub.id, "license_id": lic.id, "product_id": lic.product_id,
             "platform_id": platform.id, "installation_label": "rec-dev-1"},
            staff_id,
        )
        installation.status = "PENDING_ACTIVATION"
        db_session.commit()
        pending = create_pending_activation(
            installation=installation, license_id=lic.id, product_id=lic.product_id,
            platform_id=platform.id, mode="MANUAL_APPROVAL",
        )

        result_fresh = run_reconciliation(now=pending.created_at + timedelta(hours=1), dry_run=True)
        assert not any(f.entity_id == str(pending.id) for f in result_fresh.findings)

        result_stale = run_reconciliation(now=pending.created_at + timedelta(hours=49), dry_run=True)
        assert any(f.check == "STALE_PENDING_ACTIVATION" and f.entity_id == str(pending.id) for f in result_stale.findings)


# -- apply mode -----------------------------------------------------------------

def test_apply_creates_deduped_notification(app, seeded):
    staff_id = make_staff(app, "rec7@example.com")
    with app.app_context():
        from app.commercial_ops.reconciliation import run_reconciliation
        from app.extensions import db_session
        from app.models.commercial_ops import InternalNotification

        _sub, lic = _subscription_and_license(app, staff_id, sub_status="ACTIVE", license_status="DRAFT")

        first = run_reconciliation(as_of=date(2026, 7, 26), dry_run=False)
        assert first.notifications_created >= 1
        count_after_first = db_session.query(InternalNotification).count()

        second = run_reconciliation(as_of=date(2026, 7, 26), dry_run=False)
        assert second.notifications_deduped >= 1
        assert db_session.query(InternalNotification).count() == count_after_first
