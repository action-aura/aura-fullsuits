"""Dashboard Interactive Kit -- stage 2: derived KPI tiles + interactive
charts.

Same fixtures and the same discipline as test_owner_ui_dashboard_redesign.py
and test_owner_ui_charts_kit.py: real HTTP round trips through the app,
real permission seeding, and -- for every tile that claims a drill-down --
a real assertion that the linked list returns the SAME count the tile
showed, not merely that some href with a plausible shape was rendered.

Nothing here touches dashboard/services.py's shape: every new number is
arithmetic over keys get_dashboard_summary() already returned, which is
exactly why each drill-down can be proved equal to a real list query.
"""
from __future__ import annotations

from tests.conftest import force_login, make_license, make_staff


def _has_tile(html: str, label: str) -> bool:
    """True only when the real stat-tile markup for this label is present.
    Bare-substring matching would be meaningless: the command-palette JSON
    island renders on every page and carries label-like text of its own
    (the same reason test_owner_ui_dashboard_redesign.py checks for real
    card markup rather than a label)."""
    return f'<span class="aura-stat-tile__label">{label}</span>' in html


def _linked_tile(html: str, href: str) -> bool:
    return f'<a class="aura-stat-tile aura-stat-tile--default" href="{href}">' in html


def _static_tile(html: str, label: str) -> bool:
    return f'aura-stat-tile--static" role="group" aria-label="{label}"' in html


# ----------------------------------------------------------- new tiles --

def test_super_admin_sees_the_derived_kpi_tiles(app, client, seeded):
    staff_id = make_staff(app, "dash-kpi-super@example.com", super_admin=True)
    make_license(app, staff_id)
    force_login(client, app, staff_id)

    html = client.get("/").get_data(as_text=True)

    for label in (
        "Total subscriptions",
        "Total licenses",
        "Active licenses",
        "Active installations",
        "Products with active installations",
        "Open items awaiting action",
    ):
        assert _has_tile(html, label), f"missing new KPI tile: {label}"
    # The tiles that already existed must survive this pass unchanged.
    for label in ("Total customers", "Pilot customers", "Active subscriptions", "Expiring in 7 days"):
        assert _has_tile(html, label)


def test_active_subscription_share_hint_is_derived_not_queried(app, client, seeded):
    """make_license() leaves exactly one subscription, and it is ACTIVE, so
    the share is 100% -- a value derivable purely from numbers already in
    the template context (active_subscriptions / sum(subs_by_product))."""
    staff_id = make_staff(app, "dash-kpi-hint@example.com", super_admin=True)
    make_license(app, staff_id)
    force_login(client, app, staff_id)

    html = client.get("/").get_data(as_text=True)

    assert '<span class="aura-stat-tile__hint">100% of all subscriptions</span>' in html


# ------------------------------------------- drill-downs that match --

def test_every_new_drilldown_links_to_a_list_returning_the_same_count(app, client, seeded):
    staff_id = make_staff(app, "dash-kpi-drill@example.com", super_admin=True)
    make_license(app, staff_id)

    # Give the single customer a real PILOT lifecycle so the customer-status
    # drill-down is asserted against a non-zero count rather than 0 == 0.
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer

        customer = db_session.query(Customer).first()
        customer.lifecycle_status = "PILOT"
        db_session.commit()

    force_login(client, app, staff_id)
    html = client.get("/").get_data(as_text=True)

    assert _linked_tile(html, "/subscriptions")
    assert _linked_tile(html, "/subscriptions?status=ACTIVE")
    assert _linked_tile(html, "/licenses")
    assert _linked_tile(html, "/installations?status=ACTIVE")
    assert _linked_tile(html, "/customers?status=PILOT")
    assert _linked_tile(html, "/customers?status=ACTIVE")

    # The real point of this test: each of those URLs must select exactly
    # the set its tile counted. Comparing the dashboard summary against the
    # very query the linked route runs is the only way to prove that -- a
    # matching href string alone would prove nothing.
    with app.app_context():
        from app.customers.services import list_customers
        from app.dashboard.services import get_dashboard_summary
        from app.installations import list_queries as installations_list
        from app.licensing import list_queries as licensing_list
        from app.subscriptions import list_queries as subscriptions_list

        summary = get_dashboard_summary()

        assert subscriptions_list.list_subscriptions()["total"] == sum(summary["subs_by_product"].values())
        assert subscriptions_list.list_subscriptions(status="ACTIVE")["total"] == summary["active_subscriptions"]
        assert licensing_list.list_licenses()["total"] == sum(summary["licenses_by_status"].values())
        assert licensing_list.list_licenses(status="ACTIVE")["total"] == summary["licenses_by_status"].get("ACTIVE", 0)
        assert installations_list.list_installations(status="ACTIVE")["total"] == sum(
            summary["active_installations_by_product"].values()
        )
        assert list_customers(status="PILOT")["total"] == summary["pilot_customers"]
        assert list_customers(status="ACTIVE")["total"] == summary["active_customers"]
        # Non-trivially non-zero, so the equalities above are real evidence.
        assert summary["pilot_customers"] == 1
        assert summary["active_subscriptions"] == 1
        assert sum(summary["licenses_by_status"].values()) == 1


def test_tiles_with_no_matching_filter_stay_unlinked(app, client, seeded):
    """"Drop rather than invent": the subscriptions list has no end-date
    filter and no list route shows the mixed commercial-ops queue, so those
    tiles must render as static blocks, never as links to a list showing a
    different set."""
    staff_id = make_staff(app, "dash-kpi-unlinked@example.com", super_admin=True)
    force_login(client, app, staff_id)

    html = client.get("/").get_data(as_text=True)

    assert _static_tile(html, "Expiring in 7 days")
    assert _static_tile(html, "Expiring in 30 days")
    assert _static_tile(html, "Open items awaiting action")
    assert _static_tile(html, "Products with active installations")


def test_customer_status_drilldown_is_withheld_from_a_view_own_only_role(app, client, seeded):
    """SALES holds customers.view + customers.view_own but not
    customers.view_all, so /customers?status=PILOT would show only its own
    assigned customers -- fewer records than the company-wide number on the
    tile. The tile still renders; the link does not."""
    staff_id = make_staff(app, "dash-kpi-sales@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)

    html = client.get("/").get_data(as_text=True)

    assert _has_tile(html, "Pilot customers")
    assert _static_tile(html, "Pilot customers")
    assert _static_tile(html, "Active customers")
    assert not _linked_tile(html, "/customers?status=PILOT")
    assert not _linked_tile(html, "/customers?status=ACTIVE")
    # The unfiltered "browse all customers" entry point is unaffected -- it
    # never claimed to match a filtered count.
    assert _linked_tile(html, "/customers")


# --------------------------------------------------- permission gating --

def test_role_without_licence_or_installation_permission_gets_neither_tiles_nor_charts(app, client, seeded):
    """FINANCE holds subscriptions.view but neither licenses.view nor
    installations.view (app/staff/seed_data.py), so the licensing and
    installation KPI tiles AND their charts must both be absent -- the
    tiles are gated by the same card-level permission as the chart they sit
    above, never independently."""
    staff_id = make_staff(app, "dash-kpi-finance@example.com", role_codes=["FINANCE"])
    force_login(client, app, staff_id)

    html = client.get("/").get_data(as_text=True)

    assert _has_tile(html, "Total subscriptions")
    assert not _has_tile(html, "Total licenses")
    assert not _has_tile(html, "Active licenses")
    assert not _has_tile(html, "Active installations")
    assert not _has_tile(html, "Products with active installations")
    assert "aura-donut-chart" not in html
    assert not _linked_tile(html, "/licenses")
    assert not _linked_tile(html, "/installations?status=ACTIVE")


def test_role_with_no_overview_permission_sees_no_new_tiles_at_all(app, client, seeded):
    staff_id = make_staff(app, "dash-kpi-none@example.com", role_codes=[])
    force_login(client, app, staff_id)

    html = client.get("/").get_data(as_text=True)

    assert "Nothing to show here yet" in html
    for label in ("Total subscriptions", "Total licenses", "Active installations", "Open items awaiting action"):
        assert not _has_tile(html, label)
    assert "aura-bar-chart" not in html
    assert "aura-donut-chart" not in html


# ------------------------------------------------- chart interactivity --

def test_donut_svg_stays_aria_hidden_while_the_legend_carries_text_and_links(app, client, seeded):
    """The accessible name lives in the <ul> legend, never in the SVG --
    a deliberate, documented decision (dashboard-interactive-kit-contract.md
    point (a)) that the interactivity pass must not quietly reverse."""
    staff_id = make_staff(app, "dash-kpi-donut@example.com", super_admin=True)
    make_license(app, staff_id)  # -> one ISSUED license, one donut slice
    force_login(client, app, staff_id)

    html = client.get("/").get_data(as_text=True)
    # The <circle> attributes are laid out one per line in the macro, so
    # compare against a whitespace-normalised copy rather than pinning the
    # assertion to the template's indentation.
    compact = " ".join(html.split())

    assert '<svg viewBox="0 0 42 42" class="aura-donut-chart__svg" aria-hidden="true" focusable="false">' in html
    assert 'class="aura-donut-chart__legend"' in html
    # Real text + real drill-down link in the legend, not in the graphic.
    assert 'href="/licenses?status=ISSUED"' in html
    assert 'aria-label="Issued: 1 (' in html
    # The segment carries only the pairing hook -- no name, no focus stop.
    assert 'class="aura-donut-chart__segment aura-donut-chart__segment--success" data-slice="1"' in compact
    assert '<li class="aura-donut-chart__legend-item" data-slice="1">' in html
    donut_svg = html.split('class="aura-donut-chart__svg"')[1].split("</svg>")[0]
    assert "<circle" in donut_svg
    assert "tabindex" not in donut_svg
    assert "aria-label" not in donut_svg


def test_chart_data_points_are_keyboard_reachable_and_carry_a_value_readout(app, client, seeded):
    """Every data point is either a real link (already tabbable) or a
    tabindex="0" role="img" element, so the hover readout has a keyboard
    equivalent. The visible readout is aria-hidden because the focus
    target's accessible name is the same string."""
    staff_id = make_staff(app, "dash-kpi-keyboard@example.com", super_admin=True)
    make_license(app, staff_id)
    force_login(client, app, staff_id)

    html = client.get("/").get_data(as_text=True)

    assert '<span class="aura-bar-chart__track" role="img" tabindex="0" aria-label="' in html
    assert '<span class="aura-bar-chart__readout" aria-hidden="true">' in html
    assert '<span class="aura-donut-chart__readout" aria-hidden="true">' in html
    assert "% of total)" in html
    # Renewal pipeline: derived from active/expiring counts already in
    # context, and its labels are translated prose, so they must NOT be
    # forced LTR the way product identifiers are.
    assert '<bdi dir="auto">Not expiring within 30 days</bdi>' in html
    assert 'aria-label="Not expiring within 30 days: 1 (' in html


def test_charts_render_a_real_empty_state_with_a_create_action(app, client, seeded):
    """No licenses, no subscriptions, no installations exist for a freshly
    seeded database, so every chart is empty -- and an empty chart must say
    what would appear there and offer the action that creates the first
    record (the audit's top empty-state finding)."""
    staff_id = make_staff(app, "dash-kpi-empty@example.com", super_admin=True)
    force_login(client, app, staff_id)

    html = client.get("/").get_data(as_text=True)

    assert "No data yet." in html
    assert 'class="aura-chart-empty__hint">Licenses appear here grouped by status' in html
    assert 'class="aura-chart-empty__hint">Subscriptions appear here grouped by product' in html
    assert 'class="aura-chart-empty__hint">Active installations appear here grouped by product' in html
    assert '<a class="aura-chart-empty__action" href="/licenses/new">New license</a>' in html
    assert '<a class="aura-chart-empty__action" href="/subscriptions/new">New subscription</a>' in html
    assert '<a class="aura-chart-empty__action" href="/installations/new">Register installation</a>' in html
    # A chart with no rows must not leave an empty chart shell behind.
    assert "aura-donut-chart__legend" not in html


def test_empty_state_action_is_gated_on_the_real_create_permission(app, client, seeded):
    """VIEWER holds licenses.view but not licenses.create, so it gets the
    empty state's explanation without a link to an action it would be 403'd
    out of."""
    staff_id = make_staff(app, "dash-kpi-viewer@example.com", role_codes=["VIEWER"])
    force_login(client, app, staff_id)

    html = client.get("/").get_data(as_text=True)

    assert 'class="aura-chart-empty__hint">Licenses appear here grouped by status' in html
    assert '<a class="aura-chart-empty__action" href="/licenses/new">' not in html
    assert '<a class="aura-chart-empty__action" href="/subscriptions/new">' not in html
    assert '<a class="aura-chart-empty__action" href="/installations/new">' not in html


# ------------------------------------------------------- charts.css --

def _charts_css() -> str:
    import pathlib

    return (
        pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "charts.css"
    ).read_text(encoding="utf-8")


def test_chart_interactivity_respects_reduced_motion():
    css = _charts_css()
    assert "@media (prefers-reduced-motion: reduce)" in css
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    for selector in (".aura-donut-chart__segment", ".aura-bar-chart__fill", ".aura-bar-chart__readout"):
        assert selector in reduced


def _split_selector_list(selector_list: str) -> list[str]:
    """Split on top-level commas only. A naive split(",") would tear
    `:is(:hover, :focus-within)` in half and report a false positive."""
    parts: list[str] = []
    depth = 0
    current = ""
    for char in selector_list:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return [part.strip() for part in parts if part.strip()]


def test_cross_highlight_rules_never_share_a_selector_list_with_plain_rules():
    """A browser that doesn't support :has() drops the ENTIRE rule the
    selector appears in. Keeping :has() rules in their own blocks is what
    makes the baseline hover/focus styling degrade gracefully instead of
    disappearing with it."""
    import re

    css = _charts_css()
    assert ":has(" in css  # the cross-highlight exists at all
    # Comments are stripped first: this file DOCUMENTS the :has() rules in
    # prose, and a comment mentioning them is not a selector list.
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for block in css.split("}"):
        if ":has(" not in block:
            continue
        selector_list = block.split("{")[0]
        selectors = _split_selector_list(selector_list)
        assert selectors, "a :has() block with no selector"
        assert all(":has(" in s for s in selectors), f"mixed :has()/plain selector list: {selector_list.strip()}"


def test_charts_css_uses_tokens_not_raw_hex_colors():
    import re

    assert re.search(r"#[0-9a-fA-F]{3,8}\b", _charts_css()) is None
