# Phase 9.5B-R Milestone 1 — String Audit Plan

## Method (real, not sampled)

1. `grep -rn` every gated template directory (`layout/`, `auth/`, `employees/`, `profile/`) for Jinja text
   nodes, `<label>`/`<button>`/`<th>`/`placeholder`/`title`/`aria-label` content.
2. `grep -rn` the corresponding Python route/service modules
   (`app/auth/routes.py`, `app/employees/routes.py`, `app/employees/self_routes.py`,
   `app/staff/routes.py`, `app/api_operations/routes.py`, `app/employees/services.py`,
   `app/staff/services.py`, `app/security/rbac.py`) for `jsonify({"error": ...})`, flash-equivalent
   strings, and any raised user-facing message text.
3. Cross-reference every string against the classification rules (Milestone 1 of the governing spec):
   USER_VISIBLE, MACHINE_IDENTIFIER, LOG/OPERATOR ONLY, API ERROR CODE, DEFERRED FUTURE FEATURE, DEAD.
4. Record results in `current-owner-string-inventory.md` (raw list) and
   `string-classification-report.md` (classified, with source file:line).

## Scope boundary applied here

Per `phase9-5b-r-scope-and-boundaries.md`, the audit is exhaustive for: `layout/base.html`, every
`auth/*.html`, every `employees/*.html`, every `profile/*.html`, and their backing Python routes/services.
The remaining 37 Phase 5-8 templates are inventoried at the directory/template level only (confirming they
exist and extend the shared layout) — not string-by-string, since they are not translated this phase
(documented scope reduction, not an oversight).
