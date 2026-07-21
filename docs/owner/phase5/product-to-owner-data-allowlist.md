# Phase 5 -- Product-to-Owner Data Allowlist (Part W)

Every future contract between a product (Retail/Clinic) and Owner is built as an explicit allowlist serializer (`owner/app/api/serializers.py`) -- no serializer ever forwards an arbitrary dict or `**model.__dict__`.

## Allowed client-to-Owner fields (future activation/license-check/registration requests)
`product_code`, `platform`, `app_version`, `release_channel`, `installation_id`, `license_key` (transmitted once, activation only, never logged), `device_public_key`, `fingerprint_hash` (privacy-safe hash, not a raw hardware ID), `timestamp`, `request_nonce`, `contract_version`.

## Allowed Owner-to-client fields (future responses)
`activation_result`, `license_status`, `valid_from`, `valid_until`, `offline_grace_policy`, `enabled_entitlements`, `allowed_device_count`, `allowed_release_channel`, `server_timestamp`, `signed_response_metadata`, `correlation_id`.

## Forbidden fields (never serialized, guarded at construction time)
`patient*`, `diagnosis*`, `prescription*`, `medical_note*`, `clinical_note*`, `sale_line*`, `invoice_line*`, `inventory_quantity*`, `customer_purchase*`, `local_database*`, `card_number*`, `bank_account*`, `appointment*`.

## Where this is implemented
- `owner/app/api/serializers.py` -- `_guard()` raises `ForbiddenFieldError` if any output key matches a forbidden marker; every `serialize_*` function is built from a fixed key set, not a dynamic one.
- `owner/contracts/*.schema.json` -- 7 JSON Schemas with `"additionalProperties": false`, mirroring these exact allowlists as machine-checkable specifications for a future implementer.
- `owner/tests/test_data_boundary.py::test_license_check_serializer_output_is_a_fixed_allowlist` -- asserts the live serializer's output key set equals exactly the documented allowlist above, not a superset.

## Contract activation status
All 7 schemas are **inactive specifications**. The one live route group (`owner/app/api/routes.py`) is only registered when `OWNER_EXTERNAL_API_ENABLED=true` (default `false`) and even then implements only a read-only version-check endpoint plus a stub that returns `501 not_implemented_in_phase_5` for registration -- no live activation logic exists.
