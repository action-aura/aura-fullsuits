# Phase 8V-P7 — Local Deactivation Recovery — Final

## Result: unchanged from Phase 8V-P5/P6, reconfirmed

No source affecting deactivation/reactivation changed this session (this phase's changes are entirely
in the commercial-artifact/version-alignment and physical-scenario-evidence surfaces; no edit touched
`deactivation.py`, `state_machine.py`'s `DEVICE_DEACTIVATED` transitions, or `LicensingScreen.kt` on
either product). The established contract stands: explicit local deactivation invalidates/removes the
local binding; fresh activation with the license key is required; renewal-without-key does not apply
after explicit deactivation; the full key is never retained for automatic recovery.

The real, disclosed P2 gap (no reachable in-app path from `DEVICE_DEACTIVATED` to the existing
key-entry form, despite the state machine defining a legitimate `reactivation_requested ->
ACTIVATION_REQUIRED` transition) remains open, unfixed, by the same deliberate scope decision recorded
in Phase 8V-P6: this phase's real engineering and validation time went to the artifact-alignment and
multi-device scenario work that was this phase's actual mandate. Per the governing spec's own
instruction, this P2 does not block the final Phase 8 tag decision when fresh activation remains safely
possible through a documented supported path (it is).

## Disposition

P2, disclosed, unfixed, unchanged in substance across three consecutive sessions now. Carried forward to
the final residual risk register.
