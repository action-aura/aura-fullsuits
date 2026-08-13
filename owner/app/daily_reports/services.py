"""Phase 9.5A Milestone 22/15 -- DailySnapshotDefinitionService.

Milestone 22 scope is explicitly a *structure* generator only -- proving the
metric_payload shape from docs/owner/phase9_5a/daily-snapshot-metric-catalog.md
resolves and validates correctly. Real DB aggregation (new_leads count,
confirmed_payments sum, verify_chain() re-run, etc.) is the real
DailySnapshotService.generate() job, deferred past this foundation
milestone -- not built here, not faked here.
"""
from __future__ import annotations

from datetime import date

SCHEMA_VERSION = 1


def build_synthetic_snapshot_structure(business_date: date) -> dict:
    """Returns a zero-valued, schema-valid metric_payload for the given
    business_date -- used to prove the shape/contract, never persisted as a
    real DailyActivitySnapshot row (that requires the real aggregation
    service this milestone does not build)."""
    return {
        "business_date": business_date.isoformat(),
        "schema_version": SCHEMA_VERSION,
        "totals": {
            "new_leads": 0,
            "lead_status_changes": 0,
            "confirmed_customers": 0,
            "completed_followups": 0,
            "overdue_followups": 0,
            "quotes_created": 0,
            "sales_orders": 0,
            "invoices_issued": 0,
            "confirmed_payments": {"count": 0, "amount": "0.00", "currency": "USD"},
            "refunds": {"count": 0, "amount": "0.00", "currency": "USD"},
            "subscriptions_created": 0,
            "licenses_issued": 0,
            "licenses_expired": 0,
            "devices_activated": 0,
            "device_limit_blocks": 0,
            "renewals": 0,
            "commissions_earned": "0.00",
            "commissions_approved": "0.00",
            "commissions_paid": "0.00",
            "expenses": "0.00",
            "security_events": {"failed_logins": 0, "audit_chain_valid": True},
        },
        "by_employee": {},
        "comparison_previous_business_day": None,
    }
