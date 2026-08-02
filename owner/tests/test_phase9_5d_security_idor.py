"""Phase 9.5D Milestone 23 -- security/IDOR/segregation-of-duties/tampering
tests, matching Phase 9.5C's own dedicated security-pass precedent
(test_phase9_5c_api_idor.py). Extends beyond what Milestones 18/19 already
proved (Quote IDOR, a few permission-denial checks) to every remaining
document type, plus a real tampering scenario found while writing this
milestone.
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


def _seed_customer_and_plan(app, staff_id, plan_code, price=Decimal("500.00")):
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.customers.services import create_customer
    from app.extensions import db_session
    from app.models.catalog import Product

    with app.app_context():
        customer = create_customer({"legal_name": f"Sec Test Customer {plan_code}"}, actor_staff_user_id=staff_id)
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="Sec Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        plan = create_plan(
            {"plan_code": plan_code, "product_id": product.id, "name": "Sec Test Plan", "billing_model": "MONTHLY", "effective_date": date.today() - timedelta(days=1)},
            actor_staff_user_id=None,
        )
        add_plan_price(plan, price, "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        return customer.id, plan.id


def _build_confirmed_order(app, staff_sales, staff_finance, profile_id, customer_id, plan_id):
    """Returns order_id -- a real, confirmed SalesOrder owned by staff_sales."""
    with app.app_context():
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
        from app.commercial_sales.sales_orders import confirm_order, create_order_from_quote
        import uuid

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_sales)
        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_sales)
        submit_quote(quote, actor_staff_user_id=staff_sales)
        record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_sales)
        order = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_sales, idempotency_key=str(uuid.uuid4()))
        confirm_order(order, actor_staff_user_id=staff_finance)
        return order.id


def _build_issued_invoice(app, staff_sales, staff_finance, profile_id, customer_id, plan_id):
    with app.app_context():
        from app.commercial_sales.invoices import create_invoice_from_order, issue_invoice
        from app.models.commercial_sales import SalesOrder
        from app.extensions import db_session
        import uuid

        order_id = _build_confirmed_order(app, staff_sales, staff_finance, profile_id, customer_id, plan_id)
        order = db_session.get(SalesOrder, order_id)
        invoice = create_invoice_from_order(order, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_sales, idempotency_key=str(uuid.uuid4()))
        issue_invoice(invoice, actor_staff_user_id=staff_finance)
        return invoice.id


def test_peer_order_returns_404_via_api(app, client, seeded):
    staff_a = make_staff(app, "sec1a@example.com", role_codes=["SALES"])
    staff_b = make_staff(app, "sec1b@example.com", role_codes=["SALES"])
    staff_fin = make_staff(app, "sec1f@example.com", role_codes=["FINANCE"])
    with app.app_context():
        profile_a_id = _make_profile(app, staff_a, "EMP-SEC1A").id
        _make_profile(app, staff_b, "EMP-SEC1B")
    customer_id, plan_id = _seed_customer_and_plan(app, staff_a, "SEC1_PLAN")
    order_id = _build_confirmed_order(app, staff_a, staff_fin, profile_a_id, customer_id, plan_id)

    force_login(client, app, staff_b)
    resp = client.get(f"/api/operations/v1/orders/{order_id}")
    assert resp.status_code == 404


def test_peer_invoice_returns_404_via_api(app, client, seeded):
    staff_a = make_staff(app, "sec2a@example.com", role_codes=["SALES"])
    staff_b = make_staff(app, "sec2b@example.com", role_codes=["SALES"])
    staff_fin = make_staff(app, "sec2f@example.com", role_codes=["FINANCE"])
    with app.app_context():
        profile_a_id = _make_profile(app, staff_a, "EMP-SEC2A").id
        _make_profile(app, staff_b, "EMP-SEC2B")
    customer_id, plan_id = _seed_customer_and_plan(app, staff_a, "SEC2_PLAN")
    invoice_id = _build_issued_invoice(app, staff_a, staff_fin, profile_a_id, customer_id, plan_id)

    force_login(client, app, staff_b)
    resp = client.get(f"/api/operations/v1/invoices/{invoice_id}")
    assert resp.status_code == 404


def test_peer_order_returns_404_via_web(app, client, seeded):
    staff_a = make_staff(app, "sec3a@example.com", role_codes=["SALES"])
    staff_b = make_staff(app, "sec3b@example.com", role_codes=["SALES"])
    staff_fin = make_staff(app, "sec3f@example.com", role_codes=["FINANCE"])
    with app.app_context():
        profile_a_id = _make_profile(app, staff_a, "EMP-SEC3A").id
        _make_profile(app, staff_b, "EMP-SEC3B")
    customer_id, plan_id = _seed_customer_and_plan(app, staff_a, "SEC3_PLAN")
    order_id = _build_confirmed_order(app, staff_a, staff_fin, profile_a_id, customer_id, plan_id)

    force_login(client, app, staff_b)
    resp = client.get(f"/orders/{order_id}")
    assert resp.status_code == 404


def test_sales_cannot_void_invoice_directly(app, client, seeded):
    """orders.approve/invoices.issue are FINANCE-only -- a SALES actor
    forging a direct POST to /invoices/<id>/void must be rejected by the
    route decorator regardless of whether they can see the invoice."""
    staff_a = make_staff(app, "sec4a@example.com", role_codes=["SALES"])
    staff_fin = make_staff(app, "sec4f@example.com", role_codes=["FINANCE"])
    with app.app_context():
        profile_a_id = _make_profile(app, staff_a, "EMP-SEC4A").id
    customer_id, plan_id = _seed_customer_and_plan(app, staff_a, "SEC4_PLAN")
    invoice_id = _build_issued_invoice(app, staff_a, staff_fin, profile_a_id, customer_id, plan_id)

    force_login(client, app, staff_a)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/invoices/{invoice_id}/void", json={"reason": "forged"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 403


def test_sales_cannot_approve_refund_or_commission(app, client, seeded):
    staff_a = make_staff(app, "sec5a@example.com", role_codes=["SALES"])
    with app.app_context():
        _make_profile(app, staff_a, "EMP-SEC5A")

    force_login(client, app, staff_a)
    csrf = _csrf(client)
    import uuid

    fake_id = uuid.uuid4()
    # Permission denial must fire before the 404 existence check --
    # confirmed by using a nonexistent id and still getting 403, not 404.
    resp = client.post(f"/api/operations/v1/refunds/{fake_id}/approve", json={}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 403
    resp = client.post(f"/api/operations/v1/commissions/{fake_id}/approve", json={}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 403
    resp = client.post("/api/operations/v1/commission-payout-batches", json={"batch_reference": "X"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 403


def test_cross_customer_payment_allocation_rejected(app, seeded):
    """Milestone 23 real finding: allocate_payment() never checked that
    the payment and invoice belong to the same customer. Without this, a
    FINANCE actor (or a tampered payment_record_id) could credit Customer
    A's confirmed payment against Customer B's invoice."""
    staff_a = make_staff(app, "sec6a@example.com", role_codes=["SALES"])
    staff_fin = make_staff(app, "sec6f@example.com", role_codes=["FINANCE"])
    with app.app_context():
        profile_a_id = _make_profile(app, staff_a, "EMP-SEC6A").id
    customer_a_id, plan_id = _seed_customer_and_plan(app, staff_a, "SEC6A_PLAN")
    invoice_id = _build_issued_invoice(app, staff_a, staff_fin, profile_a_id, customer_a_id, plan_id)

    with app.app_context():
        from app.commercial_sales.payments import confirm_payment, submit_payment
        from app.customers.services import create_customer

        customer_b = create_customer({"legal_name": "Sec6 Customer B"}, actor_staff_user_id=staff_a)
        payment_b = submit_payment(
            customer_id=customer_b.id, amount=Decimal("500.00"), currency="USD", method="CASH",
            payment_date=date.today(), actor_staff_user_id=staff_a,
        )
        confirm_payment(payment_b, actor_staff_user_id=staff_fin)

    with app.app_context():
        import pytest

        from app.commercial_sales.allocation import allocate_payment
        from app.commercial_sales.errors import CommercialSalesError
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice
        from app.models.subscriptions import PaymentRecord

        invoice_a = db_session.get(CommercialInvoice, invoice_id)
        payment_b_reloaded = db_session.get(PaymentRecord, payment_b.id)

        with pytest.raises(CommercialSalesError) as exc:
            allocate_payment(payment=payment_b_reloaded, invoice=invoice_a, amount=Decimal("500.00"), actor_staff_user_id=staff_fin)
        assert exc.value.code == "PAYMENT_CUSTOMER_MISMATCH"


def test_cross_customer_allocation_rejected_via_api(app, client, seeded):
    staff_a = make_staff(app, "sec7a@example.com", role_codes=["SALES"])
    staff_fin = make_staff(app, "sec7f@example.com", role_codes=["FINANCE"])
    with app.app_context():
        profile_a_id = _make_profile(app, staff_a, "EMP-SEC7A").id
    customer_a_id, plan_id = _seed_customer_and_plan(app, staff_a, "SEC7A_PLAN")
    invoice_id = _build_issued_invoice(app, staff_a, staff_fin, profile_a_id, customer_a_id, plan_id)

    with app.app_context():
        from app.commercial_sales.payments import confirm_payment, submit_payment
        from app.customers.services import create_customer

        customer_b = create_customer({"legal_name": "Sec7 Customer B"}, actor_staff_user_id=staff_a)
        payment_b = submit_payment(
            customer_id=customer_b.id, amount=Decimal("500.00"), currency="USD", method="CASH",
            payment_date=date.today(), actor_staff_user_id=staff_a,
        )
        confirm_payment(payment_b, actor_staff_user_id=staff_fin)
        payment_b_id = payment_b.id

    force_login(client, app, staff_fin)
    csrf = _csrf(client)
    resp = client.post(
        "/api/operations/v1/allocations",
        json={"payment_record_id": str(payment_b_id), "commercial_invoice_id": str(invoice_id), "amount": "500.00"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "PAYMENT_CUSTOMER_MISMATCH"


def test_stale_version_on_order_confirm_rejected(app, client, seeded):
    """Tampering: a client submitting a stale/forged version must be
    rejected, not silently accepted -- optimistic locking is real, not
    decorative."""
    staff_a = make_staff(app, "sec8a@example.com", role_codes=["SALES"])
    staff_fin = make_staff(app, "sec8f@example.com", role_codes=["FINANCE"])
    with app.app_context():
        profile_a_id = _make_profile(app, staff_a, "EMP-SEC8A").id
    customer_id, plan_id = _seed_customer_and_plan(app, staff_a, "SEC8_PLAN")

    with app.app_context():
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
        from app.commercial_sales.sales_orders import create_order_from_quote
        import uuid

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_a_id, actor_staff_user_id=staff_a)
        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_a)
        submit_quote(quote, actor_staff_user_id=staff_a)
        record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_a)
        order = create_order_from_quote(quote, actor_employee_profile_id=profile_a_id, actor_staff_user_id=staff_a, idempotency_key=str(uuid.uuid4()))
        order_id = order.id

    force_login(client, app, staff_fin)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/orders/{order_id}/confirm", json={"version": 999}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "STALE_VERSION"
