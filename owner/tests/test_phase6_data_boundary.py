"""Part Y DATA BOUNDARY: extends Phase 5's test_data_boundary.py coverage to
the Phase 6 tables and the external API surface."""
from __future__ import annotations

import json

import pytest

from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff

FORBIDDEN_TERMS = (
    "patient", "diagnosis", "prescription", "medical_note", "clinical_note",
    "sale_line", "invoice_line", "inventory_quantity", "customer_purchase",
    "local_database", "card_number", "bank_account", "appointment",
)


def test_no_phase6_table_or_column_matches_forbidden_term():
    from app.models import Base

    phase6_tables = [
        "owner_signing_keys", "owner_device_public_keys", "owner_activation_requests", "owner_signed_assertions",
        "owner_entitlement_snapshots", "owner_external_idempotency_records", "owner_offline_policies",
        "owner_license_offline_policy_assignments", "owner_security_nonce_records", "owner_key_rotation_events",
        "owner_rate_limit_counters", "owner_service_health_events",
    ]
    tables = {t.name: t for t in Base.metadata.sorted_tables}
    for name in phase6_tables:
        table = tables[name]
        for column in table.columns:
            lowered = column.name.lower()
            for term in FORBIDDEN_TERMS:
                assert term not in lowered, f"{name}.{column.name} matches forbidden term '{term}'"


def test_assertion_payload_guard_rejects_forbidden_field():
    from app.licensing_service.assertions import AssertionError_, _guard_payload

    with pytest.raises(AssertionError_):
        _guard_payload({"card_number": "4111"})
    with pytest.raises(AssertionError_):
        _guard_payload({"patient_name": "x"})
    # a legitimate assertion payload passes through unchanged
    safe = _guard_payload({"license_public_id": "abc", "entitlements": {}})
    assert safe == {"license_public_id": "abc", "entitlements": {}}


def test_no_licensing_service_module_imports_products_or_commercial_runtime():
    import pathlib
    import re

    licensing_service_dir = pathlib.Path(__file__).resolve().parent.parent / "app" / "licensing_service"
    api_external_dir = pathlib.Path(__file__).resolve().parent.parent / "app" / "api_external"
    offenders = []
    for directory in (licensing_service_dir, api_external_dir):
        for py_file in directory.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            if re.search(r"^\s*(from|import)\s+(products|commercial_runtime)\b", text, re.MULTILINE):
                offenders.append(str(py_file))
    assert offenders == []


def test_activation_response_contains_no_forbidden_fields(app, client, seeded, signing_key):
    actor_id = make_staff(app, "db1@example.com")
    license_id, full_key = make_license(app, actor_id)
    resp = client.post(
        "/api/licensing/v1/activations",
        data=json.dumps(build_activation_body(make_device_keypair(), full_key=full_key, installation_id="db-dev-1")),
        content_type="application/json",
    )
    assert resp.status_code == 200
    blob = json.dumps(resp.get_json()).lower()
    for term in FORBIDDEN_TERMS:
        assert term not in blob


def test_service_info_leaks_no_internal_detail(client, seeded):
    resp = client.get("/api/licensing/v1/service-info")
    body = resp.get_json()
    forbidden_keys = ("database_host", "redis_host", "path", "stack", "table_count", "staff")
    blob = json.dumps(body).lower()
    for key in forbidden_keys:
        assert key not in blob
