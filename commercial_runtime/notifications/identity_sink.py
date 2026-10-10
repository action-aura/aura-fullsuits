"""Outbound email -- the queue that account mail (verification, password reset)
goes into instead of being sent inline.

Before the real-email wave, commercial_runtime/identity/verification.py called
SMTP synchronously: a slow relay stalled the sign-up request, a failure was
silent, and on a desktop it ran BEFORE licence activation (no installation id,
no device signer yet), so there was nothing it could ever have relayed through.
Now verification.py hands the finished message to a "mail sink" and returns; the
notifications worker sends it on its next tick, whenever a transport exists
(see worker.py: account mail ignores the company's notifications toggle).

The identity package must not import a product, so the product registers this
sink (verification.set_mail_sink) at boot. Clinic never does, and keeps the
original inline send.
"""
from __future__ import annotations

import logging
from typing import Callable

from .outbox import EmailOutboxRepository

log = logging.getLogger("aura.notifications")


class IdentityMailSink:
    def __init__(self, *, conn_factory: Callable[[], object],
                 possible: Callable[[], bool], on_queued: Callable[[object], None]):
        self._conn_factory = conn_factory
        self._possible = possible
        self._on_queued = on_queued

    def possible(self) -> bool:
        """Can mail ever leave this install: own SMTP, or an Owner URL to relay
        through. NOT "is it activated yet" -- mail queued before activation
        waits and goes out after it."""
        return bool(self._possible())

    def enqueue(self, *, company_id, email_type: str, recipient: str, subject: str,
                body_text: str, body_html: str) -> bool:
        if not self.possible():
            return False
        conn = self._conn_factory()
        try:
            EmailOutboxRepository(conn).enqueue(
                company_id=company_id, email_type=email_type, recipient=recipient,
                subject=subject, body_text=body_text, body_html=body_html,
            )
            conn.commit()
        finally:
            conn.close()
        try:
            self._on_queued(company_id)
        except Exception:  # noqa: BLE001 -- the row is durable; the next boot resumes it
            log.warning("account mail queued but its worker could not be started", exc_info=True)
        return True
