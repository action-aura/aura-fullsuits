# Phase 9.5A — API Error Catalog

Every error response: `{"error_code": "...", "message": "human-readable, safe", "correlation_id": "...",
"details": {...} | null}`. `message` never includes a stack trace, a SQL fragment, or a secret value —
matches the redaction discipline already real and tested in Phase 9's `logging-and-redaction-policy.md`.

| Code | HTTP status | Meaning |
|---|---|---|
| `AUTHENTICATION_REQUIRED` | 401 | No valid session/token |
| `MFA_REQUIRED` | 401 | Valid credentials, MFA challenge not yet completed |
| `RECENT_AUTH_REQUIRED` | 401 | Session valid but outside the recent-auth window for a sensitive action |
| `PERMISSION_DENIED` | 403 | Actor lacks the required permission for this action (action-level, existence not in question) |
| `RECORD_NOT_ASSIGNED` | 404 | Reserved: an action-level "you don't own this" case distinct from record-level lookup (see `RECORD_NOT_FOUND`) — used only where revealing "exists but not yours" is acceptable (rare; most record-level cases use `RECORD_NOT_FOUND` per `data-isolation-threat-model.md`) |
| `RECORD_NOT_FOUND` | 404 | Record doesn't exist, or exists but is outside the caller's ownership (deliberately indistinguishable — anti-enumeration) |
| `VERSION_CONFLICT` | 409 | Optimistic-lock `version` mismatch on update |
| `IDEMPOTENCY_CONFLICT` | 409 | `Idempotency-Key` reused with a materially different request body than the original |
| `VALIDATION_ERROR` | 422 | Field-level validation failure, `details` carries a field->message map |
| `INVALID_STATE_TRANSITION` | 409 | e.g. converting an already-`LOST` lead, issuing an already-`ISSUED` invoice |
| `PRICE_VERSION_INACTIVE` | 422 | Line item references a `PlanPrice` whose `effective_until` has passed |
| `DEVICE_POLICY_VIOLATION` | 409 | Reserved for the future live-enforcement phase (Milestone 3's own explicit boundary) — defined now for API stability, not raised by any code this phase |
| `DUPLICATE_LEAD` | 409 | `find_duplicate_candidates()`-equivalent match at lead-creation time |
| `DUPLICATE_CUSTOMER` | 409 | Real, raised by `LeadConversionService` per `lead-conversion-contract.md` |
| `COMMISSION_NOT_ELIGIBLE` | 422 | e.g. `commissions.calculate` invoked against an invoice with no confirmed payment |
| `REFRESH_TOKEN_INVALID` | 401 | Mobile refresh token expired/revoked/unknown |
| `REFRESH_TOKEN_REUSE_DETECTED` | 401 | A previously-rotated-away refresh token was replayed — whole family revoked as a side effect |
| `INTERNAL_ERROR` | 500 | Unexpected server error — `message` is always a generic safe string, never the real exception text (matches the existing `/health/ready` no-leak discipline) |

## Reused, not reinvented

This catalog's shape (`error_code`/`message`/`correlation_id`) matches the real, existing pattern
already used by `/health/ready` (Phase 9) and the external licensing API's own reason-code convention
(Phase 6/8) — a third, incompatible error shape is not introduced.
