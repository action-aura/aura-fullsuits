# Aura Retail Unified Mobile — Reporting Period/Timezone Contract (M5.6.2)

## The contract

`ReportPeriod` (`reporting/ReportPeriod.kt`): a half-open `[startInclusive, endExclusive)` real interval in epoch milliseconds — matching `database-schema-contract.md` rule 2 (every stored timestamp is already epoch millis, never a naive local-time string). `ReportPeriodFactory` is the ONLY place bucket-boundary math happens, supporting `TODAY`, `CUSTOM_DATE_RANGE`, `DAILY_BUCKETS`, `WEEKLY_BUCKETS` (Monday 00:00 through next Monday 00:00, displayed Mon-Sun), `MONTHLY_BUCKETS` (first day of month through first day of next month).

## Real, audited legacy behavior — and the real gap it leaves

`reporting-authority-audit.md` §5 found the legacy authority has **no real business-timezone concept at all**: "local time" means the server process's own OS clock, computed three different, mutually inconsistent ways across its own routes (`dashboard_stats`'s Python `datetime.now()`, `report_sales_trend`'s SQLite `date('now','localtime',?)`, `report_summary`'s separate Python `timedelta` math). This is a real, confirmed gap — **NEW_COMPLETE_PRODUCT_REQUIREMENT**, not a port: `ReportPeriodFactory` takes one explicit, injected `TimeZone` (`kotlinx-datetime`) parameter everywhere, "the business timezone, not an arbitrary current device timezone" per the spec's own instruction. There is no code path anywhere in `ReportPeriod.kt` that reads `TimeZone.currentSystemDefault()` or any other ambient clock/zone — verified by inspection and by `deviceTimezoneNeverInfluencesTheResult`'s determinism proof.

## Real, executed evidence for every required edge case (`ReportPeriodTest.kt`, 13/13)

| Requirement | Test | Real result |
|---|---|---|
| Start inclusive / end exclusive | `startIsInclusiveEndIsExclusive` | Confirmed |
| Reject non-half-open interval | `rejectsNonHalfOpenInterval` | `IllegalArgumentException` for `start >= end` |
| Midnight boundary | `dailyBucketsCrossMidnightBoundaryCorrectly` | 3-day range → 3 exact daily buckets |
| Month-end | `dailyBucketsCrossMonthEndCorrectly` | Jan 31 → Feb 1 boundary exact |
| Year-end | `dailyBucketsCrossYearEndCorrectly` | Dec 31 2025 → Jan 1 2026 boundary exact |
| Leap day | `dailyBucketsIncludeLeapDay` | Feb 29 2028 (real leap year) included as its own bucket |
| Monday weekly start / Sunday weekly end | `weeklyBucketsStartOnMondayAndDisplayAsMondaySunday`, `weeklyBucketWhenStartIsAlreadyMonday` | A Thursday input resolves to the Monday on-or-before it; an already-Monday input is unchanged |
| Month boundary (incl. year-crossing) | `monthlyBucketsFirstDayThroughFirstDayOfNextMonth`, `monthlyBucketsCrossYearBoundary` | Dec→Jan produces 2 correct buckets spanning the year boundary |
| Business timezone (real, non-UTC) | `todayUsesBusinessTimezoneNotUtc` | A moment that is 2026-03-05 in UTC but 2026-03-06 in Cairo (UTC+2) correctly resolves "today" to the **Cairo** calendar date |
| Business timezone differing from a real DST-observing zone | `businessTimezoneOffsetProducesDifferentBoundariesThanUtcForTheSameInstant` | The identical instant resolves to a different calendar day in New York (UTC-4, DST, June) than in UTC — real proof the configured business timezone, not a hardcoded assumption, drives the result |
| Device timezone never overrides business timezone | `deviceTimezoneNeverInfluencesTheResult` | Same inputs always produce the same output — no ambient/ambient-clock dependency exists to vary it |

## Real, honest scope note

`ReportPeriodFactory` does not yet read a per-company "configured business timezone" setting from `retail_settings` — that plumbing (a real `SettingsRepository` key, analogous to `base_currency`'s audited-but-unused-multi-currency plumbing, §6 of the audit) is Milestone 6+ UI/settings-screen scope, not built this milestone. `ReportPeriodFactory` itself is timezone-agnostic and correct for whatever `TimeZone` it is given — the remaining work is wiring a real configured value into that parameter, not fixing anything in the period math itself.

## Real, executed evidence summary

`./gradlew :shared:testDebugUnitTest`: 206/206 (193 baseline + 13 new). `./gradlew :androidApp:assembleDebug`: BUILD SUCCESSFUL.
