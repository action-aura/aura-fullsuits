# Import Provenance & Audit (M5.8.18)

Real, durable record of every commit's origin and outcome, and a
real, ordered, per-import event log — proven by
`SqlDelightImportPersistenceRepositoryTest.kt` (`realProvenanceRoundTripsExactly`,
`auditEntriesAreRealAndOrderedByTimestamp`).

## Provenance: what is stored

`ImportProvenance` — `importId, dryRunId, sourceHash, safeFileName,
format, companyId, branchId, entityTypes, mappingVersion,
schemaVersion, actorId, startedAtEpochMillis, completedAtEpochMillis,
outcome, insertedCounts, updatedCounts, skippedCount, warningCount,
idempotencyKey`.

Real, deliberate exclusions (never stored, matching the audit gap
identified in `import-authority-audit.md` — the legacy Python importer
had no provenance at all, so this is a new, from-scratch design, not a
port):
- **No raw file bytes/content.** `safeFileName` is the display name
  only (already passed through `ImportFormatDetector`'s safe-text
  check); the source bytes themselves are never persisted anywhere.
- **No secrets** — the importer pipeline never handles credentials, so
  none exist to accidentally capture.
- **`sourceHash`, not the source file** — proves which exact file
  produced this outcome without retaining it.

## `insertedCounts`/`updatedCounts` real serialization

Stored as a `TEXT` column via `joinCounts`/`splitCounts`
(`SqlDelightImportPersistenceRepository`) — a simple
`ENTITY_TYPE:count,ENTITY_TYPE:count` encoding, consistent with
`entity_types`' own comma-joined-enum-name choice in
`import-dry-run-contract.md` (same real, disclosed reasoning: short,
bounded, ≤5-entity map, no JSON dependency needed for it).

## Audit log: append-only, ordered by real timestamp

`import_audit_log` — `id (autoincrement), import_id, timestamp,
event_code, detail`. `appendAuditEntry` only ever inserts, never
updates or deletes. `getAuditEntries` returns `ORDER BY timestamp`
(SQL-level ordering, not application-level sort) —
`auditEntriesAreRealAndOrderedByTimestamp` proves entries inserted
out of chronological order (`timestamp=2000` inserted before
`timestamp=1000`) are still returned in real timestamp order
(`DRY_RUN_ISSUED` before `COMMIT_STARTED`).

## Real, disclosed scope: event codes not yet emitted by a pipeline

This milestone delivers the real, tested STORAGE primitive
(`appendAuditEntry`/`getAuditEntries`) and its schema. The actual
commit pipeline that will call `appendAuditEntry` at each real
lifecycle point (`DRY_RUN_ISSUED`, `COMMIT_STARTED`,
`COMMIT_ROW_INSERTED`, `COMMIT_COMPLETED`, `COMMIT_ROLLED_BACK`, etc.)
is M5.8.15/M5.8.16 (transactional commit, not yet built) — this doc
covers the persistence contract those event codes will be written
through, not the full set of codes a completed commit will emit.
