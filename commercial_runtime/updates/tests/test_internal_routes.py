"""POST /api/updates/_internal/verify-descriptor -- the Android seam.

Kotlin makes the signed Owner call and forwards the RAW answer; this route is
the only thing that may turn it into an offer. A fake Owner signs with a REAL
Ed25519 key placed in a REAL OwnerTrustStore, so a green test has run the
production verifier against a genuine signature (see _fakes.py).

The wire shapes asserted here are the contract the Kotlin half parses
(UpdateState.kt parseDescriptorVerdict); they are asserted field by field and by
type, because "returns 200" cannot tell an OFFER Kotlin accepts from one it
silently rejects.
"""
import json

import pytest
from flask import Flask

from commercial_runtime.updates.internal_routes import make_internal_updates_blueprint
from commercial_runtime.updates.tests._fakes import (
    INSTALLATION_ID,
    FakeStateRepository,
    OwnerKey,
    make_trust_store,
    release_payload,
)

URL = "/api/updates/_internal/verify-descriptor"
SECRET = "s3cret-for-tests"
HEADERS = {"X-Aura-Internal-Secret": SECRET}
CURRENT = "1.0.0-rc.8"
REQUEST_ID = "req-android-1"
OFFER_FIELDS = {
    "product_version_id", "version", "build_number", "checksum_sha256", "size_bytes",
    "release_notes", "update_required", "below_min_supported",
}


class CountingTrustStoreFactory:
    """Counts how often the trust store was consulted: a 401 must never get as
    far as verification ('the guard ran' vs 'something else refused')."""

    def __init__(self, tmp_path, key):
        self.calls = 0
        self._tmp, self._key = tmp_path, key

    def __call__(self):
        self.calls += 1
        return make_trust_store(self._tmp, self._key)


def build_client(tmp_path, key, *, installation_id=INSTALLATION_ID, trusted_key=None):
    factory = CountingTrustStoreFactory(tmp_path, trusted_key or key)
    app = Flask(__name__)
    app.register_blueprint(make_internal_updates_blueprint(
        shared_secret=SECRET,
        trust_store_factory=factory,
        state_repository_factory=lambda: FakeStateRepository(installation_id),
        app_version=CURRENT,
    ))
    return app.test_client(), factory


def raw_owner_answer(key, **payload_kw) -> str:
    payload = release_payload(request_id=payload_kw.pop("request_id", REQUEST_ID), **payload_kw)
    return json.dumps({"contract_version": "v1", "release_descriptor": key.envelope(payload)})


def post(client, raw, request_id=REQUEST_ID, headers=HEADERS):
    return client.post(URL, json={"owner_response_raw": raw, "request_id": request_id}, headers=headers)


@pytest.fixture
def key():
    return OwnerKey()


@pytest.fixture
def ctx(tmp_path, key):
    return build_client(tmp_path, key)


def error_code(resp):
    assert resp.status_code == 409, resp.get_json()
    body = resp.get_json()
    assert body["result"] == "ERROR"
    return body["error_code"]


# ── the secret, both directions ────────────────────────────────────────────
def test_a_missing_secret_is_401_and_verification_never_ran(ctx, key):
    client, factory = ctx
    resp = post(client, raw_owner_answer(key), headers={})
    assert resp.status_code == 401
    assert factory.calls == 0


def test_a_wrong_secret_is_401_and_verification_never_ran(ctx, key):
    client, factory = ctx
    resp = post(client, raw_owner_answer(key), headers={"X-Aura-Internal-Secret": SECRET + "x"})
    assert resp.status_code == 401
    assert factory.calls == 0


def test_a_non_ascii_secret_header_is_401_not_a_crash(ctx, key):
    client, _ = ctx
    resp = post(client, raw_owner_answer(key), headers={"X-Aura-Internal-Secret": "café"})
    assert resp.status_code == 401


def test_the_right_secret_reaches_verification(ctx, key):
    client, factory = ctx
    assert post(client, raw_owner_answer(key)).status_code == 200
    assert factory.calls == 1


def test_an_empty_secret_cannot_build_the_blueprint(tmp_path, key):
    # compare_digest("", "") is True: an empty configured secret would accept a
    # request that sent no header at all.
    with pytest.raises(ValueError):
        make_internal_updates_blueprint(
            shared_secret="", trust_store_factory=lambda: None,
            state_repository_factory=lambda: None, app_version=CURRENT)


# ── OFFER ──────────────────────────────────────────────────────────────────
def test_a_signed_newer_descriptor_is_an_offer_with_every_field_typed(ctx, key):
    client, _ = ctx
    resp = post(client, raw_owner_answer(
        key, version="1.0.0-rc.9", checksum="b" * 64, size=2048,
        release_overrides={"product_version_id": "pv-123", "build_number": 12, "release_notes": "Faster till."}))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["result"] == "OFFER"
    offer = body["offer"]
    assert set(offer) == OFFER_FIELDS
    assert offer["product_version_id"] == "pv-123" and isinstance(offer["product_version_id"], str)
    assert offer["version"] == "1.0.0-rc.9"
    assert offer["build_number"] == 12 and type(offer["build_number"]) is int
    assert offer["checksum_sha256"] == "b" * 64
    assert offer["size_bytes"] == 2048 and type(offer["size_bytes"]) is int
    assert offer["release_notes"] == "Faster till."
    assert offer["update_required"] is False and offer["below_min_supported"] is False


def test_nullable_fields_and_flags_are_carried_through(ctx, key):
    client, _ = ctx
    resp = post(client, raw_owner_answer(
        key, update_required=True, below_min_supported=True,
        release_overrides={"build_number": None, "release_notes": None}))
    offer = resp.get_json()["offer"]
    assert offer["build_number"] is None and offer["release_notes"] is None
    assert offer["update_required"] is True and offer["below_min_supported"] is True


def test_signed_update_available_false_is_no_update(ctx, key):
    client, _ = ctx
    payload = release_payload(request_id=REQUEST_ID)
    payload.update(update_available=False, release=None)
    raw = json.dumps({"release_descriptor": key.envelope(payload)})
    resp = post(client, raw)
    assert resp.status_code == 200 and resp.get_json() == {"result": "NO_UPDATE"}


# ── refusals ───────────────────────────────────────────────────────────────
def test_a_signature_by_an_untrusted_key_is_refused(tmp_path, key):
    stranger = OwnerKey(key_id="owner-stranger")
    client, _ = build_client(tmp_path, key)  # trusts only `key`
    assert error_code(post(client, raw_owner_answer(stranger))) == "DESCRIPTOR_UNTRUSTED"


def test_a_key_the_store_knows_under_another_public_key_is_refused(tmp_path, key):
    impostor = OwnerKey(key_id=key.key_id)  # same key id, different key pair
    client, _ = build_client(tmp_path, key)
    assert error_code(post(client, raw_owner_answer(impostor))) == "DESCRIPTOR_UNTRUSTED"


def test_a_descriptor_for_another_installation_is_a_mismatch(ctx, key):
    client, _ = ctx
    raw = raw_owner_answer(key, installation_id="inst-someone-else")
    assert error_code(post(client, raw)) == "DESCRIPTOR_MISMATCH"


def test_a_descriptor_for_another_request_is_a_mismatch(ctx, key):
    client, _ = ctx
    raw = raw_owner_answer(key, request_id="an-older-poll")
    assert error_code(post(client, raw)) == "DESCRIPTOR_MISMATCH"


@pytest.mark.parametrize("version", [CURRENT, "1.0.0-rc.7", "0.9.0"])
def test_a_signed_same_or_older_version_is_refused_never_offered(ctx, key, version):
    # Same rule as the Windows service (verify_release_descriptor): a validly
    # signed non-newer answer is a replay/downgrade attempt, so it is refused
    # as DESCRIPTOR_INVALID rather than quietly turned into "up to date".
    client, _ = ctx
    resp = post(client, raw_owner_answer(key, version=version))
    assert error_code(resp) == "DESCRIPTOR_INVALID"


def test_an_install_that_never_activated_is_not_activated(tmp_path, key):
    client, factory = build_client(tmp_path, key, installation_id=None)
    assert error_code(post(client, raw_owner_answer(key))) == "NOT_ACTIVATED"
    assert factory.calls == 0


def test_an_owner_refusal_body_is_owner_refused(ctx):
    client, _ = ctx
    raw = json.dumps({"reason_code": "LICENSE_EXPIRED"})
    assert error_code(post(client, raw)) == "OWNER_REFUSED"


@pytest.mark.parametrize("raw", ["", "not json at all", "[1, 2]", "\"a string\"", "null", "{"])
def test_garbage_is_descriptor_invalid_or_refused_never_an_offer(ctx, raw):
    client, _ = ctx
    resp = post(client, raw)
    # Valid-JSON non-objects reach the shared verifier and are OWNER_REFUSED
    # (no release_descriptor), exactly as on Windows; unparseable text is INVALID.
    assert error_code(resp) in {"DESCRIPTOR_INVALID", "OWNER_REFUSED"}
    assert "offer" not in resp.get_json()


def test_unparseable_text_is_descriptor_invalid(ctx):
    client, _ = ctx
    assert error_code(post(client, "not json at all")) == "DESCRIPTOR_INVALID"


@pytest.mark.parametrize("body", [
    None,
    {},
    {"owner_response_raw": "{}"},
    {"request_id": REQUEST_ID},
    {"owner_response_raw": {"a": 1}, "request_id": REQUEST_ID},   # nested object, not the raw string
    {"owner_response_raw": "{}", "request_id": ""},
    {"owner_response_raw": "{}", "request_id": 7},
])
def test_a_malformed_body_is_descriptor_invalid(ctx, body):
    client, factory = ctx
    resp = client.post(URL, json=body, headers=HEADERS) if body is not None else client.post(
        URL, data="not json", headers=HEADERS)
    assert error_code(resp) == "DESCRIPTOR_INVALID"
    assert factory.calls == 0


def test_a_descriptor_with_no_signature_is_untrusted(ctx, key):
    client, _ = ctx
    envelope = key.envelope(release_payload(request_id=REQUEST_ID))
    del envelope["signature"]
    raw = json.dumps({"release_descriptor": envelope})
    assert error_code(post(client, raw)) == "DESCRIPTOR_UNTRUSTED"


# ── the raw string is what is verified ─────────────────────────────────────
@pytest.mark.parametrize("field,change", [
    ("artifact_checksum_sha256", lambda v: "c" + v[1:]),
    ("artifact_size_bytes", lambda v: v + 1),
    ("version", lambda v: "9.9.9"),
])
def test_a_tampered_signed_field_is_refused(ctx, key, field, change):
    client, _ = ctx
    answer = json.loads(raw_owner_answer(key, version="1.0.0-rc.9", checksum="b" * 64, size=2048))
    release = answer["release_descriptor"]["payload"]["release"]
    release[field] = change(release[field])
    resp = post(client, json.dumps(answer))
    assert error_code(resp) == "DESCRIPTOR_UNTRUSTED"
    assert "offer" not in resp.get_json()


def test_a_tampered_top_level_signed_flag_is_refused(ctx, key):
    client, _ = ctx
    answer = json.loads(raw_owner_answer(key))
    answer["release_descriptor"]["payload"]["update_required"] = True  # attacker forces a mandatory update
    assert error_code(post(client, json.dumps(answer))) == "DESCRIPTOR_UNTRUSTED"


def test_the_unsigned_envelope_around_the_payload_may_be_reformatted(ctx, key):
    # The signature covers the canonical payload, so whitespace and key order in
    # the transport JSON do not matter -- the scheme says so, and a genuine
    # answer that a proxy re-indented must still verify.
    client, _ = ctx
    answer = json.loads(raw_owner_answer(key))
    reordered = json.dumps(answer, indent=4, sort_keys=True, ensure_ascii=False)
    assert post(client, reordered).get_json()["result"] == "OFFER"
