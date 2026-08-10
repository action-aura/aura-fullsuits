# Product-to-Owner data boundary — e-invoicing

`PRIVACY.md`'s existing rule: customer business data stays local to the
customer's installation; the Owner Control Center never stores or
retrieves it. This wave adds a new category of sensitive local data
(JoFotara credentials, submitted invoice documents, submission audit
trail) and preserves that boundary by construction, not by convention.

## What never leaves the installation

- `client_id` / `client_secret` — encrypted at rest locally (see
  `credential-storage-design.md`), never in any HTTP request this codebase
  sends to Owner.
- `einvoice_outbox.document_xml` — the submitted UBL document. Business
  data (line items, buyer identity, amounts) that already lives in the
  customer's own `sales`/`clinic_invoices` tables; storing it again here is
  not a new privacy boundary crossing, just a second local copy for
  compliance-evidence purposes.
- `einvoice_audit` — submission attempt log. Monetary amounts and buyer PII
  are explicitly excluded by `FORBIDDEN_DETAIL_MARKERS` (extends
  `licensing_contracts/events.py`'s own list with `client_id`,
  `client_secret`, `tin`, `national_id`, etc.) — a caller passing any of
  these into an audit event's details raises, it is not silently
  persisted.

## Enforcement

`commercial_runtime/einvoicing/` contains no import of anything under
`commercial_runtime/licensing_contracts/` (the module that talks to
Owner) except two explicitly-audited, narrow reuses:
`device_identity.py`'s DPAPI wrapping functions (credentials.py) — pure
local cryptography, no network call — and nothing else. There is no
`owner_base_url`, no `OWNER_*` reference, and no outbound HTTP call to
anything but the (Phase 2) ISTD endpoint anywhere in this module.

Verify directly:

```
grep -rn "OWNER_\|owner_base_url\|licensing_contracts.client" commercial_runtime/einvoicing/
```

Returns nothing outside this doc and the DPAPI import noted above.
