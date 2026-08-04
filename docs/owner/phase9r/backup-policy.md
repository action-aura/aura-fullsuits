# Phase 9R — Backup Policy (M12)

## Why the existing local backup proof isn't enough

Phase 9.5E already proved backup/restore works
(`docs/owner/phase9_5e/` references local backup+restore); Phase 5's own
`OWNER_BACKUP_DIR` writes `pg_dump` output to the local filesystem
(`app/system/backup.py`). That's necessary but not sufficient for
production: a backup that lives only on the production host dies with the
production host (single-host-risk, `deployment-architecture.md`). Phase 9R
requires the backup to leave the host.

## What gets backed up

| Item | Method | Frequency (target) |
|---|---|---|
| PostgreSQL (full logical dump) | `pg_dump` (existing `app/system/backup.py` tooling, extended with an upload step) | Daily, plus before every migration (M17) |
| Expense attachments (`OWNER_EXPENSE_ATTACHMENT_DIR`) | File-level sync to the same external target | Daily |
| Release metadata (M10) | Included in the PostgreSQL dump (it's relational data, not separate) | Same as DB |
| Release artifacts (M11) | Relies on the object-storage provider's own versioning if it provides sufficient durability; otherwise mirrored separately | Provider-dependent, decided once a provider is selected |
| Signing-key recovery material | **Separate, manual, documented procedure — never bundled into the automatic backup** (see below) | On generation and on rotation, not on the daily schedule |

## Encryption boundary

Every automatic backup artifact (DB dump, attachment archive) is encrypted
client-side, before upload, using a key that is itself never stored in the
same destination as the backups it protects. Ordinary backups contain **no
plaintext secrets** — `OWNER_SECRET_KEY`, `OWNER_LICENSE_PEPPER`, and
database credentials are never inside a `pg_dump` (they're deployment
configuration, not application data) and are separately excluded from any
file-level backup path by name pattern, consistent with the existing
`.gitignore` exclusions for the same material.

## Signing-key recovery — the one deliberate exception

License-signing private keys (`OWNER_SIGNING_KEY_DIRECTORY`) are
security-critical enough that they get their **own** procedure, not the
general backup pipeline:

- Generated once per environment, backed up manually at generation time and
  at every rotation (M3), to a destination with stricter access control than
  the general backup bucket (e.g. a separate, more tightly scoped
  credential, or an offline/cold copy).
- Never included in the automatic daily backup sweep — a compromise of the
  general backup credential must not also hand over the signing key.
- Recovery procedure documented in `operational-runbooks.md` (M24,
  "signing-key rotation" / "suspected key compromise").

## Storage target

Per `production-cost-model.md`, an S3-compatible bucket (provider TBD,
selected by the owner — ADR-7 requires only the S3 API, not a specific
vendor), physically and administratively separate from the production
server: different account/credential than the app's own object-storage
credential where the provider supports it, so a compromised app-server
credential can't also delete backups.

## Retention

- Daily backups retained 30 days.
- One backup per week retained for 6 months (weekly rollup, not a separate
  backup — just a longer retention flag on the Sunday backup).
- Signing-key recovery material retained indefinitely (superseded, not
  deleted, on rotation — see M3 overlap policy).

Bucket versioning/immutability enabled where the provider supports it, so a
compromised deploy credential can't silently delete backup history.

## Recovery objectives (stated, not aspirational)

- **RPO (Recovery Point Objective): 24 hours.** Worst case, one day of data
  is lost — the gap between the last successful daily backup and an
  incident. Tightenable later (more frequent backups) if the pilot's actual
  transaction volume justifies the storage/bandwidth cost; not assumed
  necessary before real usage data exists.
- **RTO (Recovery Time Objective): 4 hours** for a single-operator team to
  provision a replacement host, restore the latest backup, and bring the
  application back to a verified-healthy state — bounded by manual
  provisioning time, not by the restore procedure itself (M13's isolated
  restore drill measures the actual restore step separately and will
  refine this number with real data).

Both numbers are **stated targets to design and drill against**, not proven
yet — M13's isolated restore drill is where they get tested against
reality, and this document will be updated with the measured number once
that drill runs against real external backups.

## What this milestone delivers now vs. what stays NOT VERIFIED

**Delivered now (repo-controlled):** the encryption boundary design, the
retention policy, the exclusion list for secrets, the manual signing-key
procedure, the S3-compatible interface requirement, and (in M13) restore
tooling that can be exercised against a local/isolated target.

**NOT VERIFIED until real infrastructure exists:** an actual external
backup destination, an actual scheduled upload succeeding, an actual
restore from a genuinely off-host location. Per
`infrastructure-availability-audit.md`, this is dependency #7 — no object
storage or backup destination is provisioned yet.
