"""Customer account mail: links and queueing.

Links are built from OWNER_CUSTOMER_PORTAL_BASE_URL only -- never from the request
Host, which an attacker can forge -- and the token travels in the URL FRAGMENT
(`#t=...`): a fragment is never sent to any server, so it cannot land in the
website's access logs, a CDN log or a Referer header.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from flask import current_app

from app.extensions import db_session
from app.mail import outbox, templates
from app.mail.invitations import email_is_set_up

logger = logging.getLogger(__name__)

_PATH_FOR_KIND = {
    "INVITE": "account/set-password/",
    "RESET": "account/set-password/",
    "EMAIL_CHANGE": "account/confirm-email/",
}


class PortalUrlNotSet(RuntimeError):
    pass


def build_link(token_kind: str, raw_token: str, lang: str) -> str:
    base = (current_app.config.get("CUSTOMER_PORTAL_BASE_URL") or "").strip().rstrip("/")
    if not base:
        raise PortalUrlNotSet("OWNER_CUSTOMER_PORTAL_BASE_URL is not set")
    lang = lang if lang in ("en", "ar") else "en"
    return f"{base}/{lang}/{_PATH_FOR_KIND[token_kind]}#t={quote(raw_token, safe='')}"


def queue(kind: str, to_address: str, *, lang: str, link: str | None = None, lines: list | None = None) -> bool:
    """True when a message was queued. False when email is off here or queueing
    failed (logged, never raised): what the caller shows must not depend on it."""
    if not email_is_set_up():
        return False
    try:
        subject, body_text, body_html = templates.render(kind, lang=lang, link=link, lines=lines)
        outbox.enqueue(kind=kind, to_address=to_address, subject=subject, body_text=body_text,
                       body_html=body_html, source="owner")
        db_session.commit()
        return True
    except Exception:  # noqa: BLE001
        db_session.rollback()
        logger.warning("customer account mail (%s) could not be queued", kind, exc_info=True)
        return False


def mask_email(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"
