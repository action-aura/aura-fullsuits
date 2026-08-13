# Phase 9.5B Milestone 9 — Employee Dashboard Metric Implementation

`GET /employees/dashboard` (`require_permission("employees.view_all")`), real metrics from
`owner/app/employees/dashboard.py::get_employee_dashboard_metrics()`. Employee-domain only — not the
future business-wide dashboard.

## Exact definitions (never conflated, per the governing spec's own explicit instruction)

- **Total employees**: every `EmployeeProfile` row, `ARCHIVED` included (a real, stated choice — the
  headcount metric answers "how many profiles exist," archival is a display filter elsewhere, not a
  headcount exclusion).
- **Active employees**: `employment_status == "ACTIVE"` — HR/administrative state, says nothing about
  whether the person is currently online.
- **Online now / Recently active / Offline**: derived from `EmployeePresenceSession.last_seen_at` via the
  same real thresholds as everywhere else (`ONLINE_THRESHOLD_SECONDS`/`RECENTLY_ACTIVE_THRESHOLD_SECONDS`)
  — computed with `SELECT DISTINCT employee_profile_id` inside the threshold window, never a duplicate
  hand-rolled definition. `Offline = total_employees - online_now - recently_active` (correctly includes
  employees who have never sent a single heartbeat).
- **Setup pending**: identical to `pending_employees` (same real count, two names in the metrics dict
  because the spec lists both — deliberately not two different queries that could silently drift apart).
- **Employees without completed MFA**: `ACTIVE` employees whose `StaffUser.mfa_required` is `True` and
  who have no `MfaCredential` row, or an unconfirmed one — never conflated with "MFA not required."
- **Locked / disabled accounts**: `StaffUser.is_active == False` (covers `disable_staff()`,
  `suspend_employee()`, `terminate_employee()` alike). Explicitly **not** the same as a transient
  rate-limit lockout (`app.security.ratelimit.is_locked_out()`, computed from recent `LoginAttempt` rows,
  not a persisted state) — that would require iterating every account's login-attempt history per
  dashboard load, not a real per-request cost this metric should carry; the persisted `is_active` flag is
  the real, precise, indexed signal for "currently cannot log in by administrative decision."
- **Active sessions**: `StaffSession` rows with `revoked_at IS NULL AND expires_at > now()` — the same
  real validity condition `_get_valid_session()` uses, not a separately-maintained count.

Every metric links through to the correctly-filtered `/employees` list (drill-down, per the spec's own
requirement) using the exact same query parameters `list_employees()` already accepts — no separate
drill-down query path.

Real, tested proof: `owner/tests/test_phase9_5b_dashboard.py` (3 tests: exact definitions don't conflate
setup_pending with a second count, RBAC-gated, renders for management).
