# Phase 9.5C — Milestone 15: CRM API Error Catalog

Every error the CRM API returns is `{"error": "<STABLE_CODE>", "message":
"<optional English diagnostic>"}` — `error` is the machine-readable
contract (never translated, never changes), `message` is a supplementary
`str(exc)` diagnostic for logs/debugging, not a UI string.

| Code | HTTP status | Source |
|---|---|---|
| `RECORD_NOT_FOUND` | 404 | Route-level (parent or record not found / not authorized — same status for both, avoiding existence-leakage) |
| `VALIDATION_ERROR` | 400 | Missing required field at the route layer |
| `PERMISSION_DENIED` | 403 | Route-level authority check beyond the base `@require_permission` (e.g. linking conversion to an existing Customer) |
| `INVALID_LEAD_STATE` | 409 | `conversion.py:InvalidLeadStateError` |
| `DUPLICATE_CUSTOMER` | 409 | `conversion.py:DuplicateCustomerError` — response includes `candidates` (privacy-masked per Milestone 5) |
| `STALE_LEAD_VERSION` / `STALE_CUSTOMER_VERSION` | 409 | Optimistic-lock mismatch |
| `IDEMPOTENCY_CONFLICT` | 409 | Same idempotency key reused for a different Lead |
| `INVALID_LEAD_TRANSITION` | 400 | `errors.py:LeadError` |
| `REASON_REQUIRED_FOR_LOST` / `REASON_REQUIRED_FOR_REASSIGN` / `REASON_REQUIRED_FOR_FOLLOWUP_CANCEL` / `REASON_REQUIRED_FOR_VERIFY` | 400 | Missing required reason |
| `DESTINATION_EMPLOYEE_NOT_ACTIVE` | 400 | Inactive assignment target |
| `LEAD_*_INVALID` / `LEAD_*_TOO_LONG` / `LEAD_*_REQUIRED` | 400 | Field validation (`validation.py`) |
| `CONTACT_NAME_REQUIRED` / `CONTACT_NAME_TOO_LONG` | 400 | Contact validation |
| `NOTE_VISIBILITY_INVALID` | 400 | Unrecognized visibility value |
| `INTERACTION_TYPE_INVALID` / `INTERACTION_SUMMARY_TOO_LONG` / `INTERACTION_OCCURRED_AT_TOO_FUTURE` | 400 | Interaction validation |
| `FOLLOWUP_DUE_AT_REQUIRED` / `FOLLOWUP_ALREADY_CANCELLED` / `FOLLOWUP_ALREADY_COMPLETED` | 400 | Follow-up lifecycle |
| `INVALID_LATITUDE` / `INVALID_LONGITUDE` / `INVALID_ACCURACY` / `INVALID_SOURCE` / `TIMESTAMP_TOO_FAR` / `MANUAL_ADDRESS_TOO_LONG` | 400 | Location validation — never includes the rejected coordinate value itself |

## No stack traces

Confirmed: every route catches the specific stable-code exception type
its called service can raise; no route has a bare `except Exception` that
would leak a Python traceback to the client. An unhandled exception type
(a real bug, not a validation failure) would surface as Flask's default
500 handler, which — confirmed via the existing, unchanged
`app/__init__.py` error-handling configuration from earlier Owner
phases — does not return a traceback body in production/testing config.
