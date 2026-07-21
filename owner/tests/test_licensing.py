from __future__ import annotations

import uuid

import pytest

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_staff


def _make_license(app, staff_id):
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.licensing.services import create_license
    from app.subscriptions.services import create_subscription

    product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
    plan = Plan(plan_code="LIC1", product_id=product.id, name="LIC1", billing_model="MONTHLY", currency="USD")
    customer = Customer(legal_name="License Test Co")
    db_session.add_all([plan, customer])
    db_session.commit()
    sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id}, staff_id)
    return create_license(
        {"customer_id": customer.id, "subscription_id": sub.id, "product_id": product.id, "plan_id": plan.id,
         "allowed_platforms": "WINDOWS,ANDROID", "device_limit": 1},
        staff_id,
    )


def test_generated_key_has_expected_format_and_entropy(app):
    from app.security.license_keys import generate_license_key

    full_key, prefix, masked = generate_license_key("AURA_CLINIC")
    assert full_key.startswith("AURA-CLN-1-")
    parts = full_key.split("-")
    assert len(parts) == 8  # AURA, CLN, 1, then 5 groups of 4
    # >=100 bits from a 32-symbol alphabet across 20 symbols: log2(32**20) = 100 bits.
    assert len("".join(parts[3:])) == 20


def test_generated_keys_are_not_sequential_or_deterministic(app):
    from app.security.license_keys import generate_license_key

    keys = {generate_license_key("AURA_CLINIC")[0] for _ in range(50)}
    assert len(keys) == 50  # no collisions across 50 generations


def test_full_key_never_persisted_in_plaintext(app, seeded):
    staff_id = make_staff(app, "lic1@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.services import issue_license_key

        license_row = _make_license(app, staff_id)
        license_row, full_key = issue_license_key(license_row, "test-pepper", str(uuid.uuid4()), staff_id)
        assert full_key is not None

        db_session.expire_all()
        from app.models.licensing import License

        stored = db_session.get(License, license_row.id)
        assert stored.key_secret_hmac != full_key
        assert full_key not in (stored.key_secret_hmac or "")
        assert stored.key_prefix in full_key  # only the non-secret prefix matches
        assert stored.key_suffix_masked.replace("*", "") in full_key  # only masked suffix matches


def test_reveal_only_happens_once_idempotent_replay_returns_no_key(app, seeded):
    staff_id = make_staff(app, "lic2@example.com")
    with app.app_context():
        from app.licensing.services import issue_license_key

        license_row = _make_license(app, staff_id)
        idem_key = str(uuid.uuid4())
        _, full_key_1 = issue_license_key(license_row, "test-pepper", idem_key, staff_id)
        _, full_key_2 = issue_license_key(license_row, "test-pepper", idem_key, staff_id)
        assert full_key_1 is not None
        assert full_key_2 is None  # replay of the same idempotency key never re-reveals the secret


def test_issuance_event_never_contains_the_secret(app, seeded):
    staff_id = make_staff(app, "lic3@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.services import issue_license_key
        from app.models.audit import AuditLog

        license_row = _make_license(app, staff_id)
        _, full_key = issue_license_key(license_row, "test-pepper", str(uuid.uuid4()), staff_id)

        events = db_session.query(AuditLog).filter_by(action_code="LICENSE_KEY_ISSUED").all()
        assert len(events) == 1
        payload = str(events[0].after_state_redacted)
        assert full_key not in payload
        assert full_key.split("-", 3)[-1] not in payload  # none of the 5 secret groups leak


def test_verify_license_key_hmac(app):
    from app.security.license_keys import generate_license_key, hash_license_secret, verify_license_key

    full_key, _, _ = generate_license_key("AURA_RETAIL")
    stored = hash_license_secret(full_key, "pepper-a")
    assert verify_license_key(full_key, "pepper-a", stored) is True
    assert verify_license_key(full_key, "pepper-b", stored) is False  # wrong pepper fails
    assert verify_license_key("wrong-key", "pepper-a", stored) is False


def test_invalid_status_transition_rejected(app, seeded):
    from app.licensing.services import InvalidLicenseTransitionError, transition_license

    staff_id = make_staff(app, "lic4@example.com")
    with app.app_context():
        license_row = _make_license(app, staff_id)
        with pytest.raises(InvalidLicenseTransitionError):
            transition_license(license_row, "REVOKED", staff_id)  # DRAFT -> REVOKED is not a valid path


def test_issuance_route_requires_permission_and_recent_auth(app, client, seeded):
    staff_id = make_staff(app, "lic5@example.com", role_codes=["SUPPORT"])  # SUPPORT has no licenses.issue
    force_login(client, app, staff_id)
    with app.app_context():
        license_row = _make_license(app, staff_id)
        license_id = license_row.id
    resp = client.post(f"/licenses/{license_id}/issue", data={"csrf_token": "x", "idempotency_key": "abc"})
    assert resp.status_code in (400, 403)


def test_issuance_route_redirects_to_reauth_without_recent_mfa(app, client, seeded):
    staff_id = make_staff(app, "lic6@example.com", super_admin=True, mfa=True)
    with app.app_context():
        license_row = _make_license(app, staff_id)
        license_id = license_row.id
    login_and_verify_mfa(client, "lic6@example.com")
    # Force the recent-auth window to have expired.
    with app.app_context():
        from datetime import timedelta

        from app.extensions import db_session
        from app.models.base import utcnow
        from app.models.staff import StaffSession

        session_row = db_session.query(StaffSession).order_by(StaffSession.created_at.desc()).first()
        session_row.mfa_verified_at = utcnow() - timedelta(seconds=app.config["RECENT_AUTH_WINDOW_SECONDS"] + 5)
        db_session.commit()

    page = client.get(f"/licenses/{license_id}")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(f"/licenses/{license_id}/issue", data={"csrf_token": csrf, "idempotency_key": "xyz"})
    assert resp.status_code == 302
    assert "reauth" in resp.headers["Location"]
