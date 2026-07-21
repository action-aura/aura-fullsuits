# Phase 6 -- External API Data Allowlist (extends Phase 5's `product-to-owner-data-allowlist.md`)

## Activation request -- allowed fields (exhaustive)
`contract_version`, `request_id`, `correlation_id`, `timestamp`, `nonce`, `product_code`, `platform`, `app_version`, `release_channel`, `installation_id`, `device_public_key`, `device_public_key_algorithm`, `license_key` (this call only), `idempotency_key`, `signature`.

## Check-in / deactivation request -- allowed fields (exhaustive)
`contract_version`, `request_id`, `correlation_id`, `timestamp`, `nonce`, `installation_id`, `idempotency_key` (deactivation only), `signature`. **No `license_key` field exists in this shape.**

## Activation/check-in response -- allowed fields (exhaustive)
`contract_version`, `response_id`, `correlation_id`, `server_timestamp`, `result`, `reason_code`, `decision`, `installation_id` (activation only), `signed_assertion`, `signing_key_id`, `assertion_version`, `retry_guidance` (error responses only).

## Signed assertion payload -- allowed fields (exhaustive, `assertions.py::FORBIDDEN_ASSERTION_MARKERS`-guarded)
`assertion_id`, `issuer`, `product_code`, `license_public_id`, `installation_public_id`, `platform`, `app_version_policy`, `release_channel`, `issued_at`, `not_before`, `expires_at`, `license_status`, `installation_status`, `subscription_status`, `allowed_device_count`, `device_key_fingerprint`, `entitlements`, `offline_policy`, `contract_version`.

## `signing-keys` response -- allowed fields (exhaustive)
`key_id`, `algorithm`, `public_key`, `use`, `status`, `valid_from`, `retired_at`, `schema_version`.

## `service-info` response -- allowed fields (exhaustive)
`service`, `status`, `supported_contract_versions`, `active_signing_key_id`.

## Forbidden everywhere in this list (guarded, tested)
Any of Phase 5's forbidden markers (`patient*`, `diagnosis*`, `prescription*`, `medical_note*`, `sale_line*`, `invoice_line*`, `inventory_quantity*`, `customer_purchase*`, `local_database*`, `card_number*`, `bank_account*`, `appointment*`) plus Phase 6-specific secret markers (`license_key`, `key_secret_hmac`, `pepper`, in any assertion payload -- the request itself legitimately carries `license_key` once, but the *response*/*assertion* never does). Database host/path, Redis host/path, staff information, internal table counts, and stack traces are additionally forbidden from `service-info`/health-adjacent responses specifically (Part W).

## Enforcement
Every response is built from an explicit, fixed dict literal in `activation.py`/`checkin.py`/`deactivation.py`/`routes.py` -- never `**dict(model.__dict__)`. The assertion payload additionally passes through `assertions.py::_guard_payload()`, which raises if any key matches a forbidden marker. Tested directly: `test_phase6_data_boundary.py::test_assertion_payload_guard_rejects_forbidden_field`, `test_activation_response_contains_no_forbidden_fields`, `test_service_info_leaks_no_internal_detail`.
