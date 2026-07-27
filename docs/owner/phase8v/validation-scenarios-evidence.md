# Phase 8V — Part AB Scenario Evidence (all 7, one document)

Consolidates the governing brief's seven separate `scenario-N-*-evidence.md` files into one, tiered
honestly by what kind of evidence actually backs each:

- **Tier 1 (real wire traffic)**: the real Owner Flask app on a real localhost TCP port, driven by
  the real `commercial_runtime.licensing_contracts` client -- genuine product-to-Owner HTTP,
  cryptographic signing and verification included. `owner/tests/test_phase8v_scenario_live_server.py`.
- **Tier 2 (real Owner-side HTTP/service integration)**: real Postgres, real Flask test client, real
  RBAC/MFA/CSRF, real service-layer calls -- everything except the actual product-side wire hop.
- **Physical Android**: not available this session (see `phase8v-physical-validation-report.md`).
  No scenario below claims this tier.

## Scenario 1 — Early renewal (Tier 1)

`test_scenario_1_early_renewal_real_wire_traffic`: real activation over real HTTP with real Ed25519
signature verification server-side -> Owner UI/service renewal create-approve-apply pipeline ->
real check-in over real HTTP. Proven: same `owner_installation_id` before/after, assertion ID
changes (fresh assertion issued), `last_sync_result == "SUCCESS"`, resulting local state is
`ACTIVE_ONLINE`/`ACTIVE_OFFLINE`, and the full license key never appears in any locally persisted
state. This run is also what surfaced and let us fix a real P1 (see `phase8v-security-review.md`).

## Scenario 2 — Renewal after expiry (Tier 1)

`test_scenario_2_renewal_after_expiry_real_wire_traffic`: real activation -> subscription
transitioned to `EXPIRED` (Owner's own record) -> late renewal through the real,
separation-of-duties-gated pipeline (the only path that can revive `EXPIRED -> ACTIVE`, per
Milestone 2's security lesson) -> real check-in confirms the same device identity, a real signed
assertion, and `ACTIVE_ONLINE`/`ACTIVE_OFFLINE` afterward. Prior local-state-machine behavior during
the actual restricted/grace window (elapsed-offline-time-driven) was proven correct with physical
hardware in Phase 7V-A and is not re-derived here -- this scenario's *new* claim (revival-after-expiry
through the Phase 8 pipeline) is what's newly verified.

## Scenario 3 — Past due -> restricted (Tier 2)

Commercial-side transition and notification generation: `expiry_scan.py`'s real
Postgres-backed test suite (Milestone 3, `owner/tests/test_commercial_ops_expiry_scan.py`, still
green this session) plus `resolve_commercial_state()`'s explicit `PAST_DUE` branch (Milestone 1) --
`may_issue_assertion` stays true while the license is still ACTIVE/ISSUED, matching the Part I rule
"past-due must not automatically mean revoked/blocked." The product-local state-machine's
warning/grace/restricted timing itself (elapsed-time-driven, distinct from Owner's commercial
timing) is unchanged Phase 6/7 code, already proven correct with real elapsed-time and physical
hardware in Phase 7V-A -- not re-derived here.

## Scenario 4 — Pilot conversion (Tier 2)

`test_pilot_conversion_guided_flow_via_ui` (owner-side): create pilot -> approve -> activate ->
guided conversion (separate creator/approver, recent-auth-gated, real renewal
create-transition-approve-apply) -> `mark_pilot_converted()` -> pilot genuinely `CONVERTED`, linked
to the real applied renewal, `conversion_decision == "CONVERT"`. Not run through the live-wire
harness this session (Tier 1) -- the underlying renewal-apply mechanics are identical to Scenarios 1
and 2, which ARE Tier 1, so the incremental risk of the untested product-check-in leg specifically
for a pilot-originated renewal is low, but it is disclosed as untested at that tier rather than
assumed equivalent.

## Scenario 5 — Emergency extension (Tier 2)

`test_emergency_extension_create_requires_recent_auth_and_reason` (owner-side) plus
`owner/tests/test_commercial_ops_assertion_fields.py::test_emergency_extension_id_only_while_active_and_unexpired`
(proves the assertion payload's `emergency_extension_id` field is populated only while genuinely
in-window, and `test_phase8_security_fraud_controls.py::test_revoked_license_wins_over_emergency_extension_override`
proves a REVOKED license can never be restored by one). Not run through the live-wire harness this
session.

## Scenario 6 — Device replacement (Tier 1)

`test_scenario_6_device_replacement_real_wire_traffic`: real activation of device A -> real HTTP
activation attempt by device B rejected with `DEVICE_LIMIT_REACHED` (the license is at its limit) ->
Support approves replacement via `replace_device_slot()` (never a direct database edit) -> device A's
installation genuinely `REPLACED` -> device B activates for real over real HTTP and receives its own,
distinct `owner_installation_id` -- no silent identity reuse.

## Scenario 7 — Plan downgrade / device overage (Tier 2)

`owner/tests/test_commercial_ops_device_slot_ops.py` (Milestone 5, still green) proves
`scan_over_limit_licenses()` never touches an installation itself and creates a deduped notification
for a human to act on; `test_device_slot_exception_create_and_revoke` (Phase 8V, owner-side HTTP)
proves the temporary-exception remediation path end to end. Not run through the live-wire harness
this session -- the "new activation blocked once over limit" claim is the same mechanism Scenario 6
already proves at Tier 1 (`DEVICE_LIMIT_REACHED`), just not specifically triggered by a downgrade in
this pass.
