"""Invitation mail: a staff / employee invitation link, emailed when this
server can send email.

The link is ALWAYS still shown once on the invitation page, whether or not a
mail was queued, so a mail failure (or email being off) can never lock anyone
out of an account they were just invited to. The page uses the return value to
say "sent to <email>" instead of "send it manually".
"""
from __future__ import annotations

import logging

from flask import current_app

from app.extensions import db_session
from app.mail import outbox, smtp, templates

logger = logging.getLogger(__name__)


def email_is_set_up() -> bool:
    return smtp.settings_from_config(current_app.config) is not None


def queue_staff_password_reset_email(to_address: str, link: str) -> bool:
    """Same contract as queue_invitation_email: True when queued, never raises.
    Only called after the caller has decided the account is real and active;
    what the person sees does not depend on this result."""
    if not email_is_set_up():
        return False
    try:
        subject, body_text, body_html = templates.render("staff_password_reset", link=link)
        outbox.enqueue(kind="staff_password_reset", to_address=to_address, subject=subject,
                       body_text=body_text, body_html=body_html, source="owner")
        db_session.commit()
        return True
    except Exception:  # noqa: BLE001
        db_session.rollback()
        logger.warning("password reset mail could not be queued", exc_info=True)
        return False


def queue_invitation_email(invitation, link: str) -> bool:
    """True when a message was queued. False when email is off here, or when
    queueing failed (logged, never raised: the invitation already exists)."""
    if not email_is_set_up():
        return False
    try:
        subject, body_text, body_html = templates.render("staff_invitation", link=link)
        outbox.enqueue(kind="staff_invitation", to_address=invitation.email, subject=subject,
                       body_text=body_text, body_html=body_html, source="owner")
        db_session.commit()
        return True
    except Exception:  # noqa: BLE001
        db_session.rollback()
        logger.warning("invitation mail could not be queued; the link is still shown on the page", exc_info=True)
        return False
