from __future__ import annotations

from datetime import date

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_staff


def _csrf_token_anonymous(client):
    # Only valid for a client with no authenticated session yet -- once
    # logged in, /auth/login redirects away instead of rendering the form
    # (see _csrf_token_authenticated for the post-login equivalent).
    page = client.get("/auth/login")
    return get_csrf(page.get_data(as_text=True))


def _csrf_token_authenticated(client, sub_id):
    # /subscriptions/<id> only requires subscriptions.view (every role used
    # in these tests has it) and never redirects an authenticated staff
    # member away, unlike /auth/login.
    page = client.get(f"/subscriptions/{sub_id}")
    return get_csrf(page.get_data(as_text=True))


def _make_subscription_via_app(app, staff_id, *, end_date="2026-08-01"):
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    with app.app_context():
        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        plan = Plan(
            plan_code=f"ROUTE-{staff_id}", product_id=product.id, name="Route Test Plan",
            billing_model="MONTHLY", currency="USD",
        )
        customer = Customer(legal_name="Route Test Co")
        db_session.add_all([plan, customer])
        db_session.commit()
        sub = create_subscription(
            {"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id,
             "end_date": date.fromisoformat(end_date), "device_allowance": 2},
            staff_id,
        )
        transition_subscription(sub, "ACTIVE", staff_id)
        return str(sub.id)


def test_create_renewal_requires_permission(app, client, seeded):
    staff_id = make_staff(app, "route1@example.com", role_codes=["SUPPORT"])  # no subscriptions.renew
    sub_id = _make_subscription_via_app(app, staff_id)
    force_login(client, app, staff_id)
    csrf = _csrf_token_authenticated(client, sub_id)
    resp = client.post(
        "/commercial-ops/renewals",
        json={
            "subscription_id": sub_id, "date_rule": "EARLY_RENEWAL_FROM_CURRENT_END",
            "proposed_term_start": "2026-08-01", "proposed_term_end": "2026-09-01", "currency": "USD",
        },
        headers={"X-CSRFToken": csrf, "Accept": "application/json"},
    )
    assert resp.status_code == 403


def test_create_renewal_requires_authentication(client, seeded):
    # No login at all -- /auth/login renders the real login form for an
    # anonymous client, so a genuinely valid CSRF token is used here,
    # isolating the "authentication required" check from CSRF rejection.
    csrf = _csrf_token_anonymous(client)
    resp = client.post(
        "/commercial-ops/renewals",
        json={"subscription_id": "00000000-0000-0000-0000-000000000000"},
        headers={"X-CSRFToken": csrf, "Accept": "application/json"},
    )
    assert resp.status_code in (302, 401)


def test_full_renewal_workflow_via_http(app, client, seeded):
    creator_id = make_staff(app, "route2@example.com", role_codes=["SALES"], mfa=True)
    approver_id = make_staff(app, "route2b@example.com", role_codes=["FINANCE"], mfa=True)
    sub_id = _make_subscription_via_app(app, creator_id, end_date="2026-08-01")

    login_and_verify_mfa(client, "route2@example.com")
    csrf = _csrf_token_authenticated(client, sub_id)

    create_resp = client.post(
        "/commercial-ops/renewals",
        json={
            "subscription_id": sub_id, "date_rule": "EARLY_RENEWAL_FROM_CURRENT_END",
            "proposed_term_start": "2026-08-01", "proposed_term_end": "2026-09-01", "currency": "USD",
        },
        headers={"X-CSRFToken": csrf, "Accept": "application/json"},
    )
    assert create_resp.status_code == 201, create_resp.get_data(as_text=True)
    renewal_id = create_resp.get_json()["id"]
    assert create_resp.get_json()["status"] == "DRAFT"

    for to_status in ("QUOTED", "AWAITING_CONFIRMATION", "AWAITING_PAYMENT", "PAYMENT_RECORDED"):
        resp = client.post(
            f"/commercial-ops/renewals/{renewal_id}/transition",
            json={"to_status": to_status},
            headers={"X-CSRFToken": csrf, "Accept": "application/json"},
        )
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["status"] == to_status

    # A fresh client, not a logout+relogin on the same one -- /auth/logout
    # requires POST (with its own CSRF token) to actually revoke the first
    # session; a plain GET would silently no-op, and login_submit()
    # redirects an already-authenticated request straight past the MFA
    # flow, leaving the FIRST staff member's session in effect for the
    # "approver" calls below and making the self-approval guard fire for
    # the wrong reason. A second client sidesteps that entirely.
    approver_client = app.test_client()
    login_and_verify_mfa(approver_client, "route2b@example.com")
    csrf2 = _csrf_token_authenticated(approver_client, sub_id)

    approve_resp = approver_client.post(
        f"/commercial-ops/renewals/{renewal_id}/approve",
        json={},
        headers={"X-CSRFToken": csrf2, "Accept": "application/json"},
    )
    assert approve_resp.status_code == 200, approve_resp.get_json()
    assert approve_resp.get_json()["status"] == "APPROVED"

    apply_resp = approver_client.post(
        f"/commercial-ops/renewals/{renewal_id}/apply",
        headers={"X-CSRFToken": csrf2, "Accept": "application/json"},
    )
    assert apply_resp.status_code == 200, apply_resp.get_json()
    body = apply_resp.get_json()
    assert body["status"] == "APPLIED"
    assert body["applied_renewal_record_id"] is not None

    get_resp = approver_client.get(f"/commercial-ops/renewals/{renewal_id}")
    assert get_resp.status_code == 200
    assert get_resp.get_json()["status"] == "APPLIED"


def test_self_approval_rejected_via_http(app, client, seeded):
    staff_id = make_staff(app, "route3@example.com", role_codes=["SALES"], mfa=True)
    sub_id = _make_subscription_via_app(app, staff_id, end_date="2026-08-01")

    login_and_verify_mfa(client, "route3@example.com")
    csrf = _csrf_token_authenticated(client, sub_id)

    create_resp = client.post(
        "/commercial-ops/renewals",
        json={
            "subscription_id": sub_id, "date_rule": "EARLY_RENEWAL_FROM_CURRENT_END",
            "proposed_term_start": "2026-08-01", "proposed_term_end": "2026-09-01", "currency": "USD",
        },
        headers={"X-CSRFToken": csrf, "Accept": "application/json"},
    )
    renewal_id = create_resp.get_json()["id"]
    for to_status in ("QUOTED", "AWAITING_CONFIRMATION", "AWAITING_PAYMENT", "PAYMENT_RECORDED"):
        client.post(
            f"/commercial-ops/renewals/{renewal_id}/transition",
            json={"to_status": to_status},
            headers={"X-CSRFToken": csrf, "Accept": "application/json"},
        )

    approve_resp = client.post(
        f"/commercial-ops/renewals/{renewal_id}/approve",
        json={},
        headers={"X-CSRFToken": csrf, "Accept": "application/json"},
    )
    assert approve_resp.status_code == 403
    assert approve_resp.get_json()["error"] == "self_approval_not_allowed"


def test_apply_without_recent_auth_rejected(app, client, seeded):
    # force_login never sets mfa_verified_at -- require_recent_auth on
    # /approve and /apply must reject it even though the session itself is
    # otherwise valid and has the right permission.
    staff_id = make_staff(app, "route4@example.com", role_codes=["FINANCE"])
    sub_id = _make_subscription_via_app(app, staff_id)
    force_login(client, app, staff_id)
    csrf = _csrf_token_authenticated(client, sub_id)
    resp = client.post(
        "/commercial-ops/renewals/00000000-0000-0000-0000-000000000000/apply",
        headers={"X-CSRFToken": csrf, "Accept": "application/json"},
    )
    assert resp.status_code == 401
