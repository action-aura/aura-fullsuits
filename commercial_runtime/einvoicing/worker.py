"""JoFotara e-invoicing -- OutboxWorker: the background job that actually
talks to the configured provider.

Structurally a clone of
commercial_runtime/licensing_contracts/checkin_scheduler.py's
LicenseCheckInScheduler: daemon threading.Timer, start()/stop()/
_schedule_next(). One worker instance per installation (matching that
module's per-installation, not per-company, shape), scoped to a single
`company_id` -- the install's own company. The CALLER (products/*/backend/
app.py, Step 13/14) decides whether to call start() at all; this class does
not check `enabled` itself before starting a thread, exactly like
LicenseCheckInScheduler doesn't -- that decision belongs at the call site so
"no thread exists when the feature is off" is trivially provable by reading
one call site, not by trusting this class's internals.

Everything product-specific is injected, never imported: `document_builder`
turns an einvoice_outbox row into an EInvoiceDocument by reading that
product's own sale/invoice tables, and `reconcile_fn` finds
sales/invoices that should have been enqueued but weren't (e.g. the process
crashed between the sale commit and the enqueue call) and enqueues them.
Both are supplied by products/*/backend/core/*/einvoice_adapter.py in
Step 13/14. This module is fully testable today (Step 11) against fakes.
"""
from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import audit, settings
from .outbox import OutboxRepository
from .ubl import document_sha256, to_ubl_xml

_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 3600


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_backoff_delay(attempt_count: int) -> float:
    """Exponential backoff with +/-20% jitter, capped at one hour. attempt_count
    is the count BEFORE this attempt (0 for the first retry)."""
    delay = min(_BACKOFF_BASE_SECONDS * (2 ** attempt_count), _BACKOFF_CAP_SECONDS)
    return delay * random.uniform(0.8, 1.2)


class OutboxWorker:
    def __init__(
        self,
        *,
        conn_factory: Callable[[], object],
        app_data_dir: str,
        company_id: int,
        provider,
        document_builder: Callable[[object, object], object],
        reconcile_fn: Optional[Callable[[object, int, Optional[str]], None]] = None,
        batch_size: int = 25,
        lease_seconds: int = 120,
        rate_limit_seconds: float = 2.0,
    ):
        self._conn_factory = conn_factory
        self._app_data_dir = app_data_dir
        self._company_id = company_id
        self._provider = provider
        self._document_builder = document_builder
        self._reconcile_fn = reconcile_fn
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._rate_limit_seconds = rate_limit_seconds
        self._timer: Optional[threading.Timer] = None
        self._stopped = threading.Event()

    # ─── scheduling (mirrors LicenseCheckInScheduler) ──────────────────

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
            if not settings.is_enabled(conn, self._app_data_dir, self._company_id):
                return {'ran': False, 'reason': 'disabled'}

            if self._reconcile_fn:
                self._reconcile_fn(conn, self._company_id, settings.enabled_at(conn, self._company_id))
                conn.commit()

            repo = OutboxRepository(conn)

            reclaimed = repo.reclaim_expired_leases()
            conn.commit()
            for row in reclaimed:
                self._resolve_reclaimed(conn, repo, row)
                conn.commit()

            claimed = repo.claim_due(batch_size=self._batch_size, lease_seconds=self._lease_seconds)
            conn.commit()

            outcomes = {'cleared': 0, 'rejected': 0, 'retry': 0, 'pending': 0, 'unknown': 0}
            for i, row in enumerate(claimed):
                outcome = self._process_row(conn, repo, row)
                conn.commit()
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
                if self._rate_limit_seconds and i < len(claimed) - 1:
                    time.sleep(self._rate_limit_seconds)

            return {'ran': True, 'claimed': len(claimed), 'reclaimed': len(reclaimed), 'outcomes': outcomes}
        finally:
            conn.close()

    # ─── per-row submission ─────────────────────────────────────────────

    def _process_row(self, conn, repo: OutboxRepository, row) -> str:
        # Building the document reads the product's own source row (a sale/
        # invoice) via a product-supplied callback -- e.g. a walk-in sale
        # with no customer on file has no buyer TIN/NIN/PN, which
        # document.py's require_buyer_id() correctly raises on. That must
        # degrade this ONE row to a retry (and eventually
        # FAILED_PERMANENT, never an infinite loop), not crash the rest of
        # this pass -- claimed rows after this one in the batch must still
        # get processed.
        try:
            document = self._document_builder(conn, row)
            xml = to_ubl_xml(document)
            sha = document_sha256(xml)
        except Exception as exc:
            audit.record(conn, self._app_data_dir, company_id=self._company_id, event='PROVIDER_ERROR',
                         invoice_ref=row['invoice_ref'], details={'exception_type': type(exc).__name__, 'phase': 'document_build'})
            return self._apply_retry(conn, repo, row, reason_code='DOCUMENT_BUILD_FAILED', detail=type(exc).__name__)

        conn.execute(
            "UPDATE einvoice_outbox SET document_xml=?, document_sha256=?, updated_at=? WHERE id=?",
            (xml, sha, _now_iso(), row['id']),
        )
        audit.record(conn, self._app_data_dir, company_id=self._company_id, event='SUBMIT_STARTED',
                     invoice_ref=row['invoice_ref'], attempt_no=row['attempt_count'] + 1, provider=self._provider.name)

        try:
            result = self._provider.submit_invoice(row['invoice_ref'], document)
        except Exception as exc:
            audit.record(conn, self._app_data_dir, company_id=self._company_id, event='PROVIDER_ERROR',
                         invoice_ref=row['invoice_ref'], provider=self._provider.name,
                         details={'exception_type': type(exc).__name__})
            return self._apply_retry(conn, repo, row, reason_code='PROVIDER_EXCEPTION',
                                      detail=type(exc).__name__)

        return self._apply_result(conn, repo, row, result)

    def _apply_result(self, conn, repo: OutboxRepository, row, result) -> str:
        if result.outcome == 'CLEARED':
            repo.mark_cleared(row['id'], provider_uuid=result.provider_uuid,
                               qr_payload=result.qr_payload, qr_image_base64=result.qr_image_base64)
            audit.record(conn, self._app_data_dir, company_id=self._company_id, event='SUBMIT_CLEARED',
                         invoice_ref=row['invoice_ref'], outcome='CLEARED', reason_code=result.reason_code,
                         provider=self._provider.name, http_status=result.http_status, duration_ms=result.duration_ms)
            return 'cleared'

        if result.outcome == 'REJECTED':
            repo.mark_rejected(row['id'], reason_code=result.reason_code or 'REJECTED', detail=result.detail)
            audit.record(conn, self._app_data_dir, company_id=self._company_id, event='SUBMIT_REJECTED',
                         invoice_ref=row['invoice_ref'], outcome='REJECTED', reason_code=result.reason_code,
                         provider=self._provider.name, http_status=result.http_status)
            return 'rejected'

        if result.outcome == 'PENDING':
            repo.mark_pending(row['id'])
            audit.record(conn, self._app_data_dir, company_id=self._company_id, event='SUBMIT_PENDING',
                         invoice_ref=row['invoice_ref'], outcome='PENDING', reason_code=result.reason_code,
                         provider=self._provider.name)
            return 'pending'

        if result.outcome == 'UNKNOWN':
            repo.mark_unknown(row['id'], reason_code=result.reason_code, detail=result.detail)
            audit.record(conn, self._app_data_dir, company_id=self._company_id, event='SUBMIT_UNKNOWN',
                         invoice_ref=row['invoice_ref'], outcome='UNKNOWN', reason_code=result.reason_code,
                         provider=self._provider.name)
            return 'unknown'

        # RETRY
        return self._apply_retry(conn, repo, row, reason_code=result.reason_code or 'RETRY', detail=result.detail)

    def _apply_retry(self, conn, repo: OutboxRepository, row, *, reason_code: str, detail: Optional[str]) -> str:
        max_attempts = int(settings.get_setting(conn, self._company_id, 'max_attempts'))
        attempt_after = row['attempt_count'] + 1
        if attempt_after >= max_attempts:
            repo.mark_failed_permanent_after_max_attempts(row['id'], reason_code='MAX_ATTEMPTS_EXCEEDED', detail=detail)
            audit.record(conn, self._app_data_dir, company_id=self._company_id, event='SUBMIT_REJECTED',
                         invoice_ref=row['invoice_ref'], outcome='FAILED_PERMANENT', reason_code='MAX_ATTEMPTS_EXCEEDED',
                         provider=self._provider.name, details={'attempts': attempt_after})
            return 'rejected'

        next_attempt_at = (datetime.now(timezone.utc) + timedelta(seconds=compute_backoff_delay(row['attempt_count']))).isoformat()
        repo.mark_retry(row['id'], reason_code=reason_code, detail=detail, next_attempt_at=next_attempt_at)
        audit.record(conn, self._app_data_dir, company_id=self._company_id, event='SUBMIT_RETRY_SCHEDULED',
                     invoice_ref=row['invoice_ref'], outcome='RETRY', reason_code=reason_code,
                     provider=self._provider.name, details={'next_attempt_at': next_attempt_at})
        return 'retry'

    # ─── crash-recovery lease reclaim ───────────────────────────────────

    def _resolve_reclaimed(self, conn, repo: OutboxRepository, row) -> None:
        audit.record(conn, self._app_data_dir, company_id=self._company_id, event='LEASE_RECLAIMED',
                     invoice_ref=row['invoice_ref'], provider=self._provider.name)

        result = self._provider.check_status(row['invoice_ref'], row['provider_uuid'])
        audit.record(conn, self._app_data_dir, company_id=self._company_id, event='STATUS_POLLED',
                     invoice_ref=row['invoice_ref'], outcome=result.outcome, reason_code=result.reason_code,
                     provider=self._provider.name)

        if result.outcome == 'CLEARED':
            repo.resolve_unknown_cleared(row['id'], provider_uuid=result.provider_uuid,
                                          qr_payload=result.qr_payload, qr_image_base64=result.qr_image_base64)
            return
        if result.outcome == 'UNKNOWN':
            # Cannot confirm either way -- hold exactly where it is. Never
            # auto-resubmit on an unresolved UNKNOWN.
            return
        # Any other definitive answer (REJECTED/RETRY/PENDING-as-not-yet-seen)
        # means the authority does not have a CLEARED record for this
        # invoice_ref -- safe to requeue for a fresh submission attempt.
        next_attempt_at = _now_iso()
        repo.resolve_unknown_not_received(row['id'], next_attempt_at=next_attempt_at)
