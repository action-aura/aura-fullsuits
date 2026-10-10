"""Outbound email -- EmailOutboxWorker: the background job that actually
sends via SMTP.

Structurally a clone of commercial_runtime/einvoicing/worker.py's
OutboxWorker, which is itself a clone of
commercial_runtime/licensing_contracts/checkin_scheduler.py's
LicenseCheckInScheduler -- see that module's docstring for the full lineage.
Same daemon threading.Timer start()/stop()/_schedule_next() shape, same
"one worker instance per (installation, company_id), the CALLER decides
whether to ever call start()" contract: this class never checks `enabled`
before starting a thread, so "no thread exists when the feature is off" stays
trivially provable by reading one call site (products/*/backend/app.py),
not by trusting this class's internals.

Unlike OutboxWorker, there is no injected `document_builder` -- the queued
row already carries its finished subject/body (see schema.py's own
docstring for why), so there is nothing product-specific for this class to
call back into at send time. That makes this worker fully product-agnostic
with zero injected callables beyond `conn_factory`.
"""
from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import settings
from .outbox import EmailOutboxRepository
from .smtp_client import send_email
from .transport import IDENTITY_TYPES

_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 3600


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_backoff_delay(attempt_count: int) -> float:
    """Exponential backoff with +/-20% jitter, capped at one hour --
    identical shape to einvoicing/worker.py's compute_backoff_delay (a
    deliberate small duplication rather than a shared cross-module helper:
    these two modules have no other coupling, and importing one outbox
    worker's backoff math into an unrelated domain would be a strange,
    surprising dependency for a three-line function to justify)."""
    delay = min(_BACKOFF_BASE_SECONDS * (2 ** attempt_count), _BACKOFF_CAP_SECONDS)
    return delay * random.uniform(0.8, 1.2)


class EmailOutboxWorker:
    def __init__(
        self,
        *,
        conn_factory: Callable[[], object],
        company_id,
        batch_size: int = 25,
        lease_seconds: int = 120,
        rate_limit_seconds: float = 0.5,
        transport_factory: Optional[Callable[[], object]] = None,
    ):
        # `transport_factory` (real-email wave) returns the transport for THIS
        # tick -- direct SMTP, a relay through Owner, or None -- see
        # transport.py::choose_transport. None here (the default, and what
        # Clinic and every older test pass) keeps the original behaviour
        # exactly: direct SMTP, gated by settings.is_enabled().
        self._transport_factory = transport_factory
        self._conn_factory = conn_factory
        self._company_id = company_id
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._rate_limit_seconds = rate_limit_seconds
        self._timer: Optional[threading.Timer] = None
        self._stopped = threading.Event()

    # ─── scheduling (mirrors LicenseCheckInScheduler / einvoicing's OutboxWorker) ──

    def start(self, interval_seconds: int) -> None:
        # Idempotent: a second start() (boot resume AND an account-mail
        # enqueue both asking for this company's worker) replaces the timer
        # chain instead of leaving the first one running beside it.
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._stopped.clear()
        self._schedule_next(interval_seconds)

    def stop(self) -> None:
        self._stopped.set()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _schedule_next(self, interval_seconds: int) -> None:
        if self._stopped.is_set():
            return

        def _tick():
            try:
                self.run_once()
            finally:
                self._schedule_next(interval_seconds)

        self._timer = threading.Timer(interval_seconds, _tick)
        self._timer.daemon = True
        self._timer.start()

    # ─── one pass ───────────────────────────────────────────────────────

    def run_once(self) -> dict:
        if self._transport_factory is not None:
            return self._run_once_with_transport()
        conn = self._conn_factory()
        try:
            if not settings.is_enabled(conn, self._company_id):
                # Gate re-checked on every tick, not cached at start() time --
                # an operator unsetting AURA_SMTP_HOST (or flipping this
                # company's `enabled` setting off) must stop this worker
                # from touching due rows on its very next tick, mirroring
                # einvoicing/worker.py::run_once's identical disabled-gate
                # placement (before any claim, so a disabled tick cannot
                # even move a row into SENDING).
                return {'ran': False, 'reason': 'disabled'}

            repo = EmailOutboxRepository(conn)

            reclaimed = repo.reclaim_expired_leases()
            conn.commit()

            claimed = repo.claim_due(
                company_id=self._company_id, batch_size=self._batch_size, lease_seconds=self._lease_seconds,
            )
            conn.commit()

            outcomes = {'sent': 0, 'retry': 0, 'failed_permanent': 0}
            for i, row in enumerate(claimed):
                outcome = self._process_row(conn, repo, row)
                conn.commit()
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
                if self._rate_limit_seconds and i < len(claimed) - 1:
                    time.sleep(self._rate_limit_seconds)

            return {'ran': True, 'claimed': len(claimed), 'reclaimed': reclaimed, 'outcomes': outcomes}
        finally:
            conn.close()

    def _run_once_with_transport(self) -> dict:
        """The real-email path: pick the transport for this tick, send account
        mail whenever one exists, shop alerts only when the company opted in.

        Decided and returned, never silent: "no transport yet" (an install
        waiting for activation) and "ran and sent" must be told apart by the
        caller and by a log reader."""
        transport = self._transport_factory()
        if transport is None:
            return {'ran': False, 'reason': 'no_transport'}
        conn = self._conn_factory()
        try:
            repo = EmailOutboxRepository(conn)
            reclaimed = repo.reclaim_expired_leases()
            conn.commit()

            # Account mail is not a shop alert: it ignores the notifications
            # toggle (a brand-new shop has never touched it, and the first
            # thing it needs is its verification link).
            claimed = repo.claim_due(
                company_id=self._company_id, batch_size=self._batch_size,
                lease_seconds=self._lease_seconds, only_types=IDENTITY_TYPES,
            )
            if settings.get_setting(conn, self._company_id, 'enabled') == '1':
                claimed += repo.claim_due(
                    company_id=self._company_id, batch_size=self._batch_size,
                    lease_seconds=self._lease_seconds, exclude_types=IDENTITY_TYPES,
                )
            conn.commit()

            outcomes = {'sent': 0, 'retry': 0, 'failed_permanent': 0}
            for i, row in enumerate(claimed):
                outcome = self._process_row(conn, repo, row, transport=transport)
                conn.commit()
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
                if self._rate_limit_seconds and i < len(claimed) - 1:
                    time.sleep(self._rate_limit_seconds)

            return {'ran': True, 'claimed': len(claimed), 'reclaimed': reclaimed,
                    'outcomes': outcomes, 'transport': getattr(transport, 'name', None)}
        finally:
            conn.close()

    # ─── per-row send ────────────────────────────────────────────────────

    def _process_row(self, conn, repo: EmailOutboxRepository, row, transport=None) -> str:
        try:
            if transport is not None:
                transport.send(row)
            else:
                send_email(
                    recipient=row['recipient'], subject=row['subject'],
                    body_text=row['body_text'], body_html=row['body_html'],
                )
        except Exception as exc:
            return self._apply_retry(conn, repo, row, error=type(exc).__name__)

        repo.mark_sent(row['id'])
        return 'sent'

    def _apply_retry(self, conn, repo: EmailOutboxRepository, row, *, error: str) -> str:
        max_attempts = int(settings.get_setting(conn, self._company_id, 'max_attempts'))
        attempt_after = row['attempt_count'] + 1
        if attempt_after >= max_attempts:
            repo.mark_failed_permanent(row['id'], error=error)
            return 'failed_permanent'

        next_attempt_at = (datetime.now(timezone.utc)
                            + timedelta(seconds=compute_backoff_delay(row['attempt_count']))).isoformat()
        repo.mark_retry(row['id'], error=error, next_attempt_at=next_attempt_at)
        return 'retry'
