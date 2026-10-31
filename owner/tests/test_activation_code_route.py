"""New customer flow, S5: the staff button "Make activation code" (the customer's
"I have a code" path). Support and Super Admin only, recent MFA, customer scope,
shown once, never stored in the clear, never in the audit log.
"""
from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import func, select

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_device_keypair, make_staff
from tests.test_account_activation import activate, reason, rows, world  # noqa: F401 -- world is a fixture
from tests.test_customer_onboarding import kyc  # noqa: F401 -- a fixture the world needs


def _grants(app) -> int:
    from app.models.account_activation import CustomerActivationGrant

    return len(rows(app, CustomerActivationGrant))


def _post(client, world, path_suffix="activation-code", csrf_from=None):
    page = client.get(f"/licenses/{world['licence_id']}")
    assert page.status_code == 200, page.status_code
    return client.post(f"/licenses/{world['licence_id']}/{path_suffix}", data={"csrf_token": get_csrf(page.get_data(as_text=True))})


def test_support_makes_a_code_that_is_shown_once_and_activates_the_licence(app, client, world):
    support = make_staff(app, "support-ac@example.com", role_codes=["SUPPORT"], mfa=True)
    login_and_verify_mfa(client, "support-ac@example.com")
    page = client.get(f"/licenses/{world['licence_id']}").get_data(as_text=True)
    assert 'data-activation-code="1"' in page and "Make activation code" in page
    resp = _post(client, world)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"
    code = re.search(r'id="activation-code-value"[^>]*>([A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4})<', html).group(1)
    # shown once: the licence page afterwards never carries it
    assert code not in client.get(f"/licenses/{world['licence_id']}").get_data(as_text=True)
    ok = activate(client, make_device_keypair(), code=code)
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()["signed_assertion"]["payload"]["license_public_id"] == world["licence_id"]
    with app.app_context():  # the audit trail records that a code was made, never the code
        from app.extensions import db_session
        from app.models.audit import AuditLog

        entries = db_session.execute(select(AuditLog).where(AuditLog.action_code == "ACTIVATION_CODE_MADE")).scalars().all()
        assert len(entries) == 1 and str(entries[0].actor_staff_user_id) == str(support)
        assert code.replace("-", "") not in repr(entries[0].__dict__)


def test_a_seller_cannot_make_a_code_and_nothing_is_written(app, client, world):
    seller = make_staff(app, "seller-ac@example.com", role_codes=["SALES"], mfa=True)
    login_and_verify_mfa(client, "seller-ac@example.com")
    page = client.get(f"/licenses/{world['licence_id']}")
    assert b'data-activation-code="1"' not in page.data  # no button
    resp = client.post(f"/licenses/{world['licence_id']}/activation-code", data={"csrf_token": get_csrf(page.get_data(as_text=True))})
    assert resp.status_code == 403 and _grants(app) == 0


def test_without_recent_mfa_it_redirects_to_reauthentication_and_writes_nothing(app, client, world):
    support = make_staff(app, "support-nr@example.com", role_codes=["SUPPORT"], mfa=True)
    force_login(client, app, support)  # signed in, but no MFA confirmation in the recent-auth window
    page = client.get(f"/licenses/{world['licence_id']}")
    resp = client.post(f"/licenses/{world['licence_id']}/activation-code", data={"csrf_token": get_csrf(page.get_data(as_text=True))})
    assert resp.status_code == 302 and "reauth" in resp.headers["Location"] and _grants(app) == 0


def test_a_code_needs_an_issued_licence_and_the_feature_on(app, client, world):
    make_staff(app, "admin-ac@example.com", super_admin=True, mfa=True)
    login_and_verify_mfa(client, "admin-ac@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License

        licence = db_session.execute(select(License)).scalars().one()
        licence.status = "REVOKED"
        db_session.commit()
    refused = _post(client, world)
    assert refused.status_code == 302 and _grants(app) == 0
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License

        db_session.execute(select(License)).scalars().one().status = "ISSUED"
        db_session.commit()
    token = get_csrf(client.get(f"/licenses/{world['licence_id']}").get_data(as_text=True))
    app.config.update(CUSTOMER_ACCOUNTS_ENABLED=False)
    off = client.post(f"/licenses/{world['licence_id']}/activation-code", data={"csrf_token": token})
    assert off.status_code == 404 and _grants(app) == 0  # the route does not exist while accounts are off
    app.config.update(CUSTOMER_ACCOUNTS_ENABLED=True)
    assert _post(client, world).status_code == 200 and _grants(app) == 1  # allow twin: super admin, issued licence, feature on
