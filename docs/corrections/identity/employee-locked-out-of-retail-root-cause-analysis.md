# Correction — Screen-Created Employees Locked Out of Retail

Status: **FIXED and mutation-proven both directions (2026-09-05)**

## Symptom

Observed 2026-09-05 on a two-device rehearsal (a real phone AND a real
desktop till), same cashier account, same reason on both:

- UI (phone): "Blocked by your subscription/license: Access denied to
  retail. Contact your Admin."
- Raw API (both devices), on every `/api/sub/retail/*` route:
  `{"code": 403, "error": "Access denied to retail. Contact your Admin."}`

The account held `retail.sell=full` at the time — it could not sell, take a
payment, or open a cash drawer.

## Root cause

`commercial_runtime/identity/mt_auth.py::mt_require_subsystem('retail')` — the
blanket gate on ~80 retail routes — ran a literal
`SELECT access_level FROM user_permissions WHERE user_id=? AND subsystem=?`
with `subsystem='retail'`, and refused whenever that row was missing or
`'none'` (previously mt_auth.py ~lines 749-767).

Nothing the product ships ever writes that row: `onboarding_routes.create_employee`
(~line 915: `perms = data.get('permissions', {})`, inserted ~line 1039) inserts
only what the request body's `permissions` key carries; both real creation
screens send `{email, role}` and nothing else
(`products/retail/frontend/employees.js:611`, Android `AuraApi.kt:315`); and
`user_accounts.seed_capabilities_for_user` writes only the eight namespaced
codes (`retail.sell`, `retail.refund`, `retail.discount`,
`retail.stock.adjust`, `retail.reports`, `retail.cash.close`,
`retail.cash.approve`, `retail.employees`) — never the bare `'retail'` row.

So every cashier and manager created through the product's own screens held
namespaced capability rows and no legacy row at all, and the blanket gate
refused them regardless. `docs/launch-readiness/multi-device-design.md:35`
says the blanket literal is *replaced* by capability codes; the
implementation stacked the fine-grained gate (`mt_require_capability`) on
top of the old one without ever removing it.

## Why every suite was green

27 retail test files hand-insert the legacy row before every request, e.g.
`products/retail/tests/retail_route_capability_matrix_test.py:727-730`, whose
own comment says why: "mt_require_subsystem still demands the legacy
un-namespaced grant, so without this the request never reaches the
capability check." That describes a fixture working around a known-bad gate,
not a gate that was already correct — every one of those 27 files
manufactured the exact row production never has, so none could ever have
caught this.

## Fix

The blanket gate is now a derived view of the namespaced capability rows,
implemented once and read from both callers:

- `commercial_runtime/identity/user_accounts.py`: new
  `user_holds_subsystem(conn, user_id, subsystem)` — an explicit row for
  `subsystem` itself decides if present (see below); otherwise held iff at
  least one `<subsystem>.<code>` row is granted; with no rows at all, denied.
- `commercial_runtime/identity/mt_auth.py`: new `_read_subsystem(user_id, subsystem)`
  delegates to it (mirroring `_read_capability`/`user_has_capability`), and
  `mt_require_subsystem`'s inline SELECT is replaced with a call to it.

## What the fix deliberately keeps

- **An explicit legacy `'none'` row still revokes the subsystem outright** — a
  decision an admin made on purpose through `update_perms` (which still
  accepts `'retail'`/`'clinic'` via `_LEGACY_SUBSYSTEMS`), and it must keep
  working even when every namespaced code is granted.
- The admin bypass (`role == 'admin'` → always allowed) is unchanged.
- The `_is_module_enabled` license/module check (402) is unchanged.
- Fail-closed behavior on a registry read failure is unchanged — a lookup
  failure still refuses the request, logged, via `_subsystem_denied_response`.

## Tests

- `commercial_runtime/identity/tests/test_user_holds_subsystem.py` (8 tests) —
  unit coverage of the derived rule, including the dot-boundary guard and the
  with/without-`row_factory` positional-indexing contract.
- `products/retail/tests/retail_employee_created_via_screen_can_work_test.py`
  (3 tests) — end-to-end through the real `POST /api/admin/employees` route
  with the exact `{email, role}` body the Employees screen sends: a
  screen-created cashier can now reach `/api/sub/retail/customers`; an
  explicit legacy revocation still locks it out; so does revoking every
  namespaced code.

## Verification

Run 2026-09-05 by an independent pass, from the worktree, one retail file per
process. Green baseline: `test_user_holds_subsystem.py` 8 passed;
`test_employee_admin_routes.py` 48 passed;
`retail_employee_created_via_screen_can_work_test.py` 3 passed;
`retail_route_capability_matrix_test.py` 46 passed (after the fixture repair
below); `retail_employee_management_test.py` 33 passed;
`retail_auth_hardening_test.py` 16 passed.

**Mutation A — bug restored** (`user_holds_subsystem` made to ignore the
namespaced rows: `if False and name.startswith(prefix) ...`):
`test_a_cashier_created_from_the_employees_screen_can_use_the_retail_app`
went red with exactly `{'code': 403, 'error': 'Access denied to retail.
Contact your Admin.'}`; the two unit tests that stand on capability codes
alone went red; the two revocation tests stayed green, as they must (they
cannot tell this mutation from the fix — that is why the allow-half has its
own test).

**Mutation B — fail-open** (both `return` statements made `return True`):
`test_an_explicit_legacy_revocation_still_locks_the_cashier_out` and
`test_a_cashier_denied_every_capability_is_refused` went red (the gate
answered 200 with data), five unit tests went red (no rows, explicit
revocation, all codes denied, other subsystem, dot-boundary). Source restored
after each run; `git diff --stat` back to the +53-line fix only.

**On the real artefact:** the desktop till on :5010 was restarted on the
fixed code and probed as the very cashier that had been refused —
`GET /api/sub/retail/customers` 200, `POST /api/sub/retail/customers` 200
(customer `db0b432c-…` created) — same session shape as the 403 above.

**One pre-existing test had to change**, and here is exactly what it can no
longer catch: `retail_route_capability_matrix_test.py::
test_import_routes_now_carry_the_subsystem_gate` built a manager with seeded
codes and NO legacy row and asserted 403 — the bug restated as the
expectation. Its helper now writes an explicit `('retail', 'none')`
revocation instead, so the test still proves the subsystem gate runs on the
five import routes (the capability gate alone would answer 400 from the
handler). It no longer catches "a manager with no legacy row reaches an
import route", because that is now the intended behaviour, pinned by the
can-work test.

**On the phone:** the embedded backend is baked into the APK at build time,
so the Mi Note 10 kept the old gate until `assembleDebug` was rebuilt from
this tree (the staged copy was checked to contain `user_holds_subsystem`) and
installed over the running app with its data kept. Signed in as the same
cashier, the Customers screen went from "No customers yet" (the list call
was 403) to a populated list, and "Add customer" saved ("Customer added") —
the row then reached the desktop till through the sync relay within 45 s.
Same account, both devices, both directions.
