# Phase 9.5B-R Milestone 15 — API Localization Boundary

## `/api/operations/v1` and `/api/licensing/v1` — zero changes this phase

Neither API blueprint was touched by this phase (confirmed: `git diff` scope for this phase includes no
file under `owner/app/api_operations/` or `owner/app/api_external/`/`owner/app/licensing_service/`).

## Stable values, unchanged

Every JSON field name (`employee_number`, `employment_status`, `presence`, `version`, etc.), every error
code (`RECORD_NOT_FOUND`, `VERSION_CONFLICT`, `INVALID_STATE_TRANSITION`, `LAST_SUPER_ADMIN_REQUIRED`,
`PERMISSION_DENIED`, `VALIDATION_ERROR`, `EMPLOYEE_NOT_ACTIVE`), every state value
(`ACTIVE`/`SUSPENDED`/etc.), every timestamp (`_iso()`'s ISO 8601 format), and every numeric serialization
remain exactly what Phase 9.5B built them as — a future mobile/API client branches on these values, never
on display text, and this phase introduces no reason for that to change.

## No localized display message added to the API this phase

The governing spec allows an *optional* supplemental localized message field on API responses, gated by
explicit, bounded locale negotiation. Not implemented this phase — no real client exists yet to consume
it (Phase 9.5A's own mobile-auth ADR: no mobile client, no `/api/operations/v1/auth/mobile/*` routes
built), and adding a speculative field with nothing to consume it would be exactly the kind of
premature-feature work this session's own established discipline avoids. Recorded honestly as NOT
IMPLEMENTED (not silently skipped) — a future phase adding a real API-consuming client can add this
field then, using `select_locale()` (already real, already tested) as the negotiation mechanism.

## Licensing protocol / cryptographic canonicalization — unchanged, verified

`owner/app/licensing_service/canonical.py`, `signing.py`, `assertions.py` — byte-for-byte untouched
(confirmed via `git diff --stat` for this phase's full commit range: zero files under
`licensing_service/` appear). No signed assertion content, canonicalization algorithm, or request
signature verification is affected by locale in any way — these operate entirely below and independent of
the presentation layer this phase adds.

## Real, tested proof

`test_phase9_5b_r_api_boundary.py`: the operations API returns identical `error` code strings and
`employment_status`/`presence` values regardless of the caller's active session locale (a real test
switches locale via `/locale/ar`, then hits `/api/operations/v1/employees/<id>` and asserts the JSON
`employment_status` field is still the raw `"ACTIVE"`, never `"نشط"`).
