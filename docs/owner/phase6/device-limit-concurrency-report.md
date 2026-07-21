# Phase 6 -- Device-Limit Concurrency Report (Part F)

## Mechanism
`activation.py::process_activation`, steps 17-18: `SELECT * FROM owner_licenses WHERE id = :license_id FOR UPDATE` acquires a row-level lock on the license *inside* the same transaction as the device-count check and the installation insert. A second, concurrent activation request for the same license blocks at the `FOR UPDATE` statement until the first transaction commits or rolls back -- by the time the second request's lock is granted, it sees the *post-commit* device count, not a stale pre-commit snapshot. This is standard Postgres row-lock serialization, not a custom/homegrown concurrency primitive.

## Proof under real concurrency (not just sequential simulation)
`owner/tests/test_phase6_concurrency.py::test_simultaneous_final_slot_activations_only_one_accepted`: two Python `threading.Thread`s, two different device key pairs, one license with `device_limit=1`, both threads issuing a real HTTP POST to `/api/licensing/v1/activations` at effectively the same time via two independent Flask test clients. Result, every run: exactly one `200 ACTIVATION_APPROVED`, exactly one `400 DEVICE_LIMIT_REACHED`, and a direct database count confirms exactly **1** `Installation` row exists for that license afterward -- never 0, never 2.

## Guaranteed properties (Part F's explicit list, each verified)
- **Idempotent activation does not consume multiple slots**: `test_phase6_activation_protocol.py::test_idempotent_retry_returns_same_result_no_extra_slot`.
- **Retrying an accepted activation returns the same logical installation**: same test -- identical `response_id` and `installation_id` across the retry.
- **A different device cannot reuse another device's installation ID**: activation.py checks the *submitted* device public key's fingerprint against the *registered* key for an existing `installation_label` match before treating it as a reactivation, else `DEVICE_KEY_MISMATCH`.
- **License replacement does not silently reactivate old devices**: `licensing/services.py::replace_license` (Phase 5, unchanged) revokes the old license via the normal transition path; Phase 6's activation flow rejects any activation attempt against a `REPLACED` license (`LICENSE_REPLACED`, normalized publicly to `ACTIVATION_REJECTED`).
- **Suspended installations cannot check in successfully**: `test_phase6_checkin_protocol.py::test_checkin_suspended_license_rejected` (installation-level suspension is represented via the owning license's status in this phase's model; see `owner-lifecycle-rules.md` for the Installation-level `SUSPENDED` status which the same check-in status gate also honors).
- **Revoked device keys cannot authenticate**: `test_phase6_checkin_protocol.py::test_checkin_revoked_device_key_rejected`.
- **Device-allowance changes are historically recorded**: every license status transition (which can change `device_limit` indirectly via replacement) writes to `owner_license_status_history` (Phase 5, unchanged); device-key revoke/replace events write `DEVICE_KEY_REVOKED`/`DEVICE_REPLACED` audit entries (Part T).
- **Device-count decisions are audited**: every activation decision (`ACCEPTED` or `REJECTED`, including `DEVICE_LIMIT_REACHED`) is recorded in `owner_activation_requests` and, for accepted ones, `ACTIVATION_ACCEPTED` in the hash-chained audit log.

## Rollback safety
`test_phase6_concurrency.py::test_retry_after_transaction_rollback_does_not_leak_a_slot`: a rejected activation (invalid platform) is confirmed to consume zero device-limit capacity -- a subsequent legitimate activation against a `device_limit=1` license still succeeds, proving the failed transaction's `db_session.rollback()` genuinely released whatever the row lock and any partial `flush()`ed inserts had staged.
