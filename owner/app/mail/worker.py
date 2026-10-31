"""The background sender for owner_email_outbox.

Same shape as commercial_runtime/notifications/worker.py (daemon
threading.Timer, run_once() for tests, backoff 30 * 2^n capped at an hour with
+/-20% jitter, 8 attempts, 0.5 s pacing between messages) so the two queues are
operated the same way.

Started ONLY by start_worker_if_configured() -- OWNER_SMTP_HOST set AND not
TESTING. With SMTP off no thread exists at all, which is provable by reading
that one function; run_once() re-checks the setting on every tick anyway (an
operator who unsets the host stops sends on the next tick).
"""
from __future__ import annotations

import dataclasses
import logging
import random
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from flask import Flask

from app.extensions import db_session
from app.mail import outbox, smtp, templates
from app.models.email_outbox import EmailOutbox

log = logging.getLogger("aura.owner.mail")

BACKOFF_BASE_SECONDS = 30
BACKOFF_CAP_SECONDS = 3600
MAX_ATTEMPTS = 8
TICK_SECONDS = 15
PACING_SECONDS = 0.5
# Sent on every message: the footer says "do not reply", so a reply that does
# arrive goes to a mailbox a person reads.
REPLY_TO = "support@actionaura.me"


def _sender_for(config, settings: smtp.SmtpSettings, kind: str) -> tuple:
    """(settings, reply_to) for one message. Everything but customer mail goes out
    as before. Customer website-account mail (kinds `customer_*`) may use its own
    sender (OWNER_CUSTOMER_MAIL_FROM, e.g. noreply@actionaura.me) and its Reply-To
    comes from OWNER_CUSTOMER_MAIL_REPLY_TO -- empty means NO Reply-To header,
    never the support@ default, because that mailbox may not exist."""
    if not kind.startswith(templates.CUSTOMER_KIND_PREFIX):
        return settings, REPLY_TO
    sender = (config.get("CUSTOMER_MAIL_FROM") or "").strip()
    if sender:
        settings = dataclasses.replace(settings, from_address=sender)
    return settings, ((config.get("CUSTOMER_MAIL_REPLY_TO") or "").strip() or None)


def compute_backoff_delay(attempt_count: int) -> float:
    delay = min(BACKOFF_BASE_SECONDS * (2 ** attempt_count), BACKOFF_CAP_SECONDS)
    return delay * random.uniform(0.8, 1.2)


class EmailWorker:
    def __init__(self, app: Flask, *, ssl_context: Optional[ssl.SSLContext] = None, batch_size: int = 25,
                 lease_seconds: int = 120, pacing_seconds: float = PACING_SECONDS, max_attempts: int = MAX_ATTEMPTS):
        self._app = app
        self._ssl_context = ssl_context
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._pacing_seconds = pacing_seconds
        self._max_attempts = max_attempts
        self._timer: Optional[threading.Timer] = None
        self._stopped = threading.Event()

    # -- scheduling ----------------------------------------------------
    def start(self, interval_seconds: float = TICK_SECONDS) -> None:
        self._stopped.clear()
        self._schedule_next(interval_seconds)

    def stop(self) -> None:
        self._stopped.set()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _schedule_next(self, interval_seconds: float) -> None:
        if self._stopped.is_set():
            return

        def _tick():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 -- a dead timer chain would stop all mail for good
                log.exception("owner mail: worker tick crashed")
            finally:
                self._schedule_next(interval_seconds)

        self._timer = threading.Timer(interval_seconds, _tick)
        self._timer.daemon = True
        self._timer.start()

    # -- one pass ------------------------------------------------------
    def run_once(self) -> dict:
        with self._app.app_context():
            try:
                settings = smtp.settings_from_config(self._app.config)
                if settings is None:
                    # Decided and logged, not silent: "never started" and "ran
                    # and declined" look identical in a log otherwise.
                    log.info("owner mail: tick skipped -- OWNER_SMTP_HOST is not set")
                    return {"ran": False, "reason": "smtp_not_configured"}
                reclaimed = outbox.reclaim_expired_leases()
                claimed = outbox.claim_due(batch_size=self._batch_size, lease_seconds=self._lease_seconds)
                outcomes = {"sent": 0, "retry": 0, "failed_permanent": 0}
                for i, row_id in enumerate(claimed):
                    outcome = self._process_row(settings, row_id)
                    outcomes[outcome] += 1
                    if self._pacing_seconds and i < len(claimed) - 1:
                        time.sleep(self._pacing_seconds)
                if claimed:
                    log.info("owner mail: tick claimed=%s outcomes=%s", len(claimed), outcomes)
                return {"ran": True, "claimed": len(claimed), "reclaimed": reclaimed, "outcomes": outcomes}
            finally:
                db_session.remove()

    def _process_row(self, settings: smtp.SmtpSettings, row_id) -> str:
        row = db_session.get(EmailOutbox, row_id)
        to, subject, text, html, attempts = row.to_address, row.subject, row.body_text, row.body_html, row.attempt_count
        try:
            send_settings, reply_to = _sender_for(self._app.config, settings, row.kind)
            smtp.send_message(send_settings, to=to, subject=subject, body_text=text, body_html=html,
                              reply_to=reply_to, ssl_context=self._ssl_context)
        except Exception as exc:  # noqa: BLE001 -- any transport failure is a retry decision
            # Class name only: an SMTP error text can echo the recipient or the login.
            error = type(exc).__name__
            db_session.rollback()
            if attempts + 1 >= self._max_attempts:
                outbox.mark_failed_permanent(row_id, error=error)
                outcome = "failed_permanent"
            else:
                outbox.mark_retry(row_id, error=error, next_attempt_at=datetime.now(timezone.utc)
                                  + timedelta(seconds=compute_backoff_delay(attempts)))
                outcome = "retry"
            db_session.commit()
            log.warning("owner mail: send failed (%s); outcome=%s", error, outcome)
            return outcome
        outbox.mark_sent(row_id)
        db_session.commit()
        return "sent"


def start_worker_if_configured(app: Flask) -> Optional[EmailWorker]:
    """The ONLY place a worker thread is ever started. No SMTP host, or a test
    app: returns None and nothing runs."""
    if app.config.get("TESTING"):
        return None
    if smtp.settings_from_config(app.config) is None:
        return None
    worker = EmailWorker(app)
    worker.start()
    app.extensions["owner_mail_worker"] = worker
    log.info("owner mail: worker started (tick every %ss)", TICK_SECONDS)
    return worker
