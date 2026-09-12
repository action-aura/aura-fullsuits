"""Part Y ACTIVATION: the real HTTP surface, real Ed25519 signatures, real
Postgres-backed replay/idempotency/rate-limit -- no mocks."""
from __future__ import annotations

import json
import uuid

from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff


def _activate(client, body):
    return client.post("/api/licensing/v1/activations", data=json.dumps(body), content_type="application/json")


def test_valid_activation_approved(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor1@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=2)
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="dev-001")

    resp = _activate(client, body)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["result"] == "SUCCESS"
    assert data["reason_code"] == "ACTIVATION_APPROVED"
    assert "signed_assertion" in data
    assert full_key not in json.dumps(data)  # never echoed back


def test_invalid_license_key_rejected_generically(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor2@example.com")
    make_license(app, actor_id)
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key="AURA-CLN-1-ZZZZ-ZZZZ-ZZZZ-ZZZZ-ZZZZ", installation_id="dev-002")

    resp = _activate(client, body)
    assert resp.status_code == 400
    data = resp.get_json()
    # Anti-enumeration: never LICENSE_NOT_FOUND publicly.
    assert data["reason_code"] == "ACTIVATION_REJECTED"


def test_suspended_license_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor3@example.com")
    license_id, full_key = make_license(app, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.services import transition_license
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        transition_license(lic, "ACTIVE", actor_id)
        transition_license(lic, "SUSPENDED", actor_id)
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="dev-003")
    resp = _activate(client, body)
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "ACTIVATION_REJECTED"


def test_revoked_license_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor4@example.com")
    license_id, full_key = make_license(app, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.services import transition_license
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        transition_license(lic, "REVOKED", actor_id, reason="test")
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="dev-004")
    resp = _activate(client, body)
    assert resp.status_code == 400


def test_wrong_product_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor5@example.com")
    license_id, full_key = make_license(app, actor_id, product_code="AURA_CLINIC")
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="dev-005", product_code="AURA_RETAIL")
    resp = _activate(client, body)
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "PRODUCT_MISMATCH"


def test_disallowed_platform_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor6@example.com")
    license_id, full_key = make_license(app, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        lic.allowed_platforms = "WINDOWS"  # ANDROID not allowed
        db_session.commit()
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="dev-006", platform="ANDROID")
    resp = _activate(client, body)
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "PLATFORM_NOT_ALLOWED"


def test_unsupported_contract_version_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor7@example.com")
    license_id, full_key = make_license(app, actor_id)
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="dev-007", contract_version="v99")
    resp = _activate(client, body)
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "UNSUPPORTED_CONTRACT_VERSION"


def test_device_limit_reached(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor8@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=1)

    private_key_1 = make_device_keypair()
    resp1 = _activate(client, build_activation_body(private_key_1, full_key=full_key, installation_id="dev-008a"))
    assert resp1.status_code == 200

    private_key_2 = make_device_keypair()
    resp2 = _activate(client, build_activation_body(private_key_2, full_key=full_key, installation_id="dev-008b"))
    assert resp2.status_code == 400
    assert resp2.get_json()["reason_code"] == "DEVICE_LIMIT_REACHED"


def test_same_device_activating_a_different_license_rejected_cleanly(app, client, seeded, signing_key):
    """Phase 8V-P: found by real installed-product testing. A device already
    holding an ACTIVE key on one license used to crash Owner with an
    unhandled 500 (owner_device_public_keys.fingerprint UNIQUE violation)
    when it tried to activate a second, different license -- the reuse
    check only matched same-device-same-license, so a same-device-
    different-license attempt fell through to the "brand new device"
    branch and tried to INSERT a fingerprint that already existed. Must now
    reject cleanly instead of crashing."""
    actor_id = make_staff(app, "actor8b@example.com")
    license_id_1, full_key_1 = make_license(app, actor_id, device_limit=2)
    license_id_2, full_key_2 = make_license(app, actor_id, device_limit=2)

    private_key = make_device_keypair()
    resp1 = _activate(client, build_activation_body(private_key, full_key=full_key_1, installation_id="dev-008c"))
    assert resp1.status_code == 200

    resp2 = _activate(client, build_activation_body(private_key, full_key=full_key_2, installation_id="dev-008d"))
    assert resp2.status_code == 400
    assert resp2.get_json()["reason_code"] == "DEVICE_ALREADY_REGISTERED"


def test_idempotent_retry_returns_same_result_no_extra_slot(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor9@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=1)
    private_key = make_device_keypair()
    idem_key = str(uuid.uuid4())

    body1 = build_activation_body(private_key, full_key=full_key, installation_id="dev-009", idempotency_key=idem_key)
    resp1 = _activate(client, body1)
    assert resp1.status_code == 200
    data1 = resp1.get_json()

    body2 = build_activation_body(private_key, full_key=full_key, installation_id="dev-009", idempotency_key=idem_key)
    resp2 = _activate(client, body2)
    assert resp2.status_code == 200
    data2 = resp2.get_json()

    assert data1["response_id"] == data2["response_id"]
    assert data1["installation_id"] == data2["installation_id"]

    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation
        from sqlalchemy import select

        count = len(db_session.execute(select(Installation).where(Installation.license_id == license_id)).scalars().all())
        assert count == 1  # the retry did not consume a second slot


def test_same_device_key_retry_with_fresh_installation_id_reuses_installation(app, client, seeded, signing_key):
    """Phase 7V-A: the client-generated installation_id is fresh on every
    activation attempt by protocol design, so a retry after a lost SUCCESS
    response (the client never saw the first attempt succeed) arrives with a
    DIFFERENT installation_id but the SAME device key. Before this fix, this
    crashed with an unhandled UniqueViolation on the device-key fingerprint
    (globally unique) instead of being recognized as the same device."""
    actor_id = make_staff(app, "actor9b@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=1)
    private_key = make_device_keypair()

    body1 = build_activation_body(private_key, full_key=full_key, installation_id="dev-009b-attempt-1")
    resp1 = _activate(client, body1)
    assert resp1.status_code == 200
    data1 = resp1.get_json()

    body2 = build_activation_body(private_key, full_key=full_key, installation_id="dev-009b-attempt-2")
    resp2 = _activate(client, body2)
    assert resp2.status_code == 200
    data2 = resp2.get_json()

    assert data1["installation_id"] == data2["installation_id"]  # same server-side installation reused

    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation
        from sqlalchemy import select

        count = len(db_session.execute(select(Installation).where(Installation.license_id == license_id)).scalars().all())
        assert count == 1  # the retry did not consume a second device-limit slot


def test_replay_of_identical_signed_request_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor10@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=2)
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="dev-010")

    resp1 = _activate(client, body)
    assert resp1.status_code == 200
    resp2 = _activate(client, body)  # byte-identical replay, including the same nonce
    assert resp2.status_code == 400
    assert resp2.get_json()["reason_code"] == "NONCE_REUSED"


def test_invalid_signature_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor11@example.com")
    license_id, full_key = make_license(app, actor_id)
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="dev-011")
    body["signature"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="
    resp = _activate(client, body)
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_SIGNATURE"


def test_full_key_never_appears_anywhere_in_db_logs_or_error_response(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor12@example.com")
    license_id, full_key = make_license(app, actor_id)
    private_key = make_device_keypair()

    # A deliberately WRONG key first (error path), then the real one.
    bad_body = build_activation_body(private_key, full_key="AURA-CLN-1-WRNG-WRNG-WRNG-WRNG-WRNG", installation_id="dev-012")
    bad_resp = _activate(client, bad_body)
    assert full_key not in json.dumps(bad_resp.get_json())

    good_body = build_activation_body(private_key, full_key=full_key, installation_id="dev-012")
    good_resp = _activate(client, good_body)
    assert full_key not in json.dumps(good_resp.get_json())

    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog
        from app.models.licensing import License
        from app.models.licensing_service import ActivationRequest
        from sqlalchemy import select

        lic = db_session.get(License, license_id)
        assert full_key not in (lic.key_secret_hmac or "")
        for row in db_session.execute(select(ActivationRequest)).scalars().all():
            assert full_key not in json.dumps({"request_id": row.request_id, "reason_code": row.reason_code})
        for row in db_session.execute(select(AuditLog)).scalars().all():
            assert full_key not in json.dumps(row.after_state_redacted or {})
            assert full_key not in json.dumps(row.before_state_redacted or {})


def test_no_payload_rejected(client, seeded, signing_key):
    resp = client.post("/api/licensing/v1/activations", data="not json", content_type="application/json")
    assert resp.status_code == 400


def test_wrong_content_type_rejected(client, seeded, signing_key):
    resp = client.post("/api/licensing/v1/activations", data="{}", content_type="text/plain")
    assert resp.status_code == 415


def test_service_unavailable_without_active_signing_key(client, seeded):
    """Fail-closed: no active signing key means every request is rejected
    before any decision logic runs, not silently allowed through unsigned."""
    resp = client.post("/api/licensing/v1/activations", data="{}", content_type="application/json")
    assert resp.status_code == 503
    assert resp.get_json()["reason_code"] == "SIGNING_KEY_UNAVAILABLE"


def _installation_by_label(app, label):
    from sqlalchemy import select as sa_select

    from app.extensions import db_session
    from app.models.installations import Installation

    return db_session.execute(
        sa_select(Installation).where(Installation.installation_label == label)
    ).scalars().one()


def test_reactivation_after_device_key_revoked_is_rejected_not_signed_blank(app, client, seeded, signing_key):
    """Launch-readiness: an installation whose device key is REVOKED but whose
    own status is still ACTIVE must be rejected here, exactly as check-in,
    deactivation, sync and release-download already reject it.

    Found while chasing "a phone cannot be licensed at all". Every other
    protocol surface calls get_active_device_key() and raises
    DEVICE_KEY_REVOKED when it comes back None (checkin.py, deactivation.py,
    sync/routes.py, releases/distribution.py). Activation did not: the
    existing-installation branch's guard is written as

        if active_device_key is not None and active_device_key.fingerprint != ...

    so a None key -- the precise state a staff revoke via
    licensing_admin/routes.py leaves behind, since revoking a key
    deliberately does NOT touch installation.status -- fell straight past
    it. register_device_key() is only called on the OTHER branch, so
    nothing re-registered the key either, and the assertion was then signed
    with device_key_fingerprint = None.

    That is not a harmless null. Owner reported SUCCESS and recorded the
    installation ACTIVE, while every client independently re-verifying the
    assertion compared None against its own real fingerprint and raised
    ASSERTION_DEVICE_MISMATCH -- a device permanently unable to activate,
    with a server that believes it did. Retrying could never help: the
    outcome is deterministic. The failure is invisible from Owner's side,
    which is why it survived three clean desktop activations.
    """
    actor_id = make_staff(app, "revoked-key-actor@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=2)
    private_key = make_device_keypair()
    label = "dev-revoked-key"

    first = _activate(client, build_activation_body(private_key, full_key=full_key, installation_id=label))
    assert first.status_code == 200
    assert first.get_json()["result"] == "SUCCESS"

    # Exactly what licensing_admin/routes.py's revoke endpoint does. Note it
    # leaves installation.status ACTIVE -- asserted, because if a future
    # change made revoke also deactivate the installation, this test would
    # silently start exercising the INSTALLATION_DEACTIVATED path instead
    # and stop covering the bug it was written for.
    with app.app_context():
        from app.licensing_service import device_identity

        installation = _installation_by_label(app, label)
        device_identity.revoke_device_key(device_identity.get_active_device_key(installation.id), actor_id)
        installation = _installation_by_label(app, label)
        assert installation.status == "ACTIVE"
        assert device_identity.get_active_device_key(installation.id) is None

    resp = _activate(client, build_activation_body(private_key, full_key=full_key, installation_id=label))
    data = resp.get_json()

    # The bug's signature, asserted directly: never hand a client a SUCCESS
    # carrying an assertion it is structurally incapable of accepting.
    if data.get("result") == "SUCCESS":
        payload = json.loads(json.dumps(data["signed_assertion"]))["payload"]
        assert payload["device_key_fingerprint"] is not None, (
            "Owner signed an assertion with a null device_key_fingerprint -- "
            "every client rejects this as ASSERTION_DEVICE_MISMATCH."
        )

    assert resp.status_code == 400
    assert data["reason_code"] == "DEVICE_KEY_REVOKED"


def test_assertion_is_never_signed_without_a_device_fingerprint(app, client, seeded, signing_key):
    """Defence in depth behind the DEVICE_KEY_REVOKED guard above, tested
    directly rather than through activation -- that guard now stops the only
    known route here, so exercising this through the HTTP surface would pass
    whether or not this check exists, and prove nothing about it.

    A null device_key_fingerprint is unusable, not merely incomplete: the
    client compares it against its own key and raises
    ASSERTION_DEVICE_MISMATCH. Refusing to sign turns any future route to
    this state into a visible server error instead of a silently bricked
    device.
    """
    actor_id = make_staff(app, "blank-fingerprint-actor@example.com")
    _license_id, full_key = make_license(app, actor_id)
    private_key = make_device_keypair()
    label = "dev-blank-fingerprint"

    # A real activation, so the rows below are the genuine article rather
    # than a hand-built fixture that could drift from what Owner writes.
    assert _activate(client, build_activation_body(private_key, full_key=full_key, installation_id=label)).status_code == 200

    with app.app_context():
        from app.licensing_service.assertions import AssertionError_, build_assertion_payload

        installation = _installation_by_label(app, label)
        license_row = installation.license

        def _build(fingerprint):
            return build_assertion_payload(
                license_row=license_row, installation_row=installation, device_fingerprint=fingerprint,
                entitlements={}, offline_policy={}, contract_version="v1", ttl_seconds=3600,
            )

        for missing in (None, ""):
            try:
                _build(missing)
            except AssertionError_:
                pass
            else:
                raise AssertionError(f"build_assertion_payload accepted device_fingerprint={missing!r}")

        # The allow-half: a real fingerprint must still sign normally. Without
        # this, a guard that rejected EVERYTHING would pass the checks above
        # while breaking every activation on the platform.
        assert _build("a" * 64)["device_key_fingerprint"] == "a" * 64
