"""2026-10-31 dress rehearsal: the Owner-side fixes (owner_fixes.md items 1-10).

Each class of test pairs a DENY half with its ALLOW twin, and was mutation-proved
(see the commit messages for what was broken and what went red).
"""
from __future__ import annotations

from tests.conftest import force_login, make_staff


# ------------------------------------------------- 10. "your session ended"

def test_a_brand_new_visitor_is_not_told_their_session_ended(app, client, seeded):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]
    assert "reason=session_expired" not in resp.headers["Location"]
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Your session ended" not in page


def test_a_visitor_whose_session_really_ended_is_told_so(app, client, seeded):
    from app.auth.session import COOKIE_NAME

    # allow half: a cookie that no longer matches a live session (expired / revoked)
    client.set_cookie(key=COOKIE_NAME, value="a-token-that-no-longer-exists", domain="localhost")
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302 and "reason=session_expired" in resp.headers["Location"]
    assert "Your session ended" in client.get(resp.headers["Location"]).get_data(as_text=True)
