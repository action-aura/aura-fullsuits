"""JoFotara e-invoicing -- UnconfiguredProvider, the DEFAULT provider in a
shipped install now that e-invoicing itself defaults ON (see settings.py).

Jordan has mandated e-invoicing since 2024-05-31, so the feature must be
enabled out of the box -- a shop should never have to find a toggle to be
compliant. But "enabled" cannot mean "submits and claims clearance": the
real provider, DirectISTDProvider, deliberately raises NotImplementedError
because the ISTD field schema and endpoints are only issued to a taxpayer
after they register on the JoFotara portal (see providers/direct_istd.py).
Wiring MockProvider in as the live default instead would be worse than
either option -- it would print a receipt claiming "Jordan e-invoice
cleared" for a document no tax authority ever saw, a false compliance claim
on a tax document.

This class is the third way: "enabled" means the obligation is being
RECORDED -- every sale still enqueues into einvoice_outbox, gapless numbers
still get allocated -- without anything pretending to have submitted. Rows
simply queue and wait for the shop to complete portal registration and
supply real credentials, at which point swapping this out for a configured
DirectISTDProvider lets the worker drain the backlog.

The raise below is a BACKSTOP ONLY. The normal path never reaches it:
worker.py's OutboxWorker.run_once() checks `is_configured` (False here) and
returns before leasing or touching a single row -- see that method's second
early return, right beside the settings.is_enabled() one. If submit_invoice
or check_status is ever called on this provider anyway (a future call site
that forgets the is_configured guard), raising loudly here is far safer
than silently doing nothing or, worse, guessing at a response.
"""
from __future__ import annotations

from typing import Optional

from .base import EInvoiceProvider, EInvoiceProviderConfigError, SubmissionResult

_NOT_CONFIGURED_MESSAGE = (
    "E-invoicing is enabled but no JoFotara provider is configured yet. "
    "Missing: registration on the JoFotara portal, the Client-ID and "
    "Secret-Key it issues, and this seller's activity number. Documents "
    "will continue to queue in einvoice_outbox until a configured provider "
    "(e.g. DirectISTDProvider, once Phase 2 lands) is wired in -- see "
    "docs/einvoicing/phase1/operational-runbook-and-kill-switch.md."
)


class UnconfiguredProvider(EInvoiceProvider):
    name = "unconfigured"
    is_configured = False

    def submit_invoice(self, invoice_ref: str, document) -> SubmissionResult:
        raise EInvoiceProviderConfigError(_NOT_CONFIGURED_MESSAGE)

    def check_status(self, invoice_ref: str, provider_uuid: Optional[str]) -> SubmissionResult:
        raise EInvoiceProviderConfigError(_NOT_CONFIGURED_MESSAGE)
