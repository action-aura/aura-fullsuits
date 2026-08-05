# Owner Data-Minimization Contract (M7.16)

Real allowlist of data mobile may send vs. explicit prohibited list.
Source of truth: `owner/contracts/*.schema.json`
(`additionalProperties: false` on every schema — a real, enforced
allowlist, not documentation-only) plus Owner's own real, executed
Phase 6 audit, `docs/owner/phase6/external-api-forbidden-data-report.md`.

## Real allowlist — every field a mobile client may ever send to Owner

From `activation-request-v1.schema.json` (the only real, live
device-facing write contract): `contract_version`, `request_id`,
`correlation_id`, `timestamp`, `nonce`, `product_code`, `platform`,
`app_version`, `release_channel`, `installation_id`,
`device_public_key`, `device_public_key_algorithm`, `license_key`
(activation only, never persisted/logged/returned server-side),
`idempotency_key`, `signature`.

From `check-ins`/`deactivations` (no dedicated public schema file
exists yet for these two — real request shape inferred from
`checkin.py`/`deactivation.py` required-field reads, not a JSON-schema
file): `contract_version`, `request_id`, `correlation_id`,
`timestamp`, `nonce`, `installation_id`, `signature` (no license key,
no product/business data).

**Nothing else is structurally permitted** — every schema's
`additionalProperties: false` means an extra field is a hard rejection
at the validation layer, before any handler code runs.

## Real explicit prohibited list (per the M7 checkpoint's own named categories)

None of the following ever appear in any real request schema, and
Owner's own Phase 6 forbidden-term scan
(`test_phase6_data_boundary.py`, cited in
`external-api-forbidden-data-report.md`) proves none leak in the
*response* either:

- **Products/Categories/Suppliers** (Retail catalog data) — never
  sent; Owner only ever receives `product_code`, one of two whole-app
  identifiers (`AURA_RETAIL`/`AURA_CLINIC`), never a Retail product
  record.
- **Customers** (Retail's own end-customer records — distinct from
  Owner's own `Customer` CRM entity) — never sent.
- **Inventory/Stock** — never sent.
- **Sales/Returns/receipts** — never sent.
- **Imports** — never sent; Import Center (M5.8/M6.19) operates
  entirely against the local device database, with zero network call
  to Owner.
- **Dashboard/reports** — never sent.
- **Local DB/backups** — never sent; the local SQLite database and
  its backups never leave the device via this contract.
- **Barcodes** — never sent.
- **Local activity** (audit/usage logs) — never sent.

## Real, deliberate exception: the license key

The activation request legitimately carries the full plaintext
license key — the **one** real, minimum-necessary exception, scoped
tightly per `external-api-forbidden-data-report.md`: never persisted
server-side (`activation.py` explicitly `del`s the local variable
immediately after the HMAC lookup), never logged, never returned,
never in `owner_activation_requests`, never in the audit log. Proven
end-to-end by a real test that greps the License row, every
`ActivationRequest` row, and every `AuditLog` row for the exact key
string after a real activation
(`owner/tests/test_phase6_activation_protocol.py::test_full_key_
never_appears_anywhere_in_db_logs_or_error_response`).

## Real server-side reciprocal guarantee (what Owner sends back)

The signed assertion payload is itself allowlisted
(`ALLOWED_PAYLOAD_FIELDS`, `assertion_verifier.py:59-100`) and
guarded against forbidden markers on both ends (`_guard_payload`
server-side and client-side) — Owner never returns Customer PII,
staff data, database/Redis host, filesystem path, stack trace, table
count, or any Retail/Clinic business data in any licensing response,
confirmed by the real, executed live-HTTP scan in Owner's own Phase 6
report (`external-api-forbidden-data-report.md` §3-4).

## Serialization tests proving prohibited fields are structurally absent (M7.18)

The M7.17 shared commonMain contract models are, by construction,
closed data classes containing only the real allowlisted fields above
— there is no `Map<String, Any>` or open bag that could accidentally
carry a prohibited field. M7.18 adds a serialization test that
`kotlinx.serialization`-encodes every M7.17 request model and asserts
the resulting JSON key set is exactly the real allowlist (no more, no
less) — mirroring Owner's own `additionalProperties: false` schema
discipline on the mobile side of the same contract.

## Mobile contract implication

`ActivationRequest`/`CheckInRequest`/`DeactivationRequest` (M7.17)
must be sealed to exactly the real allowlisted fields above. No
future screen/feature may add a field to these request models without
a corresponding, audited Owner-side schema change — extending the
mobile model alone would either be silently rejected
(`additionalProperties: false`) or, worse, require an
un-reviewed Owner change; either way it is out of scope for a mobile-
only patch.
