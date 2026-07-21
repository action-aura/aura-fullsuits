"""Allowlist serializers for every future external contract (Part R/W).

Each function whitelists an EXACT, fixed set of output keys -- it never does
`**dict(model.__dict__)` or forwards an arbitrary dict. Constructing one with an
extra/forbidden key raises ForbiddenFieldError; this is enforced by
owner/tests/test_data_boundary.py, not merely documented.
"""
from __future__ import annotations

_FORBIDDEN_MARKERS = (
    "patient", "diagnosis", "prescription", "medical_note", "clinical_note",
    "sale_line", "invoice_line", "inventory_quantity", "customer_purchase",
    "local_database", "card_number", "bank_account", "appointment",
)


class ForbiddenFieldError(ValueError):
    pass


def _guard(payload: dict) -> dict:
    for key in payload.keys():
        lowered = key.lower()
        if any(marker in lowered for marker in _FORBIDDEN_MARKERS):
            raise ForbiddenFieldError(f"Field '{key}' is forbidden in an Owner external contract payload.")
    return payload


def serialize_license_check_response(license_row, entitlements: list[dict]) -> dict:
    return _guard(
        {
            "license_id": str(license_row.id),
            "status": license_row.status,
            "valid_from": license_row.valid_from.isoformat() if license_row.valid_from else None,
            "valid_until": license_row.valid_until.isoformat() if license_row.valid_until else None,
            "allowed_platforms": license_row.allowed_platforms,
            "device_limit": license_row.device_limit,
            "entitlements": entitlements,
            "server_timestamp": None,
            "correlation_id": None,
        }
    )


def serialize_entitlement_response(entitlement_rows: list[dict]) -> dict:
    return _guard({"entitlements": entitlement_rows, "server_timestamp": None, "correlation_id": None})


def serialize_installation_registration_response(installation) -> dict:
    return _guard(
        {
            "installation_id": str(installation.id),
            "status": installation.status,
            "activation_result": "REGISTERED",
            "server_timestamp": None,
            "correlation_id": None,
        }
    )


def serialize_product_version_check_response(version_row) -> dict:
    return _guard(
        {
            "product_code": version_row.product.product_code,
            "platform": version_row.platform.platform_code,
            "version": version_row.version,
            "release_channel": version_row.release_channel.channel_code if version_row.release_channel else None,
            "is_current_stable": version_row.is_current_stable,
            "is_deprecated": version_row.is_deprecated,
            "server_timestamp": None,
        }
    )


def serialize_activation_response(event) -> dict:
    return _guard(
        {
            "event_type": event.event_type,
            "result": event.result,
            "reason_code": event.reason_code,
            "correlation_id": event.correlation_id,
            "server_timestamp": None,
        }
    )
