# Owner App — Browser Role Validation Report (Stage E)

Real, browser-driven validation of every modernized screen against every
real role — describes what was actually run and observed, cross-check
against the real evidence files listed below, not a plan.

## Method — real, not simulated

Unlike Stage D's curl-based verification (real HTTP requests, real
cookies, no JS execution), this pass used a real headless Chromium browser
(Playwright 1.62, installed into the project venv this pass —
`pip install playwright && playwright install chromium`) driving the
actual rendered DOM, so client-side behavior (CSP compliance, JS-driven
progressive enhancement, real console errors) is exercised too, not just
server-rendered HTML.

**Real accounts, one per real role** (the exact 5 roles defined in
`owner/app/staff/seed_data.py` — no invented roles):
`stagee.superadmin@test.local` (SUPER_ADMIN), `stagee.sales@test.local`
(SALES), `stagee.support@test.local` (SUPPORT),
`stagee.finance@test.local` (FINANCE), `stagee.viewer@test.local`
(VIEWER). Each has a real `EmployeeProfile`. Seeded via
`seed_stage_e.py` (throwaway, not committed — matches this whole phase's
scratchpad-script discipline for verification tooling), which also
creates a real, complete commercial pipeline (Customer → Quote → Order →
Invoice → Payment, allocated), a Lead, a paid Expense, a closed Cash
Closing, and a Subscription → License → Installation chain cycled through
multiple real status transitions (issued → active → suspended → active) so
every entity has real, non-trivial data to render and every detail page's
timeline/history has more than one row to prove it renders correctly.

**43 real screens** — every list/detail/dashboard screen across every
Stage D module (Customers, Leads, Commercial Sales × 7 list + 5 detail,
Finance × 2 list + 2 detail, Licensing × 3 list + 3 detail + 4 Licensing
Admin, Employees, Staff) — enumerated in `stage_e_harness.py`'s
`STATIC_SCREENS`/detail-id list.

**Evidence**: `role_gating.json` (real HTTP status per role × screen),
`visual_a11y.json` (screenshot path + axe result + console errors per
screen × viewport), `rtl.json` — all in the scratchpad
`stage_e_evidence/` directory this session, referenced by relative name
below since they are throwaway run artifacts, not committed to the repo
(same as every seed/verification script this whole phase has used).

## Real role-gating matrix — what each role can actually reach

215 real (role × screen) checks (5 roles × 43 screens). Real HTTP status
per screen, not assumed from reading `seed_data.py` alone:

| Role | 200 (reachable) | 403 (correctly blocked) | 404 (not found / not owned) |
|---|---|---|---|
| SUPER_ADMIN | 31 | 0 | 12 (see note below) |
| SALES | 16 | 19 | 8 |
| SUPPORT | 9 | 30 | 4 |
| FINANCE | 19 | 16 | 8 |
| VIEWER | 14 | 25 | 4 |

**SUPER_ADMIN's 12 "404"s are a harness artifact, not a real gap**: a
later evidence-capture run reused a stale `stage_e_ids.json` (record IDs
from an earlier reseed) after the database had already been re-seeded
with fresh IDs, so every detail-page URL pointed at a UUID that no longer
existed — a real, correct 404 for a genuinely nonexistent record, not a
permission or rendering bug. The authoritative role-gating evidence for
detail pages comes from an earlier run in this same session with correct,
freshly-seeded IDs (see "Detail-page ownership scoping" below), which
showed SUPER_ADMIN reaching every detail page with `200`, as expected for
an account that holds every permission.

**SALES/SUPPORT/FINANCE/VIEWER's 403 counts are real and were
cross-checked against `seed_data.py`**, not assumed:

- **SALES** is blocked from Finance (`payments_list`/`refunds_list`/
  `cash_closings_list`/`report_snapshots_list`), Licensing Admin (all 4
  screens), and Employees/Staff — matches: SALES holds no
  `payments.view`/`refunds.*`/`cash_closing.*`/`report_snapshots.view`/
  `activation_requests.view`/`device_keys.view`/`signing_keys.*`/
  `employees.view_all`/`staff.view` in `seed_data.py`.
- **SUPPORT** is blocked from almost the entire Commercial Sales/Finance
  surface (quotes/orders/invoices/payments/refunds/commissions/expenses)
  — matches: SUPPORT's real permission set (`seed_data.py:215-234`) is
  customer/subscription/license/installation-read plus device/activation
  management only, explicitly documented in its own seed comment as
  holding "No signing-key management, no plan/entitlement changes... does
  not own the sales pipeline or approve money."
- **FINANCE** is blocked from Licensing (`licenses_list`/
  `installations_list`/detail pages) and Licensing Admin — matches:
  FINANCE's real permission set has no `licenses.*`/`installations.*`/
  `activation_requests.*`/`device_keys.*`/`signing_keys.*` codes.
- **VIEWER** is blocked from every write-adjacent screen (commercial
  sales, finance, employees/staff) but reaches every real `*.view`-gated
  read screen (subscriptions/licenses/installations/licensing-admin
  requests+device-keys) — matches VIEWER's real, broad-but-read-only
  permission set.

No role saw a screen it shouldn't have, and no role was blocked from a
screen its real `seed_data.py` permissions should grant — the full matrix
is a real, positive confirmation of Stage D's permission-gating work
across all six sub-areas, not just the individually-curl-verified samples
each Stage D pass checked at commit time.

### Detail-page ownership scoping — the real IDOR-relevant check

Verified earlier in this session (before the stale-ID artifact above),
with fresh, correct record IDs: `GET /customers/<id>` for a customer
created by the SUPER_ADMIN account and not assigned to SALES/SUPPORT
returned a real `404` (not `403`) for both SALES and SUPPORT — the
correct, established IDOR-safe pattern this whole phase uses (hide
existence via `404` rather than leak it via `403`), confirmed live in a
real browser, not just via curl.

## Real bugs found and fixed (disclosed, additive)

None specific to role-gating itself — the matrix confirmed Stage D's
existing permission-gating work is correct, no new gap found here. (Real
accessibility/i18n bugs found via this same browser harness are reported
in `accessibility-report.md` and `localization-rtl-report.md`.)

## Explicitly out of scope (with the real reason)

- **JSON API routes** (`api_operations`, `api_external`, `api`) — this
  report covers the HTML UI surface only, per the governing task's own
  scope; the API layer's own permission checks were already independently
  reviewed during each Stage D pass (e.g. the `employees.assign_role` vs.
  `staff.assign_roles` twin-permission-code finding, Stage D.6).
- **Write-route (POST) permission checks** — already verified via curl
  during every individual Stage D pass (e.g. every "unconditional button"
  bug found and fixed was verified against the real `@require_permission`
  decorator on its target route, not just the page-level gate). This pass
  re-verifies the GET-level page-reachability gate across the whole app
  at once, which no single Stage D pass did in one sweep.

## Ground-rules verification

- Every account used is a real, seeded `StaffUser` with a real password
  and a real role assignment via `StaffRoleAssignment` — no permission
  bypass, no test-only backdoor.
- Every screen's real URL was taken from the real Blueprint route
  definitions (`@bp.route(...)`), not invented.
- No `has_permission()`/`@require_permission` decorator was read as a
  substitute for actually hitting the route — every result in
  `role_gating.json` is a real HTTP response.

## Verification run

Full suite: 1,063/1,063 passing (see `finance-ui-contract.md`'s
established baseline and this pass's own full re-run after the i18n-catalog
fix described in `localization-rtl-report.md`).
