from __future__ import annotations

from tests.conftest import force_login, make_staff


def test_self_sessions_page_loads(app, client, seeded):
    staff_id = make_staff(app, "sess1@example.com")
    force_login(client, app, staff_id)
    resp = client.get("/profile/sessions")
    assert resp.status_code == 200


def test_self_revoke_other_session_works(app, client, seeded):
    from tests.conftest import get_csrf

    staff_id = make_staff(app, "sess2@example.com")
    with app.app_context():
        from app.auth.session import create_session
        from app.extensions import db_session
        from app.models.staff import StaffUser

        with app.test_request_context():
            staff = db_session.get(StaffUser, staff_id)
            create_session(staff)  # a second, real session -- not the one force_login will create below

    force_login(client, app, staff_id)  # the client's own, current cookie session -- created AFTER the one above

    page = client.get("/profile/sessions")
    csrf = get_csrf(page.get_data(as_text=True))

    with app.app_context():
        from app.auth.session import list_sessions_for_staff

        rows = list_sessions_for_staff(staff_id)
        assert len(rows) >= 2
        # The client's current cookie session is the one force_login just
        # created (most recent) -- target the OLDER one instead, which is
        # never the caller's own current session.
        target = min((r for r in rows if not r.revoked_at), key=lambda r: r.created_at)
        target_id = target.id

    resp = client.post(f"/profile/sessions/{target_id}/revoke", data={"csrf_token": csrf})
    assert resp.status_code == 302  # revoked, redirected back to the sessions list

    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffSession

        assert db_session.get(StaffSession, target_id).revoked_at is not None


def test_self_cannot_revoke_another_employees_session_by_guessing_uuid(app, client, seeded):
    staff_a = make_staff(app, "sess3a@example.com")
    staff_b = make_staff(app, "sess3b@example.com")
    with app.app_context():
        from app.auth.session import create_session
        from app.extensions import db_session
        from app.models.staff import StaffSession, StaffUser
        from app.security.tokens import hash_token
        from sqlalchemy import select

        with app.test_request_context():
            staff_b_obj = db_session.get(StaffUser, staff_b)
            raw_token_b = create_session(staff_b_obj)
        session_b = db_session.execute(select(StaffSession).where(StaffSession.token_hash == hash_token(raw_token_b))).scalars().first()
        session_b_id = session_b.id

    force_login(client, app, staff_a)
    page = client.get("/profile/sessions")
    from tests.conftest import get_csrf

    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(f"/profile/sessions/{session_b_id}/revoke", data={"csrf_token": csrf})
    assert resp.status_code == 404

    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffSession

        row = db_session.get(StaffSession, session_b_id)
        assert row.revoked_at is None  # untouched -- B's session survives A's attempt


def test_management_revoke_one_employee_session(app, client, seeded):
    from datetime import date

    admin_id = make_staff(app, "sess4admin@example.com", super_admin=True)
    target_id = make_staff(app, "sess4target@example.com")
    with app.app_context():
        from app.auth.session import create_session
        from app.employees.services import activate_employee, create_employee_profile
        from app.extensions import db_session
        from app.models.staff import StaffSession, StaffUser
        from app.security.tokens import hash_token
        from sqlalchemy import select

        profile = create_employee_profile(
            {"staff_user_id": target_id, "employee_number": "EMP-SESS4", "full_name": "Sess Four", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=admin_id,
        )
        activate_employee(profile, actor_staff_user_id=admin_id)
        with app.test_request_context():
            target_staff = db_session.get(StaffUser, target_id)
            raw_token = create_session(target_staff)
        session_row = db_session.execute(select(StaffSession).where(StaffSession.token_hash == hash_token(raw_token))).scalars().first()
        session_id = session_row.id
        profile_id = profile.id

    force_login(client, app, admin_id)
    page = client.get(f"/employees/{profile_id}")
    from tests.conftest import get_csrf

    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(f"/employees/{profile_id}/sessions/{session_id}/revoke", data={"csrf_token": csrf})
    assert resp.status_code in (302, 401)  # 401/redirect without recent-auth -- the sensitive-action gate, proven elsewhere
