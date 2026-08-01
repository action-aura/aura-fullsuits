# Phase 9.5B Milestone 5 — Employee List UI Contract (real code)

`GET /employees` (`owner/app/employees/routes.py::list_view`, `require_permission("employees.view_all")`).
Real filters wired to `app.employees.queries.list_employees()`: search (name/employee number, ILIKE),
department, employment status, role (via `StaffRoleAssignment`/`Role` join), presence (real SQL `EXISTS`
clause mirroring `presence_state()`'s own thresholds — never a Python-side post-filter), archived
inclusion toggle. Sort: name (default)/employee number/start date/status. Pagination via the existing,
real `app.services.pagination.paginate()` (same filtered-statement-computes-total discipline as every
other Phase 9.5A list endpoint — no separate unfiltered count).

No N+1: `bulk_presence_states()` (Milestone 8) computes presence for the whole page in one query, called
once per request with the page's own employee IDs — not once per row.

Never exposed in the list response/template: password data, MFA secret, recovery codes, session tokens,
setup-token raw values, management-only `EmployeeProfile.notes` (omitted from `list.html` entirely).

Responsive: `list.html`'s table has `class="responsive-table"` and per-cell `data-label` attributes: under
720px (`layout/base.html`'s new, scoped `@media` block, Milestone 14) it renders as stacked cards instead
of a horizontal table — added once, in the shared layout, reused by every future table that opts in via
the same class, without touching any pre-existing Owner table's behavior.

Real, tested proof: `owner/tests/test_phase9_5b_queries.py` (6 tests — archived exclusion, search, department
filter, role filter, presence filter matching derivation exactly, pagination total matches the filtered
count) + `owner/tests/test_phase9_5b_web_routes.py`'s end-to-end create→accept→list→detail flow.
