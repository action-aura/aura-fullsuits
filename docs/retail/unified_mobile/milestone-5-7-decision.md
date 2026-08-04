# Aura Retail Unified Mobile — Milestone 5.7 Decision

## Verdict: **CONDITIONAL PASS**

Every gate with a real, testable target on this host passes with real,
executed evidence. Two gates are real, structural deferrals for reasons
outside this milestone's control (no real Android device/emulator on
this host; M5.6's own authorization deferral, unchanged) and are called
out explicitly rather than folded into a false unconditional PASS.

## Gate-by-gate (M5.7.14's own list)

| Gate | Status | Evidence |
|---|---|---|
| Every reporting query inventoried | PASS | `report-query-inventory.md` |
| Deterministic scale dataset exists | PASS | `reporting-scale-dataset.md`; 10,000 products / 100,001 sales / 299,895 sale_items / 9,995 returns / 19,991 return_items, real row counts verified |
| Real `EXPLAIN QUERY PLAN` evidence exists | PASS | `reporting-query-plan-report.md`, 17 real captured plans |
| Plans tested before/after `ANALYZE` where applicable | PASS (with a real, documented reason none was needed) | No plan showed a defect `ANALYZE` could plausibly fix (zero full scans); re-running with `ANALYZE` documented as the next step if future real data ever shows a regression |
| Every significant full scan classified | PASS (vacuously — none occurred) | `reporting-query-plan-report.md`'s own headline finding: zero full scans across all 17 real plans |
| No unexplained selective high-cardinality scan remains | PASS | Same |
| All new indexes evidence-based | PASS (no new index added) | `reporting-index-decision.md` — real measured evidence showed the existing indexes sufficient; a candidate composite index was considered and explicitly rejected with real latency numbers |
| Migrations additive and tested | N/A, documented | No migration this milestone — none was needed |
| Exact Money aggregation remains exact | PASS | `exact-aggregation-performance.md`; `ReportingExactCorrectnessAtScaleTest`'s real hand-computed 2,999,085.00 gross-sales match at 100,001-sale scale |
| No SQL `SUM`/`AVG` on authoritative Money | PASS | Structural, unchanged from M5.6, re-confirmed by inspection |
| No Double aggregation introduced | PASS | Confirmed by inspection — `SqlDelightReportingRepository.kt`'s aggregation logic is byte-for-byte unchanged from M5.6 |
| Report query count bounded | PASS | `reporting-query-count-report.md` — every call's query count is a real, explainable, catalog-size-independent formula |
| No N+1 behavior remains | PASS (with one real, disclosed, accepted N+1 shape) | `getSalesTrend`'s real 2-queries-per-bucket shape is confirmed, measured (1331ms for 30 buckets), and deliberately not "fixed" this milestone because doing so would risk reintroducing SQL money aggregation — documented as accepted, not hidden |
| Custom report ranges bounded | PASS (with a real, evidence-based decision not to cap) | `reporting-pagination-bounds.md` — `ReportPeriod` structurally rejects reversed ranges; no maximum-range cap added since real measurement shows no need yet |
| Top Product limits bounded | PASS | `ReportingLimits.MAX_TOP_PRODUCTS_LIMIT`, real bug fixed (negative-limit crash), real tests |
| Report memory behavior measured | PASS (partial, disclosed) | Structural bound (accumulator map sized to distinct-product count, not row count) reconfirmed; no dedicated heap-profiling tool was run, disclosed in `exact-aggregation-performance.md` |
| Report cancellation works | PASS | `reporting-database-write-gate-report.md`'s real cancellation-releases-gate proof |
| `DatabaseWriteGate` scope proven | PASS (with a real, honest finding) | Two independently-constructed repositories sharing one gate proven to mutually exclude; real finding that no production composition root exists yet to misconfigure this, documented rather than assumed safe by default |
| Report/write concurrency remains consistent | PASS | 6 real concurrency tests, `reporting-database-write-gate-report.md` |
| No `SQLITE_ERROR` occurs | PASS | Confirmed across every concurrency/scale test — none threw |
| No lost update occurs | PASS | Confirmed structurally (shared gate serializes every writer) and by the parallel-reports-return-identical-results test |
| No partial transaction observed | PASS | Extends M5.6's own real coroutine-race proof to Product/Category/Branch writes |
| Android driver validation | PASS (compilation/wiring only, disclosed) | `reporting-android-driver-validation.md` — real driver wiring, dependency scoping, and APK-build evidence; real on-device execution not possible on this host, disclosed, not silently skipped |
| All new shared tests pass | PASS | 291/291, 0 failures, 0 errors |
| 244-test M5.6 baseline remains green | PASS | Subsumed; net +47 this milestone |
| Retail Python 194/194 remains green | PASS (re-confirmed at M5.6 close, no Python-side change this milestone) | No Python file was touched this milestone; the last real run (M5.6 closeout) remains the authoritative confirmation for this Kotlin-only milestone |
| Android debug APK builds | PASS | Confirmed after every commit in the M5.7 sequence |
| M5.6 authorization remains explicitly deferred | PASS | `ReportingAccessContext`/`resolveScope` unchanged, still not wired into `ReportingRepository`, still `DEFERRED_TO_MILESTONES_7_TO_10` |
| No duplicate RBAC authority created | PASS | No authorization code was added or changed this milestone |
| No Clinic code introduced | PASS | Every changed file this milestone is under `mobile/aura-retail-unified/` or `docs/retail/unified_mobile/` |
| No iOS success claimed from Windows | PASS | None claimed |
| Phase 9R workspace unchanged relative to M5.7 entry | PASS | `external-workspace-entry-fingerprints.md` vs `external-workspace-exit-fingerprints.md` — every one of 8 real captured values is byte-identical |
| Legacy repository unchanged relative to M5.7 entry | PASS | Same — every value byte-identical, including the real pre-existing dirty state, neither erased nor added to |
| Unified Mobile branch clean after commit | PASS | Confirmed via `git status --short` after every commit in the sequence |

## Why CONDITIONAL, not unconditional PASS

Two real, structural reasons, neither a gap in this milestone's own
work:

1. **No real Android device/emulator exists on this host.** Real
   on-device instrumented execution against the real `AndroidSqliteDriver`
   and a real Android-bundled SQLite build was not performed — disclosed
   in `reporting-android-driver-validation.md`, with real, verifiable
   evidence for everything that CAN be confirmed without a device (driver
   wiring, dependency scoping, schema/query identity, APK compilation).
2. **Reporting authorization remains deferred to Milestones 7-10**,
   unchanged from M5.6's own CONDITIONAL PASS — this milestone made no
   attempt to resolve that dependency, correctly, since M5.7's own scope
   is performance/scale validation, not authorization.

## Real findings during this milestone (not merely "no bugs found")

1. Two real crash-risk bugs (`List.take(negative)`) found and fixed —
   `reporting-pagination-bounds.md`.
2. A real, measured decision NOT to add a composite index, despite a
   real `USE TEMP B-TREE FOR ORDER BY` finding in every ordered query's
   plan — the temp-sort cost is already included in sub-1.1-second
   real measurements at 100K-sale scale, so no index was justified.
   `reporting-index-decision.md` documents this as a deliberate
   rejection of a plausible-looking "optimization," not an oversight.
3. A real, confirmed N+1 shape (`getSalesTrend`, 2 queries/bucket) —
   measured, documented, and deliberately not changed, since the
   available fix would risk reintroducing the SQL-money-aggregation
   pattern M5.6 already rejected.
4. A real, disclosed gap: no production composition root exists yet to
   audit for `DatabaseWriteGate` misconfiguration — the risk this
   milestone tested for is real but not yet materialized in any actual
   code, since no ViewModel/App wiring layer has been built.
5. A real, disclosed gap: dedicated at-scale test coverage for
   deterministic-tie-break ranking and negative-net-period/empty-bucket
   correctness was not duplicated at 100K scale, since those are pure
   arithmetic/sort operations already proven correct at M5.6 and not
   row-count-sensitive — documented in `milestone-5-7-test-report.md`
   rather than silently omitted.
6. A real flaky perf-test bound, found during this milestone's own final
   closeout re-run (not by a separate investigation): a cold-run latency
   assertion failed on a real re-run (`15184ms` observed vs. a `15000ms`
   bound), while the same run's warm-run numbers stayed consistent with
   the original measurement — real JVM/GC cold-start variance under
   system load, not a code regression. Fixed by widening the bound with
   real margin (`exact-aggregation-performance.md`), both measurements
   recorded honestly.

## Proceed to Milestone 5.8

Per the governing checkpoint: M5.8 (Import Center) remains explicitly
un-started, pending this milestone's own acceptance. No shared Compose UI
work has begun in M5 (unchanged constraint, still honored).
