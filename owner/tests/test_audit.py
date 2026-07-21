from __future__ import annotations

from tests.conftest import make_staff


def test_audit_created_for_sensitive_write(app, seeded):
    staff_id = make_staff(app, "a1@example.com")
    with app.app_context():
        from app.customers.services import create_customer
        from app.extensions import db_session
        from app.models.audit import AuditLog

        create_customer({"legal_name": "Audit Test Co"}, staff_id)
        events = db_session.query(AuditLog).filter_by(action_code="CUSTOMER_CREATED").all()
        assert len(events) == 1


def test_secret_fields_redacted(app):
    from app.audit.services import redact

    result = redact({"password": "hunter2", "totp_secret": "ABC", "legal_name": "Fine Co", "token_hash": "xyz"})
    assert result["password"] == "<redacted>"
    assert result["totp_secret"] == "<redacted>"
    assert result["token_hash"] == "<redacted>"
    assert result["legal_name"] == "Fine Co"


def test_hash_chain_links_sequential_entries(app, seeded):
    staff_id = make_staff(app, "a2@example.com")
    with app.app_context():
        from app.customers.services import create_customer
        from app.extensions import db_session
        from app.models.audit import AuditLog

        create_customer({"legal_name": "Chain Co 1"}, staff_id)
        create_customer({"legal_name": "Chain Co 2"}, staff_id)
        rows = db_session.query(AuditLog).order_by(AuditLog.created_at.asc(), AuditLog.id.asc()).all()
        assert rows[-1].previous_hash == rows[-2].current_hash


def test_verify_chain_detects_tampering(app, seeded):
    staff_id = make_staff(app, "a3@example.com")
    with app.app_context():
        from app.audit.services import verify_chain
        from app.customers.services import create_customer
        from app.extensions import db_session
        from app.models.audit import AuditLog

        create_customer({"legal_name": "Tamper Co"}, staff_id)
        ok_before, _ = verify_chain()
        assert ok_before is True

        row = db_session.query(AuditLog).first()
        row.action_code = "TAMPERED"
        db_session.commit()

        ok_after, broken_id = verify_chain()
        assert ok_after is False
        assert broken_id == str(row.id)


def test_audit_log_has_no_update_or_delete_route(app, client, seeded):
    """The audit blueprint exposes no PUT/PATCH/DELETE on any /audit/* route --
    append-only is enforced by simply not building any mutating route, not by a
    runtime check."""
    with app.test_request_context():
        rules = [r for r in app.url_map.iter_rules() if r.rule.startswith("/audit")]
    methods_seen = set()
    for r in rules:
        methods_seen |= r.methods
    assert "PUT" not in methods_seen
    assert "PATCH" not in methods_seen
    assert "DELETE" not in methods_seen
