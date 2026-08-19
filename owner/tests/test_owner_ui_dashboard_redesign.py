"""Dashboard Interactive Kit -- Dashboard page redesign. Proves the new
markup preserves the exact permission-gating the old <dl>-based dashboard
had (same has_permission()/has_any_permission() codes), renders real
values from get_dashboard_summary(), and wires real drill-down links --
never a second, independently-derived data source."""
from __future__ import annotations

from tests.conftest import force_login, make_license, make_staff

# The real markup Task 3 built for each quick-action card (see
# dashboard/index.html's quick-actions block): a plain <a> with the
# aura-quick-action-card class and a real drill-down href, sourced from
# command_palette/service.py::get_static_commands()'s "create" entries.
# Route paths verified against each blueprint's url_prefix + @bp.route.
QUICK_ACTION_ROUTES = {
    "new_quote": "/quotes/new",
    "new_payment": "/payments/new",
    "new_lead": "/leads/new",
    "new_expense": "/operations/expenses/new",
    "new_cash_closing": "/operations/cash-closings/new",
}


def _has_quick_action_card(html: str, href: str) -> bool:
    """True only if the real Dashboard card markup for this href is present
    -- not just the label text or href appearing anywhere on the page (the
    command-palette JSON island renders unconditionally on every page and
    would otherwise make this check meaningless)."""
    return f'<a class="aura-quick-action-card" href="{href}"' in html


def test_super_admin_sees_quick_actions_and_stat_tiles(app, client, seeded):
    staff_id = make_staff(app, "dash-super@example.com", super_admin=True)
    force_login(client, app, staff_id)

    resp = client.get("/")
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "aura-quick-actions" in html
    assert _has_quick_action_card(html, QUICK_ACTION_ROUTES["new_quote"])
    assert _has_quick_action_card(html, QUICK_ACTION_ROUTES["new_payment"])
    assert _has_quick_action_card(html, QUICK_ACTION_ROUTES["new_cash_closing"])
    assert "aura-stat-tile" in html
    assert "Total customers" in html


def test_role_with_zero_relevant_permissions_gets_the_empty_overview_state(app, client, seeded):
    staff_id = make_staff(app, "dash-none@example.com", role_codes=[])
    force_login(client, app, staff_id)

    resp = client.get("/")
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "Nothing to show here yet" in html
    assert "aura-quick-actions" not in html
    assert "New Quote" not in html


def test_licenses_donut_drill_down_links_use_the_real_status_filter(app, client, seeded):
    staff_id = make_staff(app, "dash-lic@example.com", super_admin=True)
    # make_license() -> issue_license_key() drives the license to a real
    # "ISSUED" status (app/licensing/services.py), so the donut has a real,
    # known status bucket to build a drill-down link for -- not just the
    # href-shape check the previous version of this test settled for.
    make_license(app, staff_id)
    force_login(client, app, staff_id)

    resp = client.get("/")
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "/licenses?status=ISSUED" in html


def test_quick_action_cards_are_individually_gated_on_their_own_permission(app, client, seeded):
    staff_id = make_staff(app, "dash-quotes-only@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)

    resp = client.get("/")
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert _has_quick_action_card(html, QUICK_ACTION_ROUTES["new_quote"])
    assert not _has_quick_action_card(html, QUICK_ACTION_ROUTES["new_cash_closing"])
    # SALES has no cash_closing.* permission (see app/staff/seed_data.py) --
    # confirm the real href is absent outright, not just the card wrapper.
    assert f'href="{QUICK_ACTION_ROUTES["new_cash_closing"]}"' not in html
