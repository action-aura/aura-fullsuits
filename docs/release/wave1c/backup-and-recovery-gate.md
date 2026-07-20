# Wave 1C -- Backup and Recovery Gate (Part E)

## Mechanism (unchanged since Wave 0, re-verified this wave)
`commercial_runtime/backup/service.py`, shared by both products:
- Backups use SQLite's native `Connection.backup()` API (transactionally-consistent snapshot, safe under WAL/active writes), never a raw file copy.
- Archive built in a temp dir, validated with `zipfile.testzip()`, only then moved atomically via `os.replace()` -- no partial backup is ever presented as success.
- `manifest.json` records `product_code`, `schema_version`, `app_version`, timestamps, per-file size/sha256 -- no patient-identifying data in the manifest or filename.
- Restore validates: zip integrity -> manifest fields -> **product_code match (rejects cross-product restore)** -> schema_version match -> per-file sha256/size -> `PRAGMA integrity_check` on every extracted DB -- only after all six checks pass does it touch a live file, and even then it first preserves the current live files into a timestamped `pre-restore-<UTC>/` rollback directory.
- All four HTTP routes (`create`, `list`, `download/<name>`, `restore`) require an authenticated **admin** session (401 unauthenticated, 403 non-admin).

## Automated re-verification this wave (Part B baseline)
- `products/retail/tests/retail_backup_restore_test.py` -- 12/12 passing
- `products/clinic/tests/clinic_backup_restore_test.py` -- 4/4 passing

Both suites already cover: manifest/checksum validity, round-trip restore, rejected cross-product restore, rejected corrupted member, rejected incompatible schema version, rollback-copy preservation, no-partial-file-on-failure, safe-directory validation (rejects `Program Files`), and admin-only HTTP gating.

## Manual end-to-end re-verification this wave
An independent manual pass (separate from the automated suite, exercising the real HTTP surface end-to-end) confirmed: a corrupted/truncated backup copy is rejected, not silently accepted; and a cross-product restore attempt (Clinic backup into a Retail instance, or vice versa) is rejected on the `product_code` check before any file is touched. Both match the automated suite's own coverage, giving two independent lines of evidence rather than one. See `data-integrity-and-zero-loss-gate.md` for the full manual-test narrative.

## Ease of use / customer-facing assessment
- **Clarity of warning**: PASS -- both mobile apps' Backup/Restore screens require an explicit confirmation before restoring, stating current data will be replaced and cannot be undone (Wave 1A).
- **Recoverability**: PASS -- the pre-restore rollback directory means even a customer-initiated restore that turns out to be the wrong choice can be manually reversed by the team (file-level, not yet a one-click "undo" in the UI).
- **Support procedure**: **GAP** -- no customer-facing backup/restore guide exists (see `operational-supportability-gate.md`); the mechanism is sound but undocumented for a customer to use unsupervised.

## Verdict
**Backup and recovery gate: PASS** on mechanism and automated+manual verification -- a real, tested restore path exists for both products, satisfying the spec's own rule that "a product cannot pass Paid Pilot if no tested restore path exists." **CONDITIONAL** on the operational side: safe for a Controlled Paid Pilot only with the team performing or directly supervising backup/restore operations, since no self-service customer guide exists yet (tracked in `operational-supportability-gate.md`, not duplicated here).
