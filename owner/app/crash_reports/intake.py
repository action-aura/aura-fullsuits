"""Crash reports -- intake (POST /api/licensing/v1/crash-reports).

An installed product sends the bugs it hit, device-signed. Authentication is NOT
reimplemented: authenticate_device_request() is the same function release
discovery and download authorization use (own nonce scope), so timestamp /
replay / signature / licence rules cannot drift between endpoints.

Validation is all-or-nothing and happens BEFORE authentication (like discovery's
shape check), so a malformed request never burns a nonce. Every field is
type- and length-checked; an unexpected key inside a report or a frame is
dropped, never stored -- Owner keeps only the fields the product is documented
to send.

Idempotence: the product sends its RUNNING TOTAL for each bug, not a delta. The
upsert therefore takes count = max(existing, incoming), so a batch that is
retried after a lost response (or sent twice) does not double-count. first_seen
only ever moves earlier and last_seen only ever later.

Not audit-logged per report: a busy till can send a batch every half hour and an
audit row per report would drown the real events. One log line per request
(installation, how many) instead.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.extensions import db_session
from app.models.crash_reports import CrashReport
from app.releases.distribution import DownloadAuthorizationRejected, authenticate_device_request

logger = logging.getLogger(__name__)

NONCE_SCOPE = "crash_report"
MAX_REPORTS_PER_REQUEST = 20
MAX_MESSAGE_CHARS = 300
MAX_FRAMES = 30
MAX_FIELD_CHARS = 200
MAX_COUNT = 100000
RETENTION_DAYS = 90
VALID_KINDS = frozenset({"server", "thread", "client"})
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{32}$")
_REQUIRED_FIELDS = ("installation_id", "timestamp", "nonce", "signature", "request_id", "reports")


def _invalid() -> DownloadAuthorizationRejected:
    return DownloadAuthorizationRejected("INVALID_REQUEST")


def _text(value, *, max_len: int, min_len: int = 0) -> str:
    if not isinstance(value, str) or not (min_len <= len(value) <= max_len):
        raise _invalid()
    return value


def _timestamp(value) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise _invalid()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _invalid()
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _int(value, *, low: int, high: int) -> int:
    # bool is an int subclass: True must not pass for a count of 1.
    if isinstance(value, bool) or not isinstance(value, int) or not (low <= value <= high):
        raise _invalid()
    return value


def _frame(raw) -> dict:
    if not isinstance(raw, dict):
        raise _invalid()
    return {
        "file": _text(raw.get("file"), max_len=MAX_FIELD_CHARS),
        "function": _text(raw.get("function"), max_len=MAX_FIELD_CHARS),
        "line": _int(raw.get("line"), low=0, high=10_000_000),
    }


def _report(raw) -> dict:
    if not isinstance(raw, dict):
        raise _invalid()
    fingerprint = raw.get("fingerprint")
    if not isinstance(fingerprint, str) or not _FINGERPRINT_RE.match(fingerprint):
        raise _invalid()
    kind = raw.get("kind")
    if kind not in VALID_KINDS:
        raise _invalid()
    frames = raw.get("frames")
    if not isinstance(frames, list) or len(frames) > MAX_FRAMES:
        raise _invalid()
    route = raw.get("route")
    if route is not None:
        route = _text(route, max_len=MAX_FIELD_CHARS)
    first_seen = _timestamp(raw.get("first_seen_at"))
    last_seen = _timestamp(raw.get("last_seen_at"))
    return {
        "fingerprint": fingerprint,
        "kind": kind,
        "exception_type": _text(raw.get("exception_type"), max_len=MAX_FIELD_CHARS, min_len=1),
        "message": _text(raw.get("message"), max_len=MAX_MESSAGE_CHARS),
        "frames": [_frame(f) for f in frames],
        "route": route,
        "app_version": _text(raw.get("app_version"), max_len=64, min_len=1),
        "platform": _text(raw.get("platform"), max_len=32, min_len=1),
        "first_seen_at": min(first_seen, last_seen),
        "last_seen_at": max(first_seen, last_seen),
        "count": _int(raw.get("count"), low=1, high=MAX_COUNT),
    }


def _validate(body) -> list:
    """Returns the cleaned reports, or raises INVALID_REQUEST. The whole
    request is refused if ANY report is invalid: a partial accept would make
    the product mark rows sent that Owner never stored."""
    if not isinstance(body, dict) or not all(k in body for k in _REQUIRED_FIELDS):
        raise _invalid()
    reports = body["reports"]
    if not isinstance(reports, list) or not (1 <= len(reports) <= MAX_REPORTS_PER_REQUEST):
        raise _invalid()
    return [_report(r) for r in reports]


def accept_crash_reports(body: dict, *, config: dict) -> dict:
    """The full 200 response body. Raises DownloadAuthorizationRejected for any
    shape/auth failure (the route maps it to a public reason code)."""
    cleaned = _validate(body)
    installation, license_row = authenticate_device_request(body, nonce_scope=NONCE_SCOPE, config=config)

    product_code = installation.product.product_code
    for report in cleaned:
        statement = pg_insert(CrashReport).values(
            installation_id=installation.id,
            license_id=license_row.id,
            product_code=product_code,
            **report,
        )
        # The conflict target is the unique constraint. The message / frames /
        # exception stay as first received (same fingerprint = same bug);
        # only the running figures move, and only in their one direction.
        statement = statement.on_conflict_do_update(
            constraint="uq_owner_crash_reports_install_fp_version",
            set_={
                "count": func.greatest(CrashReport.count, statement.excluded["count"]),
                "last_seen_at": func.greatest(CrashReport.last_seen_at, statement.excluded["last_seen_at"]),
                "first_seen_at": func.least(CrashReport.first_seen_at, statement.excluded["first_seen_at"]),
                "updated_at": func.now(),
            },
        )
        db_session.execute(statement)
    db_session.commit()
    logger.info("crash reports: installation=%s accepted=%s", installation.id, len(cleaned))
    return {"contract_version": "v1", "result": "ACCEPTED", "accepted": len(cleaned)}


def prune_old_reports(*, now: datetime | None = None, days: int = RETENTION_DAYS, dry_run: bool = True) -> int:
    """Deletes reports not seen for `days` days (last_seen_at). Returns how many
    rows matched; deletes them only when dry_run is False."""
    from datetime import timedelta

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    matched = db_session.query(func.count(CrashReport.id)).filter(CrashReport.last_seen_at < cutoff).scalar() or 0
    if not dry_run and matched:
        db_session.execute(delete(CrashReport).where(CrashReport.last_seen_at < cutoff))
        db_session.commit()
    logger.info("crash reports: prune cutoff=%s matched=%s applied=%s", cutoff.isoformat(), matched, not dry_run)
    return int(matched)
