package com.actionaura.retail.reporting

import kotlinx.datetime.DateTimeUnit
import kotlinx.datetime.Instant
import kotlinx.datetime.LocalDate
import kotlinx.datetime.TimeZone
import kotlinx.datetime.atStartOfDayIn
import kotlinx.datetime.plus
import kotlinx.datetime.toLocalDateTime

/**
 * M5.6.2 -- half-open `[startInclusive, endExclusive)` real interval,
 * epoch milliseconds (UTC-unambiguous, matches `database-schema-
 * contract.md` rule 2 -- every stored timestamp is already epoch millis,
 * never a naive local-time string like the legacy authority's
 * `created_at`, `reporting-authority-audit.md` §5).
 */
data class ReportPeriod(val startInclusiveEpochMillis: Long, val endExclusiveEpochMillis: Long) {
    init {
        require(startInclusiveEpochMillis < endExclusiveEpochMillis) {
            "ReportPeriod requires startInclusive < endExclusive, got $startInclusiveEpochMillis..$endExclusiveEpochMillis"
        }
    }

    fun contains(epochMillis: Long): Boolean = epochMillis >= startInclusiveEpochMillis && epochMillis < endExclusiveEpochMillis
}

enum class ReportPeriodType { TODAY, CUSTOM_DATE_RANGE, DAILY_BUCKETS, WEEKLY_BUCKETS, MONTHLY_BUCKETS }

/**
 * NEW_COMPLETE_PRODUCT_REQUIREMENT (`reporting-authority-audit.md` §5):
 * the legacy authority has no real business-timezone concept at all --
 * "local time" there means the server process's own OS clock, computed
 * three different, mutually inconsistent ways across its own routes
 * (`dashboard_stats`'s Python `datetime.now()`, `report_sales_trend`'s
 * SQLite `date('now','localtime',?)`, `report_summary`'s separate Python
 * `timedelta` math). The unified authority makes this one explicit,
 * injected `TimeZone` (`kotlinx-datetime`) -- "the business timezone, not
 * an arbitrary current device timezone" -- never implicitly read from the
 * running device.
 *
 * `ReportPeriodFactory` is the ONLY place bucket-boundary math happens --
 * every real date-boundary edge case (midnight, month-end, year-end, leap
 * day, arbitrary timezone offset) is centralized here, proven by
 * `reporting-period-timezone-contract.md`'s real test evidence, not
 * scattered across each reporting query.
 */
object ReportPeriodFactory {

    fun today(businessTimeZone: TimeZone, nowEpochMillis: Long): ReportPeriod {
        val nowDate = Instant.fromEpochMilliseconds(nowEpochMillis).toLocalDateTime(businessTimeZone).date
        return dayPeriod(nowDate, businessTimeZone)
    }

    fun customRange(startInclusiveEpochMillis: Long, endExclusiveEpochMillis: Long): ReportPeriod =
        ReportPeriod(startInclusiveEpochMillis, endExclusiveEpochMillis)

    /** One bucket per calendar day (business timezone) overlapping the given overall range. */
    fun dailyBuckets(businessTimeZone: TimeZone, overallStartInclusiveEpochMillis: Long, overallEndExclusiveEpochMillis: Long): List<ReportPeriod> {
        val startDate = Instant.fromEpochMilliseconds(overallStartInclusiveEpochMillis).toLocalDateTime(businessTimeZone).date
        val endDate = Instant.fromEpochMilliseconds(overallEndExclusiveEpochMillis - 1).toLocalDateTime(businessTimeZone).date
        val buckets = mutableListOf<ReportPeriod>()
        var cursor = startDate
        while (cursor <= endDate) {
            buckets += dayPeriod(cursor, businessTimeZone)
            cursor = cursor.plus(1, DateTimeUnit.DAY)
        }
        return buckets
    }

    /** Monday 00:00 (business timezone) through the next Monday 00:00 -- "display as Monday-Sunday." */
    fun weeklyBuckets(businessTimeZone: TimeZone, overallStartInclusiveEpochMillis: Long, overallEndExclusiveEpochMillis: Long): List<ReportPeriod> {
        val startDate = Instant.fromEpochMilliseconds(overallStartInclusiveEpochMillis).toLocalDateTime(businessTimeZone).date
        val endDate = Instant.fromEpochMilliseconds(overallEndExclusiveEpochMillis - 1).toLocalDateTime(businessTimeZone).date
        var cursor = mondayOnOrBefore(startDate)
        val buckets = mutableListOf<ReportPeriod>()
        while (cursor <= endDate) {
            val weekEnd = cursor.plus(7, DateTimeUnit.DAY)
            buckets += ReportPeriod(startOfDayEpochMillis(cursor, businessTimeZone), startOfDayEpochMillis(weekEnd, businessTimeZone))
            cursor = weekEnd
        }
        return buckets
    }

    /** First day of calendar month (business timezone) through first day of next month. */
    fun monthlyBuckets(businessTimeZone: TimeZone, overallStartInclusiveEpochMillis: Long, overallEndExclusiveEpochMillis: Long): List<ReportPeriod> {
        val startDate = Instant.fromEpochMilliseconds(overallStartInclusiveEpochMillis).toLocalDateTime(businessTimeZone).date
        val endDate = Instant.fromEpochMilliseconds(overallEndExclusiveEpochMillis - 1).toLocalDateTime(businessTimeZone).date
        var cursor = LocalDate(startDate.year, startDate.month, 1)
        val buckets = mutableListOf<ReportPeriod>()
        while (cursor <= endDate) {
            val nextMonth = cursor.plus(1, DateTimeUnit.MONTH)
            buckets += ReportPeriod(startOfDayEpochMillis(cursor, businessTimeZone), startOfDayEpochMillis(nextMonth, businessTimeZone))
            cursor = nextMonth
        }
        return buckets
    }

    private fun dayPeriod(date: LocalDate, tz: TimeZone): ReportPeriod =
        ReportPeriod(startOfDayEpochMillis(date, tz), startOfDayEpochMillis(date.plus(1, DateTimeUnit.DAY), tz))

    private fun startOfDayEpochMillis(date: LocalDate, tz: TimeZone): Long = date.atStartOfDayIn(tz).toEpochMilliseconds()

    /** kotlinx-datetime's `DayOfWeek` enum: `MONDAY.ordinal == 0` .. `SUNDAY.ordinal == 6`. */
    private fun mondayOnOrBefore(date: LocalDate): LocalDate = date.minus(date.dayOfWeek.ordinal, DateTimeUnit.DAY)
}

private fun LocalDate.minus(days: Int, unit: DateTimeUnit.DateBased): LocalDate = this.plus(-days, unit)
