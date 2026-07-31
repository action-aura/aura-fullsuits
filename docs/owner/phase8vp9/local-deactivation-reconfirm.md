# Phase 8V-P9 Part O — Local Deactivation Re-confirmation

## Disposition: unchanged, P2, not re-run physically this session

Local deactivation (`deactivate` route / "Deactivate This Device" UI action) was already evidenced in
Phase 8V-P7 and earlier phases as real and correct, and classified P2 in the residual risk register.
No code in the deactivation path (`commercial_runtime/licensing_contracts/routes.py`'s `deactivate`
handler, `client.py`'s `deactivate()`, or the Kotlin `LicensingCoordinator` deactivate call) changed
this session -- the only source change was the stale-assertion guard inside `checkin_scheduler.py`'s
check-in ingestion path, which `deactivate()` does not call.

Per this phase's own instruction ("Do not repeat completed scenarios without a technical reason"),
this was not physically re-run. Confirmed by code inspection that the deactivation path is untouched:

- `git diff` for this session's commits touches only `checkin_scheduler.py`, `events.py`,
  `device_slot_ops.py`, `preflight.py`, plus the 8 version files and their tests -- no deactivation
  code.
- The real "Deactivate This Device" button is still present and reachable on the rc.5 Licensing screen
  (confirmed visually during this session's Scenario 7 UI navigation, see
  `scenario7-device-facing-final.md`), unchanged in text/behavior from rc.4.

No new evidence needed; prior evidence stands.
