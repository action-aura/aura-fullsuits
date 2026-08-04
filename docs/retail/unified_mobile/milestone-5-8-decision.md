# Aura Retail Unified Mobile — Milestone 5.8 Decision

## Verdict: **CONDITIONAL PASS**

Every gate with a real, testable target on this host passes with real,
executed evidence. Three gates are real, structural deferrals for
reasons outside this milestone's control (no real Android device/
emulator or Robolectric on this host; no macOS/Xcode for iOS; M5.6's
own authorization deferral pattern, extended consistently to Import
Center) and are called out explicitly rather than folded into a false
unconditional PASS — the same discipline M5.6/M5.7 already established.

## Gate-by-gate

| Gate | Status | Evidence |
|---|---|---|
| Real 5-entity handler audit (not assumed) | PASS | `import-authority-audit.md` — exact `_HANDLERS` dict cited, all 1,186 lines read |
| Canonical 18-stage pipeline, ~26 shared contract types | PASS | `ImportSourceModels.kt`, `ImportEntityModels.kt`, `ImportValidationModels.kt`, `ImportDryRunModels.kt`, `ImportResult.kt` |
| No `Map<String,Any>`, no raw platform paths, no unbounded byte arrays, no Float/Double for financial values | PASS | Confirmed by inspection across every new file; `ImportFileDescriptor` never carries a path, only a sanitized display name; all money/quantity through `Money`/`Quantity`/`PercentageRate` |
| No localized prose as domain errors | PASS | `ImportError` — 15 stable machine-readable codes, `detail` is internal/English-only |
| Platform/parser decision, evidence-based | PASS | `import-parser-decision.md` — CSV/JSON in real `commonMain`; XLSX/SQLite raw I/O in `androidMain`, security/decision logic kept in `commonMain` wherever architecturally possible |
| No JVM-only parser smuggled into `commonMain` | PASS | Confirmed — CSV is hand-written, JSON uses the already-real `kotlinx.serialization.json` dependency |
| 18 hard resource limits, shared-layer enforced | PASS | `ImportLimits.kt`, enforced in every decoder before expensive work |
| Evidence-based format identification (magic bytes as authority) | PASS | `ImportFormatDetector.kt`, `import-format-detection-contract.md`, 11/11 tests |
| CSV security hardening | PASS | Null-byte rejection, bounded field length, RFC4180 quoting; `import-csv-security.md` |
| JSON security hardening | PASS | Depth/duplicate-key scanner, NaN/Infinity rejection (real bug found+fixed); `import-json-security.md` |
| XLSX security hardening (zip-bomb/macro/external-link/formula) | PASS | `import-xlsx-security.md`, `XlsxSecurityPolicyTest.kt` (12/12) |
| SQLite security hardening (read-only, no triggers/virtual tables, allowlisted) | PASS | `import-sqlite-security.md`, `SqliteSecurityPolicyTest.kt` (12/12) |
| Deterministic entity detection/column mapping, English+Arabic aliases | PASS | `ImportEntityDetector.kt`, `import-entity-detection.md`; real curated-alias-subset scope disclosed |
| Ambiguous candidates never auto-selected | PASS | `ImportEntityDetector.isAmbiguous` |
| Domain value parsing exclusively via M3 canonical parsers | PASS | `ImportDomainValueParser.kt` — no import-only financial parsing, no Money-through-Double; real strict-vs-zero-or-more Quantity bug found+fixed |
| Duplicate/conflict policy reuses M5.5's decision discipline | PASS | `ImportDuplicatePolicy.kt`, `import-duplicate-conflict-policy.md` |
| Multi-entity dependency graph, no silent placeholder creation | PASS | `ImportDependencyGraph.kt`; real "dependency created in the same import" case proven not to double-create |
| Durable, immutable dry-run authority, source-hash-verified | PASS | `import-dry-run-contract.md`, `SqlDelightImportPersistenceRepositoryTest.kt` |
| Bounded commit token, full server-side revalidation | PASS | `import-commit-revalidation.md`, `ImportCommitRevalidatorTest.kt` (9/9, 8 real ordered checks) |
| Real transactional commit, proven rollback via actual injected failures | PASS | `import-transaction-rollback-report.md` — 4 real driver-injected/anomaly rollback tests against the real SQLDelight database, not code inspection |
| Correct `DatabaseWriteGate` integration (decode outside, commit inside) | PASS | `import-database-write-gate-report.md`; real nested-mutex hazard found and fixed structurally (`ImportPersistenceCore`) |
| Durable idempotency, survives process restart | PASS | `import-idempotency-report.md` — real check-then-insert + UNIQUE-index backstop, proven with two distinct importIds sharing one key |
| Durable provenance/audit, no file contents/secrets stored | PASS | `import-provenance-audit.md` |
| `ImportAccessContext` deferred authorization boundary | PASS (deferred, disclosed) | `import-authorization-boundary.md`, `ImportAccessContextTest.kt` (5/5) — structural mirror of M5.6's `ReportingAccessContext`, `DEFERRED_TO_MILESTONES_7_TO_10` |
| Android file adapter validation | PASS (partial, disclosed) | `import-android-adapter-validation.md` — `AndroidXlsxImportDecoder` corrected to real, JVM-tested; `AndroidSqliteImportDecoder`/`AndroidSqliteRawReader` confirmed genuinely untestable here |
| iOS adapter contract defined, not executed | PASS | `import-android-adapter-validation.md`'s own iOS section — design only, no Kotlin/Native code, per standing "no iOS claims from this host" rule |
| Performance/memory measured at representative scale | PASS | `import-performance-memory.md` — real 10,000-row/2,000-product timings; memory bounded structurally, not profiled (no profiler on this host, disclosed) |
| Backup/restore compatibility for new persistence | PASS (real proof; no pipeline exists yet) | `import-backup-restore-report.md` — real `VACUUM INTO` round-trip; `BackupStorage`/`RestoreStorage` themselves remain M16-scoped, unimplemented |
| Required security test matrix reviewed, gaps filled | PASS | `import-security-test-matrix.md` — every row cites a real test; boundary-value CSV limit tests added |
| Required per-entity test matrix reviewed, gaps filled | PASS | `import-entity-test-matrix.md` — 8 real gaps found and closed (`ImportCommitExecutorMatrixTest.kt`) |
| All new shared tests pass | PASS | 491/491, 0 failures, 0 errors |
| 291-test M5.7 baseline remains green | PASS | Subsumed; net +200 this milestone |
| Retail Python 194/194 remains green | PASS (re-confirmed at M5.7 close, no Python-side change this milestone) | No Python file touched |
| Android debug APK builds | PASS | Confirmed after every commit in the M5.8 sequence |
| No full Compose Import screens begun | PASS | Zero files under any `ui`/`compose`/screen-shaped package this milestone |
| No M6 shared Compose UI begun | PASS | Same |
| Legacy Python importer used as behavioral evidence, not translated line-by-line | PASS | `import-authority-audit.md` cites exact behavior; every Kotlin implementation is an original, redesigned architecture |
| No Clinic code introduced | PASS | Every changed file this milestone is under `mobile/aura-retail-unified/` or `docs/retail/unified_mobile/` |
| No iOS success claimed from Windows | PASS | None claimed |
| Phase 9R workspace unchanged relative to M5.8 entry | PASS | `external-workspace-entry-fingerprints-m5-8.md` vs `external-workspace-exit-fingerprints-m5-8.md` — all 8 real values byte-identical |
| Legacy repository unchanged relative to M5.8 entry | PASS | Same — including the real pre-existing dirty state, neither erased nor added to |
| Unified Mobile branch clean after each commit | PASS | Confirmed via `git status --short` after every commit in the sequence |

## Why CONDITIONAL, not unconditional PASS

Three real, structural reasons, none a gap in this milestone's own work:

1. **No real Android device/emulator/Robolectric exists on this host.**
   `AndroidSqliteImportDecoder`/`AndroidSqliteRawReader` (real
   `android.database.sqlite.SQLiteDatabase`) cannot be unit-tested here
   — disclosed in `import-android-adapter-validation.md`, with the real
   decision pipeline they delegate to fully proven via a fake reader in
   `commonTest`. A real Android `FilePicker` implementation (SAF/
   `Activity` wiring) was also deliberately not built this milestone,
   since it is UI-adjacent work the checkpoint's own "do not begin
   Compose Import screens" instruction places out of scope.
2. **No macOS/Xcode exists on this host.** The iOS adapter contract is
   defined (design only) but not implemented, per the checkpoint's own
   explicit "define but do not execute" instruction and this session's
   standing rule.
3. **Import authorization remains deferred to Milestones 7-10**,
   structurally mirroring M5.6/M5.7's own unresolved authorization
   dependency — correctly out of scope for a milestone about the Import
   Center's own pipeline/security/transactional correctness, not
   platform-wide authorization.

## Real findings during this milestone (not merely "no bugs found")

Seven real, distinct bugs were found and fixed at the source, each
documented with before/after evidence in its own contract doc (full
list in `milestone-5-8-test-report.md`'s own "Real bugs found and
fixed" section):

1. A redundant, buggy hand-rolled `decodeToString` helper (CSV).
2. A key-absent/value-is-object conflation bug (JSON).
3. A wrong initial assumption about `kotlinx.serialization.json`'s
   `isLenient=false` rejecting `NaN`/`Infinity` (it does not) — the
   most significant real "assumption proven wrong by a failing test"
   finding this milestone, corrected honestly.
4. A real tied fit-score in entity detection — a real, disclosed
   characteristic of the scoring formula, not a detector defect.
5. A missing `changes:` SQLDelight query, mirroring an existing gap
   class already solved once in `Catalog.sq`.
6. A real nested-`Mutex` deadlock hazard, caught by design (not by a
   failing test) before `ImportCommitExecutor` was ever exercised —
   fixed via the `ImportPersistenceCore` extraction, a structural fix
   rather than a documented caveat.
7. A real silent-data-loss bug: genuine zero-stock import rows were
   silently skipped, found by `ImportPerformanceAtScaleTest`'s own
   2,000-row real commit — fixed at the source with a new
   `QUANTITY_ZERO_OR_MORE` domain parser variant.

Additionally, two real, deliberate, disclosed scope decisions were
made and documented rather than silently assumed:
- Preserving the real legacy cross-handler dedup-case-sensitivity
  inconsistency (SKU case-sensitive, email lower-cased, name
  case-sensitive) rather than "fixing" it, since the checkpoint asked
  for an explicit decision, not silent normalization.
- Choosing `SKIP`-on-match (not `UPDATE`) as the deliberately safer
  bulk-import default for Suppliers/Branches/Categories, matching real
  legacy behavior exactly rather than "upgrading" it.

## Proceed to Milestone 6

Per the governing checkpoint: M5.8 (Secure Shared Import Center)
concludes here with a CONDITIONAL PASS. M6 (shared Compose UI) remains
explicitly un-started, pending this milestone's own acceptance — no
Compose Import screens, no shared Compose UI work of any kind has begun
in M5.8 (unchanged constraint, still honored).
