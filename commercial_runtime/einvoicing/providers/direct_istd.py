"""DirectISTDProvider -- Phase 2. NOT IMPLEMENTED.

The real ISTD field-level schema (element names, cardinalities, code lists,
the JSON encryption envelope, and the endpoint paths) is NOT publicly
available -- it is issued to a taxpayer only after they register on the
JoFotara portal. Nothing in this file may guess at those values.

Phase 2 fills this in from the official ISTD integration package plus real
sandbox credentials, once obtained (see
docs/einvoicing/phase2/phase2-seam.md and phase2/istd-field-mapping.md).
Until then this raises rather than shipping a plausible-looking wrong
implementation -- a wrong guess here would silently produce non-compliant
tax filings, which is worse than an obvious NotImplementedError.
"""
from __future__ import annotations

from typing import Optional

from .base import EInvoiceProvider, EInvoiceProviderConfigError, SubmissionResult

_PHASE2_TODO = (
    "DirectISTDProvider requires the official ISTD integration documentation "
    "and sandbox credentials obtained from the JoFotara portal. Not available "
    "at Phase 1. Use provider='mock' until Phase 2 lands."
)


class DirectISTDProvider(EInvoiceProvider):
    name = "direct_istd"

    def __init__(self, *, base_url: str, credentials, timeout_seconds: float = 30.0, verify_tls: bool = True) -> None:
        if not verify_tls:
            # No dev escape hatch for tax traffic, deliberately -- see
            # docs/einvoicing/phase1/... security checklist.
            raise EInvoiceProviderConfigError("TLS verification cannot be disabled for e-invoicing traffic.")
        self._base_url = base_url
        self._credentials = credentials
        self._timeout = timeout_seconds

    def submit_invoice(self, invoice_ref: str, document) -> SubmissionResult:
        raise NotImplementedError(_PHASE2_TODO)

    def check_status(self, invoice_ref: str, provider_uuid: Optional[str]) -> SubmissionResult:
        raise NotImplementedError(_PHASE2_TODO)
