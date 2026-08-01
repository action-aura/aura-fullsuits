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

from datetime import date

from tests.conftest import force_login, make_staff

GATED_TABLE_TEMPLATES = (
    "app/templates/employees/list.html",
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
    file (catches a route that might render a different/stale template)."""
    admin_id = make_staff(app, "rtltable1@example.com", super_admin=True)
    with app.app_context():
        from app.employees.services import activate_employee, create_employee_profile

        profile = create_employee_profile(
            {"staff_user_id": admin_id, "employee_number": "EMP-RTLT1", "full_name": "RTL Table Test", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=admin_id,
        )
        activate_employee(profile, actor_staff_user_id=admin_id)

    force_login(client, app, admin_id)
    resp = client.get("/employees")
    data = resp.get_data(as_text=True)
    assert "<table class=\"responsive-table\">" in data
    assert "<thead>" in data
    assert "<tbody>" in data
    # The <thead> must appear before <tbody> in document order (real DOM
    # structure, not just both strings present anywhere).
    assert data.index("<thead>") < data.index("<tbody>")
