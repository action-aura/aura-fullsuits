# Phase 8V-P7 — On-Device Data Preservation — Final

## Result: PARTIAL — real, positive evidence from this session's actual upgrade/scenario work; no full
before/after baseline comparison (never established, same disclosed gap as prior sessions)

## Real, positive evidence from this session

- Both products' in-place upgrades (rc.3 -> rc.4) preserved `firstInstallTime` and `installation_id`
  exactly (Clinic: `c7150980-...` unchanged; Retail: `e77bd448-...` unchanged) -- confirmed directly via
  `dumpsys package` and real check-in responses, both before and after the upgrade.
- Clinic's pre-existing single patient record ("Extension Success Patient", created during Phase
  8V-P6's own Scenario 5 test) was confirmed still present (`TOTAL PATIENTS: 1`) on the Clinic Dashboard
  immediately after this session's rc.4 upgrade -- real, direct confirmation that a real commercial-code
  upgrade does not disturb domain data.
- Retail's pre-existing sale (`SALE-000002`, $100.00, from Phase 8V-P5) was confirmed still reflected in
  the Dashboard's `TODAY'S SALES`/`TRANSACTIONS` counters both before and after this session's own
  Scenario 2 restriction test, and confirmed to remain exactly 1 transaction (not incremented) after the
  denied "Charge" attempt during `RESTRICTED` -- real, direct evidence that the commercial-state
  transition itself did not touch domain data, and that the denied mutation created no partial row.

## What was not done

The full Part E-equivalent baseline (minimum patient/appointment/invoice counts for Clinic; minimum
product/sale/stock counts for Retail) was never established in this or any prior session, so a complete
field-by-field before/after comparison across every domain-data category could not be performed.

## Disposition

PARTIAL, real positive evidence rather than mere absence-of-negative-evidence, consistent with this
session's own pattern of honest, incremental disclosure.
