# Phase 9.5A — Daily Snapshot Metric Catalog

`metric_payload` JSONB shape:

```json
{
  "business_date": "2026-08-01", "schema_version": 1,
  "totals": {
    "new_leads": 4, "lead_status_changes": 9, "confirmed_customers": 1,
    "completed_followups": 6, "overdue_followups": 2,
    "quotes_created": 3, "sales_orders": 2, "invoices_issued": 2,
    "confirmed_payments": {"count": 2, "amount": "2400.00", "currency": "USD"},
    "refunds": {"count": 0, "amount": "0.00", "currency": "USD"},
    "subscriptions_created": 1, "licenses_issued": 1, "licenses_expired": 0,
    "devices_activated": 2, "device_limit_blocks": 0, "renewals": 1,
    "commissions_earned": "180.00", "commissions_approved": "0.00", "commissions_paid": "150.00",
    "expenses": "75.00",
    "security_events": {"failed_logins": 1, "audit_chain_valid": true}
  },
  "by_employee": {
    "<employee_profile_id>": {
      "new_leads": 2, "completed_followups": 3, "confirmed_payments_amount": "1200.00",
      "commissions_earned": "90.00"
    }
  },
  "comparison_previous_business_day": {
    "new_leads_delta": 1, "confirmed_payments_amount_delta": "600.00"
  }
}
```

## Exact semantics (real, not left ambiguous)

- `new_leads`: count of `Lead` rows with `created_at` inside `[source_range_start, source_range_end)`.
- `overdue_followups`: count of `lead_followups`/`customer_followups` rows with `due_at < now()` and
  `completed_at IS NULL`, evaluated **as of snapshot generation time**, not backdated to midnight —
  matches the real, already-fixed Phase 8V-P9 precision lesson (a scan must evaluate time-sensitive
  state at real generation time, never truncate to a date boundary — the exact bug class
  `temporary-exception-precision-final.md` fixed in Phase 8V-P9, deliberately not repeated here).
- `confirmed_payments`: sum of `PaymentRecord.amount` where `status` is the real confirmed/verified
  value and `payment_date` falls in the business day — grouped by `currency` (a payload with mixed
  currencies reports each currency's own total, never a naively summed cross-currency figure).
- `security_events.audit_chain_valid`: the real `verify_chain()` result, re-run as part of snapshot
  generation (redundant with the scan bundle's own daily `verify_chain()` call, deliberately — two
  independent real checks of the same real invariant is not wasted duplication for something this
  security-critical).
- `comparison_previous_business_day`: computed by reading the prior `daily_activity_snapshots` row's
  own `metric_payload.totals` — if that row doesn't exist (e.g. first day of the system), the
  comparison block is omitted entirely, never a fabricated zero baseline.
