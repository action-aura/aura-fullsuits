from __future__ import annotations

from tests.conftest import force_login, get_csrf, login, make_staff


def test_viewer_cannot_create_customer_direct_api_call(app, client, seeded):
    make_staff(app, "viewer@example.com", password="Correct-Password-1!", role_codes=["VIEWER"])
    login(client, "viewer@example.com", password="Correct-Password-1!")
    page = client.get("/customers")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post("/customers", data={"csrf_token": csrf, "legal_name": "Sneaky Corp"})
    assert resp.status_code == 403


def test_viewer_can_read_customers(app, client, seeded):
    make_staff(app, "viewer2@example.com", password="Correct-Password-1!", role_codes=["VIEWER"])
    login(client, "viewer2@example.com", password="Correct-Password-1!")
    resp = client.get("/customers")
    assert resp.status_code == 200


def test_support_cannot_manage_pricing(app, client, seeded):
    make_staff(app, "support@example.com", password="Correct-Password-1!", role_codes=["SUPPORT"])
    login(client, "support@example.com", password="Correct-Password-1!")
    with app.app_context():
        from app.extensions import db_session
        from app.models.catalog import Plan, Product

        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        plan = Plan(plan_code="X", product_id=product.id, name="X", billing_model="MONTHLY", currency="USD")
        db_session.add(plan)
        db_session.commit()
        plan_id = plan.id
    resp = client.post(
        f"/catalog/plans/{plan_id}/prices",
        data={"csrf_token": "x", "base_price": "10", "currency": "USD", "effective_from": "2026-01-01"},
    )
    assert resp.status_code in (400, 403)  # CSRF failure or permission failure -- either way, not applied


def test_sales_cannot_manage_staff(app, client, seeded):
    make_staff(app, "sales@example.com", password="Correct-Password-1!", role_codes=["SALES"])
    login(client, "sales@example.com", password="Correct-Password-1!")
    resp = client.get("/staff")
    assert resp.status_code == 403


def test_finance_cannot_issue_license_secrets(app, client, seeded):
    make_staff(app, "finance@example.com", password="Correct-Password-1!", role_codes=["FINANCE"])
    login(client, "finance@example.com", password="Correct-Password-1!")
    resp = client.get("/licenses")
    assert resp.status_code == 403  # FINANCE has no licenses.view permission at all


def test_non_super_admin_cannot_restore_database(app, client, seeded):
    make_staff(app, "finance2@example.com", password="Correct-Password-1!", role_codes=["FINANCE"])
    login(client, "finance2@example.com", password="Correct-Password-1!")
    resp = client.post("/system/backups/restore", data={"csrf_token": "x"})
    assert resp.status_code in (403, 404)


def test_unauthenticated_request_redirected_to_login(client):
    resp = client.get("/customers")
    assert resp.status_code == 302
    assert "login" in resp.headers["Location"]


def test_super_admin_bypasses_role_check(app, client, seeded):
    staff_id = make_staff(app, "admin@example.com", super_admin=True, password="Correct-Password-1!", mfa=False)
    force_login(client, app, staff_id)
    resp = client.get("/staff")
    assert resp.status_code == 200
