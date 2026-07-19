# Retail — Backup / Restore UI (Wave 1A, Part G)

## What was built
No admin-only backup/restore UI existed in the Retail app prior to this wave. Added, wired to the existing Wave 0 / Phase 3.7 backend endpoints (`commercial_runtime/backup/routes.py`, unchanged this wave):
- `RetailSession.kt` — observable `isAdmin` state, populated from `session()`/login responses, never defaulted to admin on ambiguous data.
- `BackupRestoreScreen.kt` — admin-gated (`EmptyState` "Admin access required" for non-admins); "Create backup" button; list of existing backups (filename, date, size) each with a "Restore backup" action; destructive restore requires an explicit `AlertDialog` confirmation ("This will replace all current data on this device... cannot be undone").
- No open file picker or arbitrary filesystem path — restore only ever references a filename already returned by `GET /api/backup/list`.
- Wired into `RetailSettingsScreen` (`RetailExtraScreens.kt`) via a new "Backup & restore" section and a `"backup"` nav route in `AppRoot.kt`.

## Naming discrepancy resolved
The nav graph's existing `"retail_settings"` route calls `RetailSettingsScreen` (defined in `RetailExtraScreens.kt`), not a same-named `SettingsScreen` composable that exists separately in `SettingsScreen.kt` for a different purpose. Backup wiring was applied to the correct, actually-routed screen.

## Device result
User-confirmed: **"working just fine, down to the smallest detail even the back up."** Create-backup and restore both exercised live on-device against the real backend.

## Result
PASS.
