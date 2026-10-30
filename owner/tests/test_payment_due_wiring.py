"""The yearly activation is SHOWN where staff record the payment (verifier, 2026-10-30).

packages.payment_due / next_payment_due had no production caller: staff typed the
month-13 payment by hand and the 100 JOD activation was never asked for. These tests
go through the real routes (test client, real templates), never the helper alone:

  * the subscription screen shows the amount due, what it is made of, and
    pre-fills the amount and currency (English and Arabic);
  * the commercial-sales payment form (?subscription_id=) does the same, and the
    API reports the due check in its response;
  * the renewal request form does the same for its commercial amount;
  * a DIFFERENT amount is never refused -- it is recorded as entered, flagged on
    screen, and the gap is written into that payment's audit entry;
  * Package 1 (no yearly activation) is left exactly as it was.

Every deny/flag half is paired with its allow half (ENGINEERING 1): the exact
amount raises no warning; a plan without the activation shows nothing.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tests.conftest import force_login, get_csrf
from tests.test_step0_ladder import _pay_month, _rows, _sell
from tests.test_yearly_activation import _owner_world, owner_prices  # noqa: F401  (fixture)

JOD = "JOD"


def _to_month_13(app, w, sub):
    """The subscription's NEXT payment is the first of its second contract year
    (the sale's own payment + 11 more monthly payments are behind it)."""
    for _ in range(11):
        _pay_month(app, w, sub, "35.00")


@pytest.fixture()
def shop(app, client, seeded, owner_prices):
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p2")["order_id"])["subscription_id"]
    force_login(client, app, w["admin"])
    return {"w": w, "sub": sub, "client": client}


def _detail(shop):
    return shop["client"].get(f"/subscriptions/{shop['sub']}")


def _csrf(shop):
    return get_csrf(_detail(shop).get_data(as_text=True))


def _post_payment(shop, amount, currency=JOD):
    from tests.conftest import jordan_today

    with shop["client"].application.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        customer_id = db_session.get(Subscription, shop["sub"]).customer_id
    return shop["client"].post("/subscriptions/payments", data={
        "csrf_token": _csrf(shop), "customer_id": str(customer_id), "subscription_id": str(shop["sub"]),
        "amount": amount, "currency": currency, "method": "BANK_TRANSFER", "payment_date": jordan_today().isoformat(),
        "reference": "CLIQ-T1", "status": "PENDING",
    }, follow_redirects=True)


def _audit_due_check(app, payment_id):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.audit import AuditLog

        row = db_session.execute(select(AuditLog).where(
            AuditLog.action_code == "PAYMENT_RECORD_CREATED", AuditLog.entity_public_id == str(payment_id)
        )).scalars().one()
        return row.after_state_redacted


def _latest_payment(app, sub):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord

        rows = db_session.execute(select(PaymentRecord).where(PaymentRecord.subscription_id == sub)
                                  .order_by(PaymentRecord.created_at.desc())).scalars().all()
        p = rows[0]
        return {"id": p.id, "amount": Decimal(p.amount), "status": p.status, "n": len(rows)}


# ------------------------------------------------------ the subscription page
def test_the_page_shows_the_amount_due_pre_fills_it_and_names_the_activation_in_month_13(app, shop):
    html = _detail(shop).get_data(as_text=True)
    assert 'data-payment-due="35.00"' in html                      # month 2: just the month
    assert 'name="amount" type="number" step="0.01" required value="35.00"' in html
    assert 'name="currency" value="JOD"' in html
    assert "35.00 JOD month" in html and "yearly activation" not in html

    _to_month_13(app, shop["w"], shop["sub"])
    html = _detail(shop).get_data(as_text=True)
    assert 'data-payment-due="135.00"' in html
    assert 'required value="135.00"' in html
    assert "35.00 JOD month + 100.00 JOD yearly activation" in html


def test_the_page_says_the_same_in_arabic(app, shop):
    _to_month_13(app, shop["w"], shop["sub"])
    shop["client"].get("/locale/ar?next=/")
    html = _detail(shop).get_data(as_text=True)
    assert "35.00 JOD عن الشهر + 100.00 JOD تفعيل سنوي" in html
    assert "المبلغ المستحق:" in html
    assert "yearly activation" not in html


def test_package_1_shows_and_pre_fills_nothing(app, client, seeded, owner_prices):
    """Allow side: a plan without the yearly activation is left as it was."""
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p1")["order_id"])["subscription_id"]
    force_login(client, app, w["admin"])
    html = client.get(f"/subscriptions/{sub}").get_data(as_text=True)
    assert "data-payment-due" not in html
    assert 'name="amount" type="number" step="0.01" required>' in html
    assert 'name="currency" value="USD"' in html


# ------------------------------------------- a different amount: flag + audit
def test_the_exact_amount_is_recorded_with_no_warning_and_a_matching_audit_entry(app, shop):
    _to_month_13(app, shop["w"], shop["sub"])
    resp = _post_payment(shop, "135.00")
    html = resp.get_data(as_text=True)
    assert "The amount due was" not in html
    p = _latest_payment(app, shop["sub"])
    assert p["amount"] == Decimal("135.00") and p["status"] == "PENDING"
    check = _audit_due_check(app, p["id"])["due_check"]
    assert check["matches"] is True and check["difference"] == "0.00"
    assert check["expected_amount"] == "135.00" and check["activation"] == "100.00"


def test_a_short_amount_in_month_13_is_recorded_not_refused_warned_and_the_gap_is_audited(app, shop):
    _to_month_13(app, shop["w"], shop["sub"])
    before = _latest_payment(app, shop["sub"])["n"]
    resp = _post_payment(shop, "35.00")                    # the activation forgotten
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "The amount due was 135.00 JOD (35.00 JOD month + 100.00 JOD yearly activation)" in html
    assert "but 35.00 JOD was recorded (difference -100.00 JOD)" in html
    p = _latest_payment(app, shop["sub"])
    assert p["n"] == before + 1 and p["amount"] == Decimal("35.00") and p["status"] == "PENDING"
    check = _audit_due_check(app, p["id"])["due_check"]
    assert check["matches"] is False and check["difference"] == "-100.00"
    assert check["entered_amount"] == "35.00" and check["expected_amount"] == "135.00"


def test_a_combined_payment_is_recorded_flagged_and_audited_with_a_positive_gap(app, shop):
    resp = _post_payment(shop, "70.00")                    # two months at once, month 2 due is 35
    assert "difference 35.00 JOD" in resp.get_data(as_text=True)
    p = _latest_payment(app, shop["sub"])
    assert _audit_due_check(app, p["id"])["due_check"]["difference"] == "35.00"


def test_a_payment_in_another_currency_is_recorded_and_flagged(app, shop):
    resp = _post_payment(shop, "35.00", currency="USD")
    assert "is in USD but the amount due is in JOD" in resp.get_data(as_text=True)
    p = _latest_payment(app, shop["sub"])
    check = _audit_due_check(app, p["id"])["due_check"]
    assert check["matches"] is False and check["difference"] is None


def test_a_package_1_payment_has_no_due_check_in_its_audit_entry(app, client, seeded, owner_prices):
    w = _owner_world(app)
    sub = _rows(app, _sell(app, w, "p1")["order_id"])["subscription_id"]
    shop = {"w": w, "sub": sub, "client": client}
    force_login(client, app, w["admin"])
    resp = _post_payment(shop, "100.00")
    assert "The amount due was" not in resp.get_data(as_text=True)
    assert "due_check" not in _audit_due_check(app, _latest_payment(app, sub)["id"])


# ------------------------------------------- the commercial-sales payment form
def _customer_of(app, sub):
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        return db_session.get(Subscription, sub).customer_id


def test_the_sales_payment_form_is_pre_filled_for_a_subscription(app, shop):
    _to_month_13(app, shop["w"], shop["sub"])
    html = shop["client"].get(f"/payments/new?subscription_id={shop['sub']}").get_data(as_text=True)
    assert 'data-payment-due="135.00"' in html
    assert "35.00 JOD month + 100.00 JOD yearly activation" in html
    assert 'name="amount" required value="135.00"' in html
    assert f'name="subscription_id" value="{shop["sub"]}"' in html
    plain = shop["client"].get("/payments/new").get_data(as_text=True)   # allow side
    assert "data-payment-due" not in plain and 'name="subscription_id"' not in plain


def test_the_sales_payment_form_links_the_payment_to_the_subscription_and_flags_a_gap(app, shop):
    from tests.conftest import jordan_today

    _to_month_13(app, shop["w"], shop["sub"])
    cid = _customer_of(app, shop["sub"])
    html = get_csrf(shop["client"].get("/payments/new").get_data(as_text=True))
    resp = shop["client"].post("/payments", data={
        "csrf_token": html, "customer_id": str(cid), "subscription_id": str(shop["sub"]), "amount": "35.00",
        "currency": JOD, "method": "BANK_TRANSFER", "payment_date": jordan_today().isoformat(), "reference": "R1",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert "The amount due was 135.00 JOD" in resp.get_data(as_text=True)
    p = _latest_payment(app, shop["sub"])          # found BY the subscription: it is linked
    assert p["amount"] == Decimal("35.00")
    assert _audit_due_check(app, p["id"])["due_check"]["difference"] == "-100.00"


def test_the_sales_payment_form_refuses_a_subscription_of_another_customer(app, shop):
    from tests.conftest import jordan_today
    from tests.test_step0_ladder import _sell as sell

    other = _rows(app, sell(app, shop["w"], "p2")["order_id"])["customer_id"]
    csrf = get_csrf(shop["client"].get("/payments/new").get_data(as_text=True))
    resp = shop["client"].post("/payments", data={
        "csrf_token": csrf, "customer_id": str(other), "subscription_id": str(shop["sub"]), "amount": "35.00",
        "currency": JOD, "method": "BANK_TRANSFER", "payment_date": jordan_today().isoformat(),
    })
    assert resp.status_code == 400
    assert "belongs to a different customer" in resp.get_data(as_text=True)


# --------------------------------------------------------------------- the API
def test_the_api_reports_the_due_check_and_still_records_a_different_amount(app, shop):
    from tests.conftest import jordan_today

    _to_month_13(app, shop["w"], shop["sub"])
    cid = _customer_of(app, shop["sub"])
    csrf = get_csrf(shop["client"].get("/profile").get_data(as_text=True))
    body = {"customer_id": str(cid), "subscription_id": str(shop["sub"]), "amount": "35.00", "currency": JOD,
            "method": "BANK_TRANSFER", "payment_date": jordan_today().isoformat()}
    resp = shop["client"].post("/api/operations/v1/payments", json=body, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["due_check"]["expected_amount"] == "135.00" and data["due_check"]["difference"] == "-100.00"
    assert data["due_check"]["matches"] is False and data["status"] == "PENDING"
    # allow side: the next one entered exactly as due matches (the 35 just recorded now counts as a month)
    body["amount"] = "35.00"
    again = shop["client"].post("/api/operations/v1/payments", json=body, headers={"X-CSRFToken": csrf}).get_json()
    assert again["due_check"]["matches"] is True
    # and an API payment with no subscription carries no due_check at all
    del body["subscription_id"]
    plain = shop["client"].post("/api/operations/v1/payments", json=body, headers={"X-CSRFToken": csrf}).get_json()
    assert "due_check" not in plain


# ------------------------------------------------------------ the renewal form
def test_the_renewal_form_is_pre_filled_and_a_different_commercial_amount_is_flagged_and_audited(app, shop):
    from datetime import date

    _to_month_13(app, shop["w"], shop["sub"])
    html = shop["client"].get(f"/commercial-ops/ui/renewals/new?subscription_id={shop['sub']}").get_data(as_text=True)
    assert 'data-payment-due="135.00"' in html and 'name="commercial_amount" type="number" step="0.01" value="135.00"' in html
    assert "35.00 JOD month + 100.00 JOD yearly activation" in html
    csrf = get_csrf(html)
    resp = shop["client"].post("/commercial-ops/ui/renewals", data={
        "csrf_token": csrf, "subscription_id": str(shop["sub"]), "proposed_term_start": date(2027, 1, 1).isoformat(),
        "proposed_term_end": date(2027, 2, 1).isoformat(), "currency": JOD, "commercial_amount": "35.00",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert "The amount due was 135.00 JOD" in resp.get_data(as_text=True)
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.audit import AuditLog

        row = db_session.execute(select(AuditLog).where(AuditLog.action_code == "RENEWAL_REQUEST_CREATED")).scalars().one()
        assert row.after_state_redacted["due_check"]["difference"] == "-100.00"
