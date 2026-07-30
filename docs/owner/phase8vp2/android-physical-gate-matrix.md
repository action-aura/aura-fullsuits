# Phase 8V-P2 — Android Physical Gate Matrix

No physical device was connected this session (`physical-android-readiness.md`). Per this phase's own
"no false completion" principle, the seven per-scenario physical evidence files the governing brief
lists (`clinic-android-early-renewal-evidence.md`, `retail-android-late-renewal-evidence.md`,
`retail-android-past-due-evidence.md`, `clinic-android-pilot-conversion-evidence.md`,
`clinic-android-emergency-extension-evidence.md`, `retail-android-device-replacement-evidence.md`,
`retail-android-plan-downgrade-evidence.md`) are **not created** as empty or templated placeholders --
doing so would misrepresent untested scenarios as having an evidence trail. This single matrix is the
honest substitute.

| Scenario | Product | Windows (real, Phase 8V-P) | Android physical (this session) |
|---|---|---|---|
| 1. Early renewal | Clinic | PASS | NOT VERIFIED -- no device |
| 2. Late renewal / revival | Retail | PASS | NOT VERIFIED -- no device |
| 3. Past due -> restricted | Retail | PASS (Owner-side) | NOT VERIFIED -- no device |
| 4. Pilot conversion | Clinic | PASS (Owner-side) | NOT VERIFIED -- no device |
| 5. Emergency extension | Clinic | PASS | NOT VERIFIED -- no device |
| 6. Device replacement | Retail | PASS | NOT VERIFIED -- no device |
| 7. Plan downgrade / overage | Retail | **PASS this session** (was CONDITIONAL; Owner-side gap fixed, see `scenario7-*.md`) | NOT VERIFIED -- no device |

Shared invariants (signed activation, no key retransmission, installation/device-key/device-slot
continuity, assertion state-version monotonicity, restart/force-stop persistence, backend
enforcement, data preservation, traffic privacy, Logcat privacy) are likewise unverified on physical
Android hardware for the same single reason: no device.

## What changed since Phase 8V-P's own matrix

Only Scenario 7's Windows/Owner column: CONDITIONAL -> PASS. Every Android cell is unchanged --
still NOT VERIFIED, for the same disclosed reason, now with a genuinely smaller remaining gap (the
underlying Owner-side defect that would have made Scenario 7 fail even if a device *had* been
connected this session is now fixed, so a future device session's Scenario 7 run is expected to pass
cleanly rather than surface the same defect again).

## Next session's exact job

Connect a physical device, install the already-built rc.3 APKs (`final-android-artifact-evidence.md`),
and run these same seven scenarios physically. Every one of them is already proven correct at the
Owner/service tier and, for six of seven, at the real-installed-Windows-product tier -- nothing about
Owner's behavior is expected to change; the device session's job is to confirm the identical outcome
reaches a real phone.
