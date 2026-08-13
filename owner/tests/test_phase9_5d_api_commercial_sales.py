"""Phase 9.5D Milestone 18 -- commercial-sales /api/operations/v1 tests.

Real HTTP layer (Flask test client), matching test_phase9_5c_api_idor.py's
established pattern -- proves the route/permission/serialization wiring
actually works end to end (create Quote -> add line -> submit -> accept
-> Order -> Invoice -> Payment -> Allocation -> Commission earning), not
just that the underlying service functions work in isolation (already
proven by every Milestone 3-17 unit test).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from tests.conftest import force_login, get_csrf, make_staff


def _csrf(client):
    return get_csrf(client.get("/profile").get_data(as_text=True))


def _make_profile(app, staff_id, employee_number):
    from app.employees.services import create_employee_profile

    return create_employee_profile(
        {
            "staff_user_id": staff_id,
            "employee_number": employee_number,
            "full_name": f"Employee {employee_number}",
            "employment_start_date": date(2026, 1, 1),
        },
        actor_staff_user_id=staff_id,
    )


def _seed_customer_and_plan(app, staff_id, plan_code):
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.customers.services import create_customer
    from app.extensions import db_session
    from app.models.catalog import Product

    with app.app_context():
        customer = create_customer({"legal_name": "API Test Customer Co"}, actor_staff_user_id=staff_id)
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="API Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        plan = create_plan(
            {"plan_code": plan_code, "product_id": product.id, "name": "API Test Plan", "billing_model": "MONTHLY", "effective_date": date.today() - timedelta(days=1)},
            actor_staff_user_id=None,
        )
        add_plan_price(plan, Decimal("1000.00"), "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        return customer.id, plan.id


def test_full_quote_to_commission_earning_chain_via_http(app, client, seeded):
    staff_sales = make_staff(app, "apis1@example.com", role_codes=["SALES"])
    staff_finance = make_staff(app, "apis2@example.com", role_codes=["FINANCE"])
    with app.app_context():
        _make_profile(app, staff_sales, "EMP-APIS1")
        _make_profile(app, staff_finance, "EMP-APIS2")
    customer_id, plan_id = _seed_customer_and_plan(app, staff_sales, "APIS_PLAN")

    force_login(client, app, staff_sales)
    csrf = _csrf(client)

    resp = client.post(
        "/api/operations/v1/quotes", json={"customer_id": str(customer_id), "currency": "USD"}, headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    quote_id = resp.get_json()["id"]

    resp = client.post(
        f"/api/operations/v1/quotes/{quote_id}/lines",
        json={"plan_id": str(plan_id), "quantity": 1}, headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)

    resp = client.post(f"/api/operations/v1/quotes/{quote_id}/submit", json={}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    resp = client.post(f"/api/operations/v1/quotes/{quote_id}/decision", json={"accepted": True}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "ACCEPTED"

    resp = client.post("/api/operations/v1/orders", json={"quote_id": quote_id}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    order_id = resp.get_json()["id"]

    # SALES holds orders.create but not orders.approve -- confirming must be forbidden.
    resp = client.post(f"/api/operations/v1/orders/{order_id}/confirm", json={}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 403

    force_login(client, app, staff_finance)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/orders/{order_id}/confirm", json={}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "CONFIRMED"

    force_login(client, app, staff_sales)
    csrf = _csrf(client)
    resp = client.post("/api/operations/v1/invoices", json={"order_id": order_id}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    invoice_id = resp.get_json()["id"]

    force_login(client, app, staff_finance)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/invoices/{invoice_id}/issue", json={}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "ISSUED"

    # Maker-checker: SALES submits the payment it collected, FINANCE
    # confirms it -- the same actor submitting AND confirming is a real,
    # unconditional service-layer block (SELF_CONFIRMATION_FORBIDDEN),
    # not merely a permission gate.
    force_login(client, app, staff_sales)
    csrf = _csrf(client)
    resp = client.post(
        "/api/operations/v1/payments",
        json={"customer_id": str(customer_id), "amount": "1000.00", "currency": "USD", "method": "CASH", "payment_date": date.today().isoformat()},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    payment_id = resp.get_json()["id"]
    assert resp.get_json()["status"] == "PENDING"

    force_login(client, app, staff_finance)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/payments/{payment_id}/confirm", json={}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "CONFIRMED"

    resp = client.post(
        "/api/operations/v1/allocations",
        json={"payment_record_id": payment_id, "commercial_invoice_id": invoice_id, "amount": "1000.00"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)

    resp = client.get(f"/api/operations/v1/invoices/{invoice_id}")
    assert resp.get_json()["status"] == "PAID"

    # No commission plan was assigned, so nothing was earned -- proves the
    # route wiring reached post_earning_for_allocation() (Milestone 15)
    # without crashing on the "no active rule" no-op path.
    force_login(client, app, staff_sales)
    resp = client.get("/api/operations/v1/commissions")
    assert resp.status_code == 200
    assert resp.get_json()["rows"] == []


def test_employee_cannot_read_peer_quote_via_api(app, client, seeded):
    staff_a = make_staff(app, "apis3@example.com", role_codes=["SALES"])
    staff_b = make_staff(app, "apis4@example.com", role_codes=["SALES"])
    with app.app_context():
        profile_a_id = _make_profile(app, staff_a, "EMP-APIS3").id
        _make_profile(app, staff_b, "EMP-APIS4")
    customer_id, plan_id = _seed_customer_and_plan(app, staff_a, "APIS3_PLAN")

    with app.app_context():
        from app.commercial_sales.quotes import create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_a_id, actor_staff_user_id=staff_a)
        quote_id = quote.id

    force_login(client, app, staff_b)
    resp = client.get(f"/api/operations/v1/quotes/{quote_id}")
    assert resp.status_code == 404  # not 403 -- existence itself is not confirmed


def test_payment_confirm_requires_permission(app, client, seeded):
    staff_sales = make_staff(app, "apis5@example.com", role_codes=["SALES"])
    with app.app_context():
        _make_profile(app, staff_sales, "EMP-APIS5")
    customer_id, _plan_id = _seed_customer_and_plan(app, staff_sales, "APIS5_PLAN")

    force_login(client, app, staff_sales)
    csrf = _csrf(client)
    resp = client.post(
        "/api/operations/v1/payments",
        json={"customer_id": str(customer_id), "amount": "100.00", "currency": "USD", "method": "CASH", "payment_date": date.today().isoformat()},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201
    payment_id = resp.get_json()["id"]

    # SALES holds payments.create but not payments.confirm.
    resp = client.post(f"/api/operations/v1/payments/{payment_id}/confirm", json={}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 403


def test_finance_dashboard_forbidden_to_sales(app, client, seeded):
    staff_sales = make_staff(app, "apis6@example.com", role_codes=["SALES"])
    with app.app_context():
        _make_profile(app, staff_sales, "EMP-APIS6")

    force_login(client, app, staff_sales)
    resp = client.get("/api/operations/v1/commercial/dashboard/finance")
    assert resp.status_code == 403


def test_stale_version_returns_409(app, client, seeded):
    staff_sales = make_staff(app, "apis7@example.com", role_codes=["SALES"])
    with app.app_context():
        _make_profile(app, staff_sales, "EMP-APIS7")
    customer_id, plan_id = _seed_customer_and_plan(app, staff_sales, "APIS7_PLAN")

    force_login(client, app, staff_sales)
    csrf = _csrf(client)
    resp = client.post("/api/operations/v1/quotes", json={"customer_id": str(customer_id), "currency": "USD"}, headers={"X-CSRFToken": csrf})
    quote_id = resp.get_json()["id"]
    client.post(f"/api/operations/v1/quotes/{quote_id}/lines", json={"plan_id": str(plan_id), "quantity": 1}, headers={"X-CSRFToken": csrf})

    resp = client.post(f"/api/operations/v1/quotes/{quote_id}/submit", json={"version": 999}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert resp.get_json()["error"] == "STALE_VERSION"
