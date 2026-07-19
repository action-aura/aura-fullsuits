# Clinic — Backup / Restore UI (Wave 1A, Part L)

## What was built
Mirrors Retail's implementation (Part G) exactly, same backend contract (`commercial_runtime/backup/routes.py`), same safety requirements:
- `ClinicSession.isAdmin`-gated `BackupRestoreScreen.kt` (admin-only; `EmptyState` for non-admins).
- "Create backup" button, list of existing backups with filename/date/size, destructive restore behind an `AlertDialog` confirmation.
- No open file picker or arbitrary path — restore only references filenames already returned by `GET /api/backup/list`.
- Wired into `SettingsScreen.kt` (new `onOpenBackup` parameter, replacing the old "coming soon" placeholder — this also closes AUDIT-027's placeholder) and a new `"backup"` route in `AppRoot.kt`.
- Also closes an existing "Backup & restore — coming soon" placeholder that predated this wave.

## Device result
User-confirmed tested and working after the reinstall carrying this feature.

## Result
PASS.
