"""Update-channel wave -- release discovery (POST /api/licensing/v1/releases/latest).

Real HTTP-level tests in the style of test_phase9r_private_distribution.py:
real activation, real device signature, real PUBLISHED/DRAFT/WITHDRAWN rows,
and the response envelope verified with the real active Owner public key.

Why these assertions look the way they do (see ENGINEERING 1): a 200 with
"update_available: false" cannot tell "the selection rule rejected the row" from
"the query never saw it", so every exclusion test is paired with a positive
control in the same test (a row that SHOULD be offered is also present, and the
offered version is asserted by name).
"""
from __future__ import annotations

import base64
import copy
import json
import uuid as uuid_mod
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.extensions import db_session
from app.licensing_service.canonical import canonicalize_bytes
from app.models.catalog import Platform, Product, ProductVersion, ReleaseChannel
from app.models.licensing import License
from app.models.licensing_service import SigningKey
from app.releases import versioning
from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff, sign_body

URL = "/api/licensing/v1/releases/latest"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _activate(app, client, email, *, product_code="AURA_CLINIC"):
    actor_id = make_staff(app, email)
    license_id, full_key = make_license(app, actor_id, product_code=product_code)
    private_key = make_device_keypair()
    resp = client.post(
        "/api/licensing/v1/activations",
        data=json.dumps(build_activation_body(private_key, full_key=full_key, installation_id="disc-dev", product_code=product_code)),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return license_id, resp.get_json()["installation_id"], private_key


def _add_release(app, version, *, channel="STABLE", state="PUBLISHED", product_code="AURA_CLINIC",
                 platform_code="WINDOWS", build_number=None, checksum="a" * 64, size=1234, **extra):
    with app.app_context():
        product = db_session.execute(select(Product).where(Product.product_code == product_code)).scalars().first()
        platform = db_session.execute(select(Platform).where(Platform.platform_code == platform_code)).scalars().first()
        chan = db_session.execute(select(ReleaseChannel).where(ReleaseChannel.channel_code == channel)).scalars().first()
        row = ProductVersion(
            product_id=product.id, platform_id=platform.id, release_channel_id=chan.id, version=version,
            artifact_checksum_sha256=checksum, artifact_path=f"test/{uuid_mod.uuid4().hex}.bin",
            artifact_size_bytes=size, publication_state=state, build_number=build_number,
            published_at=datetime.now(timezone.utc) if state == "PUBLISHED" else None, **extra,
        )
        db_session.add(row)
        db_session.commit()
        return str(row.id)


def _body(private_key, installation_id, **overrides):
    body = {
        "contract_version": "v1", "request_id": str(uuid_mod.uuid4()), "correlation_id": str(uuid_mod.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(), "nonce": uuid_mod.uuid4().hex,
        "installation_id": installation_id, "app_version": "1.0.0-rc.8", "release_channel": None,
    }
    body.update(overrides)
    return sign_body(private_key, body)


def _latest(client, body):
    return client.post(URL, data=json.dumps(body), content_type="application/json")


def _payload(resp):
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["release_descriptor"]["payload"]


def _offered_version(resp):
    release = _payload(resp)["release"]
    return release["version"] if release else None


@pytest.fixture()
def device(app, client, seeded, signing_key):
    _license_id, installation_id, private_key = _activate(app, client, "disc@example.com")
    return {"license_id": _license_id, "installation_id": installation_id, "key": private_key}


def _ask(client, device, **overrides):
    return _latest(client, _body(device["key"], device["installation_id"], **overrides))


# --------------------------------------------------------------------------
# semver comparator (vectors are shared with commercial_runtime/updates)
# --------------------------------------------------------------------------
ASCENDING = [
    "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11",
    "1.0.0-rc.1", "1.0.0-rc.9", "1.0.0-rc.10", "1.0.0", "1.0.1", "1.1.0", "2.0.0",
]


class TestSemverVectors:
    def test_ascending_chain_is_strictly_ordered(self):
        for lower, higher in zip(ASCENDING, ASCENDING[1:]):
            assert versioning.compare_semver(lower, higher) == -1, (lower, higher)
            assert versioning.compare_semver(higher, lower) == 1, (higher, lower)

    def test_build_metadata_is_ignored(self):
        assert versioning.compare_semver("1.0.0+abc", "1.0.0") == 0

    @pytest.mark.parametrize("bad", ["", "1.0", "01.0.0", "1.0.0-", "../1.0.0", "1.0.0/x", "1.0.0 rc", "1.0.0\n", "١.٠.٠"])
    def test_rejects_non_semver(self, bad):
        with pytest.raises(ValueError):
            versioning.parse_semver(bad)


# --------------------------------------------------------------------------
# selection rules
# --------------------------------------------------------------------------
class TestSelection:
    def test_picks_rc10_over_rc9_by_semver_not_string_order(self, app, client, device):
        _add_release(app, "1.0.0-rc.9", channel="RC")
        _add_release(app, "1.0.0-rc.10", channel="RC")
        _add_release(app, "1.0.0-rc.2", channel="RC")
        resp = _ask(client, device, release_channel="rc")
        assert _offered_version(resp) == "1.0.0-rc.10"

    def test_stable_beats_rc_of_same_core(self, app, client, device):
        _add_release(app, "1.0.0-rc.99", channel="RC")
        _add_release(app, "1.0.0", channel="STABLE")
        assert _offered_version(_ask(client, device, release_channel="rc")) == "1.0.0"

    def test_equal_version_prefers_higher_build_number(self, app, client, device):
        # Same semver can only repeat across channels (unique per product/platform/version).
        _add_release(app, "1.0.0+b1", channel="STABLE", build_number=3)
        _add_release(app, "1.0.0+b2", channel="RC", build_number=9)
        resp = _ask(client, device, release_channel="rc")
        assert _payload(resp)["release"]["build_number"] == 9

    def test_draft_and_withdrawn_are_never_offered(self, app, client, device):
        _add_release(app, "1.0.5", state="DRAFT")
        _add_release(app, "1.0.4", state="WITHDRAWN")
        _add_release(app, "1.0.1")  # positive control
        assert _offered_version(_ask(client, device)) == "1.0.1"

    def test_other_product_and_other_platform_excluded(self, app, client, device):
        _add_release(app, "1.0.9", product_code="AURA_RETAIL")
        _add_release(app, "1.0.8", platform_code="ANDROID")
        _add_release(app, "1.0.1")  # positive control
        assert _offered_version(_ask(client, device)) == "1.0.1"

    def test_row_missing_checksum_or_size_is_skipped(self, app, client, device):
        _add_release(app, "1.0.9", checksum=None)
        _add_release(app, "1.0.8", size=None)
        _add_release(app, "1.0.1")  # positive control
        assert _offered_version(_ask(client, device)) == "1.0.1"

    def test_non_semver_row_is_skipped_not_fatal(self, app, client, device):
        _add_release(app, "latest-and-greatest")
        _add_release(app, "1.0.1")
        assert _offered_version(_ask(client, device)) == "1.0.1"

    def test_pinned_licence_sees_only_its_channel(self, app, client, device):
        with app.app_context():
            rc = db_session.execute(select(ReleaseChannel).where(ReleaseChannel.channel_code == "RC")).scalars().first()
            lic = db_session.get(License, device["license_id"])
            lic.allowed_release_channel_id = rc.id
            db_session.commit()
        _add_release(app, "2.0.0", channel="STABLE")  # newer, but outside the pin
        _add_release(app, "1.0.0-rc.9", channel="RC")
        # Even a client asking for "stable" is held to the pin.
        assert _offered_version(_ask(client, device, release_channel="stable")) == "1.0.0-rc.9"

    def test_unpinned_rc_client_gets_stable_and_rc_stable_client_only_stable(self, app, client, device):
        _add_release(app, "1.0.0-rc.9", channel="RC")
        _add_release(app, "0.9.9", channel="STABLE")
        assert _offered_version(_ask(client, device, app_version="0.9.0", release_channel="RC")) == "1.0.0-rc.9"
        assert _offered_version(_ask(client, device, app_version="0.9.0", release_channel=None)) == "0.9.9"
        assert _offered_version(_ask(client, device, app_version="0.9.0", release_channel="stable")) == "0.9.9"
        # Unknown channel name falls back to STABLE rather than erroring.
        assert _offered_version(_ask(client, device, app_version="0.9.0", release_channel="nonsense")) == "0.9.9"

    def test_not_newer_means_no_update(self, app, client, device):
        _add_release(app, "1.0.0-rc.8", channel="RC")
        payload = _payload(_ask(client, device, app_version="1.0.0-rc.8", release_channel="rc"))
        assert payload["update_available"] is False
        assert payload["release"] is None
        assert payload["update_required"] is False and payload["below_min_supported"] is False

    def test_update_available_mirrors_release_presence(self, app, client, device):
        _add_release(app, "1.0.1")
        payload = _payload(_ask(client, device))
        assert payload["update_available"] is True and payload["release"] is not None
        assert payload["current_version"] == "1.0.0-rc.8"

    def test_unparseable_app_version_is_invalid_request(self, app, client, device):
        resp = _ask(client, device, app_version="not-a-version")
        assert resp.status_code == 400
        assert resp.get_json()["reason_code"] == "INVALID_REQUEST"


class TestRequiredAndMinSupported:
    def test_update_required_both_directions(self, app, client, device):
        _add_release(app, "1.0.0", forced_upgrade_threshold="1.0.0-rc.10")
        below = _payload(_ask(client, device, app_version="1.0.0-rc.9"))
        assert below["update_required"] is True
        at_or_above = _payload(_ask(client, device, app_version="1.0.0-rc.10"))
        assert at_or_above["update_available"] is True  # still offered...
        assert at_or_above["update_required"] is False  # ...but not forced (rc.10 is not < rc.10)

    def test_below_min_supported_both_directions(self, app, client, device):
        _add_release(app, "1.0.0", min_supported_version="1.0.0-rc.5")
        assert _payload(_ask(client, device, app_version="1.0.0-rc.4"))["below_min_supported"] is True
        assert _payload(_ask(client, device, app_version="1.0.0-rc.5"))["below_min_supported"] is False


# --------------------------------------------------------------------------
# authentication
# --------------------------------------------------------------------------
class TestAuthentication:
    def test_bad_signature_rejected(self, app, client, device):
        body = _body(make_device_keypair(), device["installation_id"])  # signed by a stranger
        resp = _latest(client, body)
        assert resp.status_code in (400, 401)
        assert resp.get_json()["reason_code"] == "INVALID_SIGNATURE"

    def test_tampered_body_rejected(self, app, client, device):
        body = _body(device["key"], device["installation_id"])
        body["app_version"] = "0.0.1"  # changed after signing
        assert _latest(client, body).get_json()["reason_code"] == "INVALID_SIGNATURE"

    def test_replayed_nonce_rejected(self, app, client, device):
        body = _body(device["key"], device["installation_id"])
        assert _latest(client, body).status_code == 200
        second = _latest(client, body)
        assert second.status_code == 400
        assert second.get_json()["reason_code"] == "NONCE_REUSED"

    def test_nonce_scope_is_discovery_only(self, app, client, device):
        # Documented, not exploited: discovery consumes its nonce under scope
        # "release_latest", distinct from "release_download", so spending a
        # nonce on one endpoint never blocks the other. What MUST hold -- a
        # nonce cannot be replayed against the same endpoint -- is asserted above.
        from app.licensing_service import replay

        shared = uuid_mod.uuid4().hex
        with app.app_context():
            replay.consume_nonce(shared, scope="release_download", ttl_seconds=300)
            replay.consume_nonce(shared, scope="release_latest", ttl_seconds=300)  # different scope: no ReplayError
            with pytest.raises(replay.ReplayError):
                replay.consume_nonce(shared, scope="release_latest", ttl_seconds=300)  # same scope: replay

    def test_missing_fields_rejected(self, app, client, device):
        body = _body(device["key"], device["installation_id"])
        del body["app_version"]
        resp = _latest(client, sign_body(device["key"], body))
        assert resp.status_code == 400
        assert resp.get_json()["reason_code"] == "INVALID_REQUEST"

    def test_suspended_licence_rejected(self, app, client, device):
        _add_release(app, "1.0.1")
        with app.app_context():
            lic = db_session.get(License, device["license_id"])
            lic.status = "SUSPENDED"
            db_session.commit()
        resp = _ask(client, device)
        assert resp.status_code == 400
        assert resp.get_json()["reason_code"] == "ACTIVATION_REJECTED"  # LICENSE_SUSPENDED, normalized publicly

    def test_unknown_installation_rejected(self, app, client, device):
        resp = _latest(client, _body(device["key"], str(uuid_mod.uuid4())))
        assert resp.get_json()["reason_code"] == "INSTALLATION_NOT_FOUND"

    def test_unsigned_service_returns_503(self, app, client, seeded):
        # No signing_key fixture: Owner cannot sign, so it must not answer at all.
        resp = _latest(client, {"installation_id": str(uuid_mod.uuid4())})
        assert resp.status_code == 503


# --------------------------------------------------------------------------
# the signed envelope
# --------------------------------------------------------------------------
def _verify_envelope(app, envelope) -> bool:
    from app.licensing_service.signing import load_public_key

    with app.app_context():
        key_row = db_session.execute(select(SigningKey).where(SigningKey.key_id == envelope["signing_key_id"])).scalars().first()
        assert key_row is not None, "envelope names a signing key Owner does not know"
        try:
            load_public_key(key_row.public_key).verify(
                base64.b64decode(envelope["signature"], validate=True), canonicalize_bytes(envelope["payload"])
            )
        except Exception:
            return False
        return True


class TestSignedEnvelope:
    def test_envelope_verifies_with_active_public_key_and_fails_when_a_byte_flips(self, app, client, device, signing_key):
        _add_release(app, "1.0.1")
        body = _body(device["key"], device["installation_id"])
        envelope = _latest(client, body).get_json()["release_descriptor"]
        assert envelope["algorithm"] == "ed25519"
        assert envelope["signing_key_id"] == signing_key
        assert _verify_envelope(app, envelope) is True

        tampered = copy.deepcopy(envelope)
        tampered["payload"]["release"]["artifact_checksum_sha256"] = "b" * 64  # attacker swaps the installer
        assert _verify_envelope(app, tampered) is False

    def test_payload_echoes_installation_and_request_ids(self, app, client, device):
        body = _body(device["key"], device["installation_id"])
        payload = _payload(_latest(client, body))
        assert payload["installation_id"] == device["installation_id"]
        assert payload["request_id"] == body["request_id"]
        assert payload["descriptor_version"] == 1

    def test_response_wire_shape(self, app, client, device):
        _add_release(app, "1.0.1", channel="STABLE", build_number=12, release_notes="Fixes")
        release = _payload(_ask(client, device))["release"]
        assert set(release) == {
            "product_version_id", "version", "build_number", "release_channel", "artifact_checksum_sha256",
            "artifact_size_bytes", "release_notes", "published_at",
        }
        assert release["release_channel"] == "STABLE" and release["build_number"] == 12
