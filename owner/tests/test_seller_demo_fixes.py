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
