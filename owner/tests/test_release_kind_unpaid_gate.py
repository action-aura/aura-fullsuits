"""Keep-selling, Owner side, step 2: safety fixes reach a shop that lapsed unpaid.

Owner decision 2026-10-24: an unpaid shop keeps selling and loses FEATURE
updates, but SAFETY fixes still reach it. The gate lives in release discovery
(what is offered) and authorize-download (what may be fetched) and uses the same
"unpaid" definition as the sync relay (commercial_ops/unpaid.py).

Every refusal here is paired with an allow-side assertion (ENGINEERING 1): a
paid shop must still see the newest FEATURE release, SAFETY must still be
downloadable by an unpaid shop, and SUSPENDED / CANCELLED must still be refused
exactly as before -- even for SAFETY releases.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
import uuid as uuid_mod

import pytest

from app.extensions import db_session
from app.models.catalog import ProductVersion
from tests.conftest import make_staff
from tests.support_unpaid import grant_emergency_extension, set_subscription_state
from tests.test_phase9r_private_distribution import _authorize, _build_download_body
from tests.test_release_publish_ui import admin, store  # noqa: F401  (fixtures for the upload-form tests)
from tests.test_release_discovery import _add_release, _ask, _offered_version, _payload, device  # noqa: F401  (fixture)


def _lapse(app, device, status, **kw):
    set_subscription_state(app, device["license_id"], status, **kw)


def _authorize_release(client, device, release_id):
    return _authorize(client, _build_download_body(device["key"], installation_id=device["installation_id"], product_version_id=release_id))


# ---------------------------------------------------------------- discovery
class TestDiscovery:
    def test_paid_shop_is_offered_the_newest_feature_release(self, app, client, device):
        _add_release(app, "1.0.1", release_kind="SAFETY")
        _add_release(app, "1.0.2", release_kind="FEATURE")
        assert _offered_version(_ask(client, device, app_version="1.0.0")) == "1.0.2"

    def test_unpaid_expired_shop_gets_newest_safety_even_when_a_newer_feature_exists(self, app, client, device):
        _add_release(app, "1.0.1", release_kind="SAFETY")
        _add_release(app, "1.0.2", release_kind="FEATURE")
        _add_release(app, "1.0.3", release_kind="FEATURE")
        _lapse(app, device, "EXPIRED")
        assert _offered_version(_ask(client, device, app_version="1.0.0")) == "1.0.1"

    def test_unpaid_past_due_past_grace_gets_safety_only(self, app, client, device):
        _add_release(app, "1.0.1", release_kind="SAFETY")
        _add_release(app, "1.0.2", release_kind="FEATURE")
        _lapse(app, device, "PAST_DUE", past_due_days_ago=11, staff_id=None)
        assert _offered_version(_ask(client, device, app_version="1.0.0")) == "1.0.1"

    def test_past_due_inside_grace_is_still_paid_and_sees_feature(self, app, client, device):
        _add_release(app, "1.0.1", release_kind="SAFETY")
        _add_release(app, "1.0.2", release_kind="FEATURE")
        _lapse(app, device, "PAST_DUE", past_due_days_ago=2, staff_id=None)
        assert _offered_version(_ask(client, device, app_version="1.0.0")) == "1.0.2"

    def test_an_emergency_extension_means_the_shop_is_not_unpaid_and_sees_feature(self, app, client, device):
        """Verifier N1: the till treats a shop on an extension as allowed, so the
        SAFETY-only filter must not apply to it either."""
        _add_release(app, "1.0.1", release_kind="SAFETY")
        _add_release(app, "1.0.2", release_kind="FEATURE")
        _lapse(app, device, "EXPIRED")
        assert _offered_version(_ask(client, device, app_version="1.0.0")) == "1.0.1"  # control: filtered without it
        grant_emergency_extension(app, device["license_id"], make_staff(app, "ks-ext@example.com"))
        assert _offered_version(_ask(client, device, app_version="1.0.0")) == "1.0.2"

    def test_unpaid_with_only_feature_releases_is_offered_nothing_not_an_error(self, app, client, device):
        _add_release(app, "1.0.2", release_kind="FEATURE")
        _lapse(app, device, "EXPIRED")
        payload = _payload(_ask(client, device, app_version="1.0.0"))
        assert payload["update_available"] is False and payload["release"] is None

    @pytest.mark.parametrize("status", ["SUSPENDED", "CANCELLED"])
    def test_suspended_and_cancelled_stay_refused_even_with_a_safety_release(self, app, client, device, status):
        _add_release(app, "1.0.1", release_kind="SAFETY")
        _lapse(app, device, status)
        resp = _ask(client, device, app_version="1.0.0")
        assert resp.status_code == 400
        assert resp.get_json()["reason_code"] == "ACTIVATION_REJECTED"  # SUBSCRIPTION_<status>, normalised publicly


# ----------------------------------------------------------------- download
class TestDownload:
    def test_unpaid_shop_cannot_authorize_a_feature_release(self, app, client, device):
        feature_id = _add_release(app, "1.0.2", release_kind="FEATURE")
        safety_id = _add_release(app, "1.0.1", release_kind="SAFETY")
        _lapse(app, device, "EXPIRED")
        refused = _authorize_release(client, device, feature_id)
        assert refused.status_code == 400 and refused.get_json()["reason_code"] == "RELEASE_NOT_AVAILABLE"
        # positive control in the same state: the SAFETY release is authorized
        allowed = _authorize_release(client, device, safety_id)
        assert allowed.status_code == 201, allowed.get_json()
        assert allowed.get_json()["download_token"]

    def test_paid_shop_can_authorize_a_feature_release(self, app, client, device):
        feature_id = _add_release(app, "1.0.2", release_kind="FEATURE")
        assert _authorize_release(client, device, feature_id).status_code == 201

    def test_past_due_inside_grace_can_authorize_a_feature_release(self, app, client, device):
        feature_id = _add_release(app, "1.0.2", release_kind="FEATURE")
        _lapse(app, device, "PAST_DUE", past_due_days_ago=2, staff_id=None)
        assert _authorize_release(client, device, feature_id).status_code == 201

    @pytest.mark.parametrize("status", ["SUSPENDED", "CANCELLED"])
    def test_suspended_and_cancelled_cannot_download_even_safety(self, app, client, device, status):
        safety_id = _add_release(app, "1.0.1", release_kind="SAFETY")
        _lapse(app, device, status)
        resp = _authorize_release(client, device, safety_id)
        assert resp.status_code == 400 and resp.get_json()["reason_code"] == "ACTIVATION_REJECTED"

    def test_past_due_past_grace_cannot_authorize_a_feature_release(self, app, client, device):
        """Verifier gap: only an EXPIRED shop was tried at the download level. A PAST_DUE shop
        past its grace is unpaid too (same definition as the till), so naming a FEATURE release
        id directly is refused; the SAFETY release is still authorized in the same state."""
        feature_id = _add_release(app, "1.0.2", release_kind="FEATURE")
        safety_id = _add_release(app, "1.0.1", release_kind="SAFETY")
        _lapse(app, device, "PAST_DUE", past_due_days_ago=11, staff_id=None)
        refused = _authorize_release(client, device, feature_id)
        assert refused.status_code == 400 and refused.get_json()["reason_code"] == "RELEASE_NOT_AVAILABLE"
        assert _authorize_release(client, device, safety_id).status_code == 201

    def test_a_shop_on_an_emergency_extension_can_authorize_a_feature_release(self, app, client, device):
        """Allow side of the same gate: the till treats a shop on an extension as allowed, so the
        download gate must not refuse it either (verifier N1, at the download level)."""
        feature_id = _add_release(app, "1.0.2", release_kind="FEATURE")
        _lapse(app, device, "EXPIRED")
        assert _authorize_release(client, device, feature_id).status_code == 400   # control: refused without it
        grant_emergency_extension(app, device["license_id"], make_staff(app, "ks-ext-dl@example.com"))
        allowed = _authorize_release(client, device, feature_id)
        assert allowed.status_code == 201, allowed.get_json()

    @pytest.mark.parametrize("flags", [{"with_policy": False}, {"with_history": False}], ids=["no-policy", "no-history-row"])
    def test_past_due_with_no_grace_end_is_treated_as_paid(self, app, client, device, flags):
        """PAST_DUE with no policy (or no PAST_DUE history row) has NO grace end; the device only
        restricts when it has one, so neither gate may crash on it or refuse the shop."""
        feature_id = _add_release(app, "1.0.2", release_kind="FEATURE")
        _add_release(app, "1.0.1", release_kind="SAFETY")
        _lapse(app, device, "PAST_DUE", past_due_days_ago=30, staff_id=None, **flags)
        assert _authorize_release(client, device, feature_id).status_code == 201
        assert _offered_version(_ask(client, device, app_version="1.0.0")) == "1.0.2"   # paid: sees the feature release

    def test_other_device_endpoints_still_refuse_an_expired_subscription(self, app, client, device):
        """allow_unpaid is opt-in: crash reports / the mail relay share
        authenticate_device_request and must keep refusing EXPIRED (they are online
        extras an unpaid shop does not get)."""
        from app.api_external.routes import _service_config
        from app.releases.distribution import DownloadAuthorizationRejected, authenticate_device_request
        from tests.conftest import sign_body

        _lapse(app, device, "EXPIRED")
        body = sign_body(device["key"], {
            "installation_id": device["installation_id"], "timestamp": datetime.now(timezone.utc).isoformat(),
            "nonce": uuid_mod.uuid4().hex,
        })
        with app.test_request_context():
            with pytest.raises(DownloadAuthorizationRejected) as exc:
                authenticate_device_request(body, nonce_scope="crash_reports", config=_service_config())
        assert exc.value.internal_reason_code == "SUBSCRIPTION_EXPIRED"


# ------------------------------------------------------- staff create surface
class TestStaffUploadForm:
    """The real web form (verifier gap): the manifest/import path was covered, the form that
    staff actually use was not. A release uploaded as SAFETY must be STORED as SAFETY -- if the
    field were dropped anywhere on the way, every safety fix would silently become a feature
    update and never reach an unpaid shop."""

    def test_the_form_stores_safety_and_defaults_to_feature(self, app, admin, store):
        from tests.test_release_publish_ui import _rows as version_rows, _upload

        assert _upload(admin, version="2.0.0", release_kind="SAFETY").status_code == 302
        assert _upload(admin, version="2.0.1").status_code == 302                      # field absent -> FEATURE
        assert _upload(admin, version="2.0.2", release_kind="FEATURE").status_code == 302
        kinds = {r.version: r.release_kind for r in version_rows(app)}
        assert kinds == {"2.0.0": "SAFETY", "2.0.1": "FEATURE", "2.0.2": "FEATURE"}

    def test_the_form_refuses_an_unknown_kind_and_stores_nothing(self, app, admin, store):
        from tests.test_release_publish_ui import _rows as version_rows, _upload

        resp = _upload(admin, version="2.1.0", release_kind="URGENT")
        assert resp.status_code == 400
        assert version_rows(app) == []

    def test_the_form_offers_both_kinds(self, app, admin, store):
        html = admin.get("/releases/versions/upload").get_data(as_text=True)
        assert 'name="release_kind"' in html and 'value="SAFETY"' in html and 'value="FEATURE"' in html


class TestStaffSurface:
    def test_manifest_import_defaults_to_feature_and_accepts_safety(self, app, seeded):
        with app.app_context():
            from app.catalog.services import import_release_manifest

            import_release_manifest({"version": "9.0.0", "windows": [{"product_code": "AURA_RETAIL", "sha256": "a" * 64}]}, None)
            import_release_manifest(
                {"version": "9.0.1", "windows": [{"product_code": "AURA_RETAIL", "sha256": "b" * 64, "release_kind": "SAFETY"}]}, None
            )
            kinds = {r.version: r.release_kind for r in db_session.query(ProductVersion).filter(ProductVersion.version.in_(["9.0.0", "9.0.1"]))}
        assert kinds == {"9.0.0": "FEATURE", "9.0.1": "SAFETY"}

    def test_upload_form_rejects_an_unknown_kind_and_has_en_ar_labels(self, app, seeded):
        from app.releases import upload

        with app.test_request_context():
            form = upload.UploadForm(
                product_code="AURA_RETAIL", platform_code="WINDOWS", channel_code="RC", version="9.1.0", build_number="",
                release_notes="", min_supported_version="", update_required=False, release_kind="URGENT",
            )
            with pytest.raises(upload.UploadRejected):
                upload._validate_fields(form)
            ok = upload.UploadForm(**{**form.__dict__, "release_kind": "SAFETY"})
            upload._validate_fields(ok)  # allow side: SAFETY is accepted

        from babel.messages.mofile import read_mo

        for lang, expect in (("en", "Kind of release"), ("ar", "نوع الإصدار")):
            with open(f"translations/{lang}/LC_MESSAGES/messages.mo", "rb") as fh:
                assert read_mo(fh).get("Kind of release").string == expect


# ----------------------------------------------------------------- migration
def test_migration_round_trips_and_defaults_existing_rows_to_feature():
    """Runs against a scratch database: upgrade head, an existing row reads
    FEATURE, downgrade -1 drops the column, upgrade head restores it."""
    import os
    import psycopg

    base = os.environ["OWNER_TEST_DATABASE_URL"]
    admin = base.replace("postgresql+psycopg://", "postgresql://").rsplit("/", 1)[0] + "/postgres"
    scratch = "aura_owner_ksgates_mig"
    conn = psycopg.connect(admin, autocommit=True)
    conn.execute(f"DROP DATABASE IF EXISTS {scratch} ")
    conn.execute(f"CREATE DATABASE {scratch}")
    url = base.rsplit("/", 1)[0] + "/" + scratch
    env = {**os.environ, "OWNER_DATABASE_URL": url}
    owner_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def alembic(*args):
        return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=owner_dir, env=env, check=True, capture_output=True, text=True)

    def columns():
        c = psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"))
        try:
            return [r[0] for r in c.execute("select column_name from information_schema.columns where table_name='owner_product_versions'")]
        finally:
            c.close()

    c = None
    try:
        alembic("upgrade", "a29865d5eab4")
        assert "release_kind" not in columns()
        c = psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True)
        pid, plid, chid = [c.execute(q).fetchone()[0] for q in (
            "insert into owner_products (id, product_code, name, is_active, is_sellable, commercial_status, created_at, updated_at) values (gen_random_uuid(), 'MIGP', 'x', true, true, 'ACTIVE', now(), now()) returning id",
            "insert into owner_platforms (id, platform_code, name, created_at, updated_at) values (gen_random_uuid(), 'MIGPL', 'x', now(), now()) returning id",
            "insert into owner_release_channels (id, channel_code, name, created_at, updated_at) values (gen_random_uuid(), 'MIGCH', 'x', now(), now()) returning id",
        )]
        c.execute(
            "insert into owner_product_versions (id, product_id, platform_id, release_channel_id, version, is_current_stable, is_deprecated, publication_state, created_at, updated_at) "
            "values (gen_random_uuid(), %s, %s, %s, '1.0.0', false, false, 'DRAFT', now(), now())", (pid, plid, chid),
        )
        alembic("upgrade", "head")
        assert "release_kind" in columns()
        assert c.execute("select release_kind from owner_product_versions").fetchall() == [("FEATURE",)]
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("update owner_product_versions set release_kind='URGENT'")
        alembic("downgrade", "a29865d5eab4")
        assert "release_kind" not in columns()
        alembic("upgrade", "head")
        assert "release_kind" in columns()
    finally:
        if c is not None:
            c.close()
        conn.execute(f"DROP DATABASE IF EXISTS {scratch} ")
        conn.close()
