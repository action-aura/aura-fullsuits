# Phase 8V-P — Identity and Slot Continuity Report (Part Q)

Consolidates the before/after tables already captured live in each scenario doc — real values from
Owner's own database, not the product's self-report, across real installed-product and real
simulated-device runs this session.

| Scenario | Installation ID | Device fingerprint | Assertion ID | Installations on license |
|---|---|---|---|---|
| 1 (early renewal, real Clinic.exe) | unchanged (`1bc69547-...`) | unchanged (`aa9863ef...`) | fresh (`5c884c70...` -> `7ef8fe5a...`) | unchanged (1) |
| 2 (late renewal/revival, real Retail.exe) | unchanged (`1661ac4d-...`) | unchanged (`78a8ba24...`) | fresh, 4 distinct assertions across activate/check-in/expire/revive, never repeated | unchanged (1) |
| 4 (pilot conversion, simulated device) | n/a (single activation, no second wire round-trip — see scenario doc) | n/a | fresh at activation | unchanged (1) |
| 6 (device replacement, two simulated devices) | **intentionally different** (`caec4381-...` -> `e0205c27-...`, by design — this is the one scenario where identity is SUPPOSED to change, on an explicit staff-approved `replace_device_slot()` action) | old key `REPLACED` (not reused), new key genuinely new | fresh on the new device's own activation | never exceeded 1 at any point |
| 7 (plan downgrade, two simulated devices) | both installations unchanged throughout the downgrade + scan | both fingerprints unchanged | n/a (no renewal-driven check-in in this scenario) | unchanged (2, then correctly blocked at 3rd attempt) |

## Summary against Part Q's expectations

- **Normal renewal** (Scenarios 1, 2): installation ID unchanged, device key unchanged, active
  device count unchanged, no new installation, no license-key entry, assertion state genuinely
  fresh each time, term/entitlements updated. **Matches expectation exactly.**
- **Pilot conversion** (Scenario 4): installation/device continuity structurally guaranteed by the
  same mechanism (conversion never touches `Installation`/`DevicePublicKey` rows at all — only
  `Subscription`/`RenewalRequest`/`PilotRecord`) — not independently re-proven by a second live wire
  call this session, disclosed as a narrower claim in the scenario's own doc.
- **Device replacement** (Scenario 6): identity is *deliberately* different post-replacement — the
  correct behavior, not a continuity violation, and the one case Part Q does not expect continuity
  for.
- **Emergency extension** (Scenario 5): no activation/check-in was performed against a real device
  in that scenario (Owner-side HTTP/MFA proof only) — no identity/slot table row to add here.
