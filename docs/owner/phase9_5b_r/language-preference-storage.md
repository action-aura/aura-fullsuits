# Phase 9.5B-R Milestone 3 — Language Preference Storage

## Decision: additive `StaffUser.locale` column (no existing preference mechanism to reuse)

Audited first (per the governing spec's own "prefer an existing user-preference mechanism when one
exists" instruction): `StaffUser` had no prior notion of a display preference of any kind before this
phase — confirmed by reading the full model (`owner/app/models/staff.py`) prior to this change. No reuse
candidate existed; a new nullable column was the correct, minimal answer.

## Schema

`owner_staff_users.locale` — `String(8)`, nullable (NULL = no explicit preference yet, falls through the
precedence chain), `CHECK (locale IS NULL OR locale IN ('en', 'ar'))` (defense-in-depth — the real
authority is still `app.config["LANGUAGES"]`, checked in application code before this constraint would
ever be reached). Migration `338d06dece44`, additive only, empty-and-populated-database tested,
upgrade/downgrade round-trip clean against `aura_owner_dev`; applied to `aura_owner_staging` clean too.

## Anonymous/pre-authentication persistence

`owner_locale` cookie (`httponly=True`, `samesite="Lax"`, `secure` mirrors `SESSION_COOKIE_SECURE`,
1-year max-age) — set by the same `/locale/<code>` route, read by `select_locale()`'s step 3. Not a
security-sensitive value (a display preference, not a session credential), so no CSRF token is required to
set it and GET is an appropriate, documented method (`language-switcher-security-report.md`).

## Not exposed to unrelated employees

`StaffUser.locale` is a plain column on the account's own row — never included in `_serialize_employee()`
(`owner/app/api_operations/routes.py`) or any employee-list/detail response; an employee's chosen display
language is not part of what any other employee or manager can see through the employee-portal API/UI
(confirmed: `locale` does not appear in `current-owner-string-inventory.md`'s employee-detail/list field
lists, and a grep of `api_operations/routes.py`'s `_serialize_employee()` confirms it is not selected).
