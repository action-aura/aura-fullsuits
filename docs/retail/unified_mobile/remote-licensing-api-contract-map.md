# Remote Licensing API Contract Map (M7.9)

Every real remote route touching licensing, from tier-1/tier-2
evidence. Staff-facing routes (session-cookie auth) and the real
external device protocol (signature auth) are both mapped; only the
latter is relevant to a future mobile client.

## External device protocol — `owner/app/api_external/routes.py`

Blueprint prefix `/api/licensing/v1`, only registered when
`OWNER_EXTERNAL_API_ENABLED` is true (module docstring lines 1-4).
Common response shape: `_error_response(reason_code, http_status,
correlation_id)` (`routes.py:34-45`) always includes `correlation_id`
and a `retry_guidance` field — `"safe_to_retry_with_backoff"` only for
`RATE_LIMITED`/`SERVICE_TEMPORARILY_UNAVAILABLE`, else
`"do_not_retry_without_correction"`.

| Route | Method | Auth | Idempotency | Anti-replay | Transaction | Audit | Rate limit | Sensitive fields |
|---|---|---|---|---|---|---|---|---|
| `/activations` | POST | Ed25519 device signature + full plaintext license key (this call only) | `idempotency_key`, `ExternalIdempotencyRecord` (409 `IDEMPOTENCY_CONFLICT` on conflicting retry) | `nonce` (single-use, scope=`activation`), `timestamp` window (`OWNER_ACTIVATION_TIMESTAMP_SKEW_SECONDS`) | Single commit; License row-locked (`with_for_update`) around the whole check-then-write | `ActivationEvent` row every attempt | 429 `RATE_LIMITED` + `Retry-After` header (`routes.py:80-81`) | `license_key` (never persisted/logged/returned — `del`'d immediately after HMAC lookup) |
| `/activations/check` | POST | Device signature | n/a (read-style) | Same nonce/timestamp scheme | Read-only | `ActivationEvent` (`CHECK_IN_RECORDED`) | 429 + `Retry-After` (`routes.py:128-129`) | none returned beyond the signed assertion fields |
| `/check-ins` | POST | Device signature only (no license key) | Not idempotency-key gated — replay protection is via nonce/timestamp only, not `ExternalIdempotencyRecord` | nonce/timestamp | Single commit per call | `ActivationEvent` | 429 + `Retry-After` (`routes.py:177-178`) | none |
| `/deactivations` | POST | Device signature (verified against the most-recent device key regardless of its status, so a retry after already-deactivated still verifies) | Idempotent by construction (`deactivation.py:78-80` only mutates if not already deactivated) + `idempotency_key`/`ExternalIdempotencyRecord` | nonce/timestamp | Single commit | `ActivationEvent` | 429 + `Retry-After` (`routes.py:190-191`) | none |
| `/signing-keys` | GET | None (public) | n/a | n/a | Read-only | none | not observed | public keys only |
| `/service-info` | GET | None (public) | n/a | n/a | Read-only | none | not observed | scanned clean of DB/host/path/stack-trace leakage (`external-api-forbidden-data-report.md` §4) |

Error-code contract: full detail in `licensing-error-contract.md`.
Every internal-only reason code (e.g. `LICENSE_NOT_FOUND` vs.
`LICENSE_SUSPENDED`) is normalized to the public `ACTIVATION_REJECTED`
before leaving this boundary
(`owner/app/licensing_service/reason_codes.py::to_public_reason_
code`) — deliberate anti-enumeration, not an oversight.

## Staff-facing routes (session-cookie auth, not mobile-relevant, mapped for completeness)

`owner/app/licensing/routes.py` (prefix `/licenses`): `GET /`, `GET
/new`, `POST /` (create DRAFT), `GET /<id>`, `POST /<id>/issue`
(+`require_recent_auth`), `POST /<id>/transition`
(+`require_recent_auth`, per-target permission), `POST /<id>/replace`
(+`require_recent_auth`).

`owner/app/licensing_admin/routes.py` (prefix `/licensing-admin`):
health page, signing-key list/generate/activate/rotate/revoke,
`GET /requests` (ActivationRequest audit list), device-key
list/revoke, installation replace-device, offline-policy
list/assign, entitlement-preview.

`owner/app/installations/routes.py` (prefix `/installations`): list,
new form, create (manual staff registration), detail, transition.

## Not-live prototype surface — `owner/app/api/routes.py`

Prefix `/api/v1`. Docstring: "no live product calls this in Phase 5;
no authentication/signature scheme is wired yet." `GET
/product-version-check` (real, returns real metadata — see
`mobile-release-version-contract.md`); `POST
/installation-registration` returns `501 not_implemented_in_phase_5`
unconditionally. **A mobile client must not call this blueprint** —
it is explicitly non-production per its own module docstring.

## Real request/response schemas

`owner/contracts/*.schema.json` (JSON Schema draft 2020-12,
`additionalProperties: false` on every schema — a real, enforced
allowlist, not just documentation):
`activation-request-v1.schema.json`, `activation-response-v1.schema.json`,
`license-check-request-v1.schema.json`,
`license-check-response-v1.schema.json`,
`installation-registration-v1.schema.json` (not-live, documented
above), `entitlement-response-v1.schema.json`,
`product-version-check-v1.schema.json`. Full field-level detail for
the activation request in `owner-data-minimization-contract.md`.
