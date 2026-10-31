"""2026-10-31 dress rehearsal: the Owner-side fixes (owner_fixes.md items 1-10).

Each class of test pairs a DENY half with its ALLOW twin, and was mutation-proved
(see the commit messages for what was broken and what went red).
"""
from __future__ import annotations

from tests.conftest import force_login, make_staff
from tests.test_customer_onboarding import kyc  # noqa: F401 -- a fixture


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


# ------------------------------------------------------ 9. seller dashboard

def _summary(app, who, w):
    from app.dashboard.services import get_dashboard_summary
    from app.extensions import db_session
    from app.models.staff import StaffUser
    from tests.test_customer_onboarding import _codes

    with app.app_context():
        return get_dashboard_summary(db_session.get(StaffUser, w[who]), _codes(app, w[who]))


def test_a_sellers_dashboard_counts_only_their_own_customers_a_managers_counts_all(app, client, seeded, kyc):
    from tests.test_customer_onboarding import _world, onboard

    w = _world(app)
    onboard(app, w, who="seller")
    onboard(app, w, who="other_seller")
    onboard(app, w, who="other_seller")

    mine = _summary(app, "seller", w)
    theirs = _summary(app, "other_seller", w)
    manager = _summary(app, "manager", w)
    # deny half: the seller does not see the company
    assert (mine["total_customers"], theirs["total_customers"]) == (1, 2)
    assert sum(mine["subs_by_product"].values()) == 1 and sum(theirs["subs_by_product"].values()) == 2
    assert sum(mine["licenses_by_status"].values()) == 1 and sum(theirs["licenses_by_status"].values()) == 2
    assert mine["pilots_needing_action"] == 0
    assert mine["scoped"] is True
    # allow half: a manager keeps the company view
    assert manager["scoped"] is False and manager["total_customers"] == 3
    assert sum(manager["subs_by_product"].values()) == 3 and sum(manager["licenses_by_status"].values()) == 3

    # the page says so to the seller, and not to the manager
    force_login(client, app, w["seller"])
    seller_page = client.get("/").get_data(as_text=True)
    assert 'data-dashboard-scoped="1"' in seller_page
    client.get("/auth/logout")
    force_login(client, app, w["manager"])
    assert 'data-dashboard-scoped="1"' not in client.get("/").get_data(as_text=True)
