# Owner App — Employees / Staff Accounts / Role-Assignment UI (Stage D.6)

Real implementation reference for the modernized Employees, Staff Accounts,
and Role-Assignment screens — the final Stage D sub-area. Describes what was
actually built, cross-check against the real files listed below, not a plan.

There is no separate "Role-Assignment" blueprint or template: it is the
"Roles and permissions" section on `employees/detail.html` and the "Change
roles" section on `staff/detail.html`, both posting to the same real route,
`staff.update_roles`. Confirmed by grep (`Blueprint(.roles|Blueprint(.role_assignment`
across `owner/app` — no matches).

## Files

- `owner/app/employees/status_presentation.py` — new module:
  `employment_status_badge_class`/`presence_badge_class` only (no timeline
  builder — see "Timeline-visualization decision" below).
- `owner/app/staff/list_queries.py` — new module: `list_staff_accounts()`,
  factored out of `staff/routes.py::list_staff`'s previously fully-unbounded
  query (no `.limit()`/`.offset()` at all), following
  `subscriptions/list_queries.py`'s exact pattern from Stage D.5.
- `owner/app/staff/routes.py` — `list_staff` route calls
  `list_queries.list_staff_accounts` instead of building the query inline;
  reads `page`/`status`/`q`/`sort`/`dir` from `request.args`.
- `owner/app/i18n_labels.py` — added `staff_account_filter_status_label`
  (a `callable(code)` label for the `/staff` status filter dropdown,
  distinct from `account_status_label`'s existing `(is_active, disabled)`
  keyword-only signature used to label one already-known account's own
  badge).
- `owner/app/i18n.py` — registers `employment_status_badge_class`,
  `presence_badge_class`, `staff_account_filter_status_label` as Jinja
  globals.
- `owner/app/templates/employees/list.html` — migrated onto the
  `enterprise-table-system.md` macro library (`toolbar`/`table`/
  `pagination_nav`, plus a hand-written filter form — see "Why not
  `filter_bar()`" below); badge ternaries centralized; permission gating
  added on the "Add employee"/"Pending invitations" links; no query/route
  change (`list_employees()` was already correctly paginated/filtered —
  this file's diff is markup-only).
- `owner/app/templates/employees/detail.html` — badge ternaries
  centralized; every action form/button gated with `has_permission(...)`;
  `(requires recent authentication)` added to every button whose route
  carries `@require_recent_auth`.
- `owner/app/templates/staff/list.html` — migrated onto the enterprise
  table system for the main account list; permission gating added on
  "Invite staff" and the per-invitation "Revoke" button; the "Pending
  invitations" sub-table is deliberately left on `.responsive-table`
  (unmigrated — see "Explicitly out of scope"), now with real `data-label`
  attributes added (a real, pre-existing gap on that specific table, see
  "Real bugs found and fixed").
- `owner/app/templates/staff/detail.html` — every action form/button gated
  with `has_permission(...)`; recent-auth labels added.
- `owner/tests/test_phase9_5b_r_rtl_table_structure.py` — updated to match:
  `employees/list.html` removed from `GATED_TABLE_TEMPLATES` (no longer a
  `.responsive-table` screen); `test_responsive_table_thead_actually_hides_at_mobile_width`
  retargeted from `/employees` (no longer applicable) to `/staff`'s still-
  `.responsive-table` invitations sub-table. See "Ground-rules verification"
  for why this was necessary and what was verified about it.

No database migration. No new permission code — every permission code
referenced below (`employees.view_all/create/update/suspend/terminate`,
`staff.view/create/assign_roles/disable/reset_mfa`, `security_sessions.revoke`)
already existed in `owner/app/staff/seed_data.py` and was already enforced by
a real route decorator, unchanged.

## The `employees.view_own` investigation

The task's own lead disclosed a suspicion, verified independently here
before acting on it (not trusted blindly):

**Confirmed via `grep -rn "employees.view_own" owner/app --include=*.py`
(re-run fresh for this doc):** the code appears in exactly two places —
its definition (`seed_data.py:87`, `("employees.view_own", "EMPLOYEES",
"View own employee profile")`) and its grant to the `SALES` role
(`seed_data.py:188`). It is never referenced by an `@require_permission`
decorator, never appears in an `"employees.view_own" in codes`-shaped
conditional, and never appears in any route file at all. The only other
hits are two unrelated tests (`test_phase9_5b_operations_api.py`,
`test_phase9_5b_r_api_boundary.py`) that assert the code merely exists in
a permission listing — neither exercises any enforcement of it.

**The natural candidate route, read in full:**
`owner/app/employees/self_routes.py` (`profile` blueprint, `/profile`) is
the self-service employee-profile screen — the one place "view own
employee profile" could plausibly mean something. Its own module
docstring states the real design: *"No employee_id ever appears in these
routes — every lookup resolves from the authenticated session's own
StaffUser, structurally preventing IDOR by construction."* Every route in
this blueprint (`index`, `update`, `sessions`, `revoke_session_route`) is
gated with `@require_login` only — no permission check at all, let alone
`employees.view_own` specifically.

**Conclusion: `employees.view_own` is a real, defined, granted permission
code that is genuinely unenforced anywhere in this codebase.** This is not
a gap the self-profile route needs to close for *security* — because
`/profile` never accepts an `employee_id` from the caller, there is no
"view own vs. view someone else's" distinction for the route to get wrong;
it is IDOR-proof by construction regardless of which permissions the
caller holds. But that also means the permission code, which has a
specific, restrictive-sounding description ("View own employee profile"),
currently does **nothing**: holding it grants no additional access, and
lacking it denies none. Any authenticated staff account — SUPPORT,
FINANCE, VIEWER, none of which are granted `employees.view_own` — can
reach `/profile` exactly as freely as SALES (the only role that holds it).
Empirically confirmed, not just read: `test_phase9_5b_authorization.py`'s
own attacker fixtures (SALES-role accounts probing IDOR on
`/employees/<id>/suspend`, `/employees/<id>/terminate` via the API twin,
and `/employees/<id>/sessions/revoke`) all successfully `GET /profile` to
fetch a CSRF token as an ordinary precondition of the test, never as the
thing under test — the route's accessibility to any logged-in account is
already an implicit assumption of the existing suite.

**Why this was disclosed, not silently "fixed" by adding an enforcement
check:** the task's own instruction is explicit — do not add enforcement
unless certain what it should gate and certain it changes no currently-
working account's access. Both conditions fail here. If a
`@require_permission("employees.view_own")` gate were added to
`profile.index`/`profile.update`/`profile.sessions`, every SUPPORT/
FINANCE/VIEWER staff account — none of which are granted
`employees.view_own` today — would be immediately locked out of their own
self-service profile page, phone-number edit, and session list on the
very next deploy. That is exactly the "wrong fix could lock out real
users" risk the task warned about, for a permission code whose only
currently-imaginable enforcement point (`/profile`) doesn't actually need
it for security (the route is already IDOR-proof by its own URL shape).
Left as a real, disclosed, out-of-scope finding for a future pass — the
same treatment Stage D.5 gave the JSON-API cash-closing twin — not
silently resolved either way.

**A related, similarly-shaped finding, disclosed for the same reason
(not fixed):** `owner/app/api_operations/routes.py:330` exposes
`PUT /api_operations/employees/<employee_id>/roles`
(`put_employee_roles_route`), gated by `@require_permission("employees.assign_role")`,
which calls the exact same `app.staff.services.assign_roles()` function the
web UI's `staff.update_roles` route calls — but that web route is gated by
a **different** permission code, `staff.assign_roles`. Two routes, one
underlying mutation, two different permission codes. This is the same
"JSON-API-twin" bug class Stage D.5 disclosed for Cash Closing. Verified
via `seed_data.py` that neither code changes the practical picture today:
like `staff.view`/`employees.view_all`, **neither `employees.assign_role`
nor `staff.assign_roles` is granted to any role except SUPER_ADMIN** (no
`ROLES` entry references either outside the wildcard), so no account can
reach one twin without the other today. Not fixed here — changing either
route's required permission code is a real behavior change to a write
route's permission requirement, explicitly forbidden by this pass's own
non-negotiables.

**Also confirmed, separately, since it's adjacent:** unlike `employees.view_own`,
`employees.view_presence` (also EMPLOYEES-category, also granted to SALES)
**is** enforced — at `api_operations/routes.py:220`, gating a JSON API
route. It is not separately enforced on the `/employees` list/detail HTML
pages (which show presence unconditionally once the caller already holds
the broader `employees.view_all`) — but per the task's own bug-pattern-3
scope ("action buttons/forms"), that is passive data display, not a write
action, and is unaffected by whether the page-level `employees.view_all`
gate happens to be broader than a caller-specific presence permission in a
future role. Not flagged as a bug; noted for completeness.

## No ownership-shaped bug of the Stage-D.4-cash-closing class on `list_view`/`list_staff`/either `detail`

Investigated directly, not assumed: `list_view` (`@require_permission("employees.view_all")`),
`detail` (same), and `staff.list_staff`/`staff.detail` (both
`@require_permission("staff.view")`) each require the single, flat "view
all" permission outright — there is no `_own` variant of `employees.view_all`
or `staff.view` at all (confirmed via `seed_data.py`: `employees.view_own`
exists but is a distinct code with its own, unenforced, self-service
meaning investigated above — it is never accepted as an alternate/partial
gate on `list_view`/`detail`, there is no `"employees.view_own" in codes or
has_permission("employees.view_all")`-shaped bypass anywhere in
`employees/routes.py`). There is no code path into either admin list/detail
screen for a caller holding only the "own" half of a view permission split
the way Cash Closing's `cash_closing.view_own` once did — confirmed, not a
gap.

## An additional, real, live finding: `employees.view_all`/`staff.view` are currently SUPER_ADMIN-only

Verified against `seed_data.py`'s `ROLES` dict directly (not assumed): unlike
every domain touched in Stage D.3/D.4/D.5 (which each had at least a VIEWER-
or SALES-shaped role holding the relevant `*.view` code without the matching
`*.create`/`*.update` code — the exact shape that made "unconditional button"
bugs live, exploitable gaps there), **no role other than `SUPER_ADMIN`
(via the wildcard) is granted `employees.view_all` or `staff.view` at all.**
This means, today, the Employees admin list/detail and Staff Accounts
list/detail pages are reachable by no account except SUPER_ADMIN — which
holds every permission — so none of the "unconditional action button" bugs
described below are currently live/exploitable by any real account. This is
disclosed plainly rather than silently assumed away: every button below is
still gated with `has_permission(...)` for defense-in-depth and consistency
with every prior Stage D pass's blanket policy (`components/table.html`'s
own `toolbar()` macro gates its "New X" button "regardless" of whether a
live gap is currently provable, per its own docstring) — not because a
live gap was found on these two specific screens, which is a materially
different situation from Licensing/Commercial-Sales/Finance's own VIEWER-
shaped live gaps.

## Timeline-visualization decision: badge + existing plain audit trail, not `steps()`

Investigated honestly per the task's own instruction, not forced. Employee
employment status (`app.employees.services.ALLOWED_TRANSITIONS`):

```
PENDING     --> {ACTIVE, SUSPENDED, TERMINATED}
ACTIVE      --> {SUSPENDED, TERMINATED}
SUSPENDED   --> {ACTIVE, TERMINATED}
TERMINATED  --> {ARCHIVED}
ARCHIVED    --> {}   (terminal)
```

This is an HR employment record, not a document workflow. `ACTIVE<->SUSPENDED`
is the only real cycle (an employee can be suspended and reactivated more
than once). There is no single "done" milestone comparable to License's
EXPIRED or Quote's ACCEPTED — PENDING→ACTIVE is onboarding completing, and
TERMINATED→ARCHIVED is a records-retention action, not a business "success"
state. Forcing this into `components/commercial_record.html`'s `steps()`
linear-progress-bar macro would misrepresent the real lifecycle the same
way it would for Subscription/Installation (Stage D.5's own "Timeline-
visualization decision").

`employees/detail.html` already shows a real, already-persisted, per-change
"Audit timeline" — `AuditLog` rows filtered to
`entity_type == "employee_profile"`, ordered `created_at desc`, limited to
50 (`employees/routes.py::detail`) — the honest, existing chronological
record. This is exactly the "badge + plain history" shape Stage D.5 chose
for Subscriptions/Installations. No new timeline component was built; the
existing table is unchanged.

## Real bugs found and fixed

1. **`staff.list_staff` had no pagination/`.limit()` at all** — every
   `StaffUser` row was fetched on every `/staff` page load, the same
   disclosed performance-risk class already fixed for Subscriptions/
   Licenses/Installations in Stage D.5. Fixed via
   `staff/list_queries.py::list_staff_accounts` + `paginate()` (page_size=25),
   with real, additive `status` (derived active/disabled, reproducing the
   template's own pre-existing `is_active and not disabled_at` condition),
   `search` (over `display_name`/`email`), and `sort`/`dir` (over
   `display_name`/`email`/`created_at`) — reproducing the exact prior
   `select(StaffUser).order_by(StaffUser.created_at.desc())` behavior when
   no query params are present.
2. **Missing badge-color centralization on Employees list/detail** —
   `employees/list.html`'s and `employees/detail.html`'s employment-status
   ternary was duplicated verbatim in two places; centralized into
   `employees/status_presentation.py::employment_status_badge_class`. The
   presence-status ternary had the same duplication; centralized into
   `presence_badge_class`. **Verified, not assumed, that there was no
   actual color-coverage gap to fix** (unlike Subscription/Installation/
   License in Stage D.5, which each had a real missing-danger-branch bug):
   `app.models.employees.EMPLOYMENT_STATUSES` is exactly
   `("PENDING", "ACTIVE", "SUSPENDED", "TERMINATED", "ARCHIVED")` — the
   same 5 values `employees/list.html`'s own status filter already
   enumerated — and the pre-existing ternary already covered all 5 with no
   fallthrough gap (ACTIVE→active, PENDING/SUSPENDED→warn,
   TERMINATED/ARCHIVED→danger). Reproduced byte-for-byte.
3. **Unconditional action buttons/forms bypassing the real permission** —
   found on both screens, the same bug class every prior Stage D pass
   found (see the caveat above: not currently live, since both admin
   screens are SUPER_ADMIN-only today, but gated anyway for defense-in-
   depth/consistency):
   - `employees/list.html`: "Add employee" (→ `employees.new_form`, needs
     `employees.create`) and "Pending invitations" (→
     `employees.open_invitations`, whose own route decorator requires
     `employees.create`, a **stricter** permission than the `employees.view_all`
     the list page itself requires) were both unconditional links.
   - `employees/detail.html`: "Roles and permissions" (`staff.assign_roles`),
     "Edit profile" (`employees.update`), Activate (`employees.update`),
     Suspend/Reactivate (`employees.suspend`), Terminate (`employees.terminate`),
     Archive (`employees.terminate` — the `archive` route reuses the
     terminate permission, not a separate one), "Revoke all sessions" and
     per-session "Revoke" (`security_sessions.revoke`) were all
     unconditional.
   - `staff/list.html`: "Invite staff" form (`staff.create`) and the
     per-invitation "Revoke" button (`staff.create`, matching
     `revoke_invite`'s own decorator) were unconditional.
   - `staff/detail.html`: "Change roles" (`staff.assign_roles`),
     "Reset MFA" (`staff.reset_mfa`), "Disable account" (`staff.disable`)
     were unconditional.
   All fixed via `has_permission(...)` wraps around each form/button (or,
   for the toolbar's "Add employee", `table.toolbar(new_permission=...)`),
   matching the discipline of every prior Stage D pass.
4. **Missing `(requires recent authentication)` disclosure on
   `@require_recent_auth`-gated actions** — verified against the real
   decorators in `employees/routes.py`/`staff/routes.py`: `suspend`,
   `reactivate`, `terminate`, `archive`, `revoke_all_sessions`,
   `revoke_one_session` (employees) and `update_roles`, `disable`,
   `reset_mfa_route`, `invite` (staff) all carry `@require_recent_auth`,
   but no button communicated it — unlike `licensing/detail.html`'s
   already-established "(requires recent authentication)" convention from
   Stage D.5. Added to every one of those buttons' labels, on both
   `employees/detail.html` and `staff/detail.html` (the "Roles and
   permissions"/"Change roles" form appears on both pages, posting to the
   same `staff.update_roles` route — labeled on both). `Activate`
   (`employees.update` only, no recent-auth decorator) deliberately did
   **not** get the label — verified it genuinely has none.
5. **`staff/list.html`'s "Pending invitations" sub-table had no
   `data-label` attributes** — a real, pre-existing gap on the
   `.responsive-table` mechanism this sub-table intentionally keeps (see
   "Explicitly out of scope" below): below 720px, the stacked-card view
   would have shown unlabeled values. Fixed by adding the same
   `data-label="..."` attributes every other `.responsive-table` instance
   in the app already carries — a markup-only fix to the table this pass
   deliberately did not migrate off `.responsive-table`, not a new
   mechanism.

## Why not `table.filter_bar()` for `employees/list.html`

`components/table.html`'s `filter_bar()` macro supports one status-shaped
`<select>` + search + an optional sort/direction `<select>` pair — the
shape every other Stage D screen (Customers, Leads, Subscriptions,
Licenses, Installations, Activation Requests) actually needed. Employees
has 4 real, independent, already-functional filters
(`department`/`employment_status`/`role_code`/`presence`) plus search —
calling `filter_bar()` more than once would split them across multiple
`<form>` elements (a browser GET only serializes the fields inside the
form that was actually submitted, so combined filtering would silently
break), and there is no parameter shape in the macro today for "4
independent selects in one form." `employees/list.html` instead uses a
hand-written `<form method="get" class="filters aura-list-filters">` that
reuses the exact same CSS classes and auto-submit-on-change convention
`filter_bar()` itself uses, for visual parity, without pretending the
macro's narrower shape fits — the same "reuse the system's visual
language, don't force a mismatched shape through its macro API" judgment
call the task asked to be made honestly rather than either skipped or
faked.

## Why no `sort_key` column-header links on `employees/list.html`

`app.employees.queries.list_employees` (unmodified this pass, verified
correct) has **no `direction`/`dir` parameter at all** — it always orders
ascending regardless of any query param, `stmt.order_by(sort_columns.get(sort,
EmployeeProfile.full_name), EmployeeProfile.id)` with no `.asc()/.desc()`
branch. `components/table.html`'s `table()` macro sort-link mechanism
always offers a working-looking ascending/descending toggle (that is how
every other migrated screen's real `ORDER BY` behaves) — wiring it here
would render a descending arrow that silently has no effect on row order,
a fabricated control. This phase's own principle against fabricating a
timestamp generalizes directly to fabricating interactivity: not done.
Instead, the real, pre-existing `?sort=` capability (`full_name`/
`employee_number`/`employment_start_date`/`employment_status`, always
ascending) — previously reachable only by hand-editing the URL, since the
old template had no UI for it at all — is now exposed via a real "Sort by"
`<select>` in the filter form, the same "select instead of column-header
link" solution Leads' card grid already established for a screen the
`<th>`-click mechanism doesn't fit, applied here for a different but
related reason (no direction support to toggle, not "no `<th>` to attach
to"). This is additive UI exposure of an already-real, already-tested
backend capability — not a query-layer change.

## Explicitly out of scope (with the real reason)

- **`owner/app/templates/employees/new.html`, `invitations.html`,
  `invitation_created.html`; `owner/app/templates/staff/invitation_created.html`** —
  not named in the task's scope (`employees/list.html`+`detail.html`,
  `staff/list.html`+`detail.html` only). Read for context (to verify
  linked-permission requirements for gating decisions above) but not
  modified.
- **`staff/list.html`'s "Pending invitations" sub-table migration to
  `table()`** — audited and deliberately left on `.responsive-table`
  (only its missing `data-label` attributes were fixed, see "Real bugs
  found and fixed" #5): it is a small, bounded, per-company collection
  (open invitations self-clean via acceptance/expiry/revocation) with no
  real filter/sort dimension of its own — the same reasoning Stage D.5
  applied to `licensing_admin/signing_keys.html`'s pagination (“bounded by
  a real, different scale class, not customer/record count”) and to
  License's `entitlements`/Installation's `devices` child sub-tables
  (“not the ‘list screens’ this task named”).
- **Adding `@require_permission("employees.view_own")` enforcement to
  `/profile`** — investigated at length above; not done. Real risk of
  locking out every currently-working SUPPORT/FINANCE/VIEWER account's
  self-service profile access, for a permission code whose only plausible
  enforcement point doesn't structurally need it (`/profile` is IDOR-proof
  by URL shape, not by permission check). Disclosed as a real,
  out-of-scope finding for a future, deliberate pass — not silently
  resolved either way.
- **Reconciling the `employees.assign_role` (API) vs. `staff.assign_roles`
  (web UI) permission-code split** — investigated and disclosed above; not
  fixed. Changing either route's `@require_permission` code is a real
  behavior change to a write route's permission requirement, explicitly
  forbidden by this pass's non-negotiables (and, per `seed_data.py`,
  neither code is granted to any role but SUPER_ADMIN today, so there is
  no live account this split currently affects either way).
- **Gating `employees/list.html`'s "Dashboard" link with `has_permission`** —
  audited: it points at `employees.dashboard`, which requires the exact
  same `employees.view_all` permission the list page itself already
  requires to be reached at all. Gating it would be a redundant check with
  no caller for whom it could ever differ from the page's own gate — left
  ungated, unlike "Add employee"/"Pending invitations" (both genuinely
  stricter than `employees.view_all`).
- **`employees.view_presence`'s non-enforcement on the HTML list/detail
  pages** — noted under the `employees.view_own` investigation above for
  completeness; not in scope to fix (a passive-data-display question, not
  one of the task's named "action button" bug patterns, and changing what
  data a page displays based on a narrower permission than its own page
  gate is the same class of behavior change this pass's non-negotiables
  restrict).

## Ground-rules verification

- `grep -n '_(".*") %[^(]'` across every new/changed file in this pass
  (`employees/status_presentation.py`, `staff/list_queries.py`,
  `staff/routes.py`, `i18n.py`, `i18n_labels.py`, `employees/list.html`,
  `employees/detail.html`, `staff/list.html`, `staff/detail.html`) returns
  zero matches. The one candidate flagged for double-checking,
  `staff/detail.html`'s pre-existing `_("Disabled (%(reason)s)",
  reason=staff.disabled_reason)`, was re-verified: it already uses the
  correct `_("...", name=value)` form and was left unchanged.
- No new permission code invented — every code referenced already exists
  in `owner/app/staff/seed_data.py` and is already enforced by a real
  route decorator, unchanged. No `@require_permission`/`@require_recent_auth`
  decorator was added, removed, or relaxed on any route — every write
  route touched by this pass's UI (`edit`, `activate`, `suspend`,
  `reactivate`, `terminate`, `archive`, `revoke_all_sessions`,
  `revoke_one_session`, `open_invitations`, `invite`, `revoke_invite`,
  `update_roles`, `disable`, `reset_mfa_route`) is byte-for-byte unchanged
  in this diff; only `staff.list_staff` (a GET route) was refactored to
  call a new list-query service function with identical default ordering
  to the inline query it replaced.
- `owner/tests/test_phase9_5b_r_rtl_table_structure.py` was updated because
  it directly, and correctly at the time, encoded assumptions about
  `employees/list.html`'s specific `.responsive-table` markup that this
  pass deliberately changed (the same markup-level regression guard every
  prior Stage D migration would have needed to touch, had this particular
  test existed for Customers/Leads/Subscriptions/Licenses/Installations —
  it didn't, since it predates those migrations and was never generalized
  the way `test_phase9_5b_r2_responsive_tables.py`'s `NEW_DIRS` list was).
  The regression it guards against (`.responsive-table`'s `thead {
  display: none; }` CSS rule having a real `<thead>` to match) still has a
  live, real target: `staff/list.html`'s intentionally-unmigrated
  invitations sub-table, which the retargeted test now exercises via a
  real `create_invitation()` call and a real `GET /staff`, not a stale
  assumption.
- Every `url_for()` call added/kept was cross-checked against the real
  registered endpoint names in `employees/routes.py`/`staff/routes.py`.
- No new hardcoded colors/spacing — every class used (`.badge`, `.aura-*`
  table/pagination classes, `.filters`/`.aura-list-filters`) already
  existed from the Enterprise Table System / prior Stage D passes; no new
  CSS was written this pass.
- No new JS/CDN/framework dependency; no inline `<script>` was added
  anywhere (none was needed — every new control is a real GET-form
  auto-submit or a real POST-form submit).
- RTL: no new CSS was written (see above), so no `left`/`right` physical
  property was ever a risk; the one new inline style added
  (`display:inline-flex;align-items:center;gap:...` on the "Include
  archived" checkbox label) uses only logical/direction-agnostic
  properties.

## Verification run

Targeted, before the full suite (all passed, 78/78):
`test_phase9_5b_r_rtl_table_structure.py`, `test_phase9_5b_r_hardcoded_strings.py`,
`test_phase9_5b_r2_owner_wide_template_rendering.py`,
`test_phase9_5b_r2_responsive_tables.py`, `test_phase9_5b_r_template_rendering.py`,
`test_phase9_5b_r_bilingual_e2e.py`, `test_phase9_5b_web_routes.py`,
`test_rbac.py`, `test_phase9_5b_authorization.py`, `test_security.py`,
`test_phase9_5b_management_sync.py`, `test_phase9_5b_queries.py`,
`test_phase9_5b_query_performance.py`.

Full suite (independently re-run, not trusted from the agent's own claim):
`OWNER_TEST_DATABASE_URL=postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_test_uiux`
(the dedicated Stage D/UIUX test database) — `pytest owner/tests -q` from
the `owner/` dir — **1,063 passed, 0 failed** (0:32:00). Fully clean — run
outside the 00:00–09:00 JST window that produces the two known, unrelated
pre-existing failures documented in `finance-ui-contract.md`, so this is
the cleanest full-suite result of any Stage D pass so far.

## Independent review — findings

Every new/changed file was read in full and cross-checked against the real
route decorators, permission table, and model columns before committing
(same discipline as every prior Stage D pass). Unlike Stage D.5, which had
one real regression caught during review, **every claim in this pass's
diff was independently verified accurate**: every `has_permission(...)`
gate added to an action button/form was checked against the real
`@require_permission` decorator on its target route and matched exactly;
every `(requires recent authentication)` label was checked against the
real presence/absence of `@require_recent_auth` and matched exactly
(including the one negative case — `Activate` correctly did **not** get
the label, since `employees.activate` genuinely has no recent-auth gate);
the `employees.view_own` investigation's core claim (unenforced anywhere)
was re-confirmed via a fresh grep; the `employees.assign_role`/
`staff.assign_roles` twin-code claim and the "neither granted to any role
but SUPER_ADMIN" claim were both re-confirmed directly against
`seed_data.py`. The `test_phase9_5b_r_rtl_table_structure.py` change was
read in full and confirmed to be a legitimate retarget (not a weakened
assertion) — the regression it guards against still has a real, live
target on `/staff`'s unmigrated invitations sub-table.

Curl-verified against a locally-run dev server
(`OWNER_DATABASE_URL`/`OWNER_ENV=development`, same `aura_owner_test_uiux`
DB, port 5000) for 2 real accounts: a SUPER_ADMIN account (`/employees` and
`/staff` both load with real filter/sort query combinations —
`?sort=employment_status&department=`, `?sort=email&dir=asc&status=ACTIVE`
— and `/staff/<id>` renders all three permission-gated action buttons with
their correct "(requires recent authentication)" labels) and a VIEWER-role
account (`GET /employees` and `GET /staff` both return a real `403`,
empirically confirming this pass's one live, documented behavioral fact —
that neither admin screen is reachable by any role but SUPER_ADMIN today —
rather than trusting the `seed_data.py` read alone).
