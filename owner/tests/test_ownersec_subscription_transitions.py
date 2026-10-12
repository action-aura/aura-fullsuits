"""OWNER-SEC item 3 (review D SEC-03): the generic subscription transition
route must not let a SALES account (subscriptions.update only, password-only
login) suspend/cancel/expire a live subscription. Deny and allow both ways."""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_staff


def _subscription(app, staff_id, status="ACTIVE"):
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    with app.app_context():
        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        plan = Plan(plan_code=f"OS3-{uuid.uuid4().hex}", product_id=product.id, name="p", billing_model="MONTHLY", currency="USD")
        customer = Customer(legal_name="OS3 Co")
        db_session.add_all([plan, customer])
        db_session.commit()
        sub = create_subscription(
            {"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": date(2030, 1, 1)}, staff_id
        )
        if status != "DRAFT":
            transition_subscription(sub, "ACTIVE", staff_id)
        return str(sub.id)


def _status(app, sub_id):
    from app.extensions import db_session
    from app.models.subscriptions import Subscription

    with app.app_context():
        db_session.expire_all()
        return db_session.get(Subscription, sub_id).status


def _post(client, sub_id, to_status):
    csrf = get_csrf(client.get(f"/subscriptions/{sub_id}").get_data(as_text=True))
    return client.post(f"/subscriptions/{sub_id}/transition", data={"csrf_token": csrf, "to_status": to_status, "reason": "t"})


@pytest.mark.parametrize("target", ["SUSPENDED", "CANCELLED", "EXPIRED"])
def test_sales_cannot_push_active_subscription_into_restricting_state(app, client, seeded, target):
    sales = make_staff(app, "os3-sales@example.com", role_codes=["SALES"])
    sub_id = _subscription(app, sales)
    force_login(client, app, sales)
    resp = _post(client, sub_id, target)
    assert resp.status_code == 403
    assert _status(app, sub_id) == "ACTIVE"


def test_sales_keeps_the_transitions_needed_to_sell(app, client, seeded):
    sales = make_staff(app, "os3-sales2@example.com", role_codes=["SALES"])
    force_login(client, app, sales)
    # DRAFT -> ACTIVE, ACTIVE -> PAST_DUE -> ACTIVE, DRAFT -> CANCELLED stay open.
    draft = _subscription(app, sales, status="DRAFT")
    assert _post(client, draft, "ACTIVE").status_code == 302
    assert _status(app, draft) == "ACTIVE"
    assert _post(client, draft, "PAST_DUE").status_code == 302
    assert _post(client, draft, "ACTIVE").status_code == 302
    assert _status(app, draft) == "ACTIVE"
    draft2 = _subscription(app, sales, status="DRAFT")
    assert _post(client, draft2, "CANCELLED").status_code == 302
    assert _status(app, draft2) == "CANCELLED"


def test_sales_detail_page_does_not_offer_restricted_buttons(app, client, seeded):
    sales = make_staff(app, "os3-sales3@example.com", role_codes=["SALES"])
    sub_id = _subscription(app, sales)
    force_login(client, app, sales)
    html = client.get(f"/subscriptions/{sub_id}").get_data(as_text=True)
    assert 'value="SUSPENDED"' not in html and 'value="EXPIRED"' not in html


def test_authorised_role_needs_recent_auth_then_is_allowed(app, client, seeded):
    admin = make_staff(app, "os3-admin@example.com", super_admin=True, mfa=True)
    sub_id = _subscription(app, admin)

    # Holds the capability but no recent MFA -> bounced to reauth, unchanged.
    force_login(client, app, admin)
    resp = _post(client, sub_id, "SUSPENDED")
    assert resp.status_code == 302 and "/auth/reauth" in resp.headers["Location"]
    assert _status(app, sub_id) == "ACTIVE"

    # Real MFA session -> allowed.
    c2 = app.test_client()
    login_and_verify_mfa(c2, "os3-admin@example.com")
    resp = _post(c2, sub_id, "SUSPENDED")
    assert resp.status_code == 302 and "/auth/reauth" not in resp.headers["Location"]
    assert _status(app, sub_id) == "SUSPENDED"
