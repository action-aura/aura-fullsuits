"""The notices a seller receives by email. Same contract as app/mail/invitations.py: True when a
message was queued, False when email is off on this server or queueing failed (logged, never
raised) -- the case page inside Aura Owner is the notice that counts, the mail is a courtesy."""
from __future__ import annotations

import logging

from app.extensions import db_session
from app.mail import outbox, templates
from app.mail.invitations import email_is_set_up

logger = logging.getLogger(__name__)


def queue_to_staff(staff, kind: str, lines: list[str]) -> bool:
    if staff is None or not email_is_set_up():
        return False
    try:
        subject, body_text, body_html = templates.render(kind, lang=(getattr(staff, "locale", None) or "en"), lines=lines)
        outbox.enqueue(kind=kind, to_address=staff.email, subject=subject, body_text=body_text, body_html=body_html,
                       source="owner")
        db_session.commit()
        return True
    except Exception:  # noqa: BLE001 -- a mail problem must never undo a decision that is already recorded
        db_session.rollback()
        logger.warning("seller discipline mail %s could not be queued", kind, exc_info=True)
        return False
