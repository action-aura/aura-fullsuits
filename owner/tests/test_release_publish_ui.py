"""Staff "Publish a new version" flow (Releases page): upload a DRAFT, then
publish / withdraw it -- app/releases/upload.py + app/releases/routes.py.

What each test is really guarding (ENGINEERING 1 -- an outcome alone cannot tell
"the check ran" from "something else refused"):
  * the refusal tests also assert NOTHING was left behind (no row, no file, no
    temp file), because a refusal that leaves a half-written artifact is a bug;
  * the discovery test uses the REAL signed /api/licensing/v1/releases/latest
    path and pairs every "not offered" with a positive control (the same call
    DOES offer the version once it is published);
  * the permission tests give the actor recent auth, so only the permission
    check can be what refuses them.
"""
from __future__ import annotations

import hashlib
import io
import os

import pytest
from sqlalchemy import select

from app.extensions import db_session
from app.models.audit import AuditLog
from app.models.base import utcnow
from app.models.catalog import Platform, Product, ProductVersion, ReleaseChannel
from app.models.staff import StaffSession
from tests.conftest import force_login, get_csrf, make_staff
from tests.test_release_discovery import _activate, _ask, _offered_version

MB = 1024 * 1024
UPLOAD = "/releases/versions/upload"


@pytest.fixture()
def store(app, tmp_path):
    """Artifact directory isolated per test."""
    app.config["RELEASE_ARTIFACT_DIRECTORY"] = str(tmp_path)
    return tmp_path


def _grant_recent_auth(app, staff_id):
    with app.app_context():
        for s in db_session.execute(select(StaffSession).where(StaffSession.staff_user_id == staff_id)).scalars():
            s.mfa_verified_at = utcnow()
        db_session.commit()


def _login(app, client, email, *, super_admin=False, role_codes=None, recent_auth=True):
    staff_id = make_staff(app, email, super_admin=super_admin, role_codes=role_codes)
    force_login(client, app, staff_id)
    if recent_auth:
        _grant_recent_auth(app, staff_id)
    return staff_id


def _csrf(client):
    # /releases/versions needs only catalog.view and renders the base layout
    # (which carries the logout form's token) for every role used here.
    return get_csrf(client.get("/releases/versions").get_data(as_text=True))


@pytest.fixture()
def admin(app, client, seeded):
    _login(app, client, "boss@example.com", super_admin=True)
    return client


@pytest.fixture()
def sales(app, client, seeded):
    # SALES holds catalog.view but not catalog.manage_versions.
    _login(app, client, "sales@example.com", role_codes=["SALES"])
    return client


def _upload(client, *, content=b"MZ" + b"\x00" * 100, filename="app.exe", platform="WINDOWS", version="1.0.1",
            build="", product="AURA_CLINIC", channel="RC", notes="", min_supported="", required=False):
    data = {
        "csrf_token": _csrf(client), "product_code": product, "platform_code": platform, "channel_code": channel,
        "version": version, "build_number": build, "release_notes": notes, "min_supported_version": min_supported,
        "artifact": (io.BytesIO(content), filename),
    }
    if required:
        data["update_required"] = "1"
    return client.post(UPLOAD, data=data, content_type="multipart/form-data")


def _rows(app):
    with app.app_context():
        return list(db_session.execute(select(ProductVersion)).scalars().all())


def _files(store):
    return sorted(p.name for p in store.iterdir())


def _make_row(app, version, state, *, product="AURA_CLINIC", platform="WINDOWS"):
    with app.app_context():
        row = ProductVersion(
            product_id=db_session.execute(select(Product).where(Product.product_code == product)).scalars().one().id,
            platform_id=db_session.execute(select(Platform).where(Platform.platform_code == platform)).scalars().one().id,
            release_channel_id=db_session.execute(select(ReleaseChannel).where(ReleaseChannel.channel_code == "RC")).scalars().one().id,
            version=version, artifact_checksum_sha256="a" * 64, artifact_path="x.exe", artifact_size_bytes=10,
            publication_state=state,
        )
        db_session.add(row)
        db_session.commit()
        return str(row.id)


def _state(app, version_id):
    with app.app_context():
        return db_session.get(ProductVersion, version_id).publication_state


# ---------------------------------------------------------------- P1 upload

def test_upload_windows_creates_one_draft_with_exact_checksum_size_and_file(app, admin, store):
    payload = b"MZ" + os.urandom(5000)
    resp = _upload(admin, content=payload, notes="First build.", min_supported="1.0.0-rc.1", required=True)
    assert resp.status_code == 302, resp.get_data(as_text=True)

    rows = _rows(app)
    assert len(rows) == 1
    row = rows[0]
    assert row.publication_state == "DRAFT"
    assert row.artifact_checksum_sha256 == hashlib.sha256(payload).hexdigest()
    assert row.artifact_size_bytes == len(payload)
    assert row.release_notes == "First build."
    assert row.min_supported_version == "1.0.0-rc.1"
    assert row.forced_upgrade_threshold == "1.0.1"
    with app.app_context():
        assert db_session.get(ProductVersion, row.id).release_channel.channel_code == "RC"
    assert row.artifact_path == f"aura_clinic-windows-1.0.1-{row.artifact_checksum_sha256[:8]}.exe"
    # exactly the one final file, with exactly the bytes sent, and no temp left over
    assert _files(store) == [row.artifact_path]
    assert (store / row.artifact_path).read_bytes() == payload
    with app.app_context():
        actions = [a.action_code for a in db_session.execute(select(AuditLog)).scalars()]
    assert "RELEASE_UPLOADED" in actions and "RELEASE_MANIFEST_IMPORTED" in actions


def test_upload_android_with_build_number_is_accepted(app, admin, store):
    resp = _upload(admin, content=b"PK\x03\x04" + b"0" * 50, filename="aura.apk", platform="ANDROID", build="42")
    assert resp.status_code == 302, resp.get_data(as_text=True)
    (row,) = _rows(app)
    assert row.build_number == 42 and row.publication_state == "DRAFT"


def test_non_mz_file_as_windows_is_refused_and_nothing_is_written(app, admin, store):
    resp = _upload(admin, content=b"NOTANEXE" + b"0" * 100)
    assert resp.status_code == 400
    assert "not the right kind" in resp.get_data(as_text=True)
    assert _rows(app) == []
    assert _files(store) == []  # not even a .part temp file


def test_wrong_extension_is_refused_and_nothing_is_written(app, admin, store):
    resp = _upload(admin, content=b"MZ" + b"0" * 100, filename="app.zip")
    assert resp.status_code == 400
    assert _rows(app) == [] and _files(store) == []


def test_apk_without_build_number_is_refused(app, admin, store):
    resp = _upload(admin, content=b"PK\x03\x04" + b"0" * 50, filename="aura.apk", platform="ANDROID", build="")
    assert resp.status_code == 400
    assert "build number" in resp.get_data(as_text=True)
    assert _rows(app) == [] and _files(store) == []


def test_duplicate_version_is_refused_and_leaves_the_first_untouched(app, admin, store):
    first = b"MZ" + b"1" * 100
    assert _upload(admin, content=first).status_code == 302
    (row,) = _rows(app)
    resp = _upload(admin, content=b"MZ" + b"2" * 100)  # same product/platform/version, different bytes
    assert resp.status_code == 400
    assert "already exists" in resp.get_data(as_text=True)
    rows = _rows(app)
    assert [r.id for r in rows] == [row.id]
    assert rows[0].artifact_checksum_sha256 == hashlib.sha256(first).hexdigest()
    assert _files(store) == [row.artifact_path]  # the second upload's file was not kept


def test_non_semver_version_is_refused(app, admin, store):
    resp = _upload(admin, version="latest")
    assert resp.status_code == 400
    assert _rows(app) == [] and _files(store) == []


def test_13mb_upload_succeeds_here_while_13mb_post_elsewhere_gets_413(app, seeded, store):
    big = b"MZ" + b"\x00" * (13 * MB)

    # Elsewhere: the global 12 MB ceiling still applies. A file part (not a text
    # field) so the form-memory limit cannot be what produces the 413.
    other = app.test_client()
    _login(app, other, "elsewhere@example.com", role_codes=["SALES"])
    token = _csrf(other)
    over = other.post("/catalog/plans", data={"csrf_token": token, "blob": (io.BytesIO(b"0" * (13 * MB)), "b.bin")},
                      content_type="multipart/form-data")
    assert over.status_code == 413
    # control: just under the ceiling gets past the size check (and is then refused for permission)
    under = other.post("/catalog/plans", data={"csrf_token": token, "blob": (io.BytesIO(b"0" * (11 * MB)), "b.bin")},
                       content_type="multipart/form-data")
    assert under.status_code == 403

    # The upload route accepts the same size.
    boss = app.test_client()
    _login(app, boss, "boss2@example.com", super_admin=True)
    resp = _upload(boss, content=big)
    assert resp.status_code == 302, resp.get_data(as_text=True)[:300]
    (row,) = _rows(app)
    assert row.artifact_size_bytes == len(big)


# --------------------------------------------- P2 publish / withdraw + discovery

def test_draft_is_not_offered_publish_offers_it_withdraw_stops_it(app, client, seeded, signing_key, store):
    boss = app.test_client()
    _login(app, boss, "boss@example.com", super_admin=True)
    # a shop on an older build, asking for the rc channel like the shipped clients
    _license_id, installation_id, key = _activate(app, client, "shop@example.com")
    device = {"installation_id": installation_id, "key": key}

    assert _upload(boss, version="1.0.1").status_code == 302
    (row,) = _rows(app)
    vid = str(row.id)

    # DRAFT: never offered
    assert _offered_version(_ask(client, device, release_channel="rc")) is None

    # Publish: the confirmation page first, then the POST from it
    confirm = boss.get(f"/releases/versions/{vid}/publish")
    assert confirm.status_code == 200 and "Yes, publish" in confirm.get_data(as_text=True)
    assert _state(app, vid) == "DRAFT"  # the GET changed nothing
    resp = boss.post(f"/releases/versions/{vid}/publish", data={"csrf_token": get_csrf(confirm.get_data(as_text=True))})
    assert resp.status_code == 302
    assert _state(app, vid) == "PUBLISHED"
    assert _offered_version(_ask(client, device, release_channel="rc")) == "1.0.1"  # positive control

    # Withdraw
    confirm = boss.get(f"/releases/versions/{vid}/withdraw")
    resp = boss.post(f"/releases/versions/{vid}/withdraw",
                     data={"csrf_token": get_csrf(confirm.get_data(as_text=True)), "reason": "bad build"})
    assert resp.status_code == 302
    assert _state(app, vid) == "WITHDRAWN"
    assert _offered_version(_ask(client, device, release_channel="rc")) is None
    with app.app_context():
        actions = [a.action_code for a in db_session.execute(select(AuditLog)).scalars()]
    assert "RELEASE_PUBLISHED" in actions and "RELEASE_WITHDRAWN" in actions


def test_withdraw_without_a_reason_is_refused(app, admin, store):
    vid = _make_row(app, "1.0.1", "PUBLISHED")
    resp = admin.post(f"/releases/versions/{vid}/withdraw", data={"csrf_token": _csrf(admin), "reason": "  "})
    assert resp.status_code == 302
    assert _state(app, vid) == "PUBLISHED"


# ------------------------------------------------------------- permission + auth

def test_staff_without_permission_gets_403_on_upload_publish_and_withdraw(app, sales, store):
    draft = _make_row(app, "1.0.1", "DRAFT")
    published = _make_row(app, "1.0.2", "PUBLISHED")
    token = _csrf(sales)

    up = sales.post(UPLOAD, data={
        "csrf_token": token, "product_code": "AURA_CLINIC", "platform_code": "WINDOWS", "channel_code": "RC",
        "version": "1.0.9", "artifact": (io.BytesIO(b"MZ" + b"0" * 50), "a.exe"),
    }, content_type="multipart/form-data")
    pub = sales.post(f"/releases/versions/{draft}/publish", data={"csrf_token": token})
    wd = sales.post(f"/releases/versions/{published}/withdraw", data={"csrf_token": token, "reason": "x"})

    assert (up.status_code, pub.status_code, wd.status_code) == (403, 403, 403)
    assert {r.version for r in _rows(app)} == {"1.0.1", "1.0.2"}
    assert _state(app, draft) == "DRAFT" and _state(app, published) == "PUBLISHED"
    assert sales.get(UPLOAD).status_code == 403
    assert sales.get(f"/releases/versions/{draft}/publish").status_code == 403


def test_publish_without_recent_auth_is_refused(app, client, seeded, store):
    _login(app, client, "boss@example.com", super_admin=True, recent_auth=False)
    vid = _make_row(app, "1.0.1", "DRAFT")
    resp = client.post(f"/releases/versions/{vid}/publish", data={"csrf_token": _csrf(client)})
    assert resp.status_code == 302 and "/auth/reauth" in resp.headers["Location"]
    assert _state(app, vid) == "DRAFT"
    # control: the same actor, once recently authenticated, can publish
    _grant_recent_auth(app, db_staff_id(app, "boss@example.com"))
    resp = client.post(f"/releases/versions/{vid}/publish", data={"csrf_token": _csrf(client)})
    assert resp.status_code == 302 and "/auth/reauth" not in resp.headers["Location"]
    assert _state(app, vid) == "PUBLISHED"


def db_staff_id(app, email):
    from app.models.staff import StaffUser

    with app.app_context():
        return db_session.execute(select(StaffUser).where(StaffUser.email == email)).scalars().one().id


# ----------------------------------------------------------------- P4 pages

def test_releases_page_links_to_upload_and_detail_for_managers_only(app, admin, store):
    vid = _make_row(app, "1.0.1", "DRAFT")
    page = admin.get("/releases/versions").get_data(as_text=True)
    assert UPLOAD in page and f"/releases/versions/{vid}" in page
    assert admin.get(UPLOAD).status_code == 200
    detail = admin.get(f"/releases/versions/{vid}").get_data(as_text=True)
    assert f"/releases/versions/{vid}/publish" in detail


def test_viewer_without_manage_permission_sees_no_upload_button(app, sales, store):
    page = sales.get("/releases/versions").get_data(as_text=True)
    assert UPLOAD not in page
