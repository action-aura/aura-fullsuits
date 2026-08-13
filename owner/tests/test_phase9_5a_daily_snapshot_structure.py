from __future__ import annotations

from datetime import date

from app.daily_reports.services import SCHEMA_VERSION, build_synthetic_snapshot_structure


def test_synthetic_structure_matches_catalog_shape():
    structure = build_synthetic_snapshot_structure(date(2026, 8, 1))
    assert structure["business_date"] == "2026-08-01"
    assert structure["schema_version"] == SCHEMA_VERSION
    assert structure["totals"]["confirmed_payments"] == {"count": 0, "amount": "0.00", "currency": "USD"}
    assert structure["totals"]["security_events"] == {"failed_logins": 0, "audit_chain_valid": True}
    assert structure["by_employee"] == {}
    assert structure["comparison_previous_business_day"] is None


def test_synthetic_structure_is_jsonb_serializable():
    import json

    structure = build_synthetic_snapshot_structure(date(2026, 8, 1))
    # Round-trips cleanly -- proves the shape is a valid JSONB payload before
    # any real DailyActivitySnapshot.metric_payload row is ever written.
    assert json.loads(json.dumps(structure)) == structure
