"""Phase 9 Milestone 8 -- structured logging + redaction tests."""
from __future__ import annotations

import json

from app.observability.logging_config import RedactingJsonFormatter, _redact


def test_redacts_license_key_shape():
    msg = "activation failed for key AURA-RETAIL-1-ABCD-EFGH-IJKL-MNOP-QRST"
    assert "AURA-RETAIL-1-ABCD" not in _redact(msg)
    assert "***REDACTED***" in _redact(msg)


def test_redacts_password_field():
    msg = '{"email": "a@b.com", "password": "hunter2hunter2"}'
    out = _redact(msg)
    assert "hunter2hunter2" not in out


def test_redacts_pepper_and_totp_and_recovery_code():
    for field, value in [("pepper", "supersecretpepper"), ("totp_secret", "JBSWY3DPEHPK3PXP"),
                          ("recovery_code", "AAAA-BBBB-CCCC")]:
        msg = f'{field}={value}'
        out = _redact(msg)
        assert value not in out, f"{field} leaked"


def test_redacts_pem_private_key_block():
    msg = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCB\n-----END PRIVATE KEY-----"
    out = _redact(msg)
    assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCB" not in out


def test_does_not_over_redact_ordinary_text():
    msg = "GET /health/live -> 200 (1.2ms)"
    assert _redact(msg) == msg


def test_formatter_produces_valid_structured_json():
    import logging

    record = logging.LogRecord(
        name="app", level=logging.INFO, pathname=__file__, lineno=1,
        msg="request completed", args=(), exc_info=None,
    )
    record.correlation_id = "abc-123"
    out = RedactingJsonFormatter().format(record)
    parsed = json.loads(out)
    assert parsed["service"] == "aura-owner"
    assert parsed["severity"] == "INFO"
    assert parsed["correlation_id"] == "abc-123"
    assert set(parsed.keys()) <= {"timestamp", "severity", "service", "message", "correlation_id",
                                   "reason_code", "operator_id", "installation_ref", "license_ref"}


def test_real_request_gets_a_correlation_id_header(client):
    resp = client.get("/health/live")
    assert "X-Correlation-Id" in resp.headers
    assert len(resp.headers["X-Correlation-Id"]) > 0
