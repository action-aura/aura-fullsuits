"""Outbound email -- how a queued message actually leaves the till.

Three cases, chosen on every worker tick (never cached), in this order:

    AURA_SMTP_HOST set           -> SmtpTransport: direct SMTP, exactly today's
                                    behaviour, kept for self-hosted / advanced
                                    installs that run their own mailbox
    else Owner URL + activated   -> RelayTransport: ask Owner to send it
                                    (LicensingClient.send_mail, device-signed)
    else                         -> None: NOT_CONFIGURED. Rows stay queued and
                                    the UI says so in plain words

Why a relay at all: Action Aura's mailbox password must never be inside an
installer (anyone could extract it and send mail as Action Aura), so vendor mail
is sent BY OWNER. A till holds no mailbox password. The relay body names a
template KIND plus a few parameters; it carries no subject, no body, no sender
and no credential of any kind -- `relay_payload_for` is the one place that
builds it, and the tests inspect its serialised output for a password.

Rows in email_outbox keep their rendered subject/body (the SMTP path sends
those). The relay needs structured parameters instead, so `relay_payload_for`
derives them from the row: the link out of a verify/reset body, the product name
and quantity out of a low-stock body, the non-empty lines of a report body.
That keeps the table, the producers and every existing test untouched; the cost
is that those three body shapes are now also a (tested) contract.
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from . import smtp_client

log = logging.getLogger("aura.notifications")

#: Account mail (it is not a shop alert): sent whenever a transport is
#: available, whatever the company's notifications toggle says.
IDENTITY_TYPES = frozenset({"verify_email", "password_reset"})

TRANSPORT_SMTP = "smtp"
TRANSPORT_RELAY = "relay"
TRANSPORT_NONE = "none"

_LINK_RE = re.compile(r"https?://[^\s<>\"']+")
_LOW_STOCK_RE = re.compile(r"^(?P<name>.+?) has dropped to (?P<qty>-?[0-9]+(?:\.[0-9]+)?) units on hand", re.M)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# Owner's caps (owner/app/mail/relay_limits.py). Truncating here keeps a long
# report from being refused whole; the numbers must not drift apart.
_NAME_MAX = 120
_LINE_MAX = 200
_SHIFT_LINES_MAX = 40
_REPORT_LINES_MAX = 60


class RelayError(RuntimeError):
    """Base for relay failures. Only the CLASS NAME is ever stored on a row
    (`last_error`), so each failure reason is its own class."""


class RelayNotReadyError(RelayError):
    """Owner URL configured but this install is not activated yet."""


class RelayRefusedError(RelayError):
    """Owner answered, but did not queue the message (cap, invalid, licence)."""


class RelayPayloadError(RelayError):
    """The row cannot be expressed as a relay template (e.g. no link in a
    verification body). Retrying would not change that."""


def _clean_line(text: str, limit: int) -> str:
    return _CONTROL_RE.sub(" ", str(text)).strip()[:limit]


def _body_lines(body_text: str, limit: int) -> list:
    lines = [_clean_line(raw, _LINE_MAX) for raw in (body_text or "").splitlines()]
    return [ln for ln in lines if ln][:limit]


def relay_payload_for(row) -> dict:
    """Row -> {"kind": ..., "params": {...}} for LicensingClient.send_mail.

    Never includes a subject, a body, a sender or any credential."""
    email_type = row["email_type"]
    recipient = row["recipient"]
    body_text = row["body_text"] or ""

    if email_type in IDENTITY_TYPES:
        match = _LINK_RE.search(body_text)
        if not match:
            raise RelayPayloadError("no link in the body")
        return {"kind": email_type, "params": {"email": recipient, "link": match.group(0)}}

    if email_type == "low_stock_alert":
        match = _LOW_STOCK_RE.search(body_text)
        if match:
            return {"kind": "low_stock", "params": {
                "to": recipient,
                "items": [{"name": _clean_line(match.group("name"), _NAME_MAX), "qty": match.group("qty")}],
            }}
    if email_type == "shift_close_report":
        lines = _body_lines(body_text, _SHIFT_LINES_MAX)
        if lines:
            return {"kind": "shift_close", "params": {"to": recipient, "lines": lines}}

    # report_summary, and the safe fallback for any other shape.
    lines = _body_lines(body_text, _REPORT_LINES_MAX)
    if not lines:
        raise RelayPayloadError("nothing to send")
    return {"kind": "report_summary", "params": {"to": recipient, "lines": lines}}


class SmtpTransport:
    name = TRANSPORT_SMTP

    def send(self, row) -> None:
        smtp_client.send_email(
            recipient=row["recipient"], subject=row["subject"],
            body_text=row["body_text"], body_html=row["body_html"],
        )


class RelayTransport:
    name = TRANSPORT_RELAY

    def __init__(
        self,
        *,
        client_factory: Callable[[], object],
        signer_factory: Callable[[], object],
        installation_id_provider: Callable[[], Optional[str]],
        app_version: str,
        platform: str,
    ):
        self._client_factory = client_factory
        self._signer_factory = signer_factory
        self._installation_id_provider = installation_id_provider
        self._app_version = app_version
        self._platform = platform

    def installation_id(self) -> Optional[str]:
        try:
            return self._installation_id_provider() or None
        except Exception:  # noqa: BLE001 -- an unreadable licence file means "not activated", not a crash
            log.warning("mail relay: could not read licence state", exc_info=True)
            return None

    def ready(self) -> bool:
        return self.installation_id() is not None

    def send(self, row) -> None:
        installation_id = self.installation_id()
        if installation_id is None:
            raise RelayNotReadyError("not activated")
        payload = relay_payload_for(row)
        response = self._client_factory().send_mail(
            installation_id=installation_id,
            kind=payload["kind"],
            params=payload["params"],
            signer=self._signer_factory(),
            app_version=self._app_version,
            platform=self._platform,
        )
        if not isinstance(response, dict) or response.get("result") != "QUEUED":
            reason = response.get("reason_code") if isinstance(response, dict) else None
            log.warning("mail relay: Owner did not queue the message (reason=%s)", reason)
            raise RelayRefusedError(str(reason))


def choose_transport(relay: Optional[RelayTransport] = None):
    """The transport for THIS tick, or None when nothing can send right now.

    SMTP wins over the relay: an install that configured its own mailbox keeps
    using it. A relay that is configured but not activated yet is None here --
    the rows wait (they were queued while activation was pending) and go out
    on the first tick after activation."""
    if smtp_client.is_configured():
        return SmtpTransport()
    if relay is not None and relay.ready():
        return relay
    return None


def describe(*, relay_configured: bool, relay: Optional[RelayTransport] = None) -> dict:
    """Plain facts for the status route / UI: which route mail takes and
    whether it can go out right now."""
    if smtp_client.is_configured():
        return {"transport": TRANSPORT_SMTP, "ready": True}
    if relay_configured:
        return {"transport": TRANSPORT_RELAY, "ready": bool(relay is not None and relay.ready())}
    return {"transport": TRANSPORT_NONE, "ready": False}
