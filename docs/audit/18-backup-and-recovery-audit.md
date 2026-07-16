# Backup, Restore, and Disaster Recovery Audit

## PROVEN: no backup/restore capability exists in either product, on either platform

Repo-wide grep across `commercial_runtime/`, `products/*/backend`,
`products/*/desktop` for `backup|restore|\.bak|export_db|import_db` (code, not
docs) returned **zero functional matches**. `deployment/backups/` and
`deployment/scripts/` directories exist in the repo but are **empty**.

The only non-doc hits found repo-wide are all cosmetic or unrelated:

- `android:allowBackup="false"` in both Android manifests — this **disables**
  the OS-level auto-backup mechanism, it is not application backup code (and
  disabling it is the correct choice given no server-side restore path exists
  to make an OS-level backup meaningful anyway — see `10`).
- Both Android apps' `SettingsScreen.kt` contain a settings row literally
  labeled `"Backup & restore"` that, when tapped, calls a `soon(...)` helper
  — an explicit **"coming soon" placeholder with no backing functionality**.
  This is a real, user-visible commercial-trust issue: the product presents a
  backup feature as existing (in the UI) when it does not.
- Navigation-Compose's `restoreState = true` (back-stack restoration) and a
  UI-button-state comment containing the word "restore" — both unrelated to
  data backup.

## What this means concretely

- **No automatic backup** of either product's SQLite database exists.
- **No manual backup command/button** exists (beyond the non-functional UI
  placeholder above).
- **No restore procedure** exists — there is nothing to restore *to* even if a
  customer manually copied their `.db` file themselves (copying the raw
  SQLite file while the app is running, without app-level coordination, is
  possible but unsupported and unvalidated by this audit — WAL-mode SQLite
  requires copying the `-wal`/`-shm` sidecar files consistently with the main
  file, which no in-app tooling helps a non-technical user do correctly).
- **No corrupted-backup handling**, **no backup consistency validation**, **no
  retention policy** — none of these apply, because there is no backup
  artifact to validate in the first place.
- **Customer data ownership / export**: Retail has no export capability at
  all (`03`/`12`, confirmed NOT PRESENT IN SOURCE); Clinic was not found to
  have one either in the routes sampled. A customer who wants to leave the
  product, or simply wants their own copy of their data for peace of mind,
  currently has no supported way to get it.
- **Disaster recovery documentation**: none found, customer-facing or
  internal.

## Windows vs. Android

No difference — both platforms share the identical (absent) backup posture,
since both run the same backend with the same lack of backup code.

## Severity

**Classified P1** in the defect registry — "unsafe backup/restore" is
explicitly one of the audit's own P1 examples, and this is a stronger case
than merely "unsafe": there is no backup/restore at all, for either product,
covering both ordinary business records (Retail sales/inventory) and patient
medical/billing data (Clinic). This is independently disqualifying for Gate 3
(paid SMB customers) per the audit's own gate definitions (`25`), regardless
of how the financial/security findings elsewhere in this audit are resolved.
