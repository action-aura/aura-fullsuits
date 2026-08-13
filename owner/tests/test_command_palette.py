"""Command Palette (Ctrl+K) tests (Stage C).

Same rigor as test_attention_center.py, focused on the two failure modes
the governing task explicitly calls out as non-optional for this exact
shape of feature: (1) ownership scoping must exactly mirror the real
`_own`/`_all` permission-pair rule already enforced by the corresponding
real list route -- getting this wrong is a real IDOR -- and (2) a `_all`
bypass must never be blocked by a "no EmployeeProfile" guard (the real
bug found and fixed in the Attention Center during this same stage).
Also covers: an entity type is never searched at all without its real
governing permission, the short-query bound, and the static command
list being real permission-gated data, not an invented one.
"""
from __future__ import annotations

from datetime import date

from tests.conftest import force_login, make_license, make_staff


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


def _make_lead(app, staff_id, profile_id, org_name):
    from app.leads.services import create_lead

    return create_lead(
        {"organization_or_prospect_name": org_name}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id
    )


def _make_customer(app, legal_name):
    from app.extensions import db_session
    from app.models.customers import Customer

    customer = Customer(legal_name=legal_name)
    db_session.add(customer)
    db_session.commit()
    return customer


# ------------------------------------------------------------- Leads --

def test_lead_search_own_permission_excludes_other_employees_lead(app, seeded):
    staff_a = make_staff(app, "cp-lead-a@example.com", role_codes=["SALES"])
    staff_b = make_staff(app, "cp-lead-b@example.com", role_codes=["SALES"])
    with app.test_request_context():
        from app.command_palette.service import search_entities
        from app.extensions import db_session
        from app.models.staff import StaffUser

        profile_a = _make_profile(app, staff_a, "EMP-CP-A")
        profile_b = _make_profile(app, staff_b, "EMP-CP-B")
        _make_lead(app, staff_a, profile_a.id, "Alpha Searchable Deal")
        _make_lead(app, staff_b, profile_b.id, "Beta Confidential Deal")

        actor_a = db_session.get(StaffUser, staff_a)
        results = search_entities(actor_a, "Searchable")
        labels = " ".join(r.label for r in results if r.type == "lead")
        assert "Alpha Searchable Deal" in labels

        results_cross = search_entities(actor_a, "Confidential")
        labels_cross = " ".join(r.label for r in results_cross if r.type == "lead")
        assert "Beta Confidential Deal" not in labels_cross


def test_lead_search_view_all_bypass_works_without_own_employee_profile(app, seeded):
    """The real bug-shaped proof: VIEWER holds leads.view_all but has no
    EmployeeProfile of its own -- the `_all` bypass must still return
    every employee's lead, never short-circuited by a "no profile"
    guard that runs before the bypass check (see this stage's own
    Attention Center precedent for the exact real bug this mirrors)."""
    staff_a = make_staff(app, "cp-lead-c@example.com", role_codes=["SALES"])
    viewer = make_staff(app, "cp-lead-viewer@example.com", role_codes=["VIEWER"])
    with app.test_request_context():
        from app.command_palette.service import search_entities
        from app.extensions import db_session
        from app.models.staff import StaffUser

        profile_a = _make_profile(app, staff_a, "EMP-CP-C")
        _make_lead(app, staff_a, profile_a.id, "Company-Wide Visible Lead")

        actor_viewer = db_session.get(StaffUser, viewer)
        results = search_entities(actor_viewer, "Company-Wide")
        assert any(r.label == "Company-Wide Visible Lead" for r in results if r.type == "lead")


def test_lead_search_not_run_without_leads_permission(app, seeded):
    """An actor holding neither leads.view_own nor leads.view_all must
    get zero lead results even for a query that would otherwise match a
    real record -- never "query everything, filter in the response"."""
    staff_a = make_staff(app, "cp-lead-owner@example.com", role_codes=["SALES"])
    bystander = make_staff(app, "cp-lead-bystander@example.com", role_codes=[])
    with app.test_request_context():
        from app.command_palette.service import search_entities
        from app.extensions import db_session
        from app.models.staff import StaffUser

        profile_a = _make_profile(app, staff_a, "EMP-CP-BYST")
        _make_lead(app, staff_a, profile_a.id, "Unreachable Lead For Bystander")

        actor = db_session.get(StaffUser, bystander)
        results = search_entities(actor, "Unreachable")
        assert results == []


# --------------------------------------------------------- Customers --

def test_customer_search_own_scoping_excludes_unassigned_customer(app, seeded):
    staff_a = make_staff(app, "cp-cust-a@example.com", role_codes=["SALES"])
    with app.test_request_context():
        from app.command_palette.service import search_entities
        from app.extensions import db_session
        from app.models.staff import StaffUser

        _make_profile(app, staff_a, "EMP-CP-CUST-A")
        assigned = _make_customer(app, "Assigned Searchable Customer")
        unassigned = _make_customer(app, "Unassigned Searchable Customer")
        # SALES alone (no customers.view_all) is ownership-scoped by
        # assigned_sales_staff_id -- mirrors apply_ownership_filter's real
        # Customer rule (see app/leads/ownership.py).
        assigned.assigned_sales_staff_id = staff_a
        db_session.commit()

        actor_a = db_session.get(StaffUser, staff_a)
        results = search_entities(actor_a, "Searchable Customer")
        labels = {r.label for r in results if r.type == "customer"}
        assert "Assigned Searchable Customer" in labels
        assert "Unassigned Searchable Customer" not in labels


def test_customer_search_view_all_bypass_works_without_own_employee_profile(app, seeded):
    viewer = make_staff(app, "cp-cust-viewer@example.com", role_codes=["VIEWER"])
    with app.test_request_context():
        from app.command_palette.service import search_entities
        from app.extensions import db_session
        from app.models.staff import StaffUser

        _make_customer(app, "Viewer Visible Customer")

        actor_viewer = db_session.get(StaffUser, viewer)
        results = search_entities(actor_viewer, "Viewer Visible")
        assert any(r.label == "Viewer Visible Customer" for r in results if r.type == "customer")


# -------------------------------------------------------- Licenses --

def test_license_search_requires_licenses_view_permission(app, seeded):
    holder = make_staff(app, "cp-lic-holder@example.com", role_codes=["VIEWER"])  # has licenses.view
    bystander = make_staff(app, "cp-lic-bystander@example.com", role_codes=[])
    with app.test_request_context():
        from app.command_palette.service import search_entities
        from app.extensions import db_session
        from app.models.staff import StaffUser

        license_id, _key = make_license(app, holder, product_code="AURA_CLINIC")
        from app.models.customers import Customer
        from app.models.licensing import License

        license_row = db_session.get(License, license_id)
        customer_name = db_session.get(Customer, license_row.customer_id).legal_name

        actor_holder = db_session.get(StaffUser, holder)
        results_holder = search_entities(actor_holder, customer_name)
        assert any(r.type == "license" for r in results_holder)

        actor_bystander = db_session.get(StaffUser, bystander)
        results_bystander = search_entities(actor_bystander, customer_name)
        assert not any(r.type == "license" for r in results_bystander)


# -------------------------------------------------------------- Bounds --

def test_short_query_returns_no_entity_results(app, seeded):
    staff_a = make_staff(app, "cp-short@example.com", role_codes=["SALES"])
    with app.test_request_context():
        from app.command_palette.service import search_entities
        from app.extensions import db_session
        from app.models.staff import StaffUser

        profile_a = _make_profile(app, staff_a, "EMP-CP-SHORT")
        _make_lead(app, staff_a, profile_a.id, "A")

        actor = db_session.get(StaffUser, staff_a)
        assert search_entities(actor, "") == []
        assert search_entities(actor, "A") == []  # below MIN_QUERY_LENGTH (2)


def test_unauthenticated_search_route_requires_login(app, client, seeded):
    response = client.get("/api/command-palette/search?q=test")
    assert response.status_code in (302, 401)


def test_search_route_returns_bounded_json_for_logged_in_staff(app, client, seeded):
    staff_a = make_staff(app, "cp-route@example.com", role_codes=["SALES"])
    with app.test_request_context():
        profile_a = _make_profile(app, staff_a, "EMP-CP-ROUTE")
        _make_lead(app, staff_a, profile_a.id, "Route Searchable Deal")

    force_login(client, app, staff_a)
    response = client.get("/api/command-palette/search?q=Searchable")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["query"] == "Searchable"
    assert any(r["label"] == "Route Searchable Deal" for r in payload["results"])
    assert response.headers.get("Cache-Control") == "no-store"


# ------------------------------------------------------ Static commands --

def test_static_commands_dashboard_always_present_for_any_staff(app, seeded):
    with app.test_request_context():
        from app.command_palette.service import get_static_commands

        data = get_static_commands(set())
        labels = [item["label"] for item in data["navigate"]]
        assert "Dashboard" in labels
        # No permissions held -- nothing else should appear.
        assert len(data["navigate"]) == 1
        assert data["create"] == []


def test_static_commands_gates_leads_link_on_real_permission(app, seeded):
    with app.test_request_context():
        from app.command_palette.service import get_static_commands

        without = get_static_commands(set())
        with_perm = get_static_commands({"leads.view_own"})
        assert "Leads" not in [item["label"] for item in without["navigate"]]
        assert "Leads" in [item["label"] for item in with_perm["navigate"]]


def test_static_commands_gates_quick_create_lead_on_create_permission(app, seeded):
    with app.test_request_context():
        from app.command_palette.service import get_static_commands

        without = get_static_commands(set())
        with_perm = get_static_commands({"leads.create"})
        assert without["create"] == []
        assert any(item["label"] == "New Lead" for item in with_perm["create"])
