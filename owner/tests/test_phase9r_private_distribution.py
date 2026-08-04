"""Phase 9R M11 -- private authorized release distribution. Real HTTP-level
tests: real activation, real device signature, real published/unpublished
releases, real token issuance and consumption."""
from __future__ import annotations

import json
import uuid as uuid_mod

import pytest
from sqlalchemy import select

from app.extensions import db_session
from app.models.catalog import Platform, Product, ProductVersion, ReleaseChannel
from app.releases.authority import publish_release
from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff


def _activate(client, body):
    return client.post("/api/licensing/v1/activations", data=json.dumps(body), content_type="application/json")


def _do_activation(app, client, actor_id, device_limit=1, product_code="AURA_CLINIC"):
    license_id, full_key = make_license(app, actor_id, device_limit=device_limit, product_code=product_code)
    private_key = make_device_keypair()
    resp = _activate(client, build_activation_body(private_key, full_key=full_key, installation_id="dist-dev"))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    installation_id = resp.get_json()["installation_id"]
    return license_id, installation_id, private_key


def _draft_release(app, product_code="AURA_CLINIC", platform_code="WINDOWS", **overrides):
    with app.app_context():
        product = db_session.execute(select(Product).where(Product.product_code == product_code)).scalars().first()
        platform = db_session.execute(select(Platform).where(Platform.platform_code == platform_code)).scalars().first()
        channel = db_session.execute(select(ReleaseChannel)).scalars().first()
        content = b"fake installer bytes " + uuid_mod.uuid4().bytes
        import hashlib

        from app.releases import storage

        artifact_path = f"test/{uuid_mod.uuid4().hex}.bin"
        storage.write_artifact_bytes(artifact_path, content)
        attrs = dict(
            product_id=product.id, platform_id=platform.id, release_channel_id=channel.id,
            version=f"1.0.{uuid_mod.uuid4().hex[:6]}", artifact_checksum_sha256=hashlib.sha256(content).hexdigest(),
            artifact_path=artifact_path, artifact_size_bytes=len(content),
        )
        attrs.update(overrides)
        row = ProductVersion(**attrs)
        db_session.add(row)
        db_session.commit()
        return row.id, content


def _build_download_body(private_key, *, installation_id, product_version_id, **overrides):
    from datetime import datetime, timezone

    from tests.conftest import sign_body

    body = {
        "contract_version": "v1", "request_id": str(uuid_mod.uuid4()), "correlation_id": str(uuid_mod.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(), "nonce": uuid_mod.uuid4().hex,
        "installation_id": installation_id, "product_version_id": str(product_version_id),
    }
    body.update(overrides)
    return sign_body(private_key, body)


def _authorize(client, body):
    return client.post("/api/licensing/v1/releases/authorize-download", data=json.dumps(body), content_type="application/json")


def _fetch(client, token):
    return client.get(f"/api/licensing/v1/releases/download/{token}")


class TestAuthorizeDownload:
    def test_unpublished_release_denied(self, app, client, seeded, signing_key):
        actor_id = make_staff(app, "d1@example.com")
        _, installation_id, private_key = _do_activation(app, client, actor_id)
        release_id, _ = _draft_release(app)  # never published
        resp = _authorize(client, _build_download_body(private_key, installation_id=installation_id, product_version_id=release_id))
        assert resp.status_code == 400
        assert resp.get_json()["reason_code"] == "RELEASE_NOT_AVAILABLE"

    def test_nonexistent_release_denied_with_same_code_as_unpublished(self, app, client, seeded, signing_key):
        # Anti-enumeration: must not be distinguishable from the unpublished
        # case above (same public reason code either way).
        actor_id = make_staff(app, "d2@example.com")
        _, installation_id, private_key = _do_activation(app, client, actor_id)
        resp = _authorize(client, _build_download_body(private_key, installation_id=installation_id, product_version_id=str(uuid_mod.uuid4())))
        assert resp.status_code == 400
        assert resp.get_json()["reason_code"] == "RELEASE_NOT_AVAILABLE"

    def test_wrong_product_denied(self, app, client, seeded, signing_key):
        actor_id = make_staff(app, "d3@example.com")
        _, installation_id, private_key = _do_activation(app, client, actor_id, product_code="AURA_CLINIC")
        release_id, _ = _draft_release(app, product_code="AURA_RETAIL")
        with app.app_context():
            release = db_session.get(ProductVersion, release_id)
            publish_release(release, actor_staff_user_id=actor_id)
        resp = _authorize(client, _build_download_body(private_key, installation_id=installation_id, product_version_id=release_id))
        assert resp.status_code == 400
        assert resp.get_json()["reason_code"] == "PRODUCT_MISMATCH"

    def test_wrong_platform_denied(self, app, client, seeded, signing_key):
        actor_id = make_staff(app, "d4@example.com")
        _, installation_id, private_key = _do_activation(app, client, actor_id, product_code="AURA_CLINIC")
        # Installation activated as WINDOWS by build_activation_body's default -- publish an ANDROID release.
        release_id, _ = _draft_release(app, product_code="AURA_CLINIC", platform_code="ANDROID")
        with app.app_context():
            release = db_session.get(ProductVersion, release_id)
            publish_release(release, actor_staff_user_id=actor_id)
        resp = _authorize(client, _build_download_body(private_key, installation_id=installation_id, product_version_id=release_id))
        assert resp.status_code == 400
        assert resp.get_json()["reason_code"] == "PLATFORM_NOT_ALLOWED"

    def test_wrong_installation_signature_denied(self, app, client, seeded, signing_key):
        actor_id = make_staff(app, "d5@example.com")
        _, installation_id, _real_key = _do_activation(app, client, actor_id)
        release_id, _ = _draft_release(app)
        with app.app_context():
            release = db_session.get(ProductVersion, release_id)
            publish_release(release, actor_staff_user_id=actor_id)
        impostor_key = make_device_keypair()  # never registered for this installation
        resp = _authorize(client, _build_download_body(impostor_key, installation_id=installation_id, product_version_id=release_id))
        assert resp.status_code in (400, 401)
        assert resp.get_json()["reason_code"] == "INVALID_SIGNATURE"

    def test_published_release_authorized_successfully(self, app, client, seeded, signing_key):
        actor_id = make_staff(app, "d6@example.com")
        _, installation_id, private_key = _do_activation(app, client, actor_id)
        release_id, content = _draft_release(app)
        with app.app_context():
            release = db_session.get(ProductVersion, release_id)
            publish_release(release, actor_staff_user_id=actor_id)
        resp = _authorize(client, _build_download_body(private_key, installation_id=installation_id, product_version_id=release_id))
        assert resp.status_code == 201, resp.get_data(as_text=True)
        data = resp.get_json()
        assert "download_token" in data and len(data["download_token"]) > 20
        assert data["artifact_size_bytes"] == len(content)
        assert "expires_at" in data


class TestFetchDownload:
    def _authorized_token(self, app, client, seeded, signing_key, actor_email="fetch@example.com"):
        actor_id = make_staff(app, actor_email)
        _, installation_id, private_key = _do_activation(app, client, actor_id)
        release_id, content = _draft_release(app)
        with app.app_context():
            release = db_session.get(ProductVersion, release_id)
            publish_release(release, actor_staff_user_id=actor_id)
        resp = _authorize(client, _build_download_body(private_key, installation_id=installation_id, product_version_id=release_id))
        return resp.get_json()["download_token"], content

    def test_valid_token_downloads_the_real_artifact_bytes(self, app, client, seeded, signing_key):
        token, content = self._authorized_token(app, client, seeded, signing_key)
        resp = _fetch(client, token)
        assert resp.status_code == 200
        assert resp.data == content
        assert "attachment" in resp.headers["Content-Disposition"]

    def test_token_is_single_use(self, app, client, seeded, signing_key):
        token, _ = self._authorized_token(app, client, seeded, signing_key, "fetch2@example.com")
        first = _fetch(client, token)
        assert first.status_code == 200
        second = _fetch(client, token)
        assert second.status_code == 410
        assert second.get_json()["reason_code"] == "TOKEN_ALREADY_USED"

    def test_unknown_token_rejected(self, app, client, seeded, signing_key):
        resp = _fetch(client, "not-a-real-token-" + uuid_mod.uuid4().hex)
        assert resp.status_code == 404
        assert resp.get_json()["reason_code"] == "TOKEN_NOT_FOUND"

    def test_withdrawn_release_blocks_an_already_issued_token(self, app, client, seeded, signing_key):
        from app.releases.authority import withdraw_release

        actor_id = make_staff(app, "fetch3@example.com")
        _, installation_id, private_key = _do_activation(app, client, actor_id)
        release_id, _ = _draft_release(app)
        with app.app_context():
            release = db_session.get(ProductVersion, release_id)
            publish_release(release, actor_staff_user_id=actor_id)
        resp = _authorize(client, _build_download_body(private_key, installation_id=installation_id, product_version_id=release_id))
        token = resp.get_json()["download_token"]

        with app.app_context():
            release = db_session.get(ProductVersion, release_id)
            withdraw_release(release, actor_staff_user_id=actor_id, reason="vulnerability found")

        fetch_resp = _fetch(client, token)
        assert fetch_resp.status_code == 400
        assert fetch_resp.get_json()["reason_code"] == "RELEASE_NOT_AVAILABLE"


class TestNoPathTraversalSurface:
    def test_fetch_route_never_accepts_a_raw_path(self, app, client, seeded, signing_key):
        # A "/"-containing value never even matches the <token> URL
        # converter (Werkzeug's default string converter excludes "/") --
        # the request 404s at routing, before app/releases/distribution.py
        # or app/releases/storage.py ever runs. Confirmed by the response
        # having no JSON body (Flask's own generic 404 page, not
        # _error_response's JSON shape) -- proof the traversal-shaped
        # value never reached application code at all.
        resp = client.get("/api/licensing/v1/releases/download/../../../../etc/passwd")
        assert resp.status_code == 404
        assert resp.get_json() is None
