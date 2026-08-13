"""Phase 9.5D Milestone 19 -- commercial-sales web UI tests.

Real HTTP layer (Flask test client), form-encoded POSTs matching how a
browser actually submits these routes -- not JSON. Proves the
Post/Redirect/Get flow, template rendering, and ownership/permission
wiring end to end.
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
        customer = create_customer({"legal_name": "Web Test Customer Co"}, actor_staff_user_id=staff_id)
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="Web Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        plan = create_plan(
            {"plan_code": plan_code, "product_id": product.id, "name": "Web Test Plan", "billing_model": "MONTHLY", "effective_date": date.today() - timedelta(days=1)},
            actor_staff_user_id=None,
        )
        add_plan_price(plan, Decimal("500.00"), "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        return customer.id, plan.id


def test_full_quote_to_paid_invoice_via_web_forms(app, client, seeded):
    staff_sales = make_staff(app, "webs1@example.com", role_codes=["SALES"])
    staff_finance = make_staff(app, "webs2@example.com", role_codes=["FINANCE"])
    with app.app_context():
        _make_profile(app, staff_sales, "EMP-WEBS1")
        _make_profile(app, staff_finance, "EMP-WEBS2")
    customer_id, plan_id = _seed_customer_and_plan(app, staff_sales, "WEBS_PLAN")

    force_login(client, app, staff_sales)
    csrf = _csrf(client)

    resp = client.post(
        "/quotes", data={"csrf_token": csrf, "customer_id": str(customer_id), "currency": "USD"}, follow_redirects=False,
    )
    assert resp.status_code == 302, resp.get_data(as_text=True)
    quote_id = resp.headers["Location"].rsplit("/", 1)[-1]

    resp = client.get(f"/quotes/{quote_id}")
    assert resp.status_code == 200
    assert b"DRAFT" in resp.data or "Draft".encode() in resp.data

    resp = client.post(
        f"/quotes/{quote_id}/lines",
        data={"csrf_token": csrf, "plan_id": str(plan_id), "quantity": "1", "discount_amount": "0"},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.get_data(as_text=True)

    resp = client.post(f"/quotes/{quote_id}/submit", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 302, resp.get_data(as_text=True)

    resp = client.post(f"/quotes/{quote_id}/decision", data={"csrf_token": csrf, "accepted": "1"}, follow_redirects=False)
    assert resp.status_code == 302, resp.get_data(as_text=True)

    resp = client.post(f"/orders/from-quote/{quote_id}", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 302, resp.get_data(as_text=True)
    order_id = resp.headers["Location"].rsplit("/", 1)[-1]

    # SALES holds orders.create but not orders.approve -- the route decorator forbids this.
    resp = client.post(f"/orders/{order_id}/confirm", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 403

    force_login(client, app, staff_finance)
    csrf = _csrf(client)
    resp = client.post(f"/orders/{order_id}/confirm", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 302, resp.get_data(as_text=True)

    force_login(client, app, staff_sales)
    csrf = _csrf(client)
    resp = client.post(f"/orders/{order_id}/invoice", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 302, resp.get_data(as_text=True)
    invoice_id = resp.headers["Location"].rsplit("/", 1)[-1]

    force_login(client, app, staff_finance)
    csrf = _csrf(client)
    resp = client.post(f"/invoices/{invoice_id}/issue", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 302, resp.get_data(as_text=True)

    resp = client.get(f"/invoices/{invoice_id}")
    assert resp.status_code == 200

    force_login(client, app, staff_sales)
    csrf = _csrf(client)
    resp = client.post(
        "/payments",
        data={
            "csrf_token": csrf, "customer_id": str(customer_id), "amount": "500.00", "currency": "USD",
            "method": "CASH", "payment_date": date.today().isoformat(),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.get_data(as_text=True)
    payment_id = resp.headers["Location"].rsplit("/", 1)[-1]

    force_login(client, app, staff_finance)
    csrf = _csrf(client)
    resp = client.post(f"/payments/{payment_id}/confirm", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 302, resp.get_data(as_text=True)

    resp = client.post(
        f"/invoices/{invoice_id}/allocate",
        data={"csrf_token": csrf, "payment_record_id": payment_id, "amount": "500.00"},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.get_data(as_text=True)

    resp = client.get(f"/invoices/{invoice_id}")
    assert resp.status_code == 200
    assert "Paid".encode() in resp.data or b"PAID" in resp.data


def test_peer_quote_returns_404_on_web_route(app, client, seeded):
    staff_a = make_staff(app, "webs3@example.com", role_codes=["SALES"])
    staff_b = make_staff(app, "webs4@example.com", role_codes=["SALES"])
    with app.app_context():
        profile_a_id = _make_profile(app, staff_a, "EMP-WEBS3").id
        _make_profile(app, staff_b, "EMP-WEBS4")
    customer_id, _plan_id = _seed_customer_and_plan(app, staff_a, "WEBS3_PLAN")

    with app.app_context():
        from app.commercial_sales.quotes import create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_a_id, actor_staff_user_id=staff_a)
        quote_id = quote.id

    force_login(client, app, staff_b)
    resp = client.get(f"/quotes/{quote_id}")
    assert resp.status_code == 404


def test_finance_dashboard_forbidden_to_sales_web(app, client, seeded):
    staff_sales = make_staff(app, "webs5@example.com", role_codes=["SALES"])
    with app.app_context():
        _make_profile(app, staff_sales, "EMP-WEBS5")

    force_login(client, app, staff_sales)
    resp = client.get("/commercial-dashboard/finance")
    assert resp.status_code == 403
