# Phase 9.5B Milestone 7 — Employee Self-Profile Contract (real code)

`GET/POST /profile`, `GET /profile/sessions`, `POST /profile/sessions/<uuid>/revoke`
(`owner/app/employees/self_routes.py`, `@require_login` only — every other permission is irrelevant here
because the route never accepts an ID; it always resolves from `load_current_staff()`).

## IDOR is structurally impossible, not merely tested

No route under `/profile` accepts an employee/session ID that identifies *which employee* — `find_own_profile(staff.id)`
always resolves from the authenticated session. There is no URL an employee could edit to reach another
employee's data through this blueprint; `/profile/sessions/<uuid>/revoke` takes a *session* ID, but
`revoke_session_route()` first calls `list_sessions_for_staff(staff.id)` and 404s unless that session
belongs to the caller — so even a guessed/enumerated session UUID belonging to someone else is rejected.

## Self-editable fields — enforced in the service layer, not just the form

`SELF_EDITABLE_PROFILE_FIELDS = ("phone",)` (`owner/app/employees/services.py`) plus `StaffUser.display_name`
(a different table/field entirely — `update_own_profile()` is the only function that writes either, and
it only ever reads `display_name`/`phone` from the request body, regardless of what else might be present.
Employee number, employment status, employment start date, manager, commission plan, role, MFA
requirement are physically absent from `update_own_profile()`'s signature — not merely omitted from the
HTML form (a raw POST with extra fields cannot reach them; there's no code path that accepts them).

## Real, tested proof

`test_self_profile_route_never_exposes_a_uuid_and_shows_own_data`,
`test_self_profile_cannot_edit_employee_number_field_at_all` (asserts the fixed field tuple directly, not
just a form's omission), `test_sales_employee_forbidden_from_management_employee_list` (proves the
*converse* — an employee without `employees.view_all` cannot reach the management list/detail screens at
all, so self-profile is genuinely their only view).
