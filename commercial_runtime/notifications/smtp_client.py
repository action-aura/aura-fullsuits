"""Outbound email -- SMTP transport, stdlib only (`smtplib` + `email.mime`).

No new dependency: the task this module exists for explicitly rules out a
heavy templating engine or a third-party mail-sending library, and stdlib
`smtplib` is a complete-enough SMTP/STARTTLS client for a single-recipient
transactional email (low-stock alert, on-demand report, verification code) --
there is no bulk-sending, list-management, or bounce-handling need here.

Configured ENTIRELY via env vars, all empty-by-default:

    AURA_SMTP_HOST          -- unset by default. This is the single gate
                               that keeps the whole notifications package
                               inert (see settings.py::is_enabled) --
                               matching OWNER_LICENSING_BASE_URL's
                               documented "unset == not enforced at all"
                               convention, deliberately NOT the AI-assistant
                               endpoint's real-shared-default pattern used
                               elsewhere in this repo. There is no safe
                               shared demo credential for somebody else's
                               SMTP relay the way there is for an
                               Anthropic-hosted AI endpoint, so this must
                               default to nothing, not to something that
                               quietly works for the wrong install.
    AURA_SMTP_PORT          -- default '587' (STARTTLS submission) once a
                               host is set.
    AURA_SMTP_USER
    AURA_SMTP_PASSWORD
    AURA_SMTP_FROM_ADDRESS  -- falls back to AURA_SMTP_USER if unset (most
                               SMTP relays require the two to match anyway).
    AURA_SMTP_USE_TLS       -- default '1' (STARTTLS). Set to '0' only for
                               a local debug relay that has no TLS support
                               (e.g. this module's own test fixture).

Read fresh on every call, not cached at import time -- an operator can set
these in the OS environment and restart the process (or, on the packaged
desktop app, edit a launcher env file) without a code change, matching every
other env-var-gated flag in this codebase (AURA_EINVOICING_DISABLED,
OWNER_LICENSING_BASE_URL).
"""
from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

_DEFAULT_PORT = 587
_DEFAULT_TIMEOUT_SECONDS = 15.0


class SmtpNotConfiguredError(RuntimeError):
    """Raised by send_email() when called with no AURA_SMTP_HOST set and no
    explicit config override -- callers (worker.py) must never let this
    escape as an unhandled crash; it is the expected, common-case outcome on
    any install that hasn't opted in, and worker.py's run_once() gate
    (settings.is_enabled()) is what should normally prevent this from ever
    firing in practice."""


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    from_address: str
    use_tls: bool


def load_config_from_env() -> Optional[SmtpConfig]:
    """Returns None -- not a config with empty fields -- when AURA_SMTP_HOST
    is unset/blank. That None is the load-bearing signal is_configured() and
    settings.is_enabled() both key off; never change this to return a
    "disabled" SmtpConfig instead, or every caller that checks `is None`
    silently breaks."""
    host = os.environ.get('AURA_SMTP_HOST', '').strip()
    if not host:
        return None
    username = os.environ.get('AURA_SMTP_USER', '').strip()
    from_address = os.environ.get('AURA_SMTP_FROM_ADDRESS', '').strip() or username
    try:
        port = int(os.environ.get('AURA_SMTP_PORT', '').strip() or _DEFAULT_PORT)
    except ValueError:
        port = _DEFAULT_PORT
    return SmtpConfig(
        host=host,
        port=port,
        username=username,
        password=os.environ.get('AURA_SMTP_PASSWORD', ''),
        from_address=from_address,
        use_tls=os.environ.get('AURA_SMTP_USE_TLS', '1').strip() != '0',
    )


def is_configured() -> bool:
    return load_config_from_env() is not None


def build_message(*, recipient: str, subject: str, body_text: str,
                   body_html: Optional[str], from_address: str):
    if body_html:
        msg = MIMEMultipart('alternative')
        msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))
    else:
        msg = MIMEText(body_text, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = from_address
    msg['To'] = recipient
    return msg


def send_email(
    *,
    recipient: str,
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
    config: Optional[SmtpConfig] = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Raises on any failure (SmtpNotConfiguredError, smtplib.SMTPException,
    OSError/socket errors, ...) -- never returns a sentinel/False. The
    caller (worker.py::_process_row) is the single place that catches this
    and translates it into a retry/failed-permanent outbox transition,
    mirroring einvoicing/worker.py's treatment of
    provider.submit_invoice() raising."""
    cfg = config or load_config_from_env()
    if cfg is None:
        raise SmtpNotConfiguredError(
            "AURA_SMTP_HOST is not set -- outbound email is not configured on this installation."
        )

    msg = build_message(recipient=recipient, subject=subject, body_text=body_text,
                         body_html=body_html, from_address=cfg.from_address)

    with smtplib.SMTP(cfg.host, cfg.port, timeout=timeout_seconds) as client:
        client.ehlo()
        if cfg.use_tls:
            client.starttls()
            client.ehlo()
        if cfg.username:
            client.login(cfg.username, cfg.password)
        client.sendmail(cfg.from_address, [recipient], msg.as_string())
