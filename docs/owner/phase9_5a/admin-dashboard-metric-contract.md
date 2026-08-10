# Phase 9.5A Milestone 16 — Admin Dashboard Metric Contract

## Real, existing dashboard extended (not replaced)

`owner/app/dashboard/services.py` already exists with real queries (including the previously-dead
`pilot_customers`/`active_customers` counts this phase's Lead-conversion work finally makes meaningful
— `phase9-5a-baseline.md`). Extended with the new metrics below, same module, same pattern.

## Exact semantics (server-authoritative, precisely defined per the spec's own warning against mixing)

- **`total_employees`**: count of `EmployeeProfile` rows, any `employment_status`.
- **`active_employees`**: count where `employment_status == "ACTIVE"`. Distinct from...
- **`employees_online_now`**: count of employees with a valid `EmployeePresenceSession` where
  `last_seen_at` is within the `ONLINE` threshold (< 2 min, Milestone 5) **and** the underlying
  `StaffSession` is not revoked/expired **and** `EmployeeProfile.employment_status == "ACTIVE"` — all
  three conditions, matching the spec's exact `ONLINE NOW` definition. Distinct from...
- **`recently_active_employees`**: same but the `RECENTLY_ACTIVE` threshold (2-15 min). These three
  employee counts are never conflated in the API response — three separate, distinctly-named fields,
  matching the spec's explicit "do not mix active employees / online employees / employees with
  activity today" instruction.
- **`total_leads`** / **`potential_leads`** (`status == "POTENTIAL"`) / **`followups_due_today`** /
  **`overdue_followups`**: direct `Lead`/`lead_followups` counts, management-scoped (no ownership
  filter — this is the management dashboard).
- **`confirmed_customers`**: `Customer` rows with `lifecycle_status == "ACTIVE"` (real, now-meaningful
  count — see baseline doc).
- **`active_subscriptions`** / **`active_licenses`** / **`expired_licenses`** /
  **`licenses_expiring_soon`**: delegates to the real, existing Phase 8 licensing-state resolution —
  this dashboard never recomputes license/subscription state itself, only queries the canonical
  `Subscription.status`/`License.status` (and, where "expiring soon" needs a resolved date, the real
  existing `expiry_scan.py` logic's own definitions, reused not reimplemented).
- **`active_installations`** / **`device_overage_findings`**: the latter is literally
  `len(scan_over_limit_licenses(dry_run=True).findings)` — the exact real, existing Phase 8V-P9 scan
  function, called read-only, never a parallel computation.
- **`collected_revenue`** / **`expenses`** / **`commissions_pending`** / **`commissions_approved`** /
  **`net_cash_view`**: the exact functions from `financial-metric-definitions.md`, called for the
  dashboard's own selected period (default: current month).

## `ACTIVE LICENSE` / `EXPIRED LICENSE` — deferred to the canonical licensing authority

Per the spec's own instruction, these are **not** redefined here — they resolve to whatever
`owner/app/licensing/services.py`'s real, existing canonical lifecycle logic already says, plus the
effective commercial-policy check already used by Phase 8's own dashboard/reconciliation code. This
document does not introduce a second, competing definition.

## API endpoint

`GET /api/operations/v1/dashboard/admin` — permission `dashboard.view_all`. Every field above present;
no field silently omitted or merged with another for a management caller.
