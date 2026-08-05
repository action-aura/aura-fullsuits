# Multi-Device License Policy Audit (M7.7)

Real policy matrix built entirely from `process_activation()`
(`owner/app/licensing_service/activation.py:77-349`),
`Installation.VALID_TRANSITIONS`
(`owner/app/installations/services.py:18-25`), and the real
concurrency/idempotency tests
(`owner/tests/test_phase6_concurrency.py`,
`owner/tests/test_phase6_activation_protocol.py`). No default cap or
behavior below is invented — every row cites the real enforcing code.
Where a scenario is **not currently possible** (no iOS platform row
exists — `platform-authority-audit.md`), it is marked so explicitly
rather than answered with an invented policy.

| # | Scenario | Real outcome | Evidence |
|---|---|---|---|
| 1 | Windows install, then Android install, `device_limit=2` | Both succeed; two `Installation` rows, two `DevicePublicKey` rows, license has 2 slot-consuming installations. | `activation.py:240-242` counts across all platforms, not per-platform — `count_slot_consuming_installations()` has no platform filter (`installations/services.py:106-112`). |
| 2 | Android + iOS on the same license | **Not currently reachable** — `platform` is enum-validated to `["WINDOWS","ANDROID"]` at the JSON-schema layer (`contracts/activation-request-v1.schema.json`) and `License.allowed_platforms` is only ever set from that same two-value vocabulary in current staff tooling. An iOS activation attempt would be rejected before reaching the device-limit check, with `PLATFORM_NOT_ALLOWED` if `allowed_platforms` doesn't list it, or a schema-validation rejection if the platform value itself isn't `WINDOWS`/`ANDROID`. No real code path admits `IOS` as a value today. |
| 3 | Two Android installs, `device_limit=1` | First succeeds; second real activation request rejected `DEVICE_LIMIT_REACHED`. | `activation.py:240-242`; regression-tested by `test_phase6_concurrency.py`. |
| 4 | Two simultaneous activation requests racing for the final slot (`device_limit=1`) | Exactly one succeeds (200), the other rejected (400, `DEVICE_LIMIT_REACHED`) — no double-grant. | `SELECT ... FOR UPDATE` row lock on the License (`activation.py:175-177`), proven by real concurrent-thread test `test_phase6_concurrency.py::test_simultaneous_final_slot_activations_only_one_accepted`. |
| 5 | Rejected/rolled-back activation attempt, then retry | Rejected attempt does not leak a slot — retry sees the same available capacity. | `test_phase6_concurrency.py::test_retry_after_transaction_rollback_does_not_leak_a_slot`. |
| 6 | Same device retries the exact same activation request (same `idempotency_key`, same payload) | Returns the identical logical result, no new slot consumed. | Request-level idempotency via `ExternalIdempotencyRecord` (`idempotency.py:23-92`); `test_phase6_activation_protocol.py::test_idempotent_retry_returns_same_result_no_extra_slot`. |
| 7 | Same device retries with the same `idempotency_key` but a materially different payload | Rejected — `IDEMPOTENCY_CONFLICT` (409). | `idempotency.py:38-40`, `activation.py:168-169`. |
| 8 | Same device key, fresh client-generated `installation_id` (reinstall without a preserved credential, e.g. app data cleared) | Recognized via device-key fingerprint match on the same license — reuses the existing `Installation`/`DevicePublicKey` rather than creating a duplicate row or consuming a new slot. | `activation.py:196-215` (comment: client-generated IDs are fresh on every attempt by protocol design); `test_phase6_activation_protocol.py` (device-key retry with fresh installation_id reuses installation, line ~178). |
| 9 | Same device key attempts to activate a **different** license | Rejected cleanly (does not silently reassign the device to the new license). | `test_phase6_activation_protocol.py` ("same device activating a different license rejected cleanly"). |
| 10 | Device replacement — staff revokes an old device's key and issues a new one for the same Installation | Old `DevicePublicKey.status = REPLACED` with `replaced_by_device_key_id` pointer — "an old, replaced device can never be silently reactivated" (real comment). Installation itself may transition `ACTIVE → REPLACED` via `replace_device_slot()`. | `device_identity.py:124-139`; `commercial_ops/device_slot_ops.py:51-60`; staff route `POST /licensing-admin/installations/<id>/replace-device`. |
| 11 | Revoked device retries activation/check-in | Rejected — device key `status=REVOKED` is never trusted again, even retroactively for previously-signed material. | `device_identity.py:112-121`; `owner/app/licensing_service/assertions.py:123-124` (`if key_row.status == "REVOKED": return False, "INVALID_SIGNATURE"`). |
| 12 | Deactivated installation retries deactivation (duplicate deactivation request) | Idempotent — succeeds without error, no double-processing. | `deactivation.py:78-80` (only mutates if not already deactivated); `test_phase6_checkin_protocol.py::test_deactivation_idempotent`. |
| 13 | Deactivated installation attempts to reactivate itself by simply activating again | Rejected — a fresh, full activation (new registration flow) is required; there is no "un-deactivate" path for the device itself (only staff-driven `Installation.transition` can move `SUSPENDED → ACTIVE`, and `DEACTIVATED` is terminal per `VALID_TRANSITIONS`). | `installations/services.py:18-25` (`DEACTIVATED -> {}`); `test_phase6_checkin_protocol.py::test_deactivation_then_reactivation_requires_new_activation`. |
| 14 | Staff grants a temporary extra slot beyond `device_limit` (e.g. for a hardware-replacement grace window) | Real mechanism exists — `DeviceSlotException.extra_slots`, added into the effective limit for its active window. | `resolve_effective_device_limit()`, `commercial_ops/device_slot_ops.py:122-135`. |
| 15 | Activation under a manual-approval product policy | Registration succeeds but lands in `PENDING_ACTIVATION` (not `ACTIVE`), no signed assertion issued yet; staff must approve. Device limit is **re-checked at approval time**, not just at initial request, so two pending requests cannot both later exceed the cap. | `activation.py:249,254`; `commercial_ops/activation_policy.py:44-75,149-220`; `test_commercial_ops_activation_policy.py::test_approve_pending_activation_rechecks_device_limit`. |

## Real cap source

Every row above enforces against `resolve_effective_device_limit()` —
`License.device_limit` + active `DeviceSlotException` extras — never
`Plan.max_device_count`/`Subscription.device_allowance`/the generic
`max_devices` entitlement (see the four-location finding in
`subscription-entitlement-license-map.md`). No per-platform sub-cap
exists in real code today (scenario 1 above); a future "N Windows + M
mobile" style policy would be new `OWNER_SERVER` work, not something
the mobile client can enforce locally, and is out of scope for M7.

## Deliberately not invented

No default `device_limit` value, no per-platform cap, and no
mobile-vs-desktop distinct policy is asserted here beyond what the
cited code actually enforces. `DEVICE_POLICY_PLATFORM_CATEGORIES`
(`app/models/activation_governance.py:17-20`, including a `MOBILE`
combined-cap category) exists as real schema but was not found wired
into `process_activation()`'s actual enforcement path in this reading
— flagged as a real, open question for
`licensing-gap-ownership-matrix.md` rather than assumed either way.
