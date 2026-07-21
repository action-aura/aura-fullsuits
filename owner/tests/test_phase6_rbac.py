"""Part S RBAC extensions."""
from __future__ import annotations

from tests.conftest import force_login, make_license, make_staff


def test_support_cannot_manage_signing_keys(app, client, seeded):
    staff_id = make_staff(app, "r1@example.com", role_codes=["SUPPORT"])
    force_login(client, app, staff_id)
    resp = client.post("/licensing-admin/signing-keys/generate", data={"csrf_token": "x"})
    assert resp.status_code in (400, 403)


def test_support_can_view_activation_requests(app, client, seeded):
    staff_id = make_staff(app, "r2@example.com", role_codes=["SUPPORT"])
    force_login(client, app, staff_id)
    resp = client.get("/licensing-admin/requests")
    assert resp.status_code == 200


def test_sales_cannot_view_activation_service(app, client, seeded):
    staff_id = make_staff(app, "r3@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)
    resp = client.get("/licensing-admin")
    assert resp.status_code == 403


def test_finance_cannot_manage_signing_keys(app, client, seeded):
    staff_id = make_staff(app, "r4@example.com", role_codes=["FINANCE"])
    force_login(client, app, staff_id)
    resp = client.post("/licensing-admin/signing-keys/rotate", data={"csrf_token": "x"})
    assert resp.status_code in (400, 403)


def test_viewer_can_view_but_not_revoke_device_keys(app, client, seeded):
    staff_id = make_staff(app, "r5@example.com", role_codes=["VIEWER"])
    force_login(client, app, staff_id)
    resp = client.get("/licensing-admin/device-keys")
    assert resp.status_code == 200
    resp2 = client.post("/licensing-admin/device-keys/00000000-0000-0000-0000-000000000000/revoke", data={"csrf_token": "x"})
    assert resp2.status_code in (400, 403)


def test_super_admin_can_manage_signing_keys(app, client, seeded):
    staff_id = make_staff(app, "r6@example.com", super_admin=True)
    force_login(client, app, staff_id)
    resp = client.get("/licensing-admin/signing-keys")
    assert resp.status_code == 200


def test_unauthenticated_cannot_reach_admin_views(client):
    resp = client.get("/licensing-admin")
    assert resp.status_code == 302
    assert "login" in resp.headers["Location"]
