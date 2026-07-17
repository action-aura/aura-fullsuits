# Wave 0 Correction — Local Backup/Restore Foundation (AUDIT-019)

Status: **FIXED AND VERIFIED**

## Root cause

No backup/restore code existed anywhere in `commercial_runtime/`,
`products/*/backend`, or `products/*/desktop` prior to this phase (confirmed
by a repo-wide search, matching `docs/audit/18-backup-and-recovery-audit.md`
and re-confirmed at the start of this phase — see the pre-fix reproduction
report). Neither product had any way to protect customer data against disk
failure, accidental deletion, or a bad upgrade.

## Design

`commercial_runtime/backup/service.py` (shared by both products, since both
need the same mechanism):

- `create_backup(product_code, app_data_dir, dest_dir, app_version)` builds
  one zip containing `registry.db`, `<product>.db`, and `manifest.json`.
- Both source databases are copied via SQLite's native
  `Connection.backup()` API — **not** a raw file copy. This is the
  specific requirement from the spec ("use the correct SQLite backup
  mechanism rather than copying a database during an unsafe active
  write"): `backup()` takes an internal read lock and produces a
  transactionally-consistent snapshot even while the source is open for
  writes (including WAL mode, where a raw copy could capture a torn image
  or miss un-checkpointed `-wal`/`-shm` sidecar data).
- `manifest.json` records `product_code`, `schema_version` (currently `1`,
  a manual constant bumped only on a breaking schema shape change),
  `app_version`, `created_at`, and per-file `{name, size, sha256}`.
- Filename is deterministic:
  `aura-<product>-backup-<UTC timestamp>-app<version>-schema<version>.aurabak.zip`.
- **No partial backup is ever presented as success**: the archive is built
  in a temp directory, re-opened and validated with `zipfile.testzip()`,
  and only then moved into the destination via `os.replace()` (atomic on
  the same filesystem). If any step fails first, nothing is written to
  `dest_dir` at all — verified by
  `test_create_backup_leaves_no_partial_file_when_source_db_missing`.
- `is_safe_backup_dir()` rejects a destination path under `Program Files`
  or `Program Files (x86)`; the default destination is
  `<AURA_APP_DATA>/database/../backups` (configurable via `AURA_BACKUP_DIR`
  or the HTTP API caller), never the app's own install directory.

`restore_backup(product_code, app_data_dir, backup_zip_path)`:

1. Validates the zip opens and passes `testzip()`.
2. Parses and validates `manifest.json`'s required fields.
3. **Refuses a cross-product restore** — `manifest.product_code` must match
   the caller's `product_code` (Retail backup into Clinic, or vice versa,
   is rejected before any file is touched).
4. Rejects an incompatible `schema_version` (today: must equal exactly, the
   only supported shape).
5. Verifies every declared file's `sha256` and `size` against its actual
   bytes — a corrupted/tampered member is rejected before any live file is
   touched.
6. Runs `PRAGMA integrity_check` against each extracted database, rejecting
   anything that isn't a readable, structurally sound SQLite file.
7. Only after all of the above succeed: preserves whatever is currently
   live (`registry.db`, `<product>.db`, and their `-wal`/`-shm` sidecars if
   present) into a timestamped `pre-restore-<UTC timestamp>/` directory,
   then replaces the live files via `os.replace()`.
8. If the replace step itself fails partway through, a best-effort rollback
   copies the preserved originals back over whatever was already replaced.
9. Returns the list of restored files and the rollback directory path so
   the caller can surface both to an operator.

The caller (an admin, offline, single-user) is expected to not be
concurrently writing to the target databases during a restore — this is
explicitly a manual, offline operation per the spec ("local/offline
only... no advanced backup scheduler"), not a live-failover mechanism.

## HTTP surface

`commercial_runtime/backup/routes.py` provides one blueprint factory
(`make_backup_blueprint`) mounted identically by both products' `app.py` at
`/api/backup/{create,list,download/<name>,restore}`. Every route requires
an authenticated session with `role == 'admin'` (`401` unauthenticated,
`403` non-admin) — verified by dedicated tests in both products.

## Clinic-specific privacy handling

- The default backup directory lives under `AURA_APP_DATA`, not a publicly
  shared folder, and is never written unless an admin explicitly triggers
  a backup or restore.
- No patient-identifying data appears in backup filenames, manifest
  contents, or any log line this module emits — the manifest records only
  product code, version numbers, timestamps, file sizes, and checksums.
- Android's OS-level auto-cloud-backup setting is a pre-existing,
  documented placeholder (`AUDIT-027`, unrelated to this backend module)
  and is out of scope for this Wave 0 backend-only correction — it is
  **not** claimed to be fixed here.

## Tests

`products/retail/tests/retail_backup_restore_test.py` (11 tests, the
in-depth suite): manifest/checksum validity, round-trip restore, rejected
cross-product restore, rejected corrupted member, rejected incompatible
schema version, rollback-copy preservation, no-partial-file-on-failure,
`is_safe_backup_dir` accepting/rejecting the right paths, and admin-only
HTTP gating (unauthenticated + non-admin). `products/clinic/tests/clinic_backup_restore_test.py`
(4 tests) covers what's Clinic-specific: round-trip with `product_code='clinic'`,
refusing a Retail backup, and HTTP auth gating. All tested exclusively
against synthetic, throwaway SQLite databases — no real business or patient
data was used anywhere in this wave.

## Commit

`4f37e5d` — feat: local offline backup/restore foundation (AUDIT-019).

## Residual risk

- No scheduler exists (deliberately — the spec explicitly says not to build
  one unless small and necessary; a manual admin-triggered backup satisfies
  Wave 0's minimum-safe requirement).
- Restore assumes the calling process is the only writer during the
  operation; there is no cross-process lock. A future wave should document
  (or enforce) "stop the app before restoring" more forcefully in the
  packaged product's UI/CLI, not just in this module's docstring.
- `SCHEMA_VERSION` is a manually-maintained constant, not derived from an
  actual schema hash — a future schema change must remember to bump it.
