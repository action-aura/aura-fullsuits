"""Sidebar department navigation -- one-click disclosure groups.

The sidebar used to be a flat ~25-link scroll that ran past the fold at a
1000px viewport, so every destination in the product competed for attention
at once. Each department heading is now a real disclosure <button> over its
own link list, collapsed by default.

What these tests actually defend, in order of how badly each would hurt:

1. The collapse is *presentation only*. A group a role cannot see must
   still not render at all -- a disclosure that merely hides links the
   server would happily have shown is a different, much worse thing than a
   nav that renders nothing. Permission gating is asserted directly here,
   not assumed from the fact that the markup changed shape.
2. The current page's group is expanded by the SERVER, on first paint.
   These tests never execute JavaScript, so an assertion that a group is
   open in the response body is itself the proof that no script was
   needed -- which is the whole point (no flash of an all-shut nav).
3. The ARIA is real: aria-controls has to resolve to an element that
   actually exists, and collapsed lists carry `hidden` so screen-reader
   output and Tab order match what is on screen. A disclosure whose
   aria-controls points at nothing is worse than no ARIA at all.
"""
from __future__ import annotations

import re

from tests.conftest import force_login, make_staff

# Stable per-department slugs (layout/_sidebar.html). Deliberately spelled
# out rather than scraped from the response: if a group silently stops
# rendering, this list is what notices.
ALL_SLUGS = ["overview", "crm", "sales", "finance-ops", "licensing", "management", "system"]


def _group_buttons(html: str) -> dict[str, str]:
    """slug -> the raw <button ...> tag for that department's disclosure."""
    found = {}
    for tag in re.findall(r"<button[^>]*>", html):
        match = re.search(r'data-nav-group-toggle="([a-z-]+)"', tag)
        if match:
            found[match.group(1)] = tag
    return found


def _list_tag(html: str, list_id: str) -> str | None:
    """The raw <ul ...> opening tag carrying `list_id`, or None if absent."""
    match = re.search(r'<ul[^>]*\bid="%s"[^>]*>' % re.escape(list_id), html)
    return match.group(0) if match else None


def _dashboard_html(app, client, **staff_kwargs) -> str:
    staff_id = make_staff(app, **staff_kwargs)
    force_login(client, app, staff_id)
    return client.get("/").get_data(as_text=True)


def test_every_group_heading_is_a_button_with_aria_controls_pointing_at_a_real_list(app, client, seeded):
    html = _dashboard_html(app, client, email="deptnav-super@example.com", super_admin=True)
    buttons = _group_buttons(html)

    assert sorted(buttons) == sorted(ALL_SLUGS)
    # The old inert heading must be gone -- a leftover <div> would be a
    # department nobody can open.
    assert '<div class="aura-nav-group__title"' not in html

    for slug in ALL_SLUGS:
        tag = buttons[slug]
        assert 'type="button"' in tag, slug
        assert re.search(r'aria-expanded="(true|false)"', tag), slug

        controls = re.search(r'aria-controls="([^"]+)"', tag)
        assert controls, slug
        target = _list_tag(html, controls.group(1))
        assert target is not None, "%s: aria-controls points at a nonexistent id" % slug
        assert 'class="aura-nav-group__list"' in target, slug

    ids = re.findall(r'id="(aura-nav-group-[^"]+)"', html)
    assert len(ids) == len(set(ids)), "duplicate ids would break aria-controls resolution"


def test_groups_are_collapsed_by_default_and_hidden_not_merely_clipped(app, client, seeded):
    """Everything except the current page's group ships shut, and shut means
    `hidden` -- so the links are out of the Tab order and unannounced,
    rather than invisible but still reachable."""
    html = _dashboard_html(app, client, email="deptnav-collapsed@example.com", super_admin=True)
    buttons = _group_buttons(html)

    for slug in ALL_SLUGS:
        if slug == "overview":
            continue  # holds dashboard.index, i.e. the page under test
        assert 'aria-expanded="false"' in buttons[slug], slug
        assert " hidden>" in _list_tag(html, "aura-nav-group-%s-list" % slug), slug


def test_group_containing_the_current_page_is_expanded_on_first_paint_without_js(app, client, seeded):
    """No JavaScript runs in the test client, so finding the group open in
    the response body IS the proof that the server rendered it that way."""
    staff_id = make_staff(app, "deptnav-active@example.com", super_admin=True)
    force_login(client, app, staff_id)

    # dashboard.index lives in Overview.
    html = client.get("/").get_data(as_text=True)
    assert 'aria-expanded="true"' in _group_buttons(html)["overview"]
    assert " hidden>" not in _list_tag(html, "aura-nav-group-overview-list")

    # customers.list_customers lives in CRM -- the open group must follow
    # the page, and Overview must fall shut behind it.
    resp = client.get("/customers")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    buttons = _group_buttons(html)
    assert 'aria-expanded="true"' in buttons["crm"]
    assert " hidden>" not in _list_tag(html, "aura-nav-group-crm-list")
    assert 'aria-expanded="false"' in buttons["overview"]
    assert sum('aria-expanded="true"' in tag for tag in buttons.values()) == 1


def test_role_without_a_departments_permissions_gets_no_such_group_at_all(app, client, seeded):
    """The disclosure must never become a substitute for permission gating.
    SUPPORT holds customers.view / leads.view_own / the licensing read
    permissions, but nothing in Sales, Finance/Operations, Management or
    System -- those groups must be absent from the markup entirely, not
    merely rendered collapsed."""
    html = _dashboard_html(app, client, email="deptnav-support@example.com", role_codes=["SUPPORT"])
    buttons = _group_buttons(html)

    assert "crm" in buttons
    assert "licensing" in buttons
    for absent in ("sales", "finance-ops", "management", "system"):
        assert absent not in buttons, "%s rendered for a role that cannot see it" % absent

    # And not just the heading -- none of the gated destinations leaked
    # into the collapsed markup either.
    assert "commercial_sales_web" not in html
    assert "aura-nav-group-system-list" not in html
