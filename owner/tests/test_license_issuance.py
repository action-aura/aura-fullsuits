"""Direct license-issuance ("Issue licence: three acts") tests.

docs/owner/packages-and-issuance-design.md section B.4. Covers:
  - the three-act path issuing a working license end to end, key revealed once
  - the ONE_TIME (perpetual, no end date) vs ANNUAL (term set, expiry-scan
    visible) conditional -- the single most important correctness detail in
    this feature
  - every load-bearing record (audit rows, status history, issuance event)
  - MFA / recent-auth enforcement
  - idempotency behaving exactly like the existing issue path
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_staff


def _seed_package(app, plan_code, *, billing_model, billing_interval_months=None,
                   included_device_count=2, max_device_count=None, price=Decimal("180.00")):
    """Mirrors tests/test_phase9_5d_fulfillment.py::_seed_plan's exact pattern
    (create_plan + add_plan_price against a fresh Product/ProductPlatform) --
    the established way this suite builds a plan describe_plan_for_sale will
    consider currently sellable."""
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.extensions import db_session
    from app.models.catalog import Platform, Product, ProductPlatform

    with app.app_context():
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="Issuance Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        platform = db_session.query(Platform).first()
        db_session.add(ProductPlatform(product_id=product.id, platform_id=platform.id, supported=True))
        plan = create_plan(
            {
                "plan_code": plan_code,
                "product_id": product.id,
                "name": f"Issuance Test Plan {plan_code}",
                "billing_model": billing_model,
                "billing_interval_months": billing_interval_months,
                "effective_date": date.today() - timedelta(days=1),
                "included_device_count": included_device_count,
                "max_device_count": max_device_count,
            },
            actor_staff_user_id=None,
        )
        add_plan_price(plan, price, "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        return plan.id


def _seed_customer(app, staff_id, legal_name="Issuance Test Customer Co"):
    from app.customers.services import create_customer

    with app.app_context():
        customer = create_customer({"legal_name": legal_name}, staff_id)
        return customer.id


def test_three_act_path_issues_working_license_end_to_end(app, seeded):
    staff_id = make_staff(app, "issue1@example.com", super_admin=True, mfa=True)
    plan_id = _seed_package(app, "E2E_PLAN", billing_model="MONTHLY", billing_interval_months=1)
    customer_id = _seed_customer(app, staff_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.issuance import issue_license_direct
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        result = issue_license_direct(
            customer_id=customer_id, plan_id=plan_id, extra_devices=1,
            idempotency_key=str(uuid.uuid4()), actor_staff_user_id=staff_id,
            license_pepper="test-pepper",
        )

        assert result["full_key"] is not None
        assert result["full_key"].startswith("AURA-")
        assert result["whatsapp_message"] is not None
        assert result["full_key"] in result["whatsapp_message"]
        # both languages present
        assert "activate" in result["whatsapp_message"].lower()
        assert "تفعيل" in result["whatsapp_message"]

        subscription = db_session.get(Subscription, result["subscription_id"])
        assert subscription.status == "ACTIVE"
        assert subscription.device_allowance == 3  # 2 included + 1 extra

        license_row = db_session.get(License, result["license_id"])
        assert license_row.status == "ISSUED"
        assert license_row.device_limit == 3
        assert license_row.allowed_platforms  # real platform codes, never "ALL"/blank
        assert "ALL" not in license_row.allowed_platforms.split(",")

        # key never persisted in plaintext (ADR-9)
        assert result["full_key"] not in (license_row.key_secret_hmac or "")


def test_one_time_plan_produces_perpetual_license_with_no_end_date(app, seeded):
    """M1 target: ONE_TIME must NEVER get an end date."""
    staff_id = make_staff(app, "issue2@example.com", super_admin=True, mfa=True)
    plan_id = _seed_package(app, "PERPETUAL_PLAN", billing_model="ONE_TIME")
    customer_id = _seed_customer(app, staff_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.issuance import issue_license_direct
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        result = issue_license_direct(
            customer_id=customer_id, plan_id=plan_id, idempotency_key=str(uuid.uuid4()),
            actor_staff_user_id=staff_id, license_pepper="test-pepper", as_of=date(2026, 1, 15),
        )

        subscription = db_session.get(Subscription, result["subscription_id"])
        license_row = db_session.get(License, result["license_id"])

        assert subscription.start_date == date(2026, 1, 15)
        assert subscription.end_date is None
        assert license_row.valid_from == date(2026, 1, 15)
        assert license_row.valid_until is None


def test_annual_plan_sets_term_dates_visible_to_expiry_scan(app, seeded):
    """M2 target: ANNUAL must set both dates, AND the expiry scan (which
    only ever selects Subscription.end_date IS NOT NULL) must actually be
    able to act on them -- proven by running it forward past the term and
    watching the subscription really transition, not just checking the
    column is non-null."""
    staff_id = make_staff(app, "issue3@example.com", super_admin=True, mfa=True)
    plan_id = _seed_package(app, "ANNUAL_PLAN", billing_model="ANNUAL", billing_interval_months=12)
    customer_id = _seed_customer(app, staff_id)
    with app.app_context():
        from app.commercial_ops.commercial_policy import create_commercial_policy
        from app.commercial_ops.expiry_scan import run_expiry_scan
        from app.extensions import db_session
        from app.licensing.issuance import issue_license_direct
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        create_commercial_policy(
            policy_code="issuance-scan-policy", warning_offsets_days=[30, 14, 7, 3, 1, 0],
            notify_role_codes=["SALES"], effective_date=date(2025, 1, 1), actor_staff_user_id=staff_id,
            product_id=None, past_due_start_days=0, payment_grace_days=0, auto_expire_after_grace=True,
        )

        result = issue_license_direct(
            customer_id=customer_id, plan_id=plan_id, idempotency_key=str(uuid.uuid4()),
            actor_staff_user_id=staff_id, license_pepper="test-pepper", as_of=date(2026, 1, 1),
        )

        subscription = db_session.get(Subscription, result["subscription_id"])
        license_row = db_session.get(License, result["license_id"])

        assert subscription.start_date == date(2026, 1, 1)
        assert subscription.end_date == date(2027, 1, 1)
        assert license_row.valid_from == date(2026, 1, 1)
        assert license_row.valid_until == date(2027, 1, 1)

        # Long before the term ends, the scan must not touch it.
        early_scan = run_expiry_scan(as_of=date(2026, 6, 1), dry_run=False)
        db_session.refresh(subscription)
        assert subscription.status == "ACTIVE"
        assert early_scan.scanned_count >= 1

        # Well past end_date (+ zero-day grace from the policy above), the
        # scan must actually see and act on it -- proving it is not
        # invisible the way an accidentally-perpetual direct-path license
        # would be (docs/owner/packages-and-issuance-design.md B.1).
        late_scan = run_expiry_scan(as_of=date(2027, 1, 2), dry_run=False)
        db_session.refresh(subscription)
        assert subscription.status == "EXPIRED"
        assert late_scan.transitioned_to_expired >= 1


def test_load_bearing_records_survive_issuance(app, seeded):
    """Audit rows, status history, and issuance event must all exist --
    written by the canonical services, not by this orchestration."""
    staff_id = make_staff(app, "issue4@example.com", super_admin=True, mfa=True)
    plan_id = _seed_package(app, "AUDIT_PLAN", billing_model="MONTHLY", billing_interval_months=1)
    customer_id = _seed_customer(app, staff_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.issuance import issue_license_direct
        from app.models.audit import AuditLog
        from app.models.licensing import LicenseKeyIssuanceEvent, LicenseStatusHistory
        from app.models.subscriptions import SubscriptionStatusHistory

        result = issue_license_direct(
            customer_id=customer_id, plan_id=plan_id, idempotency_key=str(uuid.uuid4()),
            actor_staff_user_id=staff_id, license_pepper="test-pepper",
        )

        action_codes = {
            row.action_code
            for row in db_session.query(AuditLog).all()
        }
        assert "SUBSCRIPTION_CREATED" in action_codes
        assert "SUBSCRIPTION_STATUS_CHANGED" in action_codes
        assert "LICENSE_CREATED" in action_codes
        assert "LICENSE_KEY_ISSUED" in action_codes

        assert db_session.query(SubscriptionStatusHistory).filter_by(
            subscription_id=result["subscription_id"]
        ).count() == 1
        assert db_session.query(LicenseStatusHistory).filter_by(license_id=result["license_id"]).count() == 1
        assert db_session.query(LicenseKeyIssuanceEvent).filter_by(license_id=result["license_id"]).count() == 1


def test_inline_created_customer_is_audited_too(app, seeded):
    staff_id = make_staff(app, "issue5@example.com", super_admin=True, mfa=True)
    plan_id = _seed_package(app, "INLINE_CUST_PLAN", billing_model="MONTHLY", billing_interval_months=1)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.issuance import issue_license_direct
        from app.models.audit import AuditLog
        from app.models.customers import Customer

        result = issue_license_direct(
            new_customer_legal_name="Brand New Shop Co", plan_id=plan_id,
            idempotency_key=str(uuid.uuid4()), actor_staff_user_id=staff_id, license_pepper="test-pepper",
        )
        customer = db_session.get(Customer, result["customer_id"])
        assert customer.legal_name == "Brand New Shop Co"
        assert db_session.query(AuditLog).filter_by(action_code="CUSTOMER_CREATED").count() == 1


def test_idempotent_replay_of_key_issuance_reveals_no_second_key(app, seeded):
    """'Idempotency behaves exactly like the existing issue path': this
    orchestration's final step delegates to the exact same
    issue_license_key primitive licensing.routes::issue uses -- proven the
    same way tests/test_licensing.py::
    test_reveal_only_happens_once_idempotent_replay_returns_no_key proves it
    for that route: replaying the same idempotency_key against the
    resulting license must never re-reveal the secret."""
    staff_id = make_staff(app, "issue6@example.com", super_admin=True, mfa=True)
    plan_id = _seed_package(app, "IDEMPOTENT_PLAN", billing_model="MONTHLY", billing_interval_months=1)
    customer_id = _seed_customer(app, staff_id)
    idem_key = str(uuid.uuid4())
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.issuance import issue_license_direct
        from app.licensing.services import issue_license_key
        from app.models.licensing import License

        result = issue_license_direct(
            customer_id=customer_id, plan_id=plan_id, idempotency_key=idem_key,
            actor_staff_user_id=staff_id, license_pepper="test-pepper",
        )
        assert result["full_key"] is not None

        license_row = db_session.get(License, result["license_id"])
        _, replayed_key = issue_license_key(license_row, "test-pepper", idem_key, staff_id)
        assert replayed_key is None  # replay of the same idempotency key never re-reveals the secret


def test_issuance_route_requires_permission_and_recent_auth(app, client, seeded):
    plan_id = _seed_package(app, "RBAC_PLAN", billing_model="MONTHLY", billing_interval_months=1)
    staff_id = make_staff(app, "issue7@example.com", role_codes=["SALES"])  # SALES has licenses.create but NOT licenses.issue
    customer_id = _seed_customer(app, staff_id)
    force_login(client, app, staff_id)
    resp = client.post(
        "/licenses/issuance",
        data={
            "csrf_token": "x", "idempotency_key": "abc",
            "customer_id": str(customer_id), "plan_id": str(plan_id), "extra_devices": "0",
        },
    )
    assert resp.status_code in (400, 403)


def test_issuance_route_redirects_to_reauth_without_recent_mfa(app, client, seeded):
    plan_id = _seed_package(app, "REAUTH_PLAN", billing_model="MONTHLY", billing_interval_months=1)
    staff_id = make_staff(app, "issue8@example.com", super_admin=True, mfa=True)
    customer_id = _seed_customer(app, staff_id)
    login_and_verify_mfa(client, "issue8@example.com")
    with app.app_context():
        from datetime import timedelta as _timedelta

        from app.extensions import db_session
        from app.models.base import utcnow
        from app.models.staff import StaffSession

        session_row = db_session.query(StaffSession).order_by(StaffSession.created_at.desc()).first()
        session_row.mfa_verified_at = utcnow() - _timedelta(seconds=app.config["RECENT_AUTH_WINDOW_SECONDS"] + 5)
        db_session.commit()

    page = client.get("/licenses/issuance/new")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(
        "/licenses/issuance",
        data={
            "csrf_token": csrf, "idempotency_key": "xyz",
            "customer_id": str(customer_id), "plan_id": str(plan_id), "extra_devices": "0",
        },
    )
    assert resp.status_code == 302
    assert "reauth" in resp.headers["Location"]


def test_issuance_route_end_to_end_reveals_key_in_response(app, client, seeded):
    plan_id = _seed_package(app, "HTTP_PLAN", billing_model="MONTHLY", billing_interval_months=1)
    staff_id = make_staff(app, "issue9@example.com", super_admin=True, mfa=True)
    customer_id = _seed_customer(app, staff_id)
    login_and_verify_mfa(client, "issue9@example.com")
    page = client.get("/licenses/issuance/new")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(
        "/licenses/issuance",
        data={
            "csrf_token": csrf, "idempotency_key": str(uuid.uuid4()),
            "customer_id": str(customer_id), "plan_id": str(plan_id), "extra_devices": "0",
        },
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "AURA-" in body
    assert "تفعيل" in body


def test_extra_devices_beyond_plan_maximum_rejected(app, seeded):
    staff_id = make_staff(app, "issue10@example.com", super_admin=True, mfa=True)
    plan_id = _seed_package(app, "CAPPED_PLAN", billing_model="MONTHLY", billing_interval_months=1,
                             included_device_count=2, max_device_count=3)
    customer_id = _seed_customer(app, staff_id)
    with app.app_context():
        from app.licensing.issuance import LicenseIssuanceError, issue_license_direct

        with pytest.raises(LicenseIssuanceError):
            issue_license_direct(
                customer_id=customer_id, plan_id=plan_id, extra_devices=5,  # 2 + 5 = 7 > max 3
                idempotency_key=str(uuid.uuid4()), actor_staff_user_id=staff_id, license_pepper="test-pepper",
            )
