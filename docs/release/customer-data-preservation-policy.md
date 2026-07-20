# Customer Data Ownership & Preservation Policy

## Ownership
All business data (Retail: products, sales, customers, suppliers, purchase orders, returns, payments; Clinic: patients, appointments, visits, prescriptions, invoices, payments) is created, stored, and owned entirely by the customer, on their own device/computer. Action Aura does not receive, transmit, or have access to this data in this release.

## What this release does and does not do
- **No cloud upload.** Every database (`registry.db`, `retail.db`/`clinic.db`) lives on local disk (`%LOCALAPPDATA%\AuraRetail` / `AuraClinic` on Windows; app-private storage on Android) and is never transmitted anywhere by this software.
- **No Owner/central system connection.** No "Aura Owner" platform, licensing server, or telemetry endpoint exists in this release to connect to.
- **No automatic updates.** Nothing in this release checks for or installs updates on its own; every install/upgrade is something the customer explicitly runs.

## Uninstall
Both Windows installers preserve all business data by default when uninstalling — the installer only ever removes what it installed (the application binaries, Start Menu/Desktop shortcuts, and its own registry uninstall entry), never the data directory, which was never something the installer wrote to in the first place (see `schema-migration-and-data-safety-report.md` and the two installer reports). An explicit, separately-confirmed option exists to also delete local data, requiring the user to affirmatively opt in and then confirm a second time — see `retail-windows-installer-report.md`'s "A real safety defect found and fixed" section for the specific silent-uninstall risk this was hardened against.

## Backup
A customer can create a backup at any time (Settings → Backup & Restore, both products, both platforms) — this produces a file in the product's own backup directory, never uploaded anywhere. Restoring validates the backup's `product_code` and `schema_version` against the running installation before touching any data (`commercial_runtime/backup/service.py`) — an incompatible or corrupted backup is rejected with a clear message rather than partially applied.

## Warnings shown to the user for destructive actions
- **Uninstall data deletion**: two sequential confirmations, worded specifically for the product (business data vs. patient/clinical data), naming the exact folder that would be deleted, explicitly stating it's kept by default and this is opt-in.
- **Restore replacing current data**: both mobile apps' Backup/Restore screens require an explicit confirmation dialog before restoring, stating current data will be replaced and this cannot be undone (Wave 1A).
- **Incompatible/corrupted backup**: rejected by `commercial_runtime/backup/service.py` with a specific error, not a silent partial restore.
- **Failed migration**: see `schema-migration-and-data-safety-report.md` — a failed schema migration leaves the original database untouched and does not advance the version marker, so it is never silently treated as "already migrated" when it wasn't.

## What a future release might change
If a future wave introduces cloud sync, licensing, or telemetry, this document is the one that must be updated first and prominently — the current, honest state is "fully local, fully customer-owned, zero data leaves the device."
