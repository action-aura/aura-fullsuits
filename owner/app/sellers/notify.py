"""Emails the seller rules send, through the one Owner mail queue (app/mail/outbox.py).

Queued, never sent here: the existing worker sends them. With email off (no OWNER_SMTP_HOST) nothing
is queued and the caller is told, so a rule never claims "the seller was emailed" when they were not.
The caller commits (the queued row rides in the caller's transaction)."""
from __future__ import annotations

import logging

from app.mail import invitations, outbox, templates

log = logging.getLogger(__name__)


def queue(kind: str, staff, lines: list[str]) -> bool:
    """True when one message was queued for this staff member."""
    if staff is None or not invitations.email_is_set_up():
        return False
    lang = staff.locale if getattr(staff, "locale", None) in ("en", "ar") else "en"
    subject, body_text, body_html = templates.render(kind, lang=lang, lines=lines)
    outbox.enqueue(kind=kind, to_address=staff.email, subject=subject, body_text=body_text, body_html=body_html, source="owner")
    return True
