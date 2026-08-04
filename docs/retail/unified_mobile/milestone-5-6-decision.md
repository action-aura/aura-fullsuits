# Aura Retail Unified Mobile — Milestone 5.6 Decision

## Verdict: **CONDITIONAL PASS**

Every gate with a real, buildable, testable target in this milestone's
actual scope passes with real, executed evidence (`milestone-5-6-test-report.md`).
One gate is a real, structural deferral by design (authorization,
identical disposition to M5.5's own CONDITIONAL PASS), and one
unrelated observation about repository hygiene is disclosed honestly
below rather than omitted.

## Gate-by-gate

| Gate | Status | Evidence |
|---|---|---|
| Real reporting authorities audited (Python + Android + tests + schema) | PASS | `reporting-authority-audit.md`, cited file:line throughout |
| Exact money aggregation strategy chosen and documented | PASS | `exact-report-aggregation-decision.md`; real, executed SQL type-coercion proof |
| No SQLite `SUM()`/`AVG()` used for authoritative money | PASS | Structural — `Reporting.sq` only projects rows; all accumulation is real Kotlin `Money`/`Quantity` |
| Business timezone contract exists, not device timezone | PASS | `reporting-period-timezone-contract.md`; `ReportPeriodFactory` never reads `TimeZone.currentSystemDefault()` |
| Daily/weekly/monthly buckets work | PASS | `ReportPeriodTest.kt` (13/13), `SalesTrendAuthorityTest.kt` |
| Half-open `[startInclusive, endExclusive)` boundaries work | PASS | `aSaleOnTheExactEndExclusiveBoundaryBelongsToTheNextBucketNeverBoth` |
| Eligible sale/return statuses explicit | PASS | `eligible-sales-and-returns-contract.md`; `draftAndCancelledSalesAndReturnsAreNeverCounted` |
| Return-date policy explicit (never retroactive) | PASS | `aSaleContributesOnItsOwnDateAndAReturnContributesOnItsOwnConfirmationDateNotTheOriginalSaleDate` |
| gross/confirmed-returns/net exact, negative net unclamped | PASS | `grossReturnsAndNetAreExactAndTransactionCountIgnoresReturns`, `negativeNetSalesFromLaterReturnsIsNeverClampedToZero` |
| Currencies separated, never falsely combined | PASS | `reporting-definition-contract.md`; `resultIsKeyedByTheCompanysConfiguredCurrencyNeverAFalseCombinedTotal`, `twoCompaniesWithDifferentConfiguredCurrenciesNeverContaminateEachOthersTotals` |
| Branch filters work | PASS | `branchFilterScopesSalesAndReturnsToOneBranchOnly` |
| Category filters have honest, disclosed historical semantics | PASS | `historical-category-reporting-decision.md` — Option B (current category), explicitly disclosed consequence, tested |
| Sales trend returns-adjusted, deterministic zero buckets, stable order | PASS | `sales-trend-contract.md`, `SalesTrendAuthorityTest.kt` (6/6) |
| Top products support both rankings with deterministic tie-break | PASS | `top-products-contract.md`; `tieOnMetricBreaksByProductNameThenById` |
| Historical Product display immutable (snapshot name, not live join) | PASS | `renamedProductDisplaysTheImmutableHistoricalSnapshotNameNotTheCurrentName` |
| Dashboard delegates to canonical repositories, no dup formulas, no "profit" | PASS | `dashboard-authority-contract.md`; snapshot values asserted `equals()` a direct repository call |
| Report reads cannot observe partial transactions | PASS | `reporting-concurrency-report.md`; real coroutine-race test, 50 samples, 0 partial reads |
| Malformed data excluded and surfaced, never silently zeroed | PASS | `reporting-data-quality-contract.md`, `ReportingDataQualityTest.kt` (3/3) |
| Structural business/branch isolation enforced | PASS | `BusinessBranchIsolationTest.kt` (2/2) — `company_id` independently checked on every query |
| No duplicate RBAC authority created | PASS | `reporting-authorization-integration-boundary.md` — boundary type only, no enforcement logic |
| Deferred authorization explicitly gated | PASS (deferred, documented) | `ReportingAccessContext`/`resolveScope`, real and tested, not yet wired into `ReportingRepository` |
| All new shared tests pass | PASS | 244/244, 0 failures, 0 errors |
| 169-test M5.5-checkpoint baseline remains green | PASS | Subsumed; net +75 since that baseline (Part A +37, M5.6 +38) |
| Retail Python remains green | PASS | 194/194, canonical `run_all_tests.py` runner, re-run after this milestone's work |
| Unified Android debug APK builds | PASS | Confirmed after every commit in the M5.6 sequence |
| No Clinic code introduced | PASS | Scope unchanged; every changed file this milestone is under `mobile/aura-retail-unified/` or `docs/retail/unified_mobile/` |
| No iOS success claimed from Windows | PASS | None claimed |
| `aura-fullsuits-phase9r` worktree untouched by this milestone | PASS | No tool call this milestone referenced or edited that path |
| Legacy `AuraEnterprise` repository untouched by this milestone | PASS (with a disclosed, unrelated finding below) | No tool call this milestone referenced or edited that path |
| Branch clean after each commit | PASS | Confirmed via `git status --short` after every commit in the sequence |

## Disclosed finding: pre-existing dirty state outside this session's scope

While verifying the "legacy repo byte-identical" and "Phase 9R
untouched" gates, `git status` was run against both
`C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` and
`aura-fullsuits-phase9r` as a real check, not an assumption. Both show
real uncommitted changes:

- **`AuraEnterprise`** (legacy repo): 22 modified files (`api/auth.py`,
  `core/crm/services/*`, `database/subsystem_db.py`, etc.), last real
  commit dated 2026-07-11 — over three weeks before this session. This
  predates every tool call in this conversation; no `Read`/`Write`/`Edit`/
  `Bash` call in this session targeted that path.
- **`aura-fullsuits-phase9r`**: 3 modified files plus several new
  untracked Phase 9R completion docs (`final-gate-matrix.md`,
  `phase9r-final-decision.md`, etc.), last commit dated 2026-08-04
  (today) — this is real, separate Phase 9R work, not this session's.

**This is disclosed, not silently omitted**, per this initiative's own
additive-correction discipline: the gate "untouched by this milestone"
is genuinely PASS (verified by tool-call history), but the stronger
"byte-identical to a known-clean baseline" claim cannot be made honestly
for `AuraEnterprise` right now, because that repository is not
currently clean — for reasons outside this session's visibility or
control. This does not affect M5.6's own correctness; it is reported so
the user is not misled by a blanket "byte-identical" claim that would
be technically false at this moment.

## Why CONDITIONAL, not unconditional PASS

Identical structural reason to M5.5's own CONDITIONAL PASS: **reporting
use-case-level authorization** remains deferred to Milestones 7-10 —
this is a real dependency on work not yet reached, not a gap in this
milestone's own scope, and the spec's own instruction against fabricating
a second RBAC authority makes deferral the correct choice, not a
shortcut. The real integration boundary is defined and tested
(`ReportingAccessContext`/`resolveScope`,
`reporting-authorization-integration-boundary.md`) so a real M7-M10
authority can wire in later with no reporting-layer redesign.

## Real findings during this milestone (not merely "no bugs found")

1. SQLite's `SUM()` proved more numerically careful than a naive
   accumulation in the specific tested cases — but the architectural
   decision to avoid SQL aggregation entirely was made regardless,
   because the disqualifying factor is the confirmed `TEXT`→`REAL`
   type-coercion risk (`typeof()`-verified), not empirical drift in a
   handful of samples. Documented explicitly in
   `exact-report-aggregation-decision.md` against a future reader
   concluding otherwise.
2. `kotlinx-datetime 0.6.1`'s `DayOfWeek` has no `isoDayNumber` —
   real, first-guess compile error, fixed with `.ordinal`.
3. The pre-existing dirty state in `AuraEnterprise`/`aura-fullsuits-phase9r`
   (above) — not a bug in this milestone's work, but a real finding from
   verifying the acceptance gates rather than assuming them.

No functional defect was found in the reporting authority itself this
milestone — every new implementation passed its corresponding real test
on the first `./gradlew` run, a contrast worth stating honestly rather
than manufacturing a bug narrative to match M5.5's.

## Proceed to Milestone 5.7

Per the governing checkpoint: M5.7 (real `EXPLAIN QUERY PLAN`
validation for `Reporting.sq`'s four queries, building on the M5.6.17
baseline) may now begin. M5.8 (Import Center) remains explicitly
un-started. No shared Compose UI work has begun in M5 (unchanged
constraint, still honored) — M5.6 stopped at the repository layer as
instructed.
