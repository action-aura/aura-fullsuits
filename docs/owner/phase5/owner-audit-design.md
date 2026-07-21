# Phase 5 -- Owner Audit Design (Part T)

## Single writer, tamper-evident hash chain (ADR-8)
`app/audit/services.py::record()` is the only function anywhere in the codebase that inserts into `owner_audit_log`. Each row stores `previous_hash` (the prior row's `current_hash`, or `None` for the first row) and `current_hash = sha256(previous_hash + canonical_json(actor, action_code, entity_type, entity_public_id, result, before_state, after_state))`. `verify_chain()` walks every row in insertion order, recomputes each hash, and returns `(False, broken_row_id)` at the first mismatch -- proving detection, not just claiming it.

## What's covered
Every sensitive write in every domain package calls `audit_record()`: login success/failure (via `LoginAttempt`, separately, plus `STAFF_LOGIN_SUCCESS`/`STAFF_LOGIN_MFA_SUCCESS` audit rows), MFA enrollment/reset, staff creation/disablement/role assignment, customer create/update/archive, subscription/license/installation status transitions, plan/price/entitlement changes, payment record changes, license issuance, backup/restore, audit export. Full action-code list is visible directly in each domain's `services.py` (no separate registry to drift out of sync).

## Secret redaction (Part T's explicit no-secrets-in-audit requirement)
`redact()` strips any field whose name contains `password`, `mfa_secret`, `totp_secret`, `recovery_code(s)`, `key_secret`, `full_key`, `token`, `token_hash`, `credential`, `pepper`, or `secret` (case-insensitive substring match) before it ever reaches `before_state_redacted`/`after_state_redacted`. Applied unconditionally inside `record()` -- a caller cannot bypass it by passing a raw dict. Verified: `owner/tests/test_audit.py::test_secret_fields_redacted`, plus the licensing-specific proof `test_issuance_event_never_contains_the_secret` that directly checks a real issued key's characters don't appear in its own audit row.

## Immutability by construction, not by permission check
There is no `PUT`/`PATCH`/`DELETE` route anywhere under `/audit/*` -- not a guarded one, an absent one. Verified: `owner/tests/test_audit.py::test_audit_log_has_no_update_or_delete_route` inspects `app.url_map` directly and asserts none of those HTTP methods appear on any `/audit` rule.

## Export
`GET /audit` (paginated, filterable by action_code/entity_type) and `POST /audit/export` (CSV, `audit.export` permission + `@require_recent_auth`) are the only two audit-facing routes beyond `verify-chain`.

## Known limitation (disclosed, see `owner-residual-risk-register.md`)
The hash chain detects tampering after the fact; it does not physically prevent a database administrator with raw SQL access from editing a row and then recomputing every subsequent hash forward to make the chain appear consistent again. This is the standard limitation of an application-level (not WORM-storage-backed) tamper-evidence control, and is explicitly named rather than glossed over.
