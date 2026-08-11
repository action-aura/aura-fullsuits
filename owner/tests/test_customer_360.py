"""UI modernization Stage D -- Customer 360 profile: real permission-gated
tab visibility + real cross-domain tab content (Quotes/Orders/Invoices/
Payments/Subscriptions/Licenses/Installations/Timeline). See
docs/owner/ui-modernization/customer-360-contract.md.

Matches test_command_palette.py's/test_attention_center.py's own precedent:
verify a tab/entry is visible only when the real permission is held, and
that real domain data (not AuditLog) drives the Timeline."""
from __future__ import annotations

from tests.conftest import force_login, make_license, make_staff


def test_super_admin_sees_every_tab_and_real_license_subscription_data(app, client, seeded):
    admin_id = make_staff(app, "c360-admin@example.com", super_admin=True)
    license_id, _full_key = make_license(app, admin_id)
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        customer_id = lic.customer_id
        key_prefix = lic.key_prefix

    force_login(client, app, admin_id)
    resp = client.get(f"/customers/{customer_id}")
    assert resp.status_code == 200
    data = resp.get_data(as_text=True)

    # Every tab is present for a super admin (holds every permission).
    for tab_id in (
        "tab-overview", "tab-contacts", "tab-interactions", "tab-followups",
        "tab-quotes", "tab-orders", "tab-invoices", "tab-payments",
        "tab-subscriptions", "tab-licenses", "tab-installations", "tab-notes", "tab-timeline",
    ):
        assert f'id="{tab_id}"' in data, tab_id

    # Real ARIA tablist wiring.
    assert 'role="tablist"' in data
    assert 'role="tabpanel"' in data

    # Licenses tab shows the real issued license (key prefix, not the
    # secret) and the Subscriptions tab shows the real ACTIVE subscription.
    assert key_prefix in data
    assert "Active" in data  # subscription_status_label("ACTIVE") in English

    # Timeline built from real domain events -- License issued/Subscription
    # created both appear, sourced from the same rows as their own tabs.
    assert "License issued" in data
    assert "Subscription created" in data


def test_viewer_role_only_sees_permitted_tabs(app, client, seeded):
    """VIEWER holds customers.view_all/licenses.view/subscriptions.view/
    installations.view but none of quotes.*/orders.*/invoices.*/
    payments.view (seed_data.py) -- the Quotes/Orders/Invoices/Payments
    tabs must not render at all for this actor, matching the same
    presentation-only permission-gating discipline as the sidebar/command
    palette (routes stay independently protected regardless)."""
    viewer_id = make_staff(app, "c360-viewer@example.com", role_codes=["VIEWER"])
    sales_id = make_staff(app, "c360-sales-owner@example.com", role_codes=["SALES"])
    with app.app_context():
        from app.customers.services import create_customer

        customer = create_customer({"legal_name": "Viewer Visibility Co"}, sales_id)
        customer_id = customer.id

    force_login(client, app, viewer_id)
    resp = client.get(f"/customers/{customer_id}")
    assert resp.status_code == 200
    data = resp.get_data(as_text=True)

    # Always-visible tabs still present.
    for tab_id in ("tab-overview", "tab-contacts", "tab-interactions", "tab-followups", "tab-notes", "tab-timeline"):
        assert f'id="{tab_id}"' in data, tab_id

    # Permission-gated tabs VIEWER does not hold.
    for tab_id in ("tab-quotes", "tab-orders", "tab-invoices", "tab-payments"):
        assert f'id="{tab_id}"' not in data, tab_id

    # Permission-gated tabs VIEWER does hold.
    for tab_id in ("tab-subscriptions", "tab-licenses", "tab-installations"):
        assert f'id="{tab_id}"' in data, tab_id


def test_sales_actor_sees_quotes_tab_but_not_finance_only_tabs(app, client, seeded):
    """SALES holds quotes.create/orders.create/invoices.create/payments.create
    (seed_data.py) but not payments.view -- Quotes/Orders/Invoices tabs
    render, Payments does not."""
    sales_id = make_staff(app, "c360-sales@example.com", role_codes=["SALES"])
    with app.app_context():
        from app.customers.services import create_customer

        customer = create_customer({"legal_name": "Sales Visibility Co"}, sales_id)
        customer_id = customer.id

    force_login(client, app, sales_id)
    resp = client.get(f"/customers/{customer_id}")
    assert resp.status_code == 200
    data = resp.get_data(as_text=True)

    assert 'id="tab-quotes"' in data
    assert 'id="tab-orders"' in data
    assert 'id="tab-invoices"' in data
    assert 'id="tab-payments"' not in data
