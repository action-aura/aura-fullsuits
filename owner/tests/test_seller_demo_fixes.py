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
