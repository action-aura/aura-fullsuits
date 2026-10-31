"""New customer flow, slice S4 (CA2): the website's account summary and installer
downloads, through the real signed website API (/api/website/v1/account/*).

The JSON asserted here is the contract the website's BFF codes against
(website/functions/_lib/account-summary.ts, functions/api/account/downloads/*).
Guards are proven by the rows they did or did not write -- above all the download
USE COUNTER, which must move exactly once per stream that started and never for a
fetch that was refused.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from tests.conftest import make_device_keypair
from tests.test_account_activation import NEW_ENOUGH, envelope, post, world  # noqa: F401 -- world is a fixture
from tests.test_customer_accounts_api import BASE, call, enable
from tests.test_customer_accounts_service import GOOD
from tests.test_customer_onboarding import kyc  # noqa: F401 -- a fixture the world needs

NEW_PASSWORD = "Website-Chosen-Passw0rd-77"  # pragma: allowlist secret -- a test value
WIN_BYTES = b"MZ-fake-windows-installer-" + os.urandom(300_000)
APK_BYTES = b"PK-fake-android-package-" + os.urandom(120_000)


@pytest.fixture()
def site(app, world):  # noqa: F811
    """The world plus the website API switched on."""
    enable(app)
    app.config.update(CUSTOMER_ACCOUNTS_ENABLED=True)
    return world


def release(app, platform_code, version, content, *, channel="STABLE", state="PUBLISHED", build=1):
    """Writes an artifact and its PUBLISHED row. Returns (row id, content)."""
    from app.extensions import db_session
    from app.models.catalog import Platform, Product, ProductVersion, ReleaseChannel
    from app.releases import storage

    with app.app_context():
        product = db_session.execute(select(Product).where(Product.product_code == "AURA_RETAIL")).scalars().one()
        platform = db_session.execute(select(Platform).where(Platform.platform_code == platform_code)).scalars().one()
        chan = db_session.execute(select(ReleaseChannel).where(ReleaseChannel.channel_code == channel)).scalars().one()
        suffix = ".exe" if platform_code == "WINDOWS" else ".apk"
        path = f"site/{uuid.uuid4().hex}{suffix}"
        storage.write_artifact_bytes(path, content)
        row = ProductVersion(
            product_id=product.id, platform_id=platform.id, release_channel_id=chan.id, version=version,
            artifact_checksum_sha256=hashlib.sha256(content).hexdigest(), artifact_path=path,
            artifact_size_bytes=len(content), publication_state=state, build_number=build,
            published_at=datetime.now(timezone.utc) if state == "PUBLISHED" else None)
        db_session.add(row)
        db_session.commit()
        return str(row.id), path


def sign_in_site(client, w, password=GOOD):
    resp = call(client, "sign-in", {"email": w["email"], "password": password})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["session_token"]


def choose_password(client, token):
    resp = call(client, "password/change", {"session_token": token, "current_password": GOOD, "new_password": NEW_PASSWORD})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["session_token"]


def summary(client, token):
    return call(client, "summary", {"session_token": token, "lang": "en"})


def authorize(client, token, offer_id):
    return call(client, "downloads/authorize", {"session_token": token, "offer_id": offer_id})


def fetch(client, token):
    return call(client, "downloads/fetch", {"token": token})


def uses(app) -> list:
    with app.app_context():
        from app.extensions import db_session
        from app.models.release_distribution import ReleaseDownloadAuthorization

        db_session.expire_all()
        return [r.use_count for r in db_session.execute(select(ReleaseDownloadAuthorization)).scalars()]


def auth_rows(app) -> int:
    return len(uses(app))


# ------------------------------------------------------------------- summary

def test_summary_is_exactly_what_the_website_reads(app, client, site):
    win_id, _ = release(app, "WINDOWS", "1.0.0-rc.17", WIN_BYTES)
    token = sign_in_site(client, site)
    resp = summary(client, token)
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) == {"ok", "account", "licences", "seller", "downloads"} and body["ok"] is True
    assert body["account"]["password_must_change"] is True and body["account"]["email"] == site["email"]
    (licence,) = body["licences"]
    assert set(licence) == {"license_public_id", "product_code", "product_name", "plan_code", "plan_name", "branch_label",
                            "device_allowance", "trial"}
    assert licence["license_public_id"] == site["licence_id"] and licence["product_code"] == "AURA_RETAIL"
    assert licence["product_name"] == "Aura Retail" and licence["device_allowance"] == 2 and licence["branch_label"] is None
    assert licence["trial"] == {"state": "NOT_STARTED", "starts_on": None, "ends_on": None, "days_left": None}
    assert set(body["seller"]) == {"name", "phone"} and body["seller"]["name"]
    assert body["downloads"] == []  # no dead buttons while the temporary password stands
    # allow twin: after the customer's own password the installer shows
    token = choose_password(client, token)
    (offer,) = summary(client, token).get_json()["downloads"]
    assert set(offer) == {"offer_id", "platform", "version", "size_bytes", "filename"}
    assert offer["offer_id"] == win_id and offer["platform"] == "windows" and offer["version"] == "1.0.0-rc.17"
    assert offer["size_bytes"] == len(WIN_BYTES) and offer["filename"] == "AuraRetail-1.0.0-rc.17.exe"
    assert re.fullmatch(r"[A-Za-z0-9._ ()-]{1,120}", offer["filename"])


def test_summary_needs_a_live_session_and_the_client_address(app, client, site):
    assert summary(client, "no-such-session").status_code == 401
    assert summary(client, "no-such-session").get_json() == {"ok": False, "error": "session_invalid"}
    token = sign_in_site(client, site)
    missing_ip = call(client, "summary", {"session_token": token}, inject_ip=False)
    assert missing_ip.status_code == 400
    assert summary(client, token).status_code == 200  # allow twin


@pytest.mark.parametrize("start_offset,end_offset,status,state,days_left", [
    (-1, 6, "ACTIVE", "ACTIVE", 6),       # day 2 of 7
    (-7, 0, "ACTIVE", "ACTIVE", 0),       # today is the last selling day: 0 days left
    (-9, -1, "ACTIVE", "ENDED", 0),       # yesterday was the last day
    (-3, 4, "CONVERTED", "CONVERTED", None),
])
def test_the_trial_is_counted_in_amman_days(app, client, site, start_offset, end_offset, status, state, days_left):
    from app.operational_reports.periods import jordan_today

    today = jordan_today()
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_ops import PilotRecord

        pilot = db_session.execute(select(PilotRecord)).scalars().one()
        pilot.trial_started_at = datetime.now(timezone.utc)
        pilot.pilot_start, pilot.pilot_end = today + timedelta(days=start_offset), today + timedelta(days=end_offset)
        pilot.status = status
        db_session.commit()
    token = choose_password(client, sign_in_site(client, site))
    trial = summary(client, token).get_json()["licences"][0]["trial"]
    assert trial["state"] == state and trial["days_left"] == days_left
    if state != "CONVERTED":
        assert trial["starts_on"] == (today + timedelta(days=start_offset)).isoformat()
        assert trial["ends_on"] == (today + timedelta(days=end_offset)).isoformat()


# ----------------------------------------------------------------- authorize

def test_authorize_is_refused_while_the_temporary_password_stands_and_writes_nothing(app, client, site):
    win_id, _ = release(app, "WINDOWS", "1.0.0-rc.17", WIN_BYTES)
    token = sign_in_site(client, site)
    refused = authorize(client, token, win_id)
    assert refused.status_code == 403 and refused.get_json() == {"ok": False, "error": "password_change_required"}
    assert auth_rows(app) == 0
    token = choose_password(client, token)
    ok = authorize(client, token, win_id)
    assert ok.status_code == 200 and auth_rows(app) == 1


def test_authorize_answers_the_exact_shape_and_binds_the_link_to_the_account(app, client, site):
    win_id, _ = release(app, "WINDOWS", "1.0.0-rc.17", WIN_BYTES)
    token = choose_password(client, sign_in_site(client, site))
    body = authorize(client, token, win_id).get_json()
    assert set(body) == {"ok", "token", "expires_in_seconds", "max_uses", "filename", "size_bytes"}
    assert body["ok"] is True and body["expires_in_seconds"] == 600 and body["max_uses"] == 3
    assert re.fullmatch(r"[A-Za-z0-9_-]{16,128}", body["token"])
    assert body["filename"] == "AuraRetail-1.0.0-rc.17.exe" and body["size_bytes"] == len(WIN_BYTES)
    with app.app_context():
        from app.extensions import db_session
        from app.models.release_distribution import ReleaseDownloadAuthorization

        (row,) = db_session.execute(select(ReleaseDownloadAuthorization)).scalars().all()
        assert row.customer_account_id == site["account_id"] and row.installation_id is None and row.license_id is None
        assert (row.max_uses, row.use_count) == (3, 0)
        assert 590 <= (row.expires_at - datetime.now(timezone.utc)).total_seconds() <= 601
        assert row.token_hash != body["token"] and body["token"] not in row.token_hash  # only the hash is stored


def test_only_a_current_offer_can_be_authorized(app, client, site):
    win_id, _ = release(app, "WINDOWS", "1.0.0-rc.17", WIN_BYTES)
    draft_id, _ = release(app, "ANDROID", "1.0.0-rc.17", APK_BYTES, state="DRAFT")
    rc_id, _ = release(app, "WINDOWS", "1.0.0-rc.99", WIN_BYTES, channel="RC")
    token = choose_password(client, sign_in_site(client, site))
    offered = [o["offer_id"] for o in summary(client, token).get_json()["downloads"]]
    assert offered == [win_id]  # a draft and a release-candidate channel build are not offered
    for bad in (draft_id, rc_id, str(uuid.uuid4()), "not-an-id"):
        resp = authorize(client, token, bad)
        assert resp.status_code == 400 and resp.get_json() == {"ok": False, "error": "offer_unavailable"}, bad
    assert auth_rows(app) == 0
    assert authorize(client, token, win_id).status_code == 200


def test_the_download_channels_are_a_server_setting_and_a_pinned_licence_sees_only_its_own(app, client, site):
    win_id, _ = release(app, "WINDOWS", "1.0.0-rc.17", WIN_BYTES)
    rc_id, _ = release(app, "WINDOWS", "1.0.0-rc.18", WIN_BYTES, channel="RC", build=2)
    token = choose_password(client, sign_in_site(client, site))
    assert [o["offer_id"] for o in summary(client, token).get_json()["downloads"]] == [win_id]
    app.config.update(WEBSITE_DOWNLOAD_CHANNELS="STABLE,RC")  # a practice server
    assert [o["offer_id"] for o in summary(client, token).get_json()["downloads"]] == [rc_id]  # newest of the allowed channels
    with app.app_context():  # a licence pinned to STABLE sees STABLE only, whatever the server allows
        from app.extensions import db_session
        from app.models.catalog import ReleaseChannel
        from app.models.licensing import License

        lic = db_session.execute(select(License)).scalars().one()
        lic.allowed_release_channel_id = db_session.execute(
            select(ReleaseChannel.id).where(ReleaseChannel.channel_code == "STABLE")).scalar_one()
        db_session.commit()
    assert [o["offer_id"] for o in summary(client, token).get_json()["downloads"]] == [win_id]


@pytest.mark.parametrize("status,offered", [("PILOT", True), ("ACTIVE", True), ("PAST_DUE", True), ("EXPIRED", True),
                                            ("SUSPENDED", False), ("CANCELLED", False)])
def test_trial_paid_and_lapsed_licences_may_download_but_suspended_and_cancelled_may_not(app, client, site, status, offered):
    win_id, _ = release(app, "WINDOWS", "1.0.0-rc.17", WIN_BYTES)
    token = choose_password(client, sign_in_site(client, site))
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        db_session.execute(select(Subscription)).scalars().one().status = status
        db_session.commit()
    listed = [o["offer_id"] for o in summary(client, token).get_json()["downloads"]]
    resp = authorize(client, token, win_id)
    assert (listed == [win_id]) is offered
    assert resp.status_code == (200 if offered else 400)
    assert auth_rows(app) == (1 if offered else 0)


# --------------------------------------------------------------------- fetch

def _token_for(app, client, site, platform="WINDOWS", content=WIN_BYTES, version="1.0.0-rc.17"):
    rid, path = release(app, platform, version, content)
    session = choose_password(client, sign_in_site(client, site))
    token = authorize(client, session, rid).get_json()["token"]
    return token, session, rid, path


def test_fetch_streams_the_installer_and_counts_one_use_when_the_stream_starts(app, client, site, monkeypatch):
    from app.releases import storage

    token, _session, _rid, _path = _token_for(app, client, site)
    # The whole-file reader must be unreachable from the website path: anything that
    # buffered the installer would call it.
    monkeypatch.setattr(storage, "read_artifact_bytes", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("buffered!")))
    resp = fetch(client, token)
    assert resp.status_code == 200
    assert resp.is_streamed  # a generator, not a 300 KB body held in memory
    assert resp.headers["Content-Type"] == "application/x-msdownload"
    assert resp.headers["Content-Length"] == str(len(WIN_BYTES))
    assert resp.headers["Content-Disposition"] == 'attachment; filename="AuraRetail-1.0.0-rc.17.exe"'
    assert resp.headers["Cache-Control"] == "no-store"
    assert uses(app) == [1]  # counted before the first byte, not after the last
    assert resp.get_data() == WIN_BYTES
    assert uses(app) == [1]  # and only once


def test_an_android_package_is_served_with_its_own_type_and_name(app, client, site):
    token, *_ = _token_for(app, client, site, platform="ANDROID", content=APK_BYTES)
    resp = fetch(client, token)
    assert resp.headers["Content-Type"] == "application/vnd.android.package-archive"
    assert resp.headers["Content-Disposition"] == 'attachment; filename="AuraRetail-1.0.0-rc.17.apk"'
    assert resp.get_data() == APK_BYTES


def test_a_link_works_three_times_and_not_a_fourth(app, client, site):
    token, *_ = _token_for(app, client, site)
    for n in (1, 2, 3):
        resp = fetch(client, token)
        assert resp.status_code == 200 and resp.get_data() == WIN_BYTES and uses(app) == [n]
    fourth = fetch(client, token)
    assert fourth.status_code == 410 and fourth.get_json()["error"] == "token_already_used"
    assert fourth.mimetype == "application/json"  # no installer bytes in a refusal
    assert uses(app) == [3]


def test_a_fetch_that_cannot_start_never_burns_a_use(app, client, site):
    token, _s, _rid, path = _token_for(app, client, site)
    from app.releases import storage

    with app.app_context():
        full = os.path.join(app.config["RELEASE_ARTIFACT_DIRECTORY"], path)
    original = open(full, "rb").read()
    os.remove(full)  # the file is gone
    gone = fetch(client, token)
    assert gone.status_code == 503 and uses(app) == [0]
    open(full, "wb").write(original[:-1] + b"X")  # same size, different bytes: the published checksum no longer matches
    storage._VERIFIED.clear()
    tampered = fetch(client, token)
    assert tampered.status_code == 503 and tampered.mimetype == "application/json" and uses(app) == [0]
    open(full, "wb").write(original)  # allow twin: the real file again
    storage._VERIFIED.clear()
    assert fetch(client, token).get_data() == WIN_BYTES and uses(app) == [1]


def test_unknown_expired_and_withdrawn_links_are_refused_without_bytes(app, client, site):
    unknown = fetch(client, "U" * 43)
    assert unknown.status_code == 404 and unknown.get_json()["error"] == "token_not_found"
    token, _s, rid, _p = _token_for(app, client, site)
    with app.app_context():
        from app.extensions import db_session
        from app.models.catalog import ProductVersion
        from app.models.release_distribution import ReleaseDownloadAuthorization

        db_session.get(ProductVersion, uuid.UUID(rid)).publication_state = "WITHDRAWN"
        db_session.commit()
    withdrawn = fetch(client, token)
    assert withdrawn.status_code == 410 and uses(app) == [0]
    with app.app_context():
        from app.extensions import db_session
        from app.models.catalog import ProductVersion
        from app.models.release_distribution import ReleaseDownloadAuthorization

        db_session.get(ProductVersion, uuid.UUID(rid)).publication_state = "PUBLISHED"
        row = db_session.execute(select(ReleaseDownloadAuthorization)).scalars().one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()
    expired = fetch(client, token)
    assert expired.status_code == 410 and expired.get_json()["error"] == "token_expired" and uses(app) == [0]


def test_a_link_dies_with_the_account_it_was_made_for(app, client, site):
    token, *_ = _token_for(app, client, site)
    with app.app_context():
        from app.customer_accounts import service
        from app.extensions import db_session
        from app.models.customer_accounts import CustomerAccount

        account = db_session.get(CustomerAccount, site["account_id"])
        service.disable_account(account, "test", None)
    gone = fetch(client, token)
    assert gone.status_code == 410 and uses(app) == [0]
    with app.app_context():
        from app.customer_accounts import service
        from app.extensions import db_session
        from app.models.customer_accounts import CustomerAccount

        service.enable_account(db_session.get(CustomerAccount, site["account_id"]), None)
    assert fetch(client, token).status_code == 200  # allow twin: enabled again, the same link works


def test_a_website_link_is_not_a_device_link_and_a_device_link_is_not_a_website_link(app, client, site):
    token, _s, rid, _p = _token_for(app, client, site)
    assert client.get(f"/api/licensing/v1/releases/download/{token}").status_code == 404  # device route, website token
    assert uses(app) == [0]
    # a real device authorization (activation by licence key, then authorize-download)
    key = make_device_keypair()
    activated = post(client, "/activations", envelope(
        key, installation_id="dev-1", idempotency_key=str(uuid.uuid4()), license_key=site["key"]))
    assert activated.status_code == 200, activated.get_json()
    from tests.test_phase9r_private_distribution import _authorize, _build_download_body

    body = _build_download_body(key, installation_id=activated.get_json()["installation_id"], product_version_id=rid)
    device_token = _authorize(client, body).get_json()["download_token"]
    assert fetch(client, device_token).status_code == 404  # website route, device token
    assert client.get(f"/api/licensing/v1/releases/download/{device_token}").status_code == 200  # allow twin


def test_the_database_makes_a_link_belong_to_a_device_or_an_account_never_both(app, client, site):
    _token_for(app, client, site)
    from sqlalchemy.exc import IntegrityError

    with app.app_context():
        from app.extensions import db_session
        from app.models.release_distribution import ReleaseDownloadAuthorization

        row = db_session.execute(select(ReleaseDownloadAuthorization)).scalars().one()
        for installation_id, account_id in ((uuid.uuid4(), site["account_id"]), (None, None)):
            clone = ReleaseDownloadAuthorization(
                product_version_id=row.product_version_id, license_id=row.license_id, installation_id=installation_id,
                customer_account_id=account_id, token_hash=uuid.uuid4().hex, expires_at=row.expires_at, max_uses=3, use_count=0)
            db_session.add(clone)
            with pytest.raises(IntegrityError):
                db_session.flush()
            db_session.rollback()


def test_the_website_endpoints_are_signed_server_to_server_only(app, client, site):
    token = sign_in_site(client, site)
    for action in ("summary", "downloads/authorize", "downloads/fetch"):
        path = f"{BASE}/{action}"
        assert client.get(path).status_code == 404
        unsigned = client.post(path, data=json.dumps({"session_token": token, "client_ip": "1.2.3.4"}), content_type="application/json")
        assert unsigned.status_code == 401, action


def test_the_forced_change_cannot_keep_the_temporary_password(app, client, site):
    token = sign_in_site(client, site)
    same = call(client, "password/change", {"session_token": token, "current_password": GOOD, "new_password": GOOD})
    assert same.status_code == 422 and same.get_json()["reason"] == "same_as_old"
    me = call(client, "me", {"session_token": token}).get_json()
    assert me["account"]["password_must_change"] is True  # nothing changed
    token = choose_password(client, token)  # allow twin: a new password clears it
    assert call(client, "me", {"session_token": token}).get_json()["account"]["password_must_change"] is False
