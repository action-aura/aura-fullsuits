"""OWNER-SEC item 1 (review D SEC-06): _safe_next must only ever return a
same-site relative path. A leading slash plus a backslash is NOT safe:
browsers fold the backslash into a slash, so slash+backslash+host navigates
off-site. Backslashes are built with chr(92) so no escape-sequence ambiguity
can silently turn a case into a different string."""
from __future__ import annotations

import pytest

from tests.conftest import get_csrf, make_staff

BS = chr(92)


@pytest.mark.parametrize(
    "value",
    [
        "/" + BS + "evil.com",
        "//evil.com",
        BS + BS + "evil.com",
        "https://evil.com",
        "/%5Cevil.com",
        "/%255Cevil.com",
        "/%2F/evil.com",
        "javascript:alert(1)",
        "/" + BS + "/evil.com",
        "/ok\r\nSet-Cookie: x=1",
        "/ok%0d%0aSet-Cookie:x=1",
        "",
        None,
    ],
)
def test_unsafe_next_values_are_refused(value):
    from app.auth.routes import _safe_next

    assert _safe_next(value) is None


@pytest.mark.parametrize("value", ["/licensing/123?x=1", "/", "/customers", "/a/b?next=%2Fx"])
def test_same_site_relative_paths_are_kept(value):
    from app.auth.routes import _safe_next

    assert _safe_next(value) == value


def test_login_with_hostile_next_lands_on_dashboard_not_attacker(app, client, seeded):
    make_staff(app, "redir@example.com", password="Correct-Password-1!")
    page = client.get("/auth/login")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(
        "/auth/login?next=/%5Cevil.com",
        data={"csrf_token": csrf, "email": "redir@example.com", "password": "Correct-Password-1!"},
    )
    assert resp.status_code == 302
    assert "evil.com" not in resp.headers["Location"]


def test_login_with_safe_next_is_honoured(app, client, seeded):
    make_staff(app, "redir2@example.com", password="Correct-Password-1!")
    page = client.get("/auth/login")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(
        "/auth/login?next=/licensing/123?x=1",
        data={"csrf_token": csrf, "email": "redir2@example.com", "password": "Correct-Password-1!"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/licensing/123?x=1")
