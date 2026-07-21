# Phase 6 -- External API Forbidden-Data Enforcement Report (extends Phase 5's `forbidden-data-enforcement-report.md`)

## Method (automated, not documentation-only, matching Phase 5's own standard)
1. **Structural table/column scan**: `test_phase6_data_boundary.py::test_no_phase6_table_or_column_matches_forbidden_term` iterates every column of all 12 new Phase 6 tables against the same 13-term forbidden list Phase 5 established.
2. **Assertion payload guard**: `assertions.py::_guard_payload()` raises `AssertionError_` for any forbidden key, tested directly against real forbidden field names (`card_number`, `patient_name`) and confirmed a legitimate payload passes through unchanged.
3. **Live response scan**: `test_phase6_data_boundary.py::test_activation_response_contains_no_forbidden_fields` performs a **real HTTP activation** (real Ed25519 signature, real Postgres) and greps the entire JSON response body for every forbidden term -- not a unit-level check of the serializer function in isolation, but the actual bytes a client would receive.
4. **`service-info` scan**: confirms no database/Redis host, filesystem path, stack trace, table count, or staff-related key appears in the public service-info payload.
5. **Cross-package import scan**: `test_no_licensing_service_module_imports_products_or_commercial_runtime` greps every `.py` file under `app/licensing_service/` and `app/api_external/` for a `products`/`commercial_runtime` import. Zero found.

## Result
All 5 checks pass. Zero forbidden terms found anywhere in the Phase 6 schema, the assertion-signing path, or a real live activation response.

## Full license key handling -- the one deliberate, minimum-necessary exception
The activation *request* legitimately carries the full plaintext license key (Part E requires this for initial activation). This is the **only** place in the entire Phase 6 surface a secret of that kind ever appears, and it is scoped tightly: never persisted (`activation.py` explicitly `del`s the local variable the instant the HMAC lookup completes), never logged (structured logging never touches the raw request body), never in the response, never in `owner_activation_requests`, never in the audit log. Directly proven end-to-end (not just by code inspection) by `test_phase6_activation_protocol.py::test_full_key_never_appears_anywhere_in_db_logs_or_error_response`, which performs a real activation with a real key and then greps the License row, every `ActivationRequest` row, and every `AuditLog` row for that exact key string.
