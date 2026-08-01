# Phase 9.5B-R Milestone 1 — String Classification Report

## USER_VISIBLE — MUST TRANSLATE (real count: 187 distinct strings across the gated set)

Every string listed in `current-owner-string-inventory.md`'s named sections (layout nav, auth/MFA/setup/
recovery-code screens, employee dashboard/list/detail/new/invitations, profile/sessions) — labels,
headings, button text, help text, placeholders, flash-equivalent `error=`/route messages, the 2
`confirm()` dialog strings, table headers, empty-state text, status/presence words as *displayed labels*.

## MACHINE_IDENTIFIER — MUST NOT TRANSLATE

`ACTIVE`/`PENDING`/`SUSPENDED`/`TERMINATED`/`ARCHIVED` (stored `EmployeeProfile.employment_status`
values), `ONLINE`/`RECENTLY_ACTIVE`/`OFFLINE` (return values of `presence_state()`), `SUPER_ADMIN`/`SALES`/
`SUPPORT`/`FINANCE`/`VIEWER` (`Role.code`), every `employees.*`/`staff.*`/`security_sessions.*` permission
code, `EMPLOYEE_SUSPENDED`/`EMPLOYEE_TERMINATED`/etc. (`AuditLog.action_code`), every UUID, `employee_number`
values (e.g. `EMP-0001`), email addresses, `platform` values (`WEB`/`ANDROID`/`IOS`), CSRF token values,
session token hashes (never rendered anyway).

## API ERROR CODE — STABLE, OPTIONAL LOCALIZED DISPLAY

`RECORD_NOT_FOUND`, `VERSION_CONFLICT`, `INVALID_STATE_TRANSITION`, `LAST_SUPER_ADMIN_REQUIRED`,
`PERMISSION_DENIED`, `VALIDATION_ERROR`, `EMPLOYEE_NOT_ACTIVE` (all from
`owner/app/api_operations/routes.py`) — JSON `"error"` field values, consumed by code (a future mobile
client), never by a human reading raw JSON. Left untranslated at the wire level (Non-Negotiable Principle 3
+ Milestone 15's own instruction); see `api-localization-boundary.md`.

## LOG/OPERATOR ONLY

Structured JSON log lines (`owner/app/observability/logging_config.py`), preflight `PreflightCheck.detail`
strings (`owner/app/commercial_ops/preflight.py`) — operator/developer-facing, English-only by design,
never shown to an Owner web user.

## DEFERRED FUTURE FEATURE

Commission-plan display strings in `employees/new.html`/`detail.html` reference a feature whose full
workflow doesn't exist yet (Phase 9.5A's own honest scope) — the *label* ("Commission plan") is real and
translated now; the eventual commission *management* screen's strings don't exist yet to classify.

## DEAD/UNREACHABLE STRING

None found in the gated set — every string audited is reachable from a real, tested route (confirmed by
cross-referencing against the 548 passing tests, which exercise every gated template at least once).

## Hardcoded strings in the 37 non-gated templates

Real, present, **not translated this phase** — see `phase9-5b-r-scope-and-boundaries.md`'s documented
reduction. Not reclassified as USER_VISIBLE-must-translate for this phase's own gate purposes; the
hardcoded-string scanner (Milestone 16) is scoped to the gated set only, with the non-gated templates on
an explicit, reviewed allowlist (directory-level, not per-string) so the scanner doesn't false-positive on
a real, documented decision.
