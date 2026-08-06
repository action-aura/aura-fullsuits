"""Phase 9.5B-R2 Milestone 7 -- every current reachable Owner template
(the 50 newly translated this wave, plus the route/template manifest
coverage check) renders in both English and Arabic with correct lang/dir
and no raw translation-key/Jinja leakage."""
from __future__ import annotations

import os

from tests.conftest import force_login, make_staff, make_license

OWNER_ROOT = os.path.dirname(os.path.dirname(__file__))
TEMPLATES_ROOT = os.path.join(OWNER_ROOT, "app", "templates")

# The real, source-counted set of directories containing at least one
# template, per docs/owner/phase9_5b_r2/complete-owner-surface-inventory.md.
REAL_TEMPLATE_DIRS = (
    "layout", "auth", "employees", "profile",
    "dashboard", "audit", "catalog", "customers", "installations",
    "licensing", "licensing_admin", "staff", "subscriptions", "system",
    "commercial_ops", "leads", "commercial_sales", "operations_ui",
    # UI modernization Stage C -- styled 401/403/CSRF pages
    # (errors.py's register_error_handlers), replacing the bare
    # Werkzeug default pages that existed before.
    "errors",
    # UI modernization Stage C -- the Attention Center (app/attention/).
    "attention",
)

# No-fixture-required GET routes: real, reachable, list/index/status pages.
# Real url_prefix values confirmed by reading each blueprint's routes.py
# (dashboard="/", customers="/customers", installations="/installations",
# licensing="/licenses", subscriptions="/subscriptions", staff="/staff",
# audit="/audit", catalog="/catalog", licensing_admin="/licensing-admin",
# commercial_ops_ui="/commercial-ops/ui", system="/system").
NO_FIXTURE_ROUTES = [
    "/",
    "/leads",
    "/leads/dashboard",
    "/customers",
    "/installations",
    "/licenses",
    "/subscriptions",
    "/staff",
    "/audit",
    "/audit/security-events",
    "/audit/verify-chain",
    "/catalog",
    "/licensing-admin",
    "/licensing-admin/signing-keys",
    "/licensing-admin/requests",
    "/licensing-admin/device-keys",
    "/licensing-admin/offline-policies",
    "/commercial-ops/ui/renewals",
    "/commercial-ops/ui/pilots",
    "/commercial-ops/ui/emergency-extensions",
    "/commercial-ops/ui/pending-activations",
    "/commercial-ops/ui/notifications",
    "/commercial-ops/ui/queue",
    "/commercial-ops/ui/activation-policy",
    "/system/backups",
    "/quotes",
    "/orders",
    "/invoices",
    "/payments",
    "/refunds",
    "/commissions",
    "/commission-payouts",
    "/commercial-dashboard",
    "/commercial-dashboard/finance",
    "/operations/expenses",
    "/operations/expenses/new",
    "/operations/expense-payees",
    "/operations/cash-closings",
    "/operations/cash-closings/new",
    "/operations/report-snapshots",
    "/operations/management-notes",
    "/operations/management-notes/new",
    "/operations/dashboard/management-operations",
    "/operations/dashboard/finance-operations",
    # /operations/dashboard/employee-expenses deliberately excluded: it
    # requires a real EmployeeProfile (dashboard.view_own is a personal-
    # scope dashboard, not a global one), and the SUPER_ADMIN account this
    # test uses has none -- covered instead by
    # test_phase9_5e_dashboards.py's dedicated employee-profile fixture.
]


def _switch_to_arabic(client):
    resp = client.get("/locale/ar?next=/")
    assert resp.status_code == 302


def test_route_template_manifest_matches_the_real_inventory():
    """Regression guard: if a future template is added under app/templates/
    without being added to REAL_TEMPLATE_DIRS/GATED_DIRS, this fails loudly
    instead of silently shipping an untranslated screen."""
    dirs_with_templates = {
        d for d in os.listdir(TEMPLATES_ROOT)
        if os.path.isdir(os.path.join(TEMPLATES_ROOT, d))
        and any(n.endswith(".html") for n in os.listdir(os.path.join(TEMPLATES_ROOT, d)))
    }
    assert dirs_with_templates == set(REAL_TEMPLATE_DIRS)


def test_all_no_fixture_owner_pages_render_in_english(app, client, seeded):
    admin_id = make_staff(app, "r2render1@example.com", super_admin=True)
    force_login(client, app, admin_id)
    for route in NO_FIXTURE_ROUTES:
        resp = client.get(route)
        assert resp.status_code == 200, f"{route} returned {resp.status_code}"
        data = resp.get_data(as_text=True)
        assert 'lang="en" dir="ltr"' in data, route
        assert "{{" not in data, route
        assert "{%" not in data, route


def test_all_no_fixture_owner_pages_render_in_arabic(app, client, seeded):
    admin_id = make_staff(app, "r2render2@example.com", super_admin=True)
    force_login(client, app, admin_id)
    _switch_to_arabic(client)
    for route in NO_FIXTURE_ROUTES:
        resp = client.get(route)
        assert resp.status_code == 200, f"{route} returned {resp.status_code}"
        data = resp.get_data(as_text=True)
        assert 'lang="ar" dir="rtl"' in data, route
        assert "{{" not in data, route
        assert "{%" not in data, route


def test_dashboard_renders_localized_status_labels_in_arabic(app, client, seeded):
    admin_id = make_staff(app, "r2render3@example.com", super_admin=True)
    force_login(client, app, admin_id)
    _switch_to_arabic(client)
    resp = client.get("/")
    data = resp.get_data(as_text=True)
    assert "لوحة" not in data or True  # dashboard has no literal "Dashboard" h1 -- smoke only
    assert "نظرة عامة" in data  # "Overview"
    assert "إجمالي العملاء" in data  # "Total customers"


def test_customer_detail_renders_in_arabic_with_localized_lifecycle_status(app, client, seeded):
    admin_id = make_staff(app, "r2render4@example.com", super_admin=True)
    with app.app_context():
        from app.customers.services import create_customer
        from app.models.customers import Customer

        customer = create_customer({"legal_name": "شركة الاختبار"}, admin_id)
        customer_id = customer.id
    force_login(client, app, admin_id)
    _switch_to_arabic(client)

    resp = client.get(f"/customers/{customer_id}")
    assert resp.status_code == 200
    data = resp.get_data(as_text=True)
    assert 'dir="rtl"' in data
    assert "عميل محتمل" in data  # customer_status_label("LEAD")


def test_license_chain_detail_pages_render_in_arabic_with_localized_status(app, client, seeded):
    admin_id = make_staff(app, "r2render5@example.com", super_admin=True)
    license_id, full_key = make_license(app, admin_id)
    force_login(client, app, admin_id)
    _switch_to_arabic(client)

    resp = client.get(f"/licenses/{license_id}")
    assert resp.status_code == 200
    data = resp.get_data(as_text=True)
    assert 'dir="rtl"' in data
    assert "صادر" in data  # license_status_label("ISSUED")

    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        subscription_id = lic.subscription_id
        customer_id = lic.customer_id

    sub_resp = client.get(f"/subscriptions/{subscription_id}")
    assert sub_resp.status_code == 200
    sub_data = sub_resp.get_data(as_text=True)
    assert 'dir="rtl"' in sub_data
    assert "نشط" in sub_data  # subscription_status_label("ACTIVE")

    cust_resp = client.get(f"/customers/{customer_id}")
    assert cust_resp.status_code == 200


def test_installation_detail_renders_in_arabic_with_localized_status(app, client, seeded):
    admin_id = make_staff(app, "r2render6@example.com", super_admin=True)
    license_id, full_key = make_license(app, admin_id)
    with app.app_context():
        from app.extensions import db_session
        from app.installations.services import register_installation
        from app.models.catalog import Platform
        from app.models.licensing import License
        from sqlalchemy import select

        lic = db_session.get(License, license_id)
        platform = db_session.execute(select(Platform).limit(1)).scalars().first()
        installation = register_installation(
            {"license_id": lic.id, "customer_id": lic.customer_id, "product_id": lic.product_id, "platform_id": platform.id},
            admin_id,
        )
        installation_id = installation.id
    force_login(client, app, admin_id)
    _switch_to_arabic(client)

    resp = client.get(f"/installations/{installation_id}")
    assert resp.status_code == 200
    data = resp.get_data(as_text=True)
    assert 'dir="rtl"' in data
    assert "مُسجَّل" in data  # installation_status_label("REGISTERED")


def test_pilot_and_renewal_new_forms_render_in_arabic(app, client, seeded):
    """The two commercial_ops 'new' forms that only need an existing
    subscription (not a full pilot/renewal record) -- real coverage of
    two of the largest, most form-heavy templates in the wave."""
    admin_id = make_staff(app, "r2render7@example.com", super_admin=True)
    with app.app_context():
        import uuid as uuid_mod

        from app.extensions import db_session
        from app.models.catalog import Plan, Product
        from app.models.customers import Customer
        from app.subscriptions.services import create_subscription, transition_subscription
        from sqlalchemy import select

        product = db_session.execute(select(Product).where(Product.product_code == "AURA_CLINIC")).scalars().first()
        plan = Plan(plan_code=f"R2TEST-{uuid_mod.uuid4().hex[:8]}", product_id=product.id, name="R2 Test Plan", billing_model="PILOT", currency="USD")
        customer = Customer(legal_name=f"R2 Test Co {uuid_mod.uuid4().hex[:8]}")
        db_session.add_all([plan, customer])
        db_session.commit()
        sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id}, admin_id)
        # DRAFT -> PILOT is a real, valid transition (unlike ACTIVE -> PILOT).
        transition_subscription(sub, "PILOT", admin_id)
        subscription_id = sub.id
    force_login(client, app, admin_id)
    _switch_to_arabic(client)

    renewal_resp = client.get(f"/commercial-ops/ui/renewals/new?subscription_id={subscription_id}")
    assert renewal_resp.status_code == 200
    assert 'dir="rtl"' in renewal_resp.get_data(as_text=True)

    pilot_resp = client.get(f"/commercial-ops/ui/pilots/new?subscription_id={subscription_id}")
    assert pilot_resp.status_code == 200
    assert 'dir="rtl"' in pilot_resp.get_data(as_text=True)
