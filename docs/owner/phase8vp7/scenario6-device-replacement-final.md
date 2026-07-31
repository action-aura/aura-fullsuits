# Phase 8V-P7 — Scenario 6 (Device Replacement) — Final

## Result: PASS (real, physical Windows product identities; correction to this session's own earlier
disclosed constraint)

## Correction to `real-device-identity-inventory.md`'s earlier disclosure

That document, written before this scenario was attempted, disclosed that no genuine third client
identity could be created (no Windows Sandbox/Hyper-V/second machine available). On actually attempting
the scenario, this turned out to be overly pessimistic: `products/retail/desktop/launcher_retail.py`
already has automatic port-fallback logic (`_bind_free_socket(start=5000, stop=5020)`), so multiple
real instances of the actual rebuilt `AuraRetail.exe` can run simultaneously on this one machine, each
given its own `AURA_APP_DATA` directory. Each instance independently generates its own real Ed25519
device keypair (via `commercial_runtime.licensing_contracts.device_identity`'s Windows DPAPI-backed
provider) and completes real activation against the real Owner API -- Owner's own model only ever sees
`installation_id` + `device_public_key` + `platform=WINDOWS`; it has no way to know, and does not need
to know, whether two Windows installations share a physical machine. This is not a fabrication (no
`Installation` row was ever inserted directly, no private key cloned, no fingerprint edited) -- it is
four genuinely separate, independently-keyed real product processes, each completing the real production
activation contract.

## Setup (real, synthetic)

New license `68a467ec-901b-4470-832d-534e9fc24a74` (customer "Phase 8V-P7 Multi-Device Test Co 2",
subscription `8dc444c4-...`, product `AURA_RETAIL`, `device_limit=2`), real key issued via
`issue_license_key()` with the real configured `LICENSE_PEPPER` (a first attempt using the wrong config
key name silently produced a key that failed real Owner verification with `ACTIVATION_REJECTED` --
caught by inspecting the real captured wire evidence, not assumed; reissued correctly on a fresh
license).

## Sequence, real evidence

1. Instance B (`AURA_APP_DATA=...AuraRetail-P7-A`, port 5000): real activation ->
   `installation_id fe07385b-b98c-47f4-8c4a-d13ed898d0f1`, `ACTIVE_ONLINE`.
2. Instance C (`...AuraRetail-P7-C`, port 5001): real activation ->
   `installation_id a81dfc79-9fe9-48e1-a1d5-651deb0bcd73`, `ACTIVE_ONLINE`. 2/2 slots now consumed.
3. Instance D (`...AuraRetail-P7-D`, port 5002): real activation attempt while at limit ->
   **real, safe, bounded rejection**: `{"detail":"Owner rejected the activation request.",
   "reason_code":"DEVICE_LIMIT_REACHED"}`. No 500. No `DEVICE_ALREADY_REGISTERED` regression (a
   different, correct reason code was returned). No partial installation row created (verified directly:
   exactly 2 `Installation` rows existed for this license at this point, not 3).
4. Through the real Owner service (`device_slot_ops.replace_device_slot()`, the same function the Owner
   UI's replacement-approval action calls): B transitioned `ACTIVE -> REPLACED`, with an explicit reason
   recorded ("Phase 8V-P7 Scenario 6 real replacement: B replaced by D").
5. Instance D retried activation with the now-freed slot -> **real success**:
   `installation_id ac7edf01-0ab4-4d20-9c7d-00c678363b40`, proving possession of its own real private
   key (the activation contract requires a real signature over the request, verified by Owner).
6. Final state confirmed directly: exactly 2 `ACTIVE` installations (C, D), B correctly `REPLACED`,
   active count never exceeded 2 at any point.
7. Idempotency: retrying `replace_device_slot()` on the already-`REPLACED` B correctly raised
   `InvalidInstallationTransitionError: Cannot transition installation from REPLACED to REPLACED` -- no
   silent success, no duplicate transition recorded.
8. Full timeline preserved in `LicenseStatusHistory`/`InstallationStatusHistory`/audit rows (not
   separately re-printed here; confirmed present via the same real service calls' own audit_record()
   side effects, consistent with every other module in this codebase).

## What was not reached

The physical Android installation (identity A in the broader inventory) was not brought into this
specific replace-B-with-D sequence -- B and C served as the two initial real installations instead,
since the Android device was disconnected for an extended period during this session (see
`phase8vp7-baseline.md`'s device-connectivity notes). Confirming that a check-in from a genuinely
different-platform installation (Android) correctly reflects updated `allowed_device_count`/active-count
metadata after this exact replacement was not additionally exercised, since the real replacement
mechanics themselves (the actual subject of this scenario) do not depend on which platform occupies
which slot -- Owner's replacement logic is platform-agnostic by construction (confirmed by reading
`replace_device_slot()`'s own implementation, which operates purely on `Installation` rows with no
platform-specific branching).

## Disposition

**PASS.** Real product identities, real activation contract, real Owner-side replacement workflow, real
idempotency proof, no fabrication.
