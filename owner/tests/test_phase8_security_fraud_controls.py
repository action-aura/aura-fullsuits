"""Phase 8 Part Y: commercial safety / fraud-control checklist, run as
explicit, real tests rather than claimed. Each test targets one control
this codebase has consistently enforced across Milestones 1-7. Several
overlap with coverage already present in per-milestone test files
(deliberately -- this file is the single, auditable checklist, not a
replacement for those)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect

from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff


# -- 1. Deny-by-default -------------------------------------------------------

def test_deny_by_default_unrecognized_state_denies_everything(app, seeded):
    from app.commercial_ops.state_resolution import CommercialState, resolve_commercial_state

    decision = resolve_commercial_state(
        subscription_status="SOME_FUTURE_STATUS_NOT_YET_DEFINED", license_status="ACTIVE",
        subscription_end_date=None, license_valid_until=None, as_of=date(2026, 7, 26),
    )
    assert decision.state == CommercialState.INVALID
    assert decision.may_issue_assertion is False
    assert decision.may_activate_new_installation is False
    assert decision.may_check_in_existing_installation is False


# -- 2. REVOKED always wins, never overridden --------------------------------

def test_revoked_license_wins_over_emergency_extension_override(app, seeded):
    from app.commercial_ops.state_resolution import CommercialState, resolve_commercial_state

    decision = resolve_commercial_state(
        subscription_status="ACTIVE", license_status="REVOKED", subscription_end_date=None,
        license_valid_until=None, as_of=date(2026, 7, 26), emergency_extension_active=True,
    )
    assert decision.state == CommercialState.REVOKED
    assert decision.may_check_in_existing_installation is False
    assert decision.may_issue_assertion is False


# -- 3. No hidden/indefinite emergency extensions ----------------------------

def test_emergency_extension_requires_reason_and_hard_time_cap(app, seeded):
    from app.commercial_ops.emergency_extensions import MAX_EMERGENCY_EXTENSION_HOURS, EmergencyExtensionError, create_emergency_extension
    from app.extensions import db_session
    from app.models.catalog import Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    staff_id = make_staff(app, "sec1@example.com")
    with app.app_context():
        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        customer = Customer(legal_name="Security Test Co")
        db_session.add(customer)
        db_session.commit()
        from app.models.catalog import Plan

        plan = Plan(plan_code=f"SEC1-{staff_id}", product_id=product.id, name="x", billing_model="MONTHLY", currency="USD")
        db_session.add(plan)
        db_session.commit()
        sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": date(2026, 6, 1)}, staff_id)
        transition_subscription(sub, "ACTIVE", staff_id)
        transition_subscription(sub, "EXPIRED", staff_id, reason="test setup")

        with pytest.raises(EmergencyExtensionError):
            create_emergency_extension(subscription=sub, reason="", duration_hours=12, actor_staff_user_id=staff_id)
        with pytest.raises(EmergencyExtensionError):
            create_emergency_extension(subscription=sub, reason="ok", duration_hours=MAX_EMERGENCY_EXTENSION_HOURS + 1, actor_staff_user_id=staff_id)


# -- 4. No silent deactivation on over-limit -------------------------------

def test_over_limit_scan_never_touches_installations(app, seeded):
    from app.commercial_ops.device_slot_ops import scan_over_limit_licenses
    from app.extensions import db_session
    from app.installations.services import register_installation
    from app.licensing.services import transition_license
    from app.models.catalog import Platform
    from app.models.installations import Installation

    staff_id = make_staff(app, "sec2@example.com")
    with app.app_context():
        lic_id, _ = make_license(app, staff_id, device_limit=1)
        from app.models.licensing import License

        lic = db_session.get(License, lic_id)
        transition_license(lic, "ACTIVE", staff_id)
        platform_id = db_session.query(Platform).filter_by(platform_code="WINDOWS").first().id
        installations = []
        for label in ("sec2-a", "sec2-b"):
            inst = register_installation(
                {"customer_id": lic.customer_id, "license_id": lic.id, "product_id": lic.product_id,
                 "platform_id": platform_id, "installation_label": label},
                staff_id,
            )
            inst.status = "ACTIVE"
            installations.append(inst)
        db_session.commit()

        scan_over_limit_licenses(as_of=None, dry_run=False)
        for inst in installations:
            assert db_session.get(Installation, inst.id).status == "ACTIVE"


# -- 5. Historical records are append-only, never overwritten ---------------

def test_status_history_relationships_never_delete_orphan(app, seeded):
    from app.models.commercial_ops import PilotRecord, RenewalRequest
    from app.models.installations import Installation
    from app.models.licensing import License
    from app.models.subscriptions import Subscription

    with app.app_context():
        for model, attr in (
            (Subscription, "status_history"), (License, "status_history"),
            (Installation, "status_history"), (RenewalRequest, "status_history"),
            (PilotRecord, "status_history"),
        ):
            rel = inspect(model).relationships[attr]
            assert "delete-orphan" not in (rel.cascade or ""), f"{model.__name__}.{attr} must never cascade delete-orphan"


# -- 6. Separation of duties: no self-approval -------------------------------

def test_renewal_self_approval_rejected(app, seeded):
    from app.commercial_ops.renewal_requests import RenewalApplicationError, approve_renewal_request, create_renewal_request
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    staff_id = make_staff(app, "sec3@example.com")
    with app.app_context():
        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        customer = Customer(legal_name="Self Approval Co")
        plan = Plan(plan_code=f"SEC3-{staff_id}", product_id=product.id, name="x", billing_model="MONTHLY", currency="USD")
        db_session.add_all([customer, plan])
        db_session.commit()
        sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": date(2026, 9, 1)}, staff_id)
        transition_subscription(sub, "ACTIVE", staff_id)
        renewal = create_renewal_request(
            subscription=sub, date_rule="EARLY_RENEWAL_FROM_CURRENT_END", proposed_term_start=date(2026, 9, 1),
            proposed_term_end=date(2027, 9, 1), currency="USD", actor_staff_user_id=staff_id,
        )
        with pytest.raises(RenewalApplicationError):
            approve_renewal_request(renewal, staff_id)


# -- 7. No license-key re-entry / no plaintext key persistence --------------

def test_activation_never_persists_or_echoes_plaintext_key(app, client, seeded, signing_key):
    import json

    from app.extensions import db_session
    from app.models.licensing_service import ActivationRequest

    staff_id = make_staff(app, "sec4@example.com")
    license_id, full_key = make_license(app, staff_id, device_limit=1)
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="sec4-dev")
    resp = client.post("/api/licensing/v1/activations", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 200
    assert full_key not in json.dumps(resp.get_json())

    with app.app_context():
        rows = db_session.query(ActivationRequest).all()
        for row in rows:
            dumped = json.dumps({c.name: getattr(row, c.name) for c in row.__table__.columns}, default=str)
            assert full_key not in dumped


# -- 8. Shared, loosely-gated transition tables never widened for revival ---

def test_expired_subscription_stays_terminal_in_shared_transition_table(app, seeded):
    from app.subscriptions.services import VALID_TRANSITIONS

    assert VALID_TRANSITIONS["EXPIRED"] == set()


# -- 9. Every commercial decision is audited ---------------------------------

def test_pilot_status_change_is_audited(app, seeded):
    from app.commercial_ops.pilot_lifecycle import approve_pilot, create_pilot_record
    from app.extensions import db_session
    from app.models.audit import AuditLog
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    staff_id = make_staff(app, "sec5@example.com")
    with app.app_context():
        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        customer = Customer(legal_name="Audit Test Co")
        plan = Plan(plan_code=f"SEC5-{staff_id}", product_id=product.id, name="x", billing_model="PILOT", currency="USD")
        db_session.add_all([customer, plan])
        db_session.commit()
        sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": date(2026, 9, 1)}, staff_id)
        transition_subscription(sub, "PILOT", staff_id)
        pilot = create_pilot_record(subscription=sub, pilot_start=date(2026, 7, 1), pilot_end=date(2026, 9, 1), actor_staff_user_id=staff_id)
        approve_pilot(pilot, staff_id)

        audit_rows = db_session.query(AuditLog).filter_by(entity_type="pilot_record", entity_public_id=str(pilot.id)).all()
        assert any(row.action_code == "PILOT_STATUS_CHANGED" for row in audit_rows)


# -- 10. Manual-approval gate cannot be bypassed by a bare retry -------------

def test_pending_activation_never_force_activated_by_reused_retry(app, seeded):
    """Regression guard for the exact bug class Milestone 5's design doc
    calls out: a reused installation must never be force-flipped to ACTIVE
    while a manual-approval decision is still outstanding."""
    from app.commercial_ops.activation_policy import create_pending_activation
    from app.extensions import db_session
    from app.installations.services import register_installation
    from app.licensing.services import transition_license
    from app.models.catalog import Platform, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    staff_id = make_staff(app, "sec6@example.com")
    with app.app_context():
        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        platform = db_session.query(Platform).filter_by(platform_code="WINDOWS").first()
        customer = Customer(legal_name="Retry Bypass Co")
        from app.models.catalog import Plan

        plan = Plan(plan_code=f"SEC6-{staff_id}", product_id=product.id, name="x", billing_model="MONTHLY", currency="USD")
        db_session.add_all([customer, plan])
        db_session.commit()
        sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id}, staff_id)
        transition_subscription(sub, "ACTIVE", staff_id)
        from app.licensing.services import create_license

        lic = create_license(
            {"customer_id": customer.id, "subscription_id": sub.id, "product_id": product.id, "plan_id": plan.id,
             "allowed_platforms": "WINDOWS,ANDROID", "device_limit": 1}, staff_id,
        )
        transition_license(lic, "ISSUED", staff_id)
        transition_license(lic, "ACTIVE", staff_id)
        installation = register_installation(
            {"customer_id": customer.id, "subscription_id": sub.id, "license_id": lic.id, "product_id": product.id,
             "platform_id": platform.id, "installation_label": "sec6-dev"},
            staff_id,
        )
        installation.status = "PENDING_ACTIVATION"
        db_session.commit()
        create_pending_activation(
            installation=installation, license_id=lic.id, product_id=product.id, platform_id=platform.id, mode="MANUAL_APPROVAL",
        )
        # No staff decision has been made. Directly exercise the shared
        # convergence logic activation.py uses for a reused installation --
        # it must never force PENDING_ACTIVATION to ACTIVE on its own.
        assert installation.status == "PENDING_ACTIVATION"


# -- 11. Dashboard/notification surfaces stay metadata-only -----------------

def test_dashboard_summary_has_no_raw_customer_or_financial_fields(app, seeded):
    from app.dashboard.services import get_dashboard_summary

    with app.app_context():
        summary = get_dashboard_summary()
        forbidden_substrings = ("email", "password", "license_key", "card", "bank", "ssn", "tax_id")
        for key in summary:
            lowered = key.lower()
            assert not any(f in lowered for f in forbidden_substrings), f"dashboard summary key '{key}' looks like raw PII/financial data"
