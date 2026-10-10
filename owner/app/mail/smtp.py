"""Owner's SMTP transport -- stdlib smtplib, no new dependency.

Configured only from OWNER_SMTP_* (app/config.py): HOST (unset == email off),
PORT (587), USER, PASSWORD, FROM (default USER), FROM_NAME ("Action Aura"),
USE_TLS (1 == STARTTLS). The password lives in this server's environment and in
no other place: not the database, not a log line, not a queue row, not a
response body.

STARTTLS verifies the server certificate by default
(ssl.create_default_context()). A caller may pass its own context -- the tests
do, to trust a self-signed fake server -- but nothing in production does.
"""
from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import Mapping, Optional

DEFAULT_PORT = 587
DEFAULT_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    user: str
    password: str
    from_address: str
    from_name: str
    use_tls: bool


def settings_from_config(config: Mapping) -> Optional[SmtpSettings]:
    """None -- not a half-filled settings object -- when OWNER_SMTP_HOST is
    unset/blank. That None is the single "email is off" signal every caller
    keys off (worker, CLI, invitation page); never return a "disabled"
    SmtpSettings instead."""
    host = (config.get("SMTP_HOST") or "").strip()
    if not host:
        return None
    user = (config.get("SMTP_USER") or "").strip()
    try:
        port = int(config.get("SMTP_PORT") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return SmtpSettings(
        host=host,
        port=port,
        user=user,
        password=config.get("SMTP_PASSWORD") or "",
        from_address=(config.get("SMTP_FROM") or "").strip() or user,
        from_name=(config.get("SMTP_FROM_NAME") or "").strip() or "Action Aura",
        use_tls=bool(config.get("SMTP_USE_TLS", True)),
    )


def build_message(settings: SmtpSettings, *, to: str, subject: str, body_text: str,
                  body_html: Optional[str] = None, reply_to: Optional[str] = None):
    if body_html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))
    else:
        msg = MIMEText(body_text, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.from_name, settings.from_address))
    msg["To"] = to
    msg["Date"] = formatdate(localtime=False)
    # A Message-ID on the sender's own domain is one of the small things
    # receiving mail servers look for.
    msg["Message-ID"] = make_msgid(domain=settings.from_address.rpartition("@")[2] or None)
    if reply_to:
        msg["Reply-To"] = reply_to
    return msg


def send_message(settings: SmtpSettings, *, to: str, subject: str, body_text: str,
                 body_html: Optional[str] = None, reply_to: Optional[str] = None,
                 ssl_context: Optional[ssl.SSLContext] = None,
                 timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> tuple:
    """Sends one message. Returns (code, text) of the server's final reply to
    DATA (the "250 ... queued as ..." line `flask email send-test` prints).
    Raises on every failure -- never returns a sentinel; the worker is the one
    place that turns an exception into a retry."""
    msg = build_message(settings, to=to, subject=subject, body_text=body_text,
                        body_html=body_html, reply_to=reply_to)
    with smtplib.SMTP(settings.host, settings.port, timeout=timeout_seconds) as client:
        client.ehlo()
        if settings.use_tls:
            client.starttls(context=ssl_context or ssl.create_default_context())
            client.ehlo()
        if settings.user:
            client.login(settings.user, settings.password)
        code, resp = client.mail(settings.from_address)
        if code != 250:
            raise smtplib.SMTPSenderRefused(code, resp, settings.from_address)
        code, resp = client.rcpt(to)
        if code not in (250, 251):
            raise smtplib.SMTPRecipientsRefused({to: (code, resp)})
        code, resp = client.data(msg.as_string())
        if code != 250:
            raise smtplib.SMTPDataError(code, resp)
        return code, resp.decode("utf-8", errors="replace")
