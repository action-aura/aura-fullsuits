"""Outbound WhatsApp -- WhatsAppOutboxWorker: the background job that
actually sends via the WhatsApp Business Cloud API.

Structurally a clone of commercial_runtime/notifications/worker.py's
EmailOutboxWorker -- same daemon threading.Timer start()/stop()/
_schedule_next() shape, same "one worker instance per (installation,
company_id), the CALLER decides whether to ever call start()" contract, same
disabled-gate-before-any-claim placement. See that module's docstring for
the full lineage and reasoning; only the send call and its params/JSON
decoding differ here.
"""
from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import whatsapp_settings
from .whatsapp_outbox import WhatsAppOutboxRepository
from .whatsapp_client import send_whatsapp_template_message

_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 3600


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_backoff_delay(attempt_count: int) -> float:
    """Identical shape to notifications/worker.py's compute_backoff_delay --
    same deliberate small duplication rather than a shared helper; see that
    function's own docstring."""
    delay = min(_BACKOFF_BASE_SECONDS * (2 ** attempt_count), _BACKOFF_CAP_SECONDS)
    return delay * random.uniform(0.8, 1.2)


class WhatsAppOutboxWorker:
    def __init__(
        self,
        *,
        conn_factory: Callable[[], object],
        company_id,
        batch_size: int = 25,
        lease_seconds: int = 120,
        rate_limit_seconds: float = 0.5,
    ):
        self._conn_factory = conn_factory
        self._company_id = company_id
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._rate_limit_seconds = rate_limit_seconds
        self._timer: Optional[threading.Timer] = None
        self._stopped = threading.Event()

    # ─── scheduling (mirrors EmailOutboxWorker) ─────────────────────────

    def start(self, interval_seconds: int) -> None:
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
        conn = self._conn_factory()
        try:
            if not whatsapp_settings.is_enabled(conn, self._company_id):
                # Re-checked every tick, before any claim -- identical
                # placement/reasoning to EmailOutboxWorker.run_once.
                return {'ran': False, 'reason': 'disabled'}

            repo = WhatsAppOutboxRepository(conn)

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

    # ─── per-row send ────────────────────────────────────────────────────

    def _process_row(self, conn, repo: WhatsAppOutboxRepository, row) -> str:
        params = json.loads(row['component_params_json']) if row['component_params_json'] else None
        try:
            wamid = send_whatsapp_template_message(
                recipient_phone_e164=row['recipient_phone_e164'],
                template_name=row['template_name'],
                language_code=row['language_code'],
                component_params=params,
            )
        except Exception as exc:
            return self._apply_retry(conn, repo, row, error=type(exc).__name__)

        repo.mark_sent(row['id'], wamid=wamid)
        return 'sent'

    def _apply_retry(self, conn, repo: WhatsAppOutboxRepository, row, *, error: str) -> str:
        max_attempts = int(whatsapp_settings.get_setting(conn, self._company_id, 'max_attempts'))
        attempt_after = row['attempt_count'] + 1
        if attempt_after >= max_attempts:
            repo.mark_failed_permanent(row['id'], error=error)
            return 'failed_permanent'

        next_attempt_at = (datetime.now(timezone.utc)
                            + timedelta(seconds=compute_backoff_delay(row['attempt_count']))).isoformat()
        repo.mark_retry(row['id'], error=error, next_attempt_at=next_attempt_at)
        return 'retry'
