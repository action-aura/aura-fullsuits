"""Phase 9.5B-R Milestone 7/16 -- bidirectional identifier safety."""
from __future__ import annotations

from datetime import date

from tests.conftest import force_login, make_staff


def test_bidi_isolate_wraps_in_bdi_ltr(app):
    with app.test_request_context():
        from app.i18n import init_app
        from flask import current_app

        filt = current_app.jinja_env.filters["bidi_isolate"]
        rendered = str(filt("EMP-0001"))
        assert rendered == '<bdi dir="ltr">EMP-0001</bdi>'


def test_bidi_isolate_escapes_html(app):
    with app.test_request_context():
        from flask import current_app

        filt = current_app.jinja_env.filters["bidi_isolate"]
        rendered = str(filt("<script>alert(1)</script>"))
        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered


def test_bidi_isolate_none_is_safe(app):
    with app.test_request_context():
        from flask import current_app

        filt = current_app.jinja_env.filters["bidi_isolate"]
        assert str(filt(None)) == ""


def test_employee_number_survives_bidi_wrapping_unchanged_when_stripped(app, client, seeded):
    """Copy-paste safety: the underlying text content, stripped of the <bdi>
    wrapper, is byte-for-byte the original database value."""
    admin_id = make_staff(app, "bidi1@example.com", super_admin=True)
    with app.app_context():
        from app.employees.services import activate_employee, create_employee_profile

        profile = create_employee_profile(
            {"staff_user_id": admin_id, "employee_number": "EMP-BIDI-0042", "full_name": "Bidi Test", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=admin_id,
        )
        activate_employee(profile, actor_staff_user_id=admin_id)
        profile_id = profile.id

    force_login(client, app, admin_id)
    client.get("/locale/ar?next=/")
    resp = client.get(f"/employees/{profile_id}")
    data = resp.get_data(as_text=True)
    assert '<bdi dir="ltr">EMP-BIDI-0042</bdi>' in data


def test_no_hidden_characters_written_to_database(app):
    """bidi_isolate is presentation-only -- it never touches the database."""
    import inspect

    from app import i18n

    source = inspect.getsource(i18n)
    assert "db_session" not in source


def test_detail_page_no_longer_has_inline_onsubmit_confirm(app, client, seeded):
    admin_id = make_staff(app, "bidi2@example.com", super_admin=True)
    with app.app_context():
        from app.employees.services import activate_employee, create_employee_profile

        profile = create_employee_profile(
            {"staff_user_id": admin_id, "employee_number": "EMP-BIDI2", "full_name": "Bidi Two", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=admin_id,
        )
        activate_employee(profile, actor_staff_user_id=admin_id)
        profile_id = profile.id

    force_login(client, app, admin_id)
    resp = client.get(f"/employees/{profile_id}")
    data = resp.get_data(as_text=True)
    assert "onsubmit=" not in data
    # suspend + terminate (this page) + the shell's own global logout
    # confirm (layout/base.html, present on every authenticated page
    # since the UI-modernization application-shell rebuild).
    assert data.count("data-confirm=") == 3
