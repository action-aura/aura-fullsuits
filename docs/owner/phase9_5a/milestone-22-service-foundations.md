# Phase 9.5A Milestone 22 — Service and Repository Foundations

Real, minimal proving services only — per the governing spec's own explicit instruction: "Do not put
business logic directly in Flask routes... Do not implement the complete sales, invoice, payout, and
expense UI workflows in this phase." No Flask routes/blueprints were added this milestone (service-layer
only, matching the spec's explicit scope) — every function below is called directly from tests, proving the
architecture (models + services + audit) works end-to-end without yet exposing an HTTP surface.

## Files created

| Module | Functions | Reuses |
|---|---|---|
| `owner/app/employees/services.py` | `create_employee_profile`, `update_employee_profile`, `suspend_employee`, `terminate_employee`, `touch_presence`, `revoke_presence` | `revoke_all_sessions_for_staff()` (Phase 8V, `owner/app/auth/session.py:128`) — no new revocation mechanism |
| `owner/app/leads/ownership.py` | `apply_ownership_filter(stmt, model, actor_employee_profile_id, *, all_permission_held)` | Shared by every Lead/Customer query per `record-ownership-policy.md`'s own instruction — one helper, not reimplemented per-route |
| `owner/app/leads/services.py` | `create_lead`, `list_own_leads`, `list_all_leads`, `change_lead_status`, `assign_lead`, `add_lead_note`, `capture_location` | `owner/app/services/pagination.py`'s real `paginate()` (unfiltered/filtered count computed from the same statement, per the anti-enumeration requirement) |
| `owner/app/leads/conversion.py` | `convert()` | `owner/app/customers/services.py`'s real `find_duplicate_candidates()` — no new duplicate-detection logic |
| `owner/app/commercial_sales/device_policy.py` | `resolve_device_policy()` | Read-only; does not touch `License.device_limit`/`resolve_effective_device_limit()` — enforcement-wiring boundary honored |
| `owner/app/commissions/services.py` | `calculate_commission()`, `resolve_active_rule()`, `preview_commission()` | Exact `Decimal`/`ROUND_HALF_UP` formula from `commission-calculation-contract.md`; preview never writes a `CommissionLedgerEntry` |
| `owner/app/daily_reports/services.py` | `build_synthetic_snapshot_structure()` | Structure-only, per Milestone 22's own scope — no DB aggregation, no `DailyActivitySnapshot` row written |
| `owner/app/catalog/services.py` (extended) | `read_active_catalog()` | Existing `Plan`/`PlanPrice` "current price" pattern already used by `add_plan_price()` (`effective_until IS NULL`) |

## Real bug caught and fixed during this milestone

`create_session()` (`owner/app/auth/session.py`) reads `request.remote_addr` — calling it outside a real
Flask request context raises `RuntimeError: Working outside of request context`. Two new tests
(`test_suspend_employee_revokes_sessions`, `test_terminate_employee_sets_end_date_and_revokes_sessions`)
hit this on first run. Fixed by wrapping the `create_session()` call in `app.test_request_context()`,
matching the existing `_windows_platform_id`-style helper pattern already used elsewhere in this test
suite — not a bug in the service code itself, a test-setup gap caught and corrected before commit.

## Design decision recorded: linking to an existing customer also activates it

`lead-conversion-contract.md` reads "new Customer row (or link to `existing_customer_id`) with
`lifecycle_status='ACTIVE'`" — read literally, both branches set `ACTIVE`, not only the create-new branch.
Implemented exactly that way: `convert()` with `existing_customer_id` set also writes
`lifecycle_status="ACTIVE"` and `converted_from_lead_id=lead.id` on the linked row (previously only the
create-new branch did this in the first draft — corrected before the test suite was written, confirmed
by `test_convert_with_existing_customer_id_links_instead_of_creating`).

## Real test coverage

29 new tests across 7 files (`owner/tests/test_phase9_5a_employee_services.py`,
`test_phase9_5a_lead_services.py`, `test_phase9_5a_lead_conversion.py`, `test_phase9_5a_device_policy.py`,
`test_phase9_5a_commission_preview.py`, `test_phase9_5a_catalog_read.py`,
`test_phase9_5a_daily_snapshot_structure.py`), all passing, calling every new service function directly
(no HTTP layer to test yet). Full existing Owner regression re-run after this milestone to confirm zero
impact on Phase 5-9 behavior (see `milestone-24-security-tests.md` and the final decision doc).
