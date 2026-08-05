# Licensing Error Contract (M7.15)

One shared mobile error model mapping real canonical server error
codes, without casual renaming. Source of truth: `owner/app/
licensing_service/reason_codes.py` (server) and `commercial_runtime/
licensing_contracts/reason_codes.py` (client's own mirrored public
subset — real, already exists, not created by M7).

## Real server-side vocabulary

`SUCCESS_CODES` (4): `ACTIVATION_APPROVED`, `ACTIVATION_ALREADY_
ACTIVE`, `CHECK_IN_ACCEPTED`, `DEACTIVATION_ACCEPTED`.

`PUBLIC_REASON_CODES` (safe to return externally — the success codes
plus the codes below, real, exact list from
`owner/app/licensing_service/reason_codes.py`; 27 non-success codes
+ 4 success codes = 31 total):

```
INVALID_REQUEST, UNSUPPORTED_CONTRACT_VERSION, INVALID_TIMESTAMP,
TIMESTAMP_OUTSIDE_ALLOWED_WINDOW, NONCE_REUSED, INVALID_SIGNATURE,
INVALID_PUBLIC_KEY, PAYLOAD_TOO_LARGE, IDEMPOTENCY_CONFLICT,
PRODUCT_MISMATCH, PLATFORM_NOT_ALLOWED, RELEASE_CHANNEL_NOT_ALLOWED,
VERSION_NOT_ALLOWED, VERSION_UNSUPPORTED, DEVICE_LIMIT_REACHED,
INSTALLATION_NOT_FOUND, INSTALLATION_SUSPENDED,
INSTALLATION_DEACTIVATED, INSTALLATION_REPLACED, DEVICE_KEY_MISMATCH,
DEVICE_KEY_REVOKED, DEVICE_ALREADY_REGISTERED,
SERVICE_TEMPORARILY_UNAVAILABLE, RATE_LIMITED,
SIGNING_KEY_UNAVAILABLE, INTERNAL_DECISION_FAILURE,
ACTIVATION_REJECTED
```

`INTERNAL_ONLY_REASON_CODES` (11, real — **never** returned externally,
normalized to `ACTIVATION_REJECTED` before leaving the API boundary,
`to_public_reason_code()`): `LICENSE_NOT_FOUND`, `LICENSE_NOT_ISSUED`,
`LICENSE_NOT_ACTIVE`, `LICENSE_SUSPENDED`, `LICENSE_EXPIRED`,
`LICENSE_REVOKED`, `LICENSE_REPLACED`, `LICENSE_NOT_YET_VALID`,
`SUBSCRIPTION_INACTIVE`, `SUBSCRIPTION_SUSPENDED`,
`SUBSCRIPTION_EXPIRED`, `SUBSCRIPTION_CANCELLED`. This is a
deliberate anti-enumeration design (a caller cannot distinguish
"license doesn't exist" from "license exists but is
suspended/expired/revoked/not-yet-valid" from response shape alone).
**A mobile client must never attempt to reverse-engineer or guess at
these internal codes — it only ever sees `ACTIVATION_REJECTED`.**

## Real client-local vocabulary (never sent by Owner)

`LOCAL_REASON_CODES`, already real and mirrored in `commercial_runtime/
licensing_contracts/reason_codes.py`: `NETWORK_UNAVAILABLE`,
`REQUEST_TIMED_OUT`, `TLS_VERIFICATION_FAILED`, `MALFORMED_RESPONSE`,
`UNSIGNED_RESPONSE_REJECTED`, `UNKNOWN_SIGNING_KEY`,
`ASSERTION_VERIFICATION_FAILED`, `ASSERTION_EXPIRED`,
`ASSERTION_NOT_YET_VALID`, `ASSERTION_PRODUCT_MISMATCH`,
`ASSERTION_PLATFORM_MISMATCH`, `ASSERTION_INSTALLATION_MISMATCH`,
`ASSERTION_DEVICE_MISMATCH`, `ASSERTION_FORBIDDEN_FIELD`,
`CLOCK_ROLLBACK_SUSPECTED`, `LOCAL_STATE_CORRUPT`,
`DEVICE_KEY_UNAVAILABLE`, `CAPABILITY_DENIED`. Kept in a genuinely
separate set from the server-sent codes so a caller can always tell
"Owner told us this" from "we decided this locally without ever
reaching Owner" — a real, deliberate design already present, not
introduced by M7.

## Retry guidance

Real, per-response field: `retry_guidance` —
`"safe_to_retry_with_backoff"` only for `RATE_LIMITED`/
`SERVICE_TEMPORARILY_UNAVAILABLE`; `"do_not_retry_without_correction"`
otherwise (`owner/app/api_external/routes.py:44`). `RATE_LIMITED`/
`SERVICE_TEMPORARILY_UNAVAILABLE` responses additionally carry a real
`Retry-After` header (`routes.py:80-81,128-129,177-178,190-191`).

## Mobile contract shared model (M7.17)

`LicensingError` must be a closed, exhaustive sealed type with exactly
three real member categories — no invented fourth category, no
casual renaming of any code string:

1. `ServerReasonCode` — wraps one of the 31 real `PUBLIC_REASON_CODES`
   values verbatim (string-for-string, not translated/relabeled).
2. `LocalReasonCode` — wraps one of the 18 real `LOCAL_REASON_CODES`
   values verbatim.
3. An explicit `retryGuidance: SAFE_TO_RETRY_WITH_BACKOFF |
   DO_NOT_RETRY_WITHOUT_CORRECTION` field, sourced from the server
   response when present, or derived locally (network/timeout errors
   → safe to retry; malformed/tampered/signature errors → do not
   retry) for `LocalReasonCode` cases.

M7.18's fixture suite must include one parsing fixture per real
`PUBLIC_REASON_CODES` value (31 fixtures) proving the shared parser
maps the exact server string to the exact matching `ServerReasonCode`
case, with no fallthrough silently swallowing an unrecognized code —
an unrecognized string must surface as a distinct, explicit
"unknown code" case rather than being silently coerced to any
existing one (forward-compatibility discipline: if Owner adds a 29th
code later, the mobile client must not misinterpret it as an
unrelated existing code).
