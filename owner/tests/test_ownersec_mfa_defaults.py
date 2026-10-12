"""OWNER-SEC item 5 (review D SEC-08): staff in roles that can change
licences/subscriptions/money/staff are sent to MFA enrolment at next login
even though their stored mfa_required flag is False -- never refused, never
let straight through to the dashboard. Read-only roles are let through."""
from __future__ import annotations

import pytest

from tests.conftest import get_csrf, login, make_staff


@pytest.mark.parametrize("role", ["SALES", "FINANCE"])
def test_money_and_licence_roles_without_mfa_are_routed_to_enrolment(app, client, seeded, role):
    make_staff(app, f"mfa-{role.lower()}@example.com", role_codes=[role])  # stored mfa_required=False
    resp = login(client, f"mfa-{role.lower()}@example.com")
    assert resp.status_code == 302
    assert "/auth/mfa-enroll" in resp.headers["Location"]
    # Not a full session yet: the dashboard is still behind login.
    assert client.get("/", follow_redirects=False).status_code in (302, 401)
    # And enrolment actually renders for them (not refused).
    assert client.get("/auth/mfa-enroll").status_code == 200


def test_super_admin_with_unset_flag_is_routed_to_enrolment(app, client, seeded):
    sid = make_staff(app, "mfa-sa@example.com", super_admin=True)
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        db_session.get(StaffUser, sid).mfa_required = False
        db_session.commit()
    resp = login(client, "mfa-sa@example.com")
    assert "/auth/mfa-enroll" in resp.headers["Location"]


@pytest.mark.parametrize("role", ["VIEWER", "SUPPORT"])
def test_read_only_and_support_roles_are_let_through(app, client, seeded, role):
    make_staff(app, f"mfa-{role.lower()}@example.com", role_codes=[role])
    resp = login(client, f"mfa-{role.lower()}@example.com")
    assert resp.status_code == 302
    assert "/auth/mfa-enroll" not in resp.headers["Location"]
    assert "/auth/mfa-verify" not in resp.headers["Location"]
    assert "/auth/login" not in resp.headers["Location"]
