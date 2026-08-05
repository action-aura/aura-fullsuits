# Activation Idempotency Contract (M7.10)

Real same-request/conflicting-retry/concurrent-final-slot/device-retry
semantics, from `owner/app/licensing_service/activation.py` and
`owner/app/licensing_service/idempotency.py`.

## Two independent idempotency layers (real, both present)

1. **Request-level, `idempotency_key`-based**: `ExternalIdempotencyRecord`
   table, `check_idempotency()`/`record_idempotency()`
   (`idempotency.py:23-92`). Key = SHA-256 of (`idempotency_key` +
   a stable request fingerprint). Same key + same fingerprint →
   returns the identical prior result, no new work performed. Same
   key + different fingerprint → `IdempotencyConflictError` → HTTP 409
   `IDEMPOTENCY_CONFLICT` (`idempotency.py:38-40`,
   `activation.py:168-169`).
2. **Device-identity-based**: independent of any client-supplied key.
   If the submitted device public key's fingerprint already maps to
   an ACTIVE `DevicePublicKey` on the same license, the request is
   treated as a legitimate re-registration (fresh client-generated
   `installation_id`, per protocol design — reinstall/app-data-clear
   scenarios) rather than a new device (`activation.py:196-215`).

## Same-request retry

Real, tested outcome: identical logical result returned, zero
additional device slot consumed
(`owner/tests/test_phase6_activation_protocol.py::test_idempotent_
retry_returns_same_result_no_extra_slot`).

## Conflicting retry (same key, different payload)

Rejected outright with `IDEMPOTENCY_CONFLICT` before any
license/device-limit logic runs.

## Concurrent final-slot semantics

Real row-level lock: `SELECT ... FOR UPDATE` on the target `License`
row (`activation.py:175-177`), taken before the effective-device-limit
check and held through the `Installation` insert and single commit
(`activation.py:174-341`, the block explicitly commented "lock the
license row, authoritative device-limit check, register-or-reuse the
installation -- all inside one transaction"). Proven, not just
claimed, by a real two-thread concurrency test hitting the Flask test
client simultaneously against a `device_limit=1` license:
`owner/tests/test_phase6_concurrency.py::test_simultaneous_final_
slot_activations_only_one_accepted` asserts exactly one 200 and one
400 `DEVICE_LIMIT_REACHED`, and the installation count never exceeds
the limit. A companion test,
`test_retry_after_transaction_rollback_does_not_leak_a_slot`, confirms
a rejected/rolled-back attempt never consumes a slot.

## Device retry semantics (reinstall)

Covered under layer 2 above — a device presenting the same key
fingerprint on a fresh `installation_id` is recognized and reuses its
existing `Installation`/`DevicePublicKey` row rather than double-
counting against `device_limit`.

## Manual-approval interaction with idempotency

Under a manual-approval product policy, pending-activation creation is
itself idempotent per installation, and approval **re-checks** the
device limit at approval time (not merely at the original request
time) — so two pending requests approved close together cannot jointly
exceed the cap
(`owner/tests/test_commercial_ops_activation_policy.py::test_approve_
pending_activation_rechecks_device_limit`).

## Mobile contract implication

The shared `ActivationRequest`/`ActivationResult` models (M7.17) must
carry a required `idempotencyKey`, and the fixture suite (M7.18) must
include: identical-retry-returns-same-result, conflicting-retry-409,
and final-slot-reached fixtures, matching this real server contract
exactly — a mobile client retry-on-timeout implementation must always
reuse the same `idempotencyKey` for a given logical attempt, never
generate a fresh one per HTTP retry.
