# Phase 8V-P5 — Final Android Data Boundary

## Result: CLEAN for the traffic actually captured (7 real exchanges); NOT a complete audit of every
scenario's traffic, since most scenarios were not captured (see `raw-wire-evidence.md`)

## Fields observed (allowed list)

Request: `contract_version`, `request_id`, `correlation_id`, `timestamp`, `nonce`, `installation_id`,
`signature` (redacted), plus on the one activation: `product_code`, `platform`, `app_version`,
`release_channel`, `device_public_key`, `device_public_key_algorithm`, `license_key` (redacted),
`idempotency_key`.

Response: `result`, `decision`, `reason_code`, `response_id`, `retry_guidance`, `server_timestamp`,
`correlation_id`, and inside `signed_assertion.payload`: `assertion_id`, `assertion_version`,
`device_key_fingerprint`, `allowed_device_count`, `app_version_policy`, `commercial_policy_version`,
`commercial_grace_end`, `emergency_extension_id`, `entitlements.*` (booleans/counts only, no domain
data).

Every one of these matches the governing spec's allowed-field list exactly. No field outside it was
observed in any of the 7 real captured exchanges.

## Forbidden data: none found in what was captured

Checked against the full forbidden lists (Clinic: patient names/IDs/appointments/visits/diagnoses/
prescriptions/notes/invoice/payment details; Retail: products/sales/sale lines/receipts/stock/
suppliers/customers/tax totals/discounts/totals/profit; general: local DB contents/paths, private
device keys, Owner private signing key, credentials, keystore passwords, staff notes, contacts, full
key after activation, general telemetry). None appear. The full activation license key is redacted
even on the one request where the protocol allows it (stricter than the spec's own minimum).

## What this audit does NOT cover

Only `activations`, `check-ins`, and `service-info` traffic was captured. Traffic for late renewal,
past-due refresh, device replacement, plan downgrade/overage, temporary exception, and stale-assertion
scenarios was never generated this session (those scenarios are NOT VERIFIED), so this boundary check
cannot speak to those payload shapes. Given the shared code path (`build_assertion_payload()` /
`resolve_commercial_assertion_fields()` is the single source for all of them, per source reading in
`phase8vp5-baseline.md`), there is no structural reason to expect a different field set, but this is an
inference from source, not direct evidence, and is reported as such rather than blurred into a claim of
full coverage.
