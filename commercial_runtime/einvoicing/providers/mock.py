"""JoFotara e-invoicing -- MockProvider, the Phase 1 default.

Makes ZERO network calls (this file must never import `requests` or open a
socket -- enforced by a source-scan test,
test_providers_mock.py::test_source_never_imports_requests). Everything the
outbox/worker/routes/UI layers do against a real provider is exercised
against this one first, so the entire pipeline is provably correct before
any live ISTD credentials exist.

Deterministic: the same (invoice_ref, document) always yields the same
provider_uuid, so a resubmission (e.g. after a crash-recovery lease reclaim)
is trivially provable as idempotent in tests -- see outbox.py /
test_worker.py's double-submission guard.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .base import EInvoiceProvider, Outcome, SubmissionResult

_NAMESPACE = uuid.UUID('a3f1e9c0-3b6d-4b8e-9a1f-6e2c8d4f0b71')  # fixed, arbitrary -- just needs to be stable


class MockProvider(EInvoiceProvider):
    name = 'mock'

    def __init__(
        self,
        *,
        fail_first_n: int = 0,
        outcome_script: Optional[List[Outcome]] = None,
        latency_seconds: float = 0.0,
        unknown_after_send: bool = False,
    ):
        self._fail_first_n = fail_first_n
        self._outcome_script = list(outcome_script) if outcome_script else None
        self._latency_seconds = latency_seconds
        self._unknown_after_send = unknown_after_send
        self._attempts: Dict[str, int] = {}
        self._submitted: Dict[str, SubmissionResult] = {}

    def _deterministic_uuid(self, invoice_ref: str) -> str:
        return str(uuid.uuid5(_NAMESPACE, invoice_ref))

    def _synthetic_qr_payload(self, invoice_ref: str, document) -> str:
        # Plainly labelled MOCK so a mock QR can never be mistaken for a
        # real ISTD one if it somehow ended up on a printed receipt.
        return (
            f"MOCK|{document.einvoice_no}|seller_tin={document.seller_tin}|"
            f"total={document.grand_total:.2f}|{datetime.now(timezone.utc).isoformat()}"
        )

    def submit_invoice(self, invoice_ref: str, document) -> SubmissionResult:
        if self._latency_seconds:
            time.sleep(self._latency_seconds)

        attempt = self._attempts.get(invoice_ref, 0) + 1
        self._attempts[invoice_ref] = attempt

        if self._outcome_script:
            index = min(attempt - 1, len(self._outcome_script) - 1)
            scripted = self._outcome_script[index]
            result = self._result_for_outcome(scripted, invoice_ref, document)
            self._submitted[invoice_ref] = result
            return result

        if attempt <= self._fail_first_n:
            result = SubmissionResult(outcome='RETRY', reason_code='MOCK_SCRIPTED_FAILURE',
                                       detail=f'mock scripted failure, attempt {attempt}')
            return result

        if self._unknown_after_send:
            result = SubmissionResult(outcome='UNKNOWN', reason_code='MOCK_CONNECTION_DROPPED',
                                       detail='mock: response lost after send, simulating a crash window')
            self._submitted[invoice_ref] = self._result_for_outcome('CLEARED', invoice_ref, document)
            return result

        result = self._result_for_outcome('CLEARED', invoice_ref, document)
        self._submitted[invoice_ref] = result
        return result

    def _result_for_outcome(self, outcome: Outcome, invoice_ref: str, document) -> SubmissionResult:
        if outcome == 'CLEARED':
            return SubmissionResult(
                outcome='CLEARED',
                provider_uuid=self._deterministic_uuid(invoice_ref),
                qr_payload=self._synthetic_qr_payload(invoice_ref, document),
                reason_code='MOCK_CLEARED',
                http_status=200,
                duration_ms=1,
            )
        if outcome == 'REJECTED':
            return SubmissionResult(outcome='REJECTED', reason_code='MOCK_REJECTED',
                                     detail='mock scripted rejection', http_status=400)
        if outcome == 'PENDING':
            return SubmissionResult(outcome='PENDING', provider_uuid=self._deterministic_uuid(invoice_ref),
                                     reason_code='MOCK_PENDING', http_status=202)
        if outcome == 'RETRY':
            return SubmissionResult(outcome='RETRY', reason_code='MOCK_RETRY', http_status=503)
        return SubmissionResult(outcome='UNKNOWN', reason_code='MOCK_UNKNOWN')

    def check_status(self, invoice_ref: str, provider_uuid: Optional[str]) -> SubmissionResult:
        cached = self._submitted.get(invoice_ref)
        if cached is not None:
            return cached
        return SubmissionResult(outcome='UNKNOWN', reason_code='MOCK_NO_RECORD',
                                 detail='mock provider has no record of this invoice_ref')
