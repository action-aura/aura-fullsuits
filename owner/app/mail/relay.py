"""Mail relay -- POST /api/licensing/v1/mail.

A product install asks Owner to send one message. This is NOT an open relay:

  * The body names a TEMPLATE KIND plus parameters. Owner renders the subject
    and body from its own strings (app/mail/templates.py). A request carrying
    its own `subject` / `body` / `html` / `from` / `bcc` -- at the top level or
    inside params -- is refused whole (400), not quietly ignored: an ignored
    field would let a caller believe it had control it does not have.
  * Every string parameter is length-capped; a link must start with http:// or
    https:// (no javascript: / data: / file: link can reach a mail body).
  * Authentication is the same device-signature check release discovery and
    crash reports use (authenticate_device_request, its own nonce scope
    "mail_relay"), so timestamp / replay / signature / licence-state rules
    cannot drift between endpoints.
  * Per-INSTALLATION caps (app/mail/relay_limits.py): 50 messages per rolling
    24 h, at most 10 identity mails (verify_email + password_reset) per 24 h,
    and the same (kind, recipient) at most once per 2 minutes. Over a cap the
    request is refused with 429 MAIL_LIMIT and NOTHING is queued. Counting is
    by installation, so one noisy till never starves another.

Validation is all-or-nothing and runs BEFORE authentication, so a malformed
request never burns a nonce.

Accepted mail is queued (source='relay') and returned as 202 QUEUED; the Owner
worker sends it. With SMTP off here the row simply waits, which is the same
"off, but nothing is lost" contract as every other optional channel.

Not audit-logged per message: a busy install can queue dozens a day and an
audit row each would drown the real events. One log line per request.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from urllib.parse import urlsplit
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text

from app.extensions import db_session
from app.mail import outbox, templates
from app.mail import relay_limits as lim
from app.models.email_outbox import EmailOutbox
from app.releases.distribution import DownloadAuthorizationRejected, authenticate_device_request

logger = logging.getLogger(__name__)

NONCE_SCOPE = "mail_relay"
KINDS = frozenset({"verify_email", "password_reset", "low_stock", "shift_close", "report_summary"})
LANGS = frozenset({"en", "ar"})

_ENVELOPE_KEYS = frozenset({
    "contract_version", "request_id", "correlation_id", "timestamp", "nonce", "installation_id", "signature",
    "app_version", "platform", "kind", "lang", "params",
})
_REQUIRED_FIELDS = ("installation_id", "timestamp", "nonce", "signature", "request_id", "kind", "params")
_PARAM_KEYS = {
    "verify_email": frozenset({"email", "link"}),
    "password_reset": frozenset({"email", "link"}),
    "low_stock": frozenset({"to", "items"}),
    "shift_close": frozenset({"to", "lines"}),
    "report_summary": frozenset({"to", "lines"}),
}
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-']{1,64}@[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,}$")
_BAD_LINK_CHARS = re.compile(r"[\s\x00-\x1f\x7f<>\"'`\\]")


class MailLimitExceeded(Exception):
    """Over a per-installation cap. `which` is for the log line only; the
    response carries the single public code MAIL_LIMIT."""

    def __init__(self, which: str):
        self.which = which
        super().__init__(which)


def _invalid() -> DownloadAuthorizationRejected:
    return DownloadAuthorizationRejected("INVALID_REQUEST")


def _text(value, *, max_len: int, min_len: int = 1) -> str:
    # Control characters (CR/LF/NUL/tab) are refused outright: these strings
    # end up on lines of a mail body, and a line break has no business in a
    # product name or a summary line.
    if not isinstance(value, str) or not (min_len <= len(value) <= max_len) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise _invalid()
    return value


def _email(value) -> str:
    value = _text(value, max_len=lim.EMAIL_MAX).strip()
    if not _EMAIL_RE.match(value):
        raise _invalid()
    return value


def _link_host_is_local(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # any other hostname: refused (see _link)
    return (ip.is_loopback or ip.is_private or ip.is_link_local) and not (ip.is_unspecified or ip.is_multicast)


def _link(value) -> str:
    # Found by adversarial verification 2026-10-10, before go-live: the relay
    # sends from Owner's trusted sender address, so a stolen/abused installation
    # key could mail "reset your password" with a link to ANY site -- phishing
    # with our reputation. The till builds these links from its own request
    # host (commercial_runtime/identity/verification.py), i.e. the shop's own
    # app on loopback or a LAN address, so only those are accepted. A hostname
    # (a machine name) is refused on purpose: the till does not need it to be
    # accepted, and a name is exactly what an attacker would register.
    value = _text(value, max_len=lim.LINK_MAX)
    if not value.lower().startswith(("http://", "https://")) or _BAD_LINK_CHARS.search(value):
        raise _invalid()
    try:
        parts = urlsplit(value)
        host = parts.hostname
        parts.port  # noqa: B018 -- raises ValueError on a malformed port
    except ValueError:
        raise _invalid()
    if "@" in parts.netloc or not host or not _link_host_is_local(host):
        raise _invalid()
    return value


def _qty(value) -> str:
    if isinstance(value, bool):
        raise _invalid()
    if isinstance(value, (int, float)):
        if value != value or value in (float("inf"), float("-inf")):
            raise _invalid()
        return f"{value:g}" if isinstance(value, float) else str(value)
    return _text(value, max_len=lim.QTY_TEXT_MAX)


def _lines(value, *, max_lines: int) -> list:
    if not isinstance(value, list) or not (1 <= len(value) <= max_lines):
        raise _invalid()
    return [_text(v, max_len=lim.LINE_MAX) for v in value]


def validate(body) -> dict:
    """Returns {kind, to, lang, link, lines}; raises INVALID_REQUEST."""
    if not isinstance(body, dict) or not all(k in body for k in _REQUIRED_FIELDS):
        raise _invalid()
    if not set(body) <= _ENVELOPE_KEYS:
        raise _invalid()
    kind = body["kind"]
    if kind not in KINDS:
        raise _invalid()
    lang = body.get("lang", "en")
    if lang not in LANGS:
        raise _invalid()
    params = body["params"]
    if not isinstance(params, dict) or not set(params) <= _PARAM_KEYS[kind] or not _PARAM_KEYS[kind] <= set(params):
        raise _invalid()
    link = None
    lines: list = []
    if kind in lim.IDENTITY_KINDS:
        to = _email(params["email"])
        link = _link(params["link"])
    elif kind == "low_stock":
        to = _email(params["to"])
        items = params["items"]
        if not isinstance(items, list) or not (1 <= len(items) <= lim.MAX_ITEMS):
            raise _invalid()
        for item in items:
            if not isinstance(item, dict) or set(item) != {"name", "qty"}:
                raise _invalid()
            lines.append(f"{_text(item['name'], max_len=lim.NAME_MAX)}: {_qty(item['qty'])}")
    else:
        to = _email(params["to"])
        lines = _lines(params["lines"], max_lines=lim.MAX_SHIFT_LINES if kind == "shift_close" else lim.MAX_REPORT_LINES)
    return {"kind": kind, "to": to, "lang": lang, "link": link, "lines": lines}


def _check_caps(installation_id, *, kind: str, to: str, now: datetime) -> None:
    # One installation's requests are serialised for the duration of this
    # transaction, so two simultaneous requests cannot both read "49 so far".
    db_session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"mail-relay:{installation_id}"})
    day_ago = now - timedelta(hours=24)
    base = select(func.count(EmailOutbox.id)).where(
        EmailOutbox.installation_id == installation_id, EmailOutbox.source == "relay", EmailOutbox.created_at >= day_ago
    )
    if db_session.execute(base).scalar_one() >= lim.TOTAL_PER_DAY:
        raise MailLimitExceeded("total_per_day")
    if kind in lim.IDENTITY_KINDS:
        # Per-installation caps cannot stop one stolen key per victim; a person
        # can only be mailed so many identity mails whoever asks. Counted across
        # ALL installations (found by adversarial verification 2026-10-10).
        # Serialised per recipient so two simultaneous requests cannot both
        # read "4 so far".
        db_session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"mail-relay-to:{to.lower()}"})
        to_count = db_session.execute(
            select(func.count(EmailOutbox.id)).where(
                EmailOutbox.source == "relay",
                EmailOutbox.kind.in_(lim.IDENTITY_KINDS),
                func.lower(EmailOutbox.to_address) == to.lower(),
                EmailOutbox.created_at >= day_ago,
            )
        ).scalar_one()
        if to_count >= lim.IDENTITY_PER_RECIPIENT_PER_DAY:
            raise MailLimitExceeded("identity_per_recipient_per_day")
        identity = db_session.execute(base.where(EmailOutbox.kind.in_(lim.IDENTITY_KINDS))).scalar_one()
        if identity >= lim.IDENTITY_PER_DAY:
            raise MailLimitExceeded("identity_per_day")
    recent = db_session.execute(
        base.where(
            EmailOutbox.kind == kind,
            func.lower(EmailOutbox.to_address) == to.lower(),
            EmailOutbox.created_at >= now - timedelta(seconds=lim.SAME_KIND_TO_SECONDS),
        )
    ).scalar_one()
    if recent:
        raise MailLimitExceeded("same_kind_and_recipient")


def accept_mail(body: dict, *, config: dict) -> dict:
    """The full 202 response body. Raises DownloadAuthorizationRejected for any
    shape/auth failure and MailLimitExceeded over a cap (the route maps them to
    public reason codes)."""
    clean = validate(body)
    installation, _license_row = authenticate_device_request(body, nonce_scope=NONCE_SCOPE, config=config)

    now = datetime.now(timezone.utc)
    _check_caps(installation.id, kind=clean["kind"], to=clean["to"], now=now)

    subject, body_text, body_html = templates.render(
        clean["kind"], lang=clean["lang"], link=clean["link"], lines=clean["lines"],
    )
    row = outbox.enqueue(
        kind=clean["kind"], to_address=clean["to"], subject=subject, body_text=body_text, body_html=body_html,
        source="relay", installation_id=installation.id,
    )
    message_id = str(row.id)
    db_session.commit()
    logger.info("mail relay: installation=%s kind=%s queued=%s", installation.id, clean["kind"], message_id)
    return {"contract_version": "v1", "result": "QUEUED", "message_id": message_id}
