"""Website-leads topbar badge: count endpoint, ownership scoping, idle-session
behaviour and the static guards (markup, [hidden] CSS, polling JS).

The count endpoint must (a) answer only for lead-permission holders, (b) scope
exactly like the real leads list, and (c) never refresh the session idle timer,
or a tab left open would keep an unattended session alive forever.
"""
from __future__ import annotations

import os
import re
from datetime import date, timedelta

from tests.conftest import force_login, make_staff

COUNT_URL = "/attention/website-leads/count"

_OWNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CSS_PATH = os.path.join(_OWNER_DIR, "app", "static", "css", "shell.css")
_JS_PATH = os.path.join(_OWNER_DIR, "app", "static", "js", "website-leads-badge.js")


def _make_profile(app, staff_id, number):
    from app.employees.services import create_employee_profile

    # ACTIVE: assign_lead requires the destination profile to be ACTIVE.
    with app.app_context():
        return create_employee_profile(
            {
                "staff_user_id": staff_id,
                "employee_number": number,
                "full_name": f"Employee {number}",
                "employment_start_date": date(2026, 1, 1),
                "employment_status": "ACTIVE",
            },
            actor_staff_user_id=staff_id,
        ).id


def _ingest(app, lead_id):
    from app.leads.website_intake import ingest_website_lead, parse_website_lead_payload, seed_website_intake_actor

    with app.app_context():
        seed_website_intake_actor()
        lead, _created = ingest_website_lead(
            parse_website_lead_payload(
                {
                    "lead_id": lead_id,
                    "name": f"Visitor {lead_id}",
                    "phone": "0790000000",
                    "business_type": "shop",
                    "business_name": f"Shop {lead_id}",
                    "branches": "1",
                    "message": "hello",
                    "lang": "en",
                }
            )
        )
        return lead.id


def _count(client):
    resp = client.get(COUNT_URL)
    assert resp.status_code == 200
    return resp.get_json()["count"]


def _set_last_seen(app, staff_id, value):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.staff import StaffSession

        rows = db_session.execute(select(StaffSession).where(StaffSession.staff_user_id == staff_id)).scalars().all()
        assert rows
        for row in rows:
            row.last_seen_at = value
        db_session.commit()


def _get_last_seen(app, staff_id):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.staff import StaffSession

        db_session.expire_all()
        row = db_session.execute(select(StaffSession).where(StaffSession.staff_user_id == staff_id)).scalars().first()
        return row.last_seen_at


def test_count_requires_login(app, client, seeded):
    resp = client.get(COUNT_URL)
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "authentication_required"}
    assert resp.headers["Cache-Control"] == "no-store"


def test_count_is_forbidden_without_lead_permissions(app, client, seeded):
    finance = make_staff(app, "badge-fin@example.com", role_codes=["FINANCE"])
    force_login(client, app, finance)
    resp = client.get(COUNT_URL)
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "forbidden"}


def test_view_all_counts_new_website_leads_only(app, client, seeded):
    from app.extensions import db_session
    from app.leads.services import change_lead_status, create_lead
    from app.models.leads import Lead

    viewer = make_staff(app, "badge-viewer@example.com", role_codes=["VIEWER"])
    profile_id = _make_profile(app, viewer, "EMP-BADGE-V")
    lead1 = _ingest(app, "1")
    _ingest(app, "2")
    with app.app_context():
        # NEW but not WEBSITE: must never be counted.
        create_lead(
            {"organization_or_prospect_name": "Walk-in", "phone": "1", "source": "OTHER"}, profile_id, viewer
        )
    force_login(client, app, viewer)
    assert _count(client) == 2

    with app.app_context():
        change_lead_status(db_session.get(Lead, lead1), "LOST", profile_id, viewer, reason="test")
    assert _count(client) == 1


def test_view_own_counts_only_assigned_website_leads(app, client, seeded):
    from app.extensions import db_session
    from app.leads.services import assign_lead
    from app.models.leads import Lead

    sales = make_staff(app, "badge-sales@example.com", role_codes=["SALES"])
    profile_id = _make_profile(app, sales, "EMP-BADGE-S")
    lead1 = _ingest(app, "1")
    _ingest(app, "2")
    force_login(client, app, sales)
    assert _count(client) == 0

    with app.app_context():
        assign_lead(db_session.get(Lead, lead1), profile_id, profile_id, sales)
    assert _count(client) == 1


def test_view_own_without_profile_counts_zero(app, client, seeded):
    sales = make_staff(app, "badge-noprofile@example.com", role_codes=["SALES"])
    _ingest(app, "1")
    force_login(client, app, sales)
    resp = client.get(COUNT_URL)
    assert resp.status_code == 200
    assert resp.get_json() == {"count": 0}


def test_count_matches_the_attention_category(app, client, seeded):
    from app.attention.service import get_attention_items
    from app.extensions import db_session
    from app.leads.services import assign_lead
    from app.models.leads import Lead
    from app.models.staff import StaffUser

    viewer = make_staff(app, "badge-cat-viewer@example.com", role_codes=["VIEWER"])
    sales = make_staff(app, "badge-cat-sales@example.com", role_codes=["SALES"])
    finance = make_staff(app, "badge-cat-fin@example.com", role_codes=["FINANCE"])
    sales_profile = _make_profile(app, sales, "EMP-BADGE-CS")
    lead1 = _ingest(app, "1")
    _ingest(app, "2")
    with app.app_context():
        assign_lead(db_session.get(Lead, lead1), sales_profile, sales_profile, sales)

    def category_items(staff_id):
        with app.test_request_context():
            items = get_attention_items(db_session.get(StaffUser, staff_id))
            return [i for i in items if i.category == "new_website_leads"]

    viewer_client = app.test_client()
    force_login(viewer_client, app, viewer)
    viewer_items = category_items(viewer)
    assert len(viewer_items) == _count(viewer_client) == 2
    assert all(i.category_label == "New website leads" for i in viewer_items)
    assert any("Shop 1" in i.title for i in viewer_items)

    sales_client = app.test_client()
    force_login(sales_client, app, sales)
    sales_items = category_items(sales)
    assert len(sales_items) == _count(sales_client) == 1
    assert "Shop 1" in sales_items[0].title

    assert category_items(finance) == []


def test_polling_does_not_extend_an_idle_session(app, client, seeded):
    from app.models.base import utcnow

    viewer = make_staff(app, "badge-idle@example.com", role_codes=["VIEWER"])
    force_login(client, app, viewer)
    stamp = utcnow() - timedelta(minutes=10)
    _set_last_seen(app, viewer, stamp)

    assert client.get(COUNT_URL).status_code == 200
    assert _get_last_seen(app, viewer) == stamp

    # Control: an ordinary page DOES refresh the timer, so the equality above
    # is proving the poll's behaviour and not a stuck column.
    assert client.get("/attention").status_code == 200
    assert _get_last_seen(app, viewer) > stamp


def test_an_idle_expired_session_gets_401_from_the_poll(app, client, seeded):
    from app.models.base import utcnow

    viewer = make_staff(app, "badge-expired@example.com", role_codes=["VIEWER"])
    force_login(client, app, viewer)
    _set_last_seen(app, viewer, utcnow() - timedelta(seconds=app.config["SESSION_IDLE_TIMEOUT_SECONDS"] + 60))
    assert client.get(COUNT_URL).status_code == 401


def test_badge_markup_only_for_lead_permission_holders_and_ships_hidden(app, client, seeded):
    viewer = make_staff(app, "badge-markup-v@example.com", role_codes=["VIEWER"])
    finance = make_staff(app, "badge-markup-f@example.com", role_codes=["FINANCE"])

    force_login(client, app, viewer)
    html = client.get("/attention").get_data(as_text=True)
    assert re.search(r'<a class="aura-topbar__website-leads" id="aura-website-leads-badge" hidden', html)
    assert 'data-count-url="/attention/website-leads/count"' in html
    assert "js/website-leads-badge.js" in html

    finance_client = app.test_client()
    force_login(finance_client, app, finance)
    html = finance_client.get("/attention").get_data(as_text=True)
    assert "aura-website-leads-badge" not in html


def test_badge_css_keeps_the_hidden_attribute_effective():
    with open(_CSS_PATH, encoding="utf-8") as fh:
        css = fh.read()
    assert re.search(r"\.aura-topbar__website-leads\[hidden\]\s*\{\s*display:\s*none;", css)


def test_badge_js_hides_at_zero_and_stops_on_auth_errors():
    # Static guard only; the real proof is the browser check.
    with open(_JS_PATH, encoding="utf-8") as fh:
        js = fh.read()
    assert re.search(r"if \(!\(count > 0\)\) \{\s*badge\.hidden = true;", js)
    assert "res.status === 401 || res.status === 403" in js
    assert "visibilitychange" in js
    assert "console." not in js
