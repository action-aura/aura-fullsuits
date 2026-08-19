"""Dashboard Interactive Kit -- keyboard shortcuts shell wiring. Proves the
new script/partial are only rendered for logged-in staff (same trust
boundary as the command palette) and that the destination list inside the
cheatsheet only contains routes the current staff member can actually
reach -- never a hardcoded route list independent of has_permission()."""
from __future__ import annotations

from tests.conftest import force_login, make_staff


def test_shortcuts_script_and_cheatsheet_only_render_for_logged_in_staff(app, client, seeded):
    resp = client.get("/auth/login")
    html = resp.get_data(as_text=True)
    assert "js/shortcuts.js" not in html
    assert "aura-shortcuts-cheatsheet" not in html

    staff_id = make_staff(app, "shortcuts-super@example.com", super_admin=True)
    force_login(client, app, staff_id)
    resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert "js/shortcuts.js" in html
    assert "aura-shortcuts-cheatsheet" in html


def test_shortcuts_cheatsheet_hides_a_destination_the_role_cannot_reach(app, client, seeded):
    staff_id = make_staff(app, "shortcuts-support@example.com", role_codes=["SUPPORT"])
    force_login(client, app, staff_id)

    resp = client.get("/")
    html = resp.get_data(as_text=True)
    # SUPPORT has customers.view and leads.view_own, but no quotes.*/expenses.* --
    # the Sales(Quotes) and Expenses destinations must not render for it.
    assert 'data-shortcut-goto="c"' in html
    assert 'data-shortcut-goto="l"' in html
    assert 'data-shortcut-goto="s"' not in html
    assert 'data-shortcut-goto="e"' not in html
