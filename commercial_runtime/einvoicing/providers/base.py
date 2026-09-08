"""JoFotara e-invoicing -- the provider interface every backend (MockProvider
today, DirectISTDProvider in Phase 2, and potentially a certified gateway
later) implements identically.

The outbox/worker layer (outbox.py, worker.py) consumes ONLY this interface
-- it never knows which concrete provider is configured. This is the seam
that let Phase 1 ship a complete, fully-tested pipeline against a fake
JoFotara before any real ISTD integration docs existed, and it's the same
seam Phase 2 plugs into (see docs/einvoicing/phase2/phase2-seam.md): only
providers/direct_istd.py changes, nothing else in this package does.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import ClassVar, Literal, Optional

Outcome = Literal["CLEARED", "REJECTED", "RETRY", "PENDING", "UNKNOWN"]


class EInvoiceProviderError(Exception):
    pass


class EInvoiceProviderConfigError(EInvoiceProviderError):
    pass


@dataclass(frozen=True)
class SubmissionResult:
    outcome: Outcome
    provider_uuid: Optional[str] = None
    qr_payload: Optional[str] = None
    qr_image_base64: Optional[str] = None
    reason_code: Optional[str] = None
    detail: Optional[str] = None  # sanitized; never a raw response body
    http_status: Optional[int] = None
    duration_ms: Optional[int] = None


class EInvoiceProvider(abc.ABC):
    """Outcome semantics -- this is the contract outbox.py's state machine
    keys off:

      CLEARED  -- terminal success. provider_uuid + a QR value are present.
      REJECTED -- terminal failure. The document itself is wrong; retrying
                  identically will never help.
      RETRY    -- transient (network, 5xx, timeout, rate limit). Back off
                  and try again later.
      PENDING  -- accepted but not yet cleared; poll with check_status.
      UNKNOWN  -- cannot determine whether the authority actually received
                  it (e.g. the connection dropped after the request was
                  sent but before a response arrived). NEVER auto-resubmit
                  on UNKNOWN -- the worker holds the row and requires
                  check_status to resolve it, or an explicit operator
                  decision. This is the anti-double-submission guarantee.

    is_configured -- whether this provider can actually reach a tax
    authority. False means the worker (worker.py's OutboxWorker.run_once)
    must not submit through it AT ALL: it leaves every queued row exactly
    where it is rather than calling submit_invoice/check_status. A provider
    that cannot submit must say so via this flag rather than raising from
    submit_invoice -- raising is indistinguishable from a transient failure
    to _apply_retry, which counts it against the row's attempt budget and
    eventually walks a perfectly good, never-actually-attempted invoice to
    FAILED_PERMANENT. See providers/unconfigured.py, the concrete provider
    that sets this False.
    """

    name: ClassVar[str]
    is_configured: ClassVar[bool] = True

    @abc.abstractmethod
    def submit_invoice(self, invoice_ref: str, document) -> SubmissionResult:
        """invoice_ref is the outbox's own idempotency key (e.g.
        'AURA_RETAIL:sale:1234') -- passed alongside the document so a
        provider can correlate a later check_status(invoice_ref, ...) call
        back to this submission without the caller needing to persist any
        provider-specific state itself."""

    @abc.abstractmethod
    def check_status(self, invoice_ref: str, provider_uuid: Optional[str]) -> SubmissionResult:
        """Crash-recovery / idempotency probe. MUST NOT create anything on
        the provider's side -- a pure read. Returning UNKNOWN is legal and
        correct when the provider genuinely cannot say; it causes the
        worker to keep holding the row, never to guess."""

    def selftest(self) -> SubmissionResult:
        """Round-trips a clearly-synthetic, unmistakably-fake document --
        never a real invoice. Used by the /api/einvoicing/selftest route to
        let an operator verify connectivity/configuration without touching
        real business data."""
        from ..document import BuyerId, EInvoiceDocument, EInvoiceLine

        synthetic = EInvoiceDocument(
            company_id=0,
            einvoice_no='SELFTEST-000000',
            local_document_no='SELFTEST',
            invoice_family='income',
            payment_type='cash',
            currency='JOD',
            issue_datetime='1970-01-01T00:00:00',
            seller_name='SELFTEST SELLER -- NOT A REAL BUSINESS',
            seller_tin='000000000',
            buyer_name='SELFTEST BUYER -- NOT A REAL CUSTOMER',
            buyer_id=BuyerId('TIN', '000000000'),
            lines=(EInvoiceLine('SELFTEST ITEM', 1, 1.0, 0.0, 0.0, 1.0),),
            subtotal=1.0, discount_total=0.0, tax_total=0.0, grand_total=1.0,
        )
        import uuid
        return self.submit_invoice(f'SELFTEST:{uuid.uuid4()}', synthetic)
