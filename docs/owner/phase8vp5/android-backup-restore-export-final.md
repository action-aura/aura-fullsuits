# Phase 8V-P5 — Android Backup/Restore/Export — Final

## Result: NOT VERIFIED this session

## Why

Not reached within this session's real time budget. The relevant on-screen messaging was observed
incidentally during Scenario 5 (both the `RESTRICTED` and `SUSPENDED/REVOKED/EXPIRED` state screens in
`LicensingScreen.kt` explicitly state "backup/restore/export remain available" -- confirmed by reading
the real rendered UI text, lines 175/181 of the Clinic screen), but the backup/restore/export workflows
themselves (locate the real supported entry point, create a backup, record filename/checksum, modify a
record, restore, confirm match, export, confirm safety) were not executed on either product.

## What is NOT claimed

No backup/restore/export evidence, physical or otherwise, is claimed for this session beyond the
incidental confirmation that the licensing screens' own copy asserts these operations remain available
in restricted states -- which is UI copy, not a functional test of the operations themselves.

## Follow-up required (next session)

Locate the real backup/restore/export entry points in both products' settings/admin screens, and run
the full Part L sequence for each: baseline -> backup -> record filename/checksum -> modify one record
-> restore -> confirm match -> export -> confirm export completeness/safety -> confirm all four
operations remain reachable during a real `RESTRICTED` state (using the same `OfflinePolicy` mechanism
proven in Scenario 5 to reach that state for real).
