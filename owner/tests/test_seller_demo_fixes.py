"""2026-10-31 three small fixes found while writing the seller guide, before the demo.

1. "Create a new customer" had no menu link (the page was reachable only by typing
   /customers/new-trial).
2. The record-payment form said "sellers never take cash" -- the owner decided the
   opposite on 2026-10-31 (see test_cash_*).
3. The seller's package lists showed test plans (and a Clinic plan).
"""
from __future__ import annotations

import re

from tests.conftest import force_login, make_staff
from tests.test_customer_onboarding import kyc  # noqa: F401 -- a fixture the conversion test needs

NEW_TRIAL = "/customers/new-trial"


def _sidebar_sales_hrefs(html: str) -> list[str]:
    """The hrefs of the Sales group, in the order the sidebar paints them."""
    button = re.search(r'<button[^>]*data-nav-group-toggle="sales"[^>]*>', html)
    assert button, "the Sales group did not render at all"
    list_id = re.search(r'aria-controls="([^"]+)"', button.group(0)).group(1)
    ul = re.search(r'<ul[^>]*\bid="%s"[^>]*>(.*?)</ul>' % re.escape(list_id), html, re.S)
    assert ul, "the Sales list is missing"
    return re.findall(r'href="([^"]+)"', ul.group(1))


def _dashboard(app, client, email, **kw) -> str:
    force_login(client, app, make_staff(app, email, **kw))
    return client.get("/").get_data(as_text=True)


# ------------------------------------------------------------- 1. menu link

def test_menu_shows_create_customer_first_in_sales_when_flag_on_and_permitted(app, client, seeded):
    app.config.update(CUSTOMER_ONBOARDING_ENABLED=True)
    hrefs = _sidebar_sales_hrefs(_dashboard(app, client, "nav-seller@example.com", role_codes=["SALES"]))
    assert hrefs[0] == NEW_TRIAL, hrefs
    assert len(hrefs) > 1  # the rest of the group is still there behind it


def test_menu_hides_create_customer_when_flag_off(app, client, seeded):
    app.config.update(CUSTOMER_ONBOARDING_ENABLED=False)
    html = _dashboard(app, client, "nav-off@example.com", role_codes=["SALES"])
    assert _sidebar_sales_hrefs(html)  # the Sales group is there ...
    assert NEW_TRIAL not in html  # ... but the dead-end link is not


def test_menu_hides_create_customer_without_the_permission(app, client, seeded):
    app.config.update(CUSTOMER_ONBOARDING_ENABLED=True)
    # FINANCE sees a Sales group (payments) but does not hold customers.onboard.
    html = _dashboard(app, client, "nav-fin@example.com", role_codes=["FINANCE"])
    assert _sidebar_sales_hrefs(html)
    assert NEW_TRIAL not in html


# ------------------------------------------------------------ 2. cash text + rule

def test_cash_is_a_method_a_seller_can_record_and_the_form_says_so(app, client, seeded):
    from tests.test_issuance_sale import RECORDED, _counts, _record, _world as sale_world

    w = sale_world(app)
    # allow half: a seller records CASH (no slip, no trial involved) ...
    result, _key = _record(app, w, method="CASH", reference="RCPT-1")
    assert _counts(app) == RECORDED and result["total"] == 100
    # ... deny half: methods outside the owner's two ways are still refused.
    from app.commercial_sales.direct_sale import DirectSaleError

    for method in ("CARD_OFFLINE", "CHEQUE", "OTHER"):
        try:
            _record(app, w, method=method)
        except DirectSaleError as exc:
            assert exc.code == "METHOD_NOT_ALLOWED"
        else:
            raise AssertionError(method + " must stay refused")
    # the form a seller sees: CASH selectable, the old "never take cash" gone
    force_login(client, app, w["seller"])
    with app.test_request_context():
        from flask import url_for

        url = url_for("license_issuance.new_form")
    html = client.get(url).get_data(as_text=True)
    assert '<option value="CASH"' in html and "within 24 hours" in html
    assert "never take cash" not in html


def test_cash_conversion_needs_no_slip_but_every_other_method_does(app, seeded, kyc):
    from app.commercial_sales.direct_sale import DirectSaleError
    from tests.test_trial_conversion import money_rows, onboard, sell, _world as trial_world

    w = trial_world(app)
    trial = onboard(app, w)
    base = money_rows(app)
    # BANK_TRANSFER with no slip: still refused, nothing written (the S3 rule).
    for slip in (None, b""):
        try:
            sell(app, w, trial, slip=slip, method="BANK_TRANSFER")
        except DirectSaleError as exc:
            assert exc.code == "SLIP_REQUIRED"
        else:
            raise AssertionError("a transfer needs its slip")
    assert money_rows(app) == base
    # CASH the seller collected: no slip yet, accepted; one order, one payment, no slip row.
    sell(app, w, trial, slip=None, method="CASH")
    after = money_rows(app)
    assert (after["orders"], after["payments"], after["slips"]) == (base["orders"] + 1, base["payments"] + 1, base["slips"])


# ------------------------------------------------------- 3. package lists

def _clinic_plan(app):
    """A priced, currently sellable Aura Clinic plan (what `seed-plans` leaves behind)."""
    with app.app_context():
        from sqlalchemy import select

        from app.catalog.services import seed_canonical_plans
        from app.extensions import db_session
        from app.models.catalog import Plan, Product

        seed_canonical_plans()
        for product in db_session.execute(select(Product).where(Product.product_code == "AURA_CLINIC")).scalars():
            product.is_sellable = True
            product.is_active = True
        db_session.commit()
        return db_session.execute(
            select(Plan).join(Product, Product.id == Plan.product_id).where(Product.product_code == "AURA_CLINIC")
        ).scalars().first().id


def test_clinic_plan_is_not_offered_or_sellable_by_a_seller_until_clinic_accounts_are_on(app, client, seeded):
    from tests.test_issuance_sale import _record, _world as sale_world

    w = sale_world(app)
    clinic = _clinic_plan(app)
    # the shared bar still sees the clinic plan, so a missing row below is the seller rule, not an empty catalog
    with app.app_context():
        from app.commercial_sales.catalog_for_sales import describe_plan_for_sale

        assert describe_plan_for_sale(clinic)["product_code"] == "AURA_CLINIC"
    force_login(client, app, w["seller"])
    page = lambda: client.get("/licenses/issuance/new").get_data(as_text=True)  # noqa: E731

    app.config.update(CLINIC_ACCOUNTS_ENABLED=False)
    off = page()
    assert str(clinic) not in off and "Aura Clinic" not in off
    assert str(w["plan"]) in off  # ... while the Retail package is still on the form
    from app.commercial_sales.direct_sale import DirectSaleError

    try:
        _record(app, w, plan_id=clinic)
    except DirectSaleError as exc:
        assert exc.code == "PACKAGE_NOT_SELLABLE"
    else:
        raise AssertionError("a hand-edited form must not sell Clinic")
    with app.app_context():
        from app.customer_onboarding import service

        assert service.sellable_packages("AURA_CLINIC") == []

    app.config.update(CLINIC_ACCOUNTS_ENABLED=True)  # the allow half: once Clinic is live it appears
    assert str(clinic) in page()
    with app.app_context():
        from app.customer_onboarding import service

        assert [p["plan_id"] for p in service.sellable_packages("AURA_CLINIC")] == [str(clinic)]
