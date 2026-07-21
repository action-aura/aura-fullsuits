# Phase 6 -- External API Reason-Code Catalog (Part G)

Full vocabulary: `owner/app/licensing_service/reason_codes.py`.

## Public (safe to return in any external HTTP response)
`ACTIVATION_APPROVED`, `ACTIVATION_ALREADY_ACTIVE`, `CHECK_IN_ACCEPTED`, `DEACTIVATION_ACCEPTED` (success); `INVALID_REQUEST`, `UNSUPPORTED_CONTRACT_VERSION`, `INVALID_TIMESTAMP`, `TIMESTAMP_OUTSIDE_ALLOWED_WINDOW`, `NONCE_REUSED`, `INVALID_SIGNATURE`, `INVALID_PUBLIC_KEY`, `PAYLOAD_TOO_LARGE`, `IDEMPOTENCY_CONFLICT` (request errors); `PRODUCT_MISMATCH`, `PLATFORM_NOT_ALLOWED`, `RELEASE_CHANNEL_NOT_ALLOWED`, `VERSION_NOT_ALLOWED`, `VERSION_UNSUPPORTED` (product errors); `DEVICE_LIMIT_REACHED`, `INSTALLATION_NOT_FOUND`, `INSTALLATION_SUSPENDED`, `INSTALLATION_DEACTIVATED`, `INSTALLATION_REPLACED`, `DEVICE_KEY_MISMATCH`, `DEVICE_KEY_REVOKED` (installation errors); `SERVICE_TEMPORARILY_UNAVAILABLE`, `RATE_LIMITED`, `SIGNING_KEY_UNAVAILABLE`, `INTERNAL_DECISION_FAILURE` (service errors); `ACTIVATION_REJECTED` (the generic normalization target for the internal-only license/subscription-state family below).

## Internal only -- never returned verbatim externally
`LICENSE_NOT_FOUND`, `LICENSE_NOT_ISSUED`, `LICENSE_NOT_ACTIVE`, `LICENSE_SUSPENDED`, `LICENSE_EXPIRED`, `LICENSE_REVOKED`, `LICENSE_REPLACED`, `LICENSE_NOT_YET_VALID`, `SUBSCRIPTION_INACTIVE`, `SUBSCRIPTION_SUSPENDED`, `SUBSCRIPTION_EXPIRED`, `SUBSCRIPTION_CANCELLED`.

## Why the split (anti-enumeration, Part G's explicit instruction)
Returning `LICENSE_NOT_FOUND` for a wrong key but `LICENSE_SUSPENDED` for a real-but-suspended one would let an attacker learn a guessed key is *real* purely from the error shape -- a materially useful signal for brute-forcing the keyspace even with rate limiting in place (it turns a binary guess into a 2-bit-per-guess oracle). `to_public_reason_code()` maps every internal-only code to the single public `ACTIVATION_REJECTED` at the API boundary (`routes.py`'s `_error_response`), while the *specific* internal code is still recorded in `owner_activation_requests.reason_code` and the audit log for staff diagnosis (`activation_requests.view` permission) -- verified: `test_phase6_activation_protocol.py::test_invalid_license_key_rejected_generically` and `test_phase6_lifecycle.py::test_revocation_blocks_checkin` both assert the public response never differentiates.

## Where this trade-off does NOT apply: check-in
Check-in already requires a valid, proven device-key signature (the caller has already demonstrated it controls a previously-activated device) -- at that point, revealing that the associated license is specifically `SUSPENDED` vs. `REVOKED` vs. `EXPIRED` doesn't help an attacker who doesn't already control the device, so check-in's rejection is still normalized to `ACTIVATION_REJECTED` for consistency and simplicity, not because it's strictly necessary the way it is for the initial, unauthenticated activation call.
