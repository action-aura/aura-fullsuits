"""Phase 9.5B-R Milestone 6/17/18 -- real bug found via actual browser
testing (not template-only testing): the .responsive-table CSS
(table.responsive-table thead { display: none; }, from Phase 9.5B) can
never match anything because the templates never wrapped their header
row in a real <thead> element -- confirmed visually via a real Playwright
screenshot at 390px width, which showed both the un-collapsed raw header
row AND the properly-stacked mobile card underneath it. Fixed by adding
real <thead>/<tbody> to every gated table. This is the regression guard.
"""
from __future__ import annotations

from tests.conftest import force_login, make_staff

GATED_TABLE_TEMPLATES = (
    # employees/list.html deliberately removed here -- UI modernization
    # Stage D.6 migrated it off .responsive-table entirely onto the
    # Enterprise Table System's own <table class="aura-table"> (contained
    # horizontal scroll, not thead-hiding -- see
    # docs/owner/ui-modernization/enterprise-table-system.md's "Responsive-
    # mode decision"). Its real <thead>/<tbody> now live in
    # components/table.html's table() macro, shared by every migrated list
    # screen, not duplicated per-template -- covered by that system's own
    # test coverage, not this Phase 9.5B-R-era file-content check.
    "app/templates/employees/detail.html",
    "app/templates/employees/invitations.html",
    "app/templates/profile/sessions.html",
)


def test_every_gated_table_template_has_real_thead_and_tbody():
    import os

    owner_root = os.path.dirname(os.path.dirname(__file__))
    for rel_path in GATED_TABLE_TEMPLATES:
        path = os.path.join(owner_root, rel_path)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "<thead>" in content, f"{rel_path} is missing <thead>"
        assert "<tbody>" in content, f"{rel_path} is missing <tbody>"


def test_responsive_table_thead_actually_hides_at_mobile_width(app, client, seeded):
    """The real, end-to-end proof: with a real <thead> now present, the CSS
    rule that was always correct (table.responsive-table thead { display:
    none; }) has something to actually match. This test can't render CSS,
    but it does confirm the structural precondition the CSS rule depends on
    is present in the real server response, not just in a static template
    file (catches a route that might render a different/stale template).

    Retargeted at /staff's "Pending invitations" table (UI modernization
    Stage D.6) -- /employees no longer uses .responsive-table at all for
    its main list (migrated onto the Enterprise Table System's own
    .aura-table-scroll contained-horizontal-scroll mechanism, a different,
    already-covered responsive strategy, see
    docs/owner/ui-modernization/enterprise-table-system.md). staff/list.html
    still genuinely uses .responsive-table for its small, bounded
    invitations sub-table (deliberately left unmigrated -- same reasoning
    Stage D.5 applied to License's entitlements/Installation's devices
    sub-tables), so the real regression this test guards against
    (table.responsive-table thead { display: none; } having nothing to
    match because <thead> was missing) still has a live target here."""
    admin_id = make_staff(app, "rtltable1@example.com", super_admin=True)
    with app.app_context():
        from app.staff.services import create_invitation

        create_invitation("rtltable-invitee@example.com", ["VIEWER"], admin_id)

    force_login(client, app, admin_id)
    resp = client.get("/staff")
    data = resp.get_data(as_text=True)
    assert "<table class=\"responsive-table\">" in data
    assert "<thead>" in data
    assert "<tbody>" in data
    # The <thead> must appear before <tbody> in document order (real DOM
    # structure, not just both strings present anywhere).
    assert data.index("<thead>") < data.index("<tbody>")
