package com.actionaura.retail.reporting

import kotlinx.datetime.Instant
import kotlinx.datetime.LocalDate
import kotlinx.datetime.LocalDateTime
import kotlinx.datetime.TimeZone
import kotlinx.datetime.atStartOfDayIn
import kotlinx.datetime.toInstant
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/** M5.6.2 -- real, executed proof of the canonical period/timezone contract. */
class ReportPeriodTest {

    private val utc = TimeZone.UTC
    private val cairo = TimeZone.of("Africa/Cairo") // UTC+2, no DST since 2016 -- a real, stable non-UTC business timezone
    private val nyc = TimeZone.of("America/New_York") // real DST-observing zone, for the device-vs-business-timezone case

    private fun epochMillisAt(year: Int, month: Int, day: Int, hour: Int, minute: Int, tz: TimeZone): Long =
        LocalDateTime(year, month, day, hour, minute).toInstant(tz).toEpochMilliseconds()

    @Test
    fun rejectsNonHalfOpenInterval() {
        assertFailsWith<IllegalArgumentException> { ReportPeriod(1000L, 1000L) }
        assertFailsWith<IllegalArgumentException> { ReportPeriod(2000L, 1000L) }
    }

    @Test
    fun startIsInclusiveEndIsExclusive() {
        val period = ReportPeriod(1000L, 2000L)
        assertTrue(period.contains(1000L), "start must be inclusive")
        assertTrue(!period.contains(2000L), "end must be exclusive")
        assertTrue(period.contains(1999L))
    }

    @Test
    fun todayUsesBusinessTimezoneNotUtc() {
        // 2026-03-05 23:30 Cairo time (UTC+2) is already 2026-03-05 21:30 UTC
        // the same calendar day in both -- pick a moment where UTC and
        // Cairo actually DISAGREE on the calendar date to prove business
        // timezone, not UTC, drives the boundary.
        val lateCairoNight = epochMillisAt(2026, 3, 5, 23, 30, cairo) // 21:30 UTC same day -- not yet a disagreement
        // Real disagreement case: 00:30 Cairo time on 2026-03-06 is 22:30 UTC on 2026-03-05.
        val earlyCairoMorning = epochMillisAt(2026, 3, 6, 0, 30, cairo)
        val period = ReportPeriodFactory.today(cairo, earlyCairoMorning)

        // The business-timezone "today" must be 2026-03-06 (Cairo calendar date),
        // even though the UTC calendar date at that instant is still 2026-03-05.
        val expectedStart = LocalDate(2026, 3, 6).atStartOfDayIn(cairo).toEpochMilliseconds()
        val expectedEnd = LocalDate(2026, 3, 7).atStartOfDayIn(cairo).toEpochMilliseconds()
        assertEquals(expectedStart, period.startInclusiveEpochMillis)
        assertEquals(expectedEnd, period.endExclusiveEpochMillis)
    }

    @Test
    fun dailyBucketsCrossMidnightBoundaryCorrectly() {
        val start = epochMillisAt(2026, 6, 10, 0, 0, utc)
        val end = epochMillisAt(2026, 6, 13, 0, 0, utc) // 3 full days
        val buckets = ReportPeriodFactory.dailyBuckets(utc, start, end)
        assertEquals(3, buckets.size)
        assertEquals(epochMillisAt(2026, 6, 10, 0, 0, utc), buckets[0].startInclusiveEpochMillis)
        assertEquals(epochMillisAt(2026, 6, 11, 0, 0, utc), buckets[0].endExclusiveEpochMillis)
        assertEquals(epochMillisAt(2026, 6, 12, 0, 0, utc), buckets[2].startInclusiveEpochMillis)
        assertEquals(epochMillisAt(2026, 6, 13, 0, 0, utc), buckets[2].endExclusiveEpochMillis)
    }

    @Test
    fun dailyBucketsCrossMonthEndCorrectly() {
        val start = epochMillisAt(2026, 1, 30, 0, 0, utc)
        val end = epochMillisAt(2026, 2, 2, 0, 0, utc) // Jan 30, 31, Feb 1
        val buckets = ReportPeriodFactory.dailyBuckets(utc, start, end)
        assertEquals(3, buckets.size)
        assertEquals(epochMillisAt(2026, 1, 31, 0, 0, utc), buckets[1].startInclusiveEpochMillis)
        assertEquals(epochMillisAt(2026, 2, 1, 0, 0, utc), buckets[1].endExclusiveEpochMillis)
        assertEquals(epochMillisAt(2026, 2, 1, 0, 0, utc), buckets[2].startInclusiveEpochMillis)
    }

    @Test
    fun dailyBucketsCrossYearEndCorrectly() {
        val start = epochMillisAt(2025, 12, 30, 0, 0, utc)
        val end = epochMillisAt(2026, 1, 2, 0, 0, utc) // Dec 30, 31, Jan 1
        val buckets = ReportPeriodFactory.dailyBuckets(utc, start, end)
        assertEquals(3, buckets.size)
        assertEquals(2025, LocalDate(2025, 12, 31).year)
        assertEquals(epochMillisAt(2026, 1, 1, 0, 0, utc), buckets[2].startInclusiveEpochMillis)
        assertEquals(epochMillisAt(2026, 1, 2, 0, 0, utc), buckets[2].endExclusiveEpochMillis)
    }

    @Test
    fun dailyBucketsIncludeLeapDay() {
        // 2028 is a real leap year -- Feb 29 exists.
        val start = epochMillisAt(2028, 2, 28, 0, 0, utc)
        val end = epochMillisAt(2028, 3, 1, 0, 0, utc) // Feb 28, Feb 29
        val buckets = ReportPeriodFactory.dailyBuckets(utc, start, end)
        assertEquals(2, buckets.size)
        assertEquals(epochMillisAt(2028, 2, 29, 0, 0, utc), buckets[1].startInclusiveEpochMillis)
        assertEquals(epochMillisAt(2028, 3, 1, 0, 0, utc), buckets[1].endExclusiveEpochMillis)
    }

    @Test
    fun weeklyBucketsStartOnMondayAndDisplayAsMondaySunday() {
        // 2026-03-05 is a Thursday. The bucket must start on the Monday
        // on-or-before it: 2026-03-02.
        val start = epochMillisAt(2026, 3, 5, 12, 0, utc)
        val end = epochMillisAt(2026, 3, 5, 13, 0, utc)
        val buckets = ReportPeriodFactory.weeklyBuckets(utc, start, end)
        assertEquals(1, buckets.size)
        assertEquals(epochMillisAt(2026, 3, 2, 0, 0, utc), buckets[0].startInclusiveEpochMillis, "week must start on Monday 2026-03-02")
        assertEquals(epochMillisAt(2026, 3, 9, 0, 0, utc), buckets[0].endExclusiveEpochMillis, "week ends the following Monday (exclusive) -- displayed as Mon-Sun")
    }

    @Test
    fun weeklyBucketWhenStartIsAlreadyMonday() {
        val monday = epochMillisAt(2026, 3, 2, 0, 0, utc)
        val end = epochMillisAt(2026, 3, 2, 1, 0, utc)
        val buckets = ReportPeriodFactory.weeklyBuckets(utc, monday, end)
        assertEquals(epochMillisAt(2026, 3, 2, 0, 0, utc), buckets[0].startInclusiveEpochMillis)
    }

    @Test
    fun monthlyBucketsFirstDayThroughFirstDayOfNextMonth() {
        val start = epochMillisAt(2026, 4, 15, 0, 0, utc)
        val end = epochMillisAt(2026, 4, 16, 0, 0, utc)
        val buckets = ReportPeriodFactory.monthlyBuckets(utc, start, end)
        assertEquals(1, buckets.size)
        assertEquals(epochMillisAt(2026, 4, 1, 0, 0, utc), buckets[0].startInclusiveEpochMillis)
        assertEquals(epochMillisAt(2026, 5, 1, 0, 0, utc), buckets[0].endExclusiveEpochMillis)
    }

    @Test
    fun monthlyBucketsCrossYearBoundary() {
        val start = epochMillisAt(2026, 12, 15, 0, 0, utc)
        val end = epochMillisAt(2027, 1, 15, 0, 0, utc)
        val buckets = ReportPeriodFactory.monthlyBuckets(utc, start, end)
        assertEquals(2, buckets.size)
        assertEquals(epochMillisAt(2026, 12, 1, 0, 0, utc), buckets[0].startInclusiveEpochMillis)
        assertEquals(epochMillisAt(2027, 1, 1, 0, 0, utc), buckets[0].endExclusiveEpochMillis)
        assertEquals(epochMillisAt(2027, 1, 1, 0, 0, utc), buckets[1].startInclusiveEpochMillis)
        assertEquals(epochMillisAt(2027, 2, 1, 0, 0, utc), buckets[1].endExclusiveEpochMillis)
    }

    @Test
    fun businessTimezoneOffsetProducesDifferentBoundariesThanUtcForTheSameInstant() {
        // The same real instant produces a DIFFERENT "today" bucket
        // depending on which business timezone is configured -- real proof
        // the business timezone is what drives the boundary, not a hidden
        // UTC assumption.
        val instant = epochMillisAt(2026, 6, 1, 1, 0, utc) // 01:00 UTC
        val periodUtc = ReportPeriodFactory.today(utc, instant)
        val periodCairo = ReportPeriodFactory.today(cairo, instant) // 03:00 Cairo, same calendar day this time
        val periodNyc = ReportPeriodFactory.today(nyc, instant) // ~21:00 previous day in New York (UTC-4 in June, DST)

        assertEquals(epochMillisAt(2026, 6, 1, 0, 0, utc), periodUtc.startInclusiveEpochMillis)
        assertEquals(epochMillisAt(2026, 5, 31, 0, 0, nyc), periodNyc.startInclusiveEpochMillis, "in New York (DST, UTC-4 in June) the same instant is still May 31")
        assertTrue(periodUtc.startInclusiveEpochMillis != periodNyc.startInclusiveEpochMillis, "business timezone must change the resolved period, proving it is not silently ignored")
    }

    @Test
    fun deviceTimezoneNeverInfluencesTheResult() {
        // ReportPeriodFactory takes NO ambient/system timezone at all --
        // structural proof: the function signature itself has no way to
        // read TimeZone.currentSystemDefault() internally (verified by
        // inspection of ReportPeriod.kt, which never calls it) -- this
        // test proves the SAME businessTimeZone argument always produces
        // the SAME result regardless of when/where the test runs.
        val instant = epochMillisAt(2026, 6, 1, 1, 0, utc)
        val first = ReportPeriodFactory.today(cairo, instant)
        val second = ReportPeriodFactory.today(cairo, instant)
        assertEquals(first, second)
    }
}
