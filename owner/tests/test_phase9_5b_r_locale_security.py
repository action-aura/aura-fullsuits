"""Phase 9.5B-R Milestone 3/4/16 -- locale resolution and language-switcher
security tests."""
from __future__ import annotations

from tests.conftest import force_login, make_staff


def test_default_locale_is_english_for_anonymous_visitor(app, client, seeded):
    resp = client.get("/auth/login")
    assert 'lang="en"' in resp.get_data(as_text=True)


def test_accept_language_arabic_is_honored(app, client, seeded):
    resp = client.get("/auth/login", headers={"Accept-Language": "ar"})
    assert 'lang="ar"' in resp.get_data(as_text=True)


def test_unsupported_locale_rejected(app, client, seeded):
    resp = client.get("/locale/fr")
    assert resp.status_code == 404


def test_unsupported_locale_does_not_change_session(app, client, seeded):
    client.get("/locale/ar")
    client.get("/locale/xx")  # rejected -- must not silently clear the prior valid selection
    resp = client.get("/auth/login")
    assert 'lang="ar"' in resp.get_data(as_text=True)


def test_switch_persists_via_session_across_requests(app, client, seeded):
    client.get("/locale/ar?next=/auth/login")
    resp = client.get("/auth/login")
    assert 'lang="ar"' in resp.get_data(as_text=True)


def test_switch_sets_a_safe_cookie(app, client, seeded):
    resp = client.get("/locale/ar?next=/auth/login")
    cookie_header = resp.headers.get("Set-Cookie", "")
    assert "owner_locale=ar" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=Lax" in cookie_header


def test_absolute_url_next_rejected_falls_back_to_default(app, client, seeded):
    resp = client.get("/locale/en?next=https://evil.example.com/phish")
    assert resp.status_code == 302
    assert resp.headers["Location"] not in ("https://evil.example.com/phish",)
    assert not resp.headers["Location"].startswith("https://evil.example.com")


def test_protocol_relative_next_rejected(app, client, seeded):
    resp = client.get("/locale/en?next=//evil.example.com/phish")
    assert resp.status_code == 302
    assert not resp.headers["Location"].startswith("//evil.example.com")
    assert not resp.headers["Location"].startswith("http://evil.example.com")


def test_relative_next_honored(app, client, seeded):
    resp = client.get("/locale/en?next=/auth/mfa-verify")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/auth/mfa-verify")


def test_authenticated_switch_persists_to_staff_locale(app, client, seeded):
    staff_id = make_staff(app, "loc1@example.com")
    force_login(client, app, staff_id)
    client.get("/locale/ar?next=/")

    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        staff = db_session.get(StaffUser, staff_id)
        assert staff.locale == "ar"


def test_anonymous_switch_does_not_require_login(app, client, seeded):
    resp = client.get("/locale/ar?next=/auth/login")
    assert resp.status_code == 302  # not redirected to login -- no auth required


def test_locale_switch_never_changes_identity_or_permissions(app, client, seeded):
    staff_id = make_staff(app, "loc2@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)

    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser
        from app.security.rbac import get_staff_permission_codes

        before_perms = get_staff_permission_codes(db_session.get(StaffUser, staff_id))

    client.get("/locale/ar?next=/")

    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser
        from app.security.rbac import get_staff_permission_codes

        after_perms = get_staff_permission_codes(db_session.get(StaffUser, staff_id))

    assert before_perms == after_perms
    # And the session itself is still valid -- a real authenticated request
    # still succeeds after the locale switch (not just "permissions look the
    # same in isolation" but "the actual session still works").
    resp = client.get("/profile")
    assert resp.status_code == 200


def test_locale_cookie_traversal_attempt_rejected(app, client, seeded):
    resp = client.get("/locale/..%2f..%2fetc")
    assert resp.status_code == 404


def test_supported_locales_config_matches_real_catalogs(app, seeded):
    with app.app_context():
        from flask import current_app

        for code in current_app.config["LANGUAGES"]:
            import os

            mo_path = os.path.join(current_app.config["BABEL_TRANSLATION_DIRECTORIES"], code, "LC_MESSAGES", "messages.mo")
            assert os.path.isfile(mo_path), f"missing compiled catalog for supported locale {code!r}"
