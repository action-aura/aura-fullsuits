"""Phase 9 Milestone 8 -- structured, redacted request logging for Aura Owner.

No structured/redacted logging existed anywhere in owner/app/ before this
phase (only ad-hoc stdlib `logging` calls in a couple of files) -- a real
gap, not a hardening tweak. Mirrors the same redaction-by-substring pattern
already proven in commercial_runtime/licensing_contracts/events.py's
FORBIDDEN_DETAIL_MARKERS (Phase 8), applied here to every log record instead
of only explicit event details.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone

from flask import Flask, g, request

# Same category of value as licensing_contracts/events.py's own
# FORBIDDEN_DETAIL_MARKERS -- anything matching gets replaced with
# "***REDACTED***" wherever it appears in a log message, not just refused
# outright, since log lines are free text (a request path, an exception
# message) rather than a structured dict with named fields to reject.
_REDACTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"AURA-[A-Z0-9]+-\d-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}",  # full license key shape
        r'"?password"?\s*[:=]\s*"?[^"\s,}]+',
        r'"?license_key"?\s*[:=]\s*"?[^"\s,}]+',
        r'"?pepper"?\s*[:=]\s*"?[^"\s,}]+',
        r'"?totp_secret"?\s*[:=]\s*"?[^"\s,}]+',
        r'"?recovery_code"?\s*[:=]\s*"?[^"\s,}]+',
        r'"?signature"?\s*[:=]\s*"?[A-Za-z0-9+/=]{20,}',  # base64-looking signature blob
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    ]
]


def _redact(message: str) -> str:
    for pattern in _REDACTION_PATTERNS:
        message = pattern.sub("***REDACTED***", message)
    return message


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        raw_message = record.getMessage()
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": record.levelname,
            "service": "aura-owner",
            "message": _redact(raw_message),
        }
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id:
            payload["correlation_id"] = correlation_id
        for extra_key in ("reason_code", "operator_id", "installation_ref", "license_ref"):
            value = getattr(record, extra_key, None)
            if value is not None:
                payload[extra_key] = _redact(str(value))
        return json.dumps(payload)


def configure_structured_logging(app: Flask) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingJsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO if app.config.get("ENV") in ("staging", "production") else logging.DEBUG)
    app.logger.handlers = [handler]
    # app.logger otherwise propagates up to the root logger (which also has
    # `handler`) -- without this, every request log line is emitted twice.
    app.logger.propagate = False

    @app.before_request
    def _assign_correlation_id():
        g.correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        g.request_start_time = time.monotonic()

    @app.after_request
    def _log_request(response):
        duration_ms = round((time.monotonic() - g.get("request_start_time", time.monotonic())) * 1000, 1)
        extra = {"correlation_id": g.get("correlation_id")}
        app.logger.info(
            "%s %s -> %s (%sms)", request.method, _redact(request.path), response.status_code, duration_ms,
            extra=extra,
        )
        response.headers["X-Correlation-Id"] = g.get("correlation_id", "")
        return response
