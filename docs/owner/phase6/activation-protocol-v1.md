# Phase 6 -- Activation Protocol v1

## Endpoints
`POST /api/licensing/v1/activations`, `POST /api/licensing/v1/activations/check` (alias of check-ins), `POST /api/licensing/v1/check-ins`, `POST /api/licensing/v1/deactivations`, `GET /api/licensing/v1/signing-keys`, `GET /api/licensing/v1/service-info`.

## Activation request fields
`contract_version` (`"v1"`), `request_id`, `correlation_id`, `timestamp` (ISO-8601, timezone-aware), `nonce`, `product_code`, `platform`, `app_version`, `release_channel` (optional), `installation_id` (client-generated), `device_public_key` (base64 raw 32 bytes), `device_public_key_algorithm` (`"ed25519"`), `license_key` (full plaintext, this call only), `idempotency_key`, `signature` (base64, over the canonical form of every other field).

## Activation response (success)
`contract_version`, `response_id`, `correlation_id`, `server_timestamp`, `result: "SUCCESS"`, `reason_code: "ACTIVATION_APPROVED"`, `decision: "APPROVED"`, `installation_id` (server-assigned public UUID -- **not** an echo of the client's `installation_id`; the client must persist this and use it for every future call), `signed_assertion` (the full envelope), `signing_key_id`, `assertion_version`.

## Check-in request fields
`contract_version`, `request_id`, `correlation_id`, `timestamp`, `nonce`, `installation_id` (the server-assigned one from activation), `signature`. **No `license_key` field exists in this request shape at all.**

## Deactivation request fields
Same as check-in plus `idempotency_key`.

## The 23-step activation sequence (implemented exactly, in order, in `app/licensing_service/activation.py::process_activation`)
1. Validate request shape. 2. Validate timestamp freshness. 3. Reject reused nonce. 4. Validate device signature. 5. Normalize the license key. 6. HMAC it with the server pepper. 7. Constant-time compare (via HMAC equality lookup). 8. Locate the license (no plaintext stored/logged). 9. Validate product. 10. Validate platform. 11. Validate release channel. 12. Validate license status. 13. Validate subscription state. 14. Validate validity dates. 15. Soft device-allowance pre-check. 16. Check idempotency. 17. Lock the license row (`SELECT ... FOR UPDATE`) and authoritatively re-check device allowance. 18. Register or reuse the installation. 19. Record the activation event. 20. Resolve entitlements. 21. Issue a signed assertion. 22. Build the safe response. 23. Commit the entire decision as one transaction.

## Full field-level contract
See `owner/contracts/activation-request-v1.schema.json` / `activation-response-v1.schema.json` / `license-check-request-v1.schema.json` (reused for check-in) / `license-check-response-v1.schema.json` for the machine-checkable JSON Schema definitions, updated this phase to match these exact field names.
