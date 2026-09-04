"""Lead discovery -- ROUTES + TEMPLATES tests.

Covers GET /leads/discover, POST /leads/discover/search, and POST
/leads/discover/import (app/leads/routes.py), plus the leads/list.html
entry-point link. The backend provider (app/leads/discovery.py) is owned
by another agent and is exercised here only through its public contract
(DiscoveredPlace, LeadDiscoveryError, is_discovery_enabled) -- every test
below drives a FakeProvider through app.leads.routes.build_provider so
these tests never make a real network call and never depend on the real
Google Places provider's own test suite (test_lead_discovery_provider.py).
"""
from __future__ import annotations

import re
from datetime import date

import pytest

from app.leads.discovery import DiscoveredPlace, LeadDiscoveryError
from app.leads.routes import _NO_PROFILE_MESSAGE
from tests.conftest import force_login, get_csrf, make_staff

PLACE_WITH_PHONE = DiscoveredPlace(
    place_id="places/acme-pharmacy-1", name="Acme Pharmacy", address="123 Main St, Irbid",
    phone="+962700000001", website="https://acme-pharmacy.example",
)
PLACE_NO_PHONE = DiscoveredPlace(
    place_id="places/no-phone-pharmacy-2", name="No Phone Pharmacy", address="456 Side St, Irbid",
    phone=None, website=None,
)


class FakeProvider:
    """Stands in for app.leads.discovery.LeadDiscoveryProvider -- returns a
    fixed list of DiscoveredPlace (real dataclass from the contract, not a
    hand-rolled duplicate) so the route/template code under test is
    exercised against the exact shape the real provider produces."""

    def __init__(self, places):
        self._places = places

    def search(self, segment: str, area: str, *, limit: int = 20) -> list[DiscoveredPlace]:
        return self._places


def _enable_discovery(app, api_key="test-key"):
    app.config["LEAD_DISCOVERY_PROVIDER"] = "google_places"
    app.config["GOOGLE_PLACES_API_KEY"] = api_key


def _disable_discovery(app):
    app.config["LEAD_DISCOVERY_PROVIDER"] = ""
    app.config["GOOGLE_PLACES_API_KEY"] = ""


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


def _csrf(client, path):
    page = client.get(path)
    return get_csrf(page.get_data(as_text=True))


# -- 1. disabled ------------------------------------------------------------

def test_discover_form_disabled_shows_notice_and_hides_entry_point(app, client, seeded):
    staff_id = make_staff(app, "disc1@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)
    _disable_discovery(app)

    resp = client.get("/leads/discover")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "OWNER_GOOGLE_PLACES_API_KEY" in body

    list_body = client.get("/leads").get_data(as_text=True)
    assert "Discover leads from a map search" not in list_body


# -- 2. enabled -> entry point visible ---------------------------------------

def test_leads_list_shows_discover_link_when_enabled(app, client, seeded):
    staff_id = make_staff(app, "disc2@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)
    _enable_discovery(app)

    list_body = client.get("/leads").get_data(as_text=True)
    assert "Discover leads from a map search" in list_body


# -- 3. actor without leads.discover -> 403 ----------------------------------

def test_discover_form_requires_leads_discover_permission(app, client, seeded):
    # FINANCE holds no leads.* permission at all (app/staff/seed_data.py --
    # deliberately not a sales-pipeline role), so it's the correct "lacks
    # leads.discover" fixture -- SUPPORT/VIEWER hold leads.view_own/
    # leads.view_all respectively but neither holds leads.discover either;
    # FINANCE is simplest because it holds none of the leads.* family.
    staff_id = make_staff(app, "disc3@example.com", role_codes=["FINANCE"])
    force_login(client, app, staff_id)
    _enable_discovery(app)

    resp = client.get("/leads/discover")
    # require_permission() (app/security/rbac.py) calls flask.abort(403) for
    # a non-JSON request -- never a redirect -- so this must be a hard 403.
    assert resp.status_code == 403


# -- 4. search renders fake results, no-phone row disabled -------------------

def test_discover_search_renders_results_and_disables_no_phone_row(app, client, seeded, monkeypatch):
    staff_id = make_staff(app, "disc4@example.com", role_codes=["SALES"])
    with app.app_context():
        _make_profile(app, staff_id, "EMP-D4")
    force_login(client, app, staff_id)
    _enable_discovery(app)
    monkeypatch.setattr("app.leads.routes.build_provider", lambda config: FakeProvider([PLACE_WITH_PHONE, PLACE_NO_PHONE]))

    csrf = _csrf(client, "/leads/discover")
    resp = client.post(
        "/leads/discover/search",
        data={"csrf_token": csrf, "segment": "pharmacies", "area": "Irbid, Jordan", "priority": "MEDIUM"},
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    assert "Acme Pharmacy" in body
    assert "+962700000001" in body
    assert "No Phone Pharmacy" in body
    assert "cannot be imported" in body
    assert 'name="place_id-0"' in body
    assert 'name="place_id-1"' in body

    # Row 0 (has a phone) must default to checked and NOT disabled.
    row0 = re.search(r'<input type="checkbox" name="select-0"[^>]*>', body).group(0)
    assert "checked" in row0
    assert "disabled" not in row0
    # Row 1 (no phone) must be disabled so it cannot be ticked client-side.
    row1 = re.search(r'<input type="checkbox" name="select-1"[^>]*>', body).group(0)
    assert "disabled" in row1
    assert "checked" not in row1


# -- 5. empty area -> 400 -----------------------------------------------------

def test_discover_search_empty_area_is_400(app, client, seeded, monkeypatch):
    staff_id = make_staff(app, "disc5@example.com", role_codes=["SALES"])
    with app.app_context():
        _make_profile(app, staff_id, "EMP-D5")
    force_login(client, app, staff_id)
    _enable_discovery(app)
    monkeypatch.setattr("app.leads.routes.build_provider", lambda config: FakeProvider([PLACE_WITH_PHONE]))

    csrf = _csrf(client, "/leads/discover")
    resp = client.post(
        "/leads/discover/search",
        data={"csrf_token": csrf, "segment": "pharmacies", "area": "", "priority": "MEDIUM"},
    )
    assert resp.status_code == 400
    assert "segment and an area" in resp.get_data(as_text=True)


# -- 6. RATE_LIMITED -> 502, busy text, API key never leaked -----------------

def test_discover_search_rate_limited_returns_502_without_leaking_key(app, client, seeded, monkeypatch):
    staff_id = make_staff(app, "disc6@example.com", role_codes=["SALES"])
    with app.app_context():
        _make_profile(app, staff_id, "EMP-D6")
    force_login(client, app, staff_id)
    _enable_discovery(app, api_key="super-secret-test-key")

    class RaisingProvider:
        def search(self, segment, area, *, limit=20):
            raise LeadDiscoveryError("RATE_LIMITED", detail="quota exceeded for super-secret-test-key")

    monkeypatch.setattr("app.leads.routes.build_provider", lambda config: RaisingProvider())

    csrf = _csrf(client, "/leads/discover")
    resp = client.post(
        "/leads/discover/search",
        data={"csrf_token": csrf, "segment": "pharmacies", "area": "Irbid, Jordan"},
    )
    assert resp.status_code == 502
    body = resp.get_data(as_text=True)
    assert "busy" in body
    assert "super-secret-test-key" not in body


# -- 7. import: one phone row imported, no-phone row skipped -----------------

def test_discover_import_creates_lead_and_skips_no_phone_row(app, client, seeded, monkeypatch):
    from app.employees.services import activate_employee

    sales_staff_id = make_staff(app, "disc7@example.com", role_codes=["SALES"])
    target_staff_id = make_staff(app, "disc7-target@example.com", role_codes=["SALES"])
    with app.app_context():
        _make_profile(app, sales_staff_id, "EMP-D7")
        target_profile = _make_profile(app, target_staff_id, "EMP-D7T")
        activate_employee(target_profile, actor_staff_user_id=target_staff_id)
        target_profile_id = target_profile.id
    force_login(client, app, sales_staff_id)
    _enable_discovery(app)
    monkeypatch.setattr("app.leads.routes.build_provider", lambda config: FakeProvider([PLACE_WITH_PHONE, PLACE_NO_PHONE]))

    csrf = _csrf(client, "/leads/discover")
    import_data = {
        "csrf_token": csrf, "count": "2", "priority": "HIGH",
        "assigned_employee_profile_id": str(target_profile_id),
        "select-0": "on", "place_id-0": PLACE_WITH_PHONE.place_id, "name-0": PLACE_WITH_PHONE.name,
        "phone-0": PLACE_WITH_PHONE.phone, "address-0": PLACE_WITH_PHONE.address, "website-0": PLACE_WITH_PHONE.website,
        # Selected even though it has no phone -- a crafted/replayed POST,
        # not something the rendered UI allows (its checkbox is disabled).
        # The route must still refuse it server-side.
        "select-1": "on", "place_id-1": PLACE_NO_PHONE.place_id, "name-1": PLACE_NO_PHONE.name,
        "phone-1": "", "address-1": PLACE_NO_PHONE.address or "", "website-1": "",
    }
    resp = client.post("/leads/discover/import", data=import_data)
    assert resp.status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.leads import Lead

        leads = db_session.query(Lead).filter_by(source="MAPS_DISCOVERY").all()
        assert len(leads) == 1
        lead = leads[0]
        assert lead.phone == PLACE_WITH_PHONE.phone
        assert lead.organization_or_prospect_name == PLACE_WITH_PHONE.name
        assert lead.location_summary == PLACE_WITH_PHONE.address
        assert lead.priority == "HIGH"
        assert lead.assigned_employee_profile_id == target_profile_id


# -- 8. DEDUP: importing the same place twice creates exactly one Lead ------

def test_discover_import_same_place_twice_creates_one_lead(app, client, seeded, monkeypatch):
    staff_id = make_staff(app, "disc8@example.com", role_codes=["SALES"])
    with app.app_context():
        _make_profile(app, staff_id, "EMP-D8")
    force_login(client, app, staff_id)
    _enable_discovery(app)
    monkeypatch.setattr("app.leads.routes.build_provider", lambda config: FakeProvider([PLACE_WITH_PHONE]))

    def _import_once():
        csrf = _csrf(client, "/leads/discover")
        data = {
            "csrf_token": csrf, "count": "1", "priority": "MEDIUM", "assigned_employee_profile_id": "",
            "select-0": "on", "place_id-0": PLACE_WITH_PHONE.place_id, "name-0": PLACE_WITH_PHONE.name,
            "phone-0": PLACE_WITH_PHONE.phone, "address-0": PLACE_WITH_PHONE.address, "website-0": PLACE_WITH_PHONE.website,
        }
        return client.post("/leads/discover/import", data=data)

    assert _import_once().status_code == 302
    assert _import_once().status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.leads import Lead

        leads = db_session.query(Lead).filter_by(source="MAPS_DISCOVERY", phone=PLACE_WITH_PHONE.phone).all()
        # THE dedup assertion: re-importing the same place_id must never
        # create a second Lead row -- create_lead()'s idempotency_key lookup
        # is what enforces this (see mutation proof in the PR/report).
        assert len(leads) == 1


# -- 9. no EmployeeProfile -> search is 400 with the no-profile message -----

def test_discover_search_without_employee_profile_is_400(app, client, seeded, monkeypatch):
    # No _make_profile() call -- this staff account has no EmployeeProfile,
    # mirroring the real Super Admin bootstrap-account gap _missing_profile()
    # exists to catch (see routes.py's own docstring on that helper).
    staff_id = make_staff(app, "disc9@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)
    _enable_discovery(app)
    monkeypatch.setattr("app.leads.routes.build_provider", lambda config: FakeProvider([PLACE_WITH_PHONE]))

    csrf = _csrf(client, "/leads/discover")
    resp = client.post(
        "/leads/discover/search",
        data={"csrf_token": csrf, "segment": "pharmacies", "area": "Irbid, Jordan"},
    )
    assert resp.status_code == 400
    assert _NO_PROFILE_MESSAGE in resp.get_data(as_text=True)


# -- 10-12. Hardening found in review, crafted-POST paths ----------------------

def _sales_actor(app, client, email, employee_number):
    staff_id = make_staff(app, email, role_codes=["SALES"])
    with app.app_context():
        _make_profile(app, staff_id, employee_number)
    force_login(client, app, staff_id)
    _enable_discovery(app)
    return staff_id


def _import_rows(client, rows, *, count=None):
    csrf = _csrf(client, "/leads/discover")
    data = {"csrf_token": csrf, "count": str(len(rows) if count is None else count),
            "priority": "MEDIUM", "assigned_employee_profile_id": ""}
    for n, row in enumerate(rows):
        data[f"select-{n}"] = "on"
        for key, value in row.items():
            data[f"{key}-{n}"] = value
    return client.post("/leads/discover/import", data=data)


def test_import_refuses_rows_without_a_place_id_instead_of_colliding_them(app, client, seeded, monkeypatch):
    """Every row lacking a place_id would otherwise share the single
    idempotency key "maps-discovery:" -- the first creates a lead and every
    later one is silently deduplicated INTO it, so three places become one
    lead with no error. A real search always renders the id; only a crafted
    or truncated POST omits it, and each such row must be refused, not
    merged. The one well-formed row in the batch must still import."""
    _sales_actor(app, client, "disc10@example.com", "EMP-D10")
    monkeypatch.setattr("app.leads.routes.build_provider", lambda config: FakeProvider([PLACE_WITH_PHONE]))

    rows = [
        {"place_id": "", "name": "Ghost Pharmacy A", "phone": "+962 7 1111 1111", "address": "x", "website": ""},
        {"place_id": "", "name": "Ghost Pharmacy B", "phone": "+962 7 2222 2222", "address": "y", "website": ""},
        {"place_id": PLACE_WITH_PHONE.place_id, "name": PLACE_WITH_PHONE.name, "phone": PLACE_WITH_PHONE.phone,
         "address": PLACE_WITH_PHONE.address or "", "website": ""},
    ]
    resp = _import_rows(client, rows)
    assert resp.status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.leads import Lead

        leads = db_session.query(Lead).filter_by(source="MAPS_DISCOVERY").all()
        names = sorted(lead.organization_or_prospect_name for lead in leads)
        # Exactly the well-formed row -- neither ghost, and not one ghost
        # standing in for both.
        assert names == [PLACE_WITH_PHONE.name], names


def test_import_clamps_a_hostile_count_to_the_search_page_size(app, client, seeded, monkeypatch):
    """`count` is a hidden input. Without a clamp a POST of count=10_000_000
    spins the loop that many times. It must be capped at MAX_RESULTS, and the
    one real row must still import."""
    from app.leads.discovery import MAX_RESULTS

    _sales_actor(app, client, "disc11@example.com", "EMP-D11")
    monkeypatch.setattr("app.leads.routes.build_provider", lambda config: FakeProvider([PLACE_WITH_PHONE]))

    # Prove the clamp by placing a second valid row at an index beyond the
    # ceiling: if the loop ran to `count`, it would be imported too.
    beyond = MAX_RESULTS + 5
    csrf = _csrf(client, "/leads/discover")
    data = {"csrf_token": csrf, "count": "10000000", "priority": "MEDIUM", "assigned_employee_profile_id": "",
            "select-0": "on", "place_id-0": PLACE_WITH_PHONE.place_id, "name-0": PLACE_WITH_PHONE.name,
            "phone-0": PLACE_WITH_PHONE.phone, "address-0": PLACE_WITH_PHONE.address or "", "website-0": "",
            f"select-{beyond}": "on", f"place_id-{beyond}": "place-beyond-ceiling",
            f"name-{beyond}": "Beyond Ceiling Ltd", f"phone-{beyond}": "+962 7 9999 9999",
            f"address-{beyond}": "z", f"website-{beyond}": ""}
    resp = client.post("/leads/discover/import", data=data)
    assert resp.status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.leads import Lead

        names = sorted(l.organization_or_prospect_name for l in db_session.query(Lead).filter_by(source="MAPS_DISCOVERY").all())
        assert names == [PLACE_WITH_PHONE.name], names


def test_results_never_link_a_non_http_website(app, client, seeded, monkeypatch):
    """The website is external data. Jinja escaping stops an attribute
    breakout, but it would not stop `javascript:` from becoming a clickable
    href. Only http(s) may be linked; anything else renders as text."""
    from app.leads.discovery import DiscoveredPlace

    _sales_actor(app, client, "disc12@example.com", "EMP-D12")
    hostile = DiscoveredPlace(place_id="p-js", name="Hostile Site", address=None,
                              phone="+962 7 3333 3333", website="javascript:alert(1)")
    benign = DiscoveredPlace(place_id="p-ok", name="Benign Site", address=None,
                             phone="+962 7 4444 4444", website="https://example.com/shop")
    monkeypatch.setattr("app.leads.routes.build_provider", lambda config: FakeProvider([hostile, benign]))

    csrf = _csrf(client, "/leads/discover")
    resp = client.post("/leads/discover/search", data={"csrf_token": csrf, "segment": "shops", "area": "Amman",
                                                       "priority": "MEDIUM", "assigned_employee_profile_id": ""})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'href="javascript:' not in body
    assert "javascript:alert(1)" in body             # still shown, as inert text
    assert 'href="https://example.com/shop"' in body  # the allow-half
