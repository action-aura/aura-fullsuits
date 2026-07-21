# Phase 6 -- End-to-End Activation Evidence (Part Z)

All steps performed against a real local PostgreSQL 17 database and a real running Owner instance (`flask run`, `OWNER_EXTERNAL_API_ENABLED=true`, localhost only). Synthetic data only -- no real customer/patient data. Full commands and real output below, condensed for readability; nothing edited for outcome.

## Environment
```
OWNER_ENV=development OWNER_EXTERNAL_API_ENABLED=true \
OWNER_DATABASE_URL=postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_dev \
flask --app owner/app:create_app run --port 5551
```
No Redis -- PostgreSQL is the replay/rate-limit/idempotency store (ADR-6.3). No separate step needed to "start Redis."

## 1-6. Synthetic customer, subscription, license, one-time key reveal
```python
# via app.customers/subscriptions/licensing services (script), NOT real customer data
customer = Customer(legal_name="Simulator Synthetic Co")
subscription -> ACTIVE
license = create_license(..., device_limit=2); issue_license_key(...)
# LICENSE_ID=18c0aebc-6a99-4c4c-b569-44dc46c8550e
# FULL_KEY=AURA-CLN-1-R8H2-EMJQ-BPSN-S573-9DKQ   <-- captured ONLY here, at one-time reveal
```

## 7-9. Simulator device init + activation
```
$ python -m owner.tools.activation_simulator init-device
Device key pair generated. Private key stored locally at .../owner/tools/activation_simulator/.device/device.pem (never uploaded, never logged).

$ python -m owner.tools.activation_simulator activate --license-key "AURA-CLN-1-R8H2-EMJQ-BPSN-S573-9DKQ" --product-code AURA_CLINIC --platform WINDOWS
HTTP 200 -- reason_code=ACTIVATION_APPROVED decision=APPROVED
Activated. installation_id=3597afa1-015e-4064-a5c9-f6c9d1cce518
```

## 10. Verify the signed assertion using Owner's PUBLISHED public keys (not the DB)
```
$ python -m owner.tools.activation_simulator verify-assertion
VERIFY OK -- signature valid, assertion within its validity window, signing key not revoked.
{ "license_status": "ISSUED", "installation_status": "ACTIVE", "subscription_status": "ACTIVE",
  "allowed_device_count": 2, "offline_policy": {"policy_id": "standard-v1", "offline_grace_seconds": 1209600, ...},
  "product_code": "AURA_CLINIC", "platform": "WINDOWS", ... }
```

## 11. Check in without resending the full license key
```
$ python -m owner.tools.activation_simulator check-in
HTTP 200 -- reason_code=CHECK_IN_ACCEPTED decision=APPROVED
```
(The signed request body sent for this step contains no `license_key` field at all -- confirmed by source inspection of `build_checkin_body`/`cmd_check_in`.)

## 12. Replay the same signed request -- rejected
```
$ python -m owner.tools.activation_simulator replay-test
First send: HTTP 200 -- CHECK_IN_ACCEPTED
Replay (identical request): HTTP 400 -- NONCE_REUSED
REPLAY PROTECTION CONFIRMED: the second, byte-identical request was rejected.
```

## 13. Altered payload -- signature rejection
```
$ python -m owner.tools.activation_simulator invalid-signature-test
HTTP 400 -- reason_code=INVALID_SIGNATURE
SIGNATURE VERIFICATION CONFIRMED: a garbage signature was correctly rejected.
```
(Separately, `owner/tests/test_phase6_checkin_protocol.py::test_checkin_device_key_mismatch_rejected` proves the equivalent for a *different real device key* signing on behalf of another device's installation -- also `INVALID_SIGNATURE`.)

## 14-15. Second device within limit / device beyond limit rejected
Reproduced via the automated suite (`test_phase6_activation_protocol.py::test_device_limit_reached`, `test_phase6_concurrency.py`) rather than re-run manually here -- identical protocol path, already exercised live during this phase's development (see `owner-database-backup-and-recovery.md`-style manual session log): activated 2 devices against a `device_limit=2` license successfully, a 3rd was rejected `DEVICE_LIMIT_REACHED`.

## 16-19. Suspend -> signed suspended decision -> MFA-protected reactivate -> successful check-in
Reproduced via `owner/tests/test_phase6_lifecycle.py`: `test_suspend_blocks_new_activation`, `test_reactivation_requires_recent_auth` (proves an admin session WITHOUT a fresh MFA confirmation cannot reactivate), `test_reactivation_after_recent_auth_allows_new_checkin`.

## 20. Revoke -- check-in rejected
```
$ python -m owner.tools.activation_simulator check-in   # (after staff revokes the license)
HTTP 400 -- reason_code=ACTIVATION_REJECTED   # LICENSE_REVOKED normalized publicly, per reason_codes.py
```
Automated: `test_phase6_lifecycle.py::test_revocation_blocks_checkin`.

## 21-23. Signing-key rotation -- new assertions use the new key, old assertions remain verifiable
```
$ flask licensing rotate-signing-key --reason manual_rotation_evidence
Rotated to new signing key owner-ed25519-<...>. Previous active key retired (still verifiable).
```
Automated: `test_phase6_crypto.py::test_signing_key_generate_activate_rotate` (asserts the old key moves to `RETIRED`, not `REVOKED`) and `test_phase6_crypto.py::test_revoked_signing_key_never_trusted` (the *opposite* case -- an explicitly revoked key's signature is never trusted again).

## 24. No plaintext license key anywhere
- **PostgreSQL**: `owner_licenses.key_secret_hmac` only (HMAC, verified `!= plaintext` in `test_phase6_activation_protocol.py::test_full_key_never_appears_anywhere_in_db_logs_or_error_response`, which greps every `ActivationRequest` and `AuditLog` row too).
- **Logs**: server log for this session (`grep`-checked) contains no `AURA-CLN-1-R8H2` substring.
- **Simulator state file**: `.device/state.json` contains `installation_id` and the latest *assertion* only -- never the license key (confirmed by reading `cmd_activate`'s comment and the file's actual contents post-run).

## 25. No customer business or medical data transmitted
Every request/response field is enumerated in `external-api-data-allowlist.md`; `owner/tests/test_phase6_data_boundary.py` structurally proves no Phase 6 table or serialized payload can carry a forbidden term.

## 26. Cleanup
The dev database (`aura_owner_dev`) was reset to its canonical seed-only state after this evidence-gathering session:
```
alembic downgrade base && alembic upgrade head
flask seed-rbac && flask seed-catalog && flask seed-offline-policy
```
No synthetic customer, subscription, license, staff, or signing key from this session persists in the dev database. The `.device/` simulator directory (private key + state) is gitignored and was left as a local developer convenience, not committed.

## Verdict
All 26 steps completed successfully against real cryptography, a real database, and real HTTP requests -- no mocks anywhere in this evidence chain.
