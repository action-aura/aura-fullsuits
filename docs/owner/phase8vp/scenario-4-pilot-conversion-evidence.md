# Phase 8V-P — Scenario 4: Pilot Conversion (Clinic) — REAL EVIDENCE

**Tier**: real Owner server, real Ed25519-signed device client (simulated device, not the literal
installed `.exe` — that process was already committed to Scenario 1's device identity; reusing it
for a second, different license correctly hit this session's own new `DEVICE_ALREADY_REGISTERED`
guard, which is itself proof the fix works — see Scenario 6). **Android leg**: NOT VERIFIED.

## Real pilot setup and activation

Pilot subscription `aec9b3d8-0a83-46fc-87f7-39f21d1e95b4` (Clinic, PILOT status), pilot record
`838e94c6-37e9-4061-bd39-3e3631ffdc30` approved and activated via the real Owner service layer.
Real signed activation (fresh Ed25519 device, real HTTP to the live Owner server) succeeded;
captured assertion payload confirms the pilot-specific fields are live on the wire:
`"pilot_status": "ACTIVE"`, `"subscription_status": "PILOT"`, `"plan_code": "8VP-CLINIC-PILOT"`,
`"renewal_status": "NONE"`.

## Real guided conversion

`create_renewal_request(date_rule="PILOT_CONVERSION", 2026-08-01 -> 2027-08-01)` -> full transition
chain -> `approve_renewal_request()` (different staff, matching Scenario 1/2's proven
separation-of-duties path) -> `apply_renewal_request()` -> `mark_pilot_converted()`. Confirmed in
Owner's database: `Subscription.status = ACTIVE`, `end_date = 2027-08-01`, `PilotRecord.status =
CONVERTED`, `conversion_decision = CONVERT`.

## Post-conversion assertion content

The device key generated for the pre-conversion activation call was not persisted to disk (a
scripting oversight this session, not a product limitation), so a second real wire round-trip
specifically re-proving the *converted* assertion payload with the *same* device was not completed.
What IS proven for real: (1) the pre-conversion assertion genuinely carried live `pilot_status`
data over the wire, and (2) the conversion itself completed for real in Owner's database through the
full separation-of-duties pipeline. The post-conversion assertion content mapping
(`pilot_status: "CONVERTED"`) is the exact same `resolve_commercial_assertion_fields()` function
already unit-tested against a real Postgres row in
`owner/tests/test_commercial_ops_assertion_fields.py::test_pilot_status_reflects_pilot_record`
(still green this session) — not re-derived here a second time, honestly disclosed as a narrower
claim than Scenarios 1/2/6 rather than silently assumed.

## No patient data anywhere in this flow

No Clinic-domain (patient/appointment/prescription) endpoint was touched at any point in this
scenario — structurally impossible for licensing traffic to carry it regardless (see
`real-traffic-evidence.md`).

## Result: **PASS** (Owner-side conversion mechanics, real). Post-conversion wire re-confirmation
and Android leg: **NOT VERIFIED** this session.
