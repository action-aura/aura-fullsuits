"""2026-10-31 dress rehearsal: the Owner-side fixes (owner_fixes.md items 1-10).

Each class of test pairs a DENY half with its ALLOW twin, and was mutation-proved
(see the commit messages for what was broken and what went red).
"""
from __future__ import annotations

from tests.conftest import force_login, make_staff
from tests.test_customer_onboarding import kyc  # noqa: F401 -- a fixture


# ------------------------------------------------- 10. "your session ended"

def test_a_brand_new_visitor_is_not_told_their_session_ended(app, client, seeded):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]
    assert "reason=session_expired" not in resp.headers["Location"]
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Your session ended" not in page


def test_a_visitor_whose_session_really_ended_is_told_so(app, client, seeded):
    from app.auth.session import COOKIE_NAME

    # allow half: a cookie that no longer matches a live session (expired / revoked)
    client.set_cookie(key=COOKIE_NAME, value="a-token-that-no-longer-exists", domain="localhost")
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302 and "reason=session_expired" in resp.headers["Location"]
    assert "Your session ended" in client.get(resp.headers["Location"]).get_data(as_text=True)


# ------------------------------------------------------ 9. seller dashboard

def _summary(app, who, w):
    from app.dashboard.services import get_dashboard_summary
    from app.extensions import db_session
    from app.models.staff import StaffUser
    from tests.test_customer_onboarding import _codes

    with app.app_context():
        return get_dashboard_summary(db_session.get(StaffUser, w[who]), _codes(app, w[who]))


def test_a_sellers_dashboard_counts_only_their_own_customers_a_managers_counts_all(app, client, seeded, kyc):
    from tests.test_customer_onboarding import _world, onboard

    w = _world(app)
    onboard(app, w, who="seller")
    onboard(app, w, who="other_seller")
    onboard(app, w, who="other_seller")

    mine = _summary(app, "seller", w)
    theirs = _summary(app, "other_seller", w)
    manager = _summary(app, "manager", w)
    # deny half: the seller does not see the company
    assert (mine["total_customers"], theirs["total_customers"]) == (1, 2)
    assert sum(mine["subs_by_product"].values()) == 1 and sum(theirs["subs_by_product"].values()) == 2
    assert sum(mine["licenses_by_status"].values()) == 1 and sum(theirs["licenses_by_status"].values()) == 2
    assert mine["pilots_needing_action"] == 0
    assert mine["scoped"] is True
    # allow half: a manager keeps the company view
    assert manager["scoped"] is False and manager["total_customers"] == 3
    assert sum(manager["subs_by_product"].values()) == 3 and sum(manager["licenses_by_status"].values()) == 3

    # the page says so to the seller, and not to the manager
    force_login(client, app, w["seller"])
    seller_page = client.get("/").get_data(as_text=True)
    assert 'data-dashboard-scoped="1"' in seller_page
    client.get("/auth/logout")
    force_login(client, app, w["manager"])
    assert 'data-dashboard-scoped="1"' not in client.get("/").get_data(as_text=True)


# ------------------------------------- 2. the generic sale form + legacy form

import io
import re
import uuid

from tests.conftest import get_csrf, jordan_today
from tests.test_trial_conversion import SLIP, confirm, money_rows, sell


def _sale_post(client, app, who_id, w, *, method, slip=None, customer_id=None, amount="100.00", plan=None):
    force_login(client, app, who_id)
    page = client.get("/licenses/issuance/new").get_data(as_text=True)
    data = {
        "csrf_token": get_csrf(page), "idempotency_key": re.search(r'name="idempotency_key" value="([^"]+)"', page).group(1),
        "plan_id": str(plan or w["plan"]), "extra_device_pairs": "0", "amount": amount, "method": method,
        "reference": "REF-" + uuid.uuid4().hex[:6], "payment_date": jordan_today().isoformat(),
    }
    if customer_id:
        data["customer_id"] = str(customer_id)
    else:
        data["new_customer_legal_name"] = "Corner Shop " + uuid.uuid4().hex[:4]
    if slip:
        data["slip_photo"] = (io.BytesIO(slip), "slip.jpg")
    return client.post("/licenses/issuance/sale", data=data, content_type="multipart/form-data")


def test_the_sale_form_needs_a_slip_for_a_transfer_but_not_for_cash(app, client, seeded, kyc):
    from tests.test_customer_onboarding import _world

    w = _world(app)
    base = money_rows(app)
    # deny: a bank transfer with no slip is refused and writes nothing
    resp = _sale_post(client, app, w["seller"], w, method="BANK_TRANSFER")
    assert resp.status_code == 400 and "Only a cash payment can be recorded without one" in resp.get_data(as_text=True)
    assert money_rows(app) == base
    # allow: the same sale as cash needs no slip ...
    resp = _sale_post(client, app, w["seller"], w, method="CASH")
    assert resp.status_code == 200 and "Sale recorded" in resp.get_data(as_text=True)
    assert money_rows(app)["payments"] == base["payments"] + 1
    # ... and a transfer WITH its slip is recorded together with the slip photo
    resp = _sale_post(client, app, w["seller"], w, method="BANK_TRANSFER", slip=SLIP)
    assert resp.status_code == 200 and "Sale recorded" in resp.get_data(as_text=True)
    after = money_rows(app)
    assert (after["payments"], after["slips"]) == (base["payments"] + 2, base["slips"] + 1)


def test_the_sale_form_sends_a_customer_in_a_trial_to_the_trial_payment_page(app, client, seeded, kyc):
    from tests.test_customer_onboarding import _world, onboard

    w = _world(app)
    trial = onboard(app, w)
    base = money_rows(app)
    # deny: a generic sale for this customer would confirm into a SECOND licence -- nothing is written
    resp = _sale_post(client, app, w["seller"], w, method="CASH", customer_id=trial.customer_id)
    assert resp.status_code == 302
    where = resp.headers["Location"]
    assert f"convert={trial.subscription_id}" in where and "from_sale=1" in where
    assert money_rows(app) == base
    assert 'data-sent-from-sale="1"' in client.get(where).get_data(as_text=True)
    # allow: once the trial has been paid for (CONVERTED), the customer is sold to the ordinary way
    confirm(app, w, sell(app, w, trial, amount="100.00"))
    resp = _sale_post(client, app, w["seller"], w, method="CASH", customer_id=trial.customer_id)
    assert resp.status_code == 200 and "Sale recorded" in resp.get_data(as_text=True)


def test_the_old_record_payment_form_is_for_finance_and_managers_not_sellers(app, client, seeded):
    from tests.test_customer_onboarding import _world

    w = _world(app)
    with app.test_request_context():
        from flask import url_for

        legacy = url_for("commercial_sales_web.new_payment_form")
    force_login(client, app, w["seller"])
    resp = client.get(legacy, follow_redirects=False)  # deny: a seller is sent to "Sell a licence"
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/licenses/issuance/new")
    client.get("/auth/logout")
    force_login(client, app, w["admin"])
    assert client.get(legacy, follow_redirects=False).status_code == 200  # allow: finance / admins keep it
