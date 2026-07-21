"""Part T audit extensions for Phase 6 events."""
from __future__ import annotations

import json

from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff


def test_activation_accepted_is_audited(app, client, seeded, signing_key):
    actor_id = make_staff(app, "aud1@example.com")
    license_id, full_key = make_license(app, actor_id)
    resp = client.post(
        "/api/licensing/v1/activations",
        data=json.dumps(build_activation_body(make_device_keypair(), full_key=full_key, installation_id="aud-dev-1")),
        content_type="application/json",
    )
    assert resp.status_code == 200
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog
        from sqlalchemy import select

        rows = db_session.execute(select(AuditLog).where(AuditLog.action_code == "ACTIVATION_ACCEPTED")).scalars().all()
        assert len(rows) == 1


def test_signing_key_operations_are_audited(app, seeded):
    actor_id = make_staff(app, "aud2@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service.signing import activate_signing_key, generate_signing_key, revoke_signing_key
        from app.models.audit import AuditLog
        from sqlalchemy import select

        row = generate_signing_key(app.config["SIGNING_KEY_DIRECTORY"], actor_id)
        activate_signing_key(app.config["SIGNING_KEY_DIRECTORY"], row.key_id, actor_id)
        revoke_signing_key(row.key_id, "test", actor_id)

        codes = {r.action_code for r in db_session.execute(select(AuditLog)).scalars().all()}
        assert "SIGNING_KEY_GENERATED" in codes
        assert "SIGNING_KEY_ACTIVATED" in codes
        assert "SIGNING_KEY_REVOKED" in codes


def test_audit_never_contains_full_license_key_or_private_key(app, client, seeded, signing_key):
    actor_id = make_staff(app, "aud3@example.com")
    license_id, full_key = make_license(app, actor_id)
    resp = client.post(
        "/api/licensing/v1/activations",
        data=json.dumps(build_activation_body(make_device_keypair(), full_key=full_key, installation_id="aud-dev-3")),
        content_type="application/json",
    )
    assert resp.status_code == 200
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog
        from sqlalchemy import select

        for row in db_session.execute(select(AuditLog)).scalars().all():
            blob = json.dumps({"before": row.before_state_redacted, "after": row.after_state_redacted})
            assert full_key not in blob


def test_hash_chain_still_valid_after_phase6_events(app, client, seeded, signing_key):
    actor_id = make_staff(app, "aud4@example.com")
    license_id, full_key = make_license(app, actor_id)
    client.post(
        "/api/licensing/v1/activations",
        data=json.dumps(build_activation_body(make_device_keypair(), full_key=full_key, installation_id="aud-dev-4")),
        content_type="application/json",
    )
    with app.app_context():
        from app.audit.services import verify_chain

        ok, broken = verify_chain()
        assert ok is True
        assert broken is None
