# EInvoiceProvider contract

`commercial_runtime/einvoicing/providers/base.py`. Every backend
(`MockProvider` today, `DirectISTDProvider` in Phase 2, and potentially a
certified gateway later) implements this identically. `outbox.py` and
`worker.py` consume only this interface — they never know which concrete
provider is configured. This is the seam that let Phase 1 ship a complete,
tested pipeline with zero real ISTD integration docs.

```python
class EInvoiceProvider(abc.ABC):
    name: ClassVar[str]

    def submit_invoice(self, invoice_ref: str, document: EInvoiceDocument) -> SubmissionResult: ...
    def check_status(self, invoice_ref: str, provider_uuid: Optional[str]) -> SubmissionResult: ...
    def selftest(self) -> SubmissionResult: ...   # default impl, synthetic document
```

`invoice_ref` (the outbox's own idempotency key, e.g.
`AURA_RETAIL:sale:1234`) is passed alongside the document so a provider can
correlate a later `check_status()` call back to the original submission
without the caller persisting any provider-specific state.

## Outcome semantics

| Outcome | Meaning | Outbox transition |
|---|---|---|
| `CLEARED` | Terminal success. `provider_uuid` + a QR value present. | → `CLEARED` |
| `REJECTED` | Terminal failure — the document itself is wrong; retrying identically never helps. | → `FAILED_PERMANENT` |
| `RETRY` | Transient (network, 5xx, timeout, rate limit). | → `QUEUED`, backoff, `attempt_count++` |
| `PENDING` | Accepted but not yet cleared. | → `AWAITING_CLEARANCE` |
| `UNKNOWN` | Cannot tell if the authority actually received it. | → `SUBMITTING_UNKNOWN` — **never auto-resubmitted** |

`UNKNOWN` is the anti-double-submission case: the worker holds the row and
requires an explicit `check_status()` answer (or an operator's manual
decision) before it moves again. See `outbox-state-machine.md`.

## MockProvider (Phase 1 default)

`providers/mock.py`. Makes **zero** network calls — enforced by a
source-scan test (`test_providers_mock.py::test_source_never_imports_requests`).
Deterministic: the same `invoice_ref` always yields the same
`provider_uuid` (`uuid.uuid5` over a fixed namespace), so idempotency is
trivially provable in tests. `qr_payload` always carries a literal `MOCK`
marker so a mock QR can never be mistaken for a real ISTD one. Constructor
supports `fail_first_n`, `outcome_script`, `latency_seconds`, and
`unknown_after_send` for exercising every retry/crash-recovery path in
tests.

## DirectISTDProvider (Phase 2 stub)

`providers/direct_istd.py`. Raises `NotImplementedError` on every method.
The real ISTD field-level schema (element names, cardinalities, code
lists, the JSON encryption envelope, endpoint paths) is not publicly
available outside the taxpayer portal — nothing in this file guesses at
it. A source-scan test
(`test_provider_direct_istd_stub.py::test_source_contains_no_fabricated_istd_details`)
guards against a future contributor filling in plausible-looking but
fabricated values before real integration docs exist. See
`../phase2/phase2-seam.md` for exactly what Phase 2 fills in.

`verify_tls=False` is rejected at construction — no dev escape hatch for
tax traffic, deliberately.
