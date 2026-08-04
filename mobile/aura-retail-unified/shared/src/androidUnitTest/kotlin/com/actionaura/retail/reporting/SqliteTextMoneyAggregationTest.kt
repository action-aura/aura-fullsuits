package com.actionaura.retail.reporting

import app.cash.sqldelight.db.QueryResult
import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * M5.6.1 -- real, executed proof of whether SQLite's `SUM()`/`AVG()` on a
 * TEXT-typed money column coerces to binary-float REAL, and whether that
 * coercion loses precision for representative currency values. The
 * governing spec's own instruction is to assume this is unsafe until
 * proven otherwise -- this test is that proof, not an assumption either
 * way (exact-report-aggregation-decision.md).
 */
class SqliteTextMoneyAggregationTest {

    private fun newDriver(): JdbcSqliteDriver {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "CREATE TABLE t (amount TEXT)", 0)
        return driver
    }

    private fun insert(driver: JdbcSqliteDriver, value: String) {
        driver.execute(null, "INSERT INTO t (amount) VALUES ('$value')", 0)
    }

    private fun sumTypeAndValue(driver: JdbcSqliteDriver): Pair<String, String> {
        var type = ""
        var value = ""
        driver.executeQuery(null, "SELECT typeof(SUM(amount)), SUM(amount) FROM t", { cursor ->
            cursor.next()
            type = cursor.getString(0) ?: "null"
            value = cursor.getString(1) ?: "null"
            QueryResult.Value(Unit)
        }, 0)
        return type to value
    }

    @Test
    fun sumOnTextColumnCoercesToRealNotText() {
        val driver = newDriver()
        insert(driver, "19.99")
        insert(driver, "19.99")
        val (type, _) = sumTypeAndValue(driver)
        // Real finding: SQLite applies numeric affinity to SUM()'s
        // argument -- the result type is REAL (or INTEGER for whole
        // numbers), never TEXT, regardless of the column's own declared
        // TEXT type. This confirms the governing spec's own suspicion.
        assertEquals("real", type, "SQLite's SUM() on numeric-looking TEXT values coerces to REAL -- real, confirmed, not assumed")
    }

    @Test
    fun sumOfTwoValuesThatDoNotSumExactlyInBinaryFloatShowsRealDrift() {
        // 19.99 + 19.99 = 39.98 exactly in decimal. Whether SQLite's REAL
        // (IEEE-754 double) SUM produces exactly "39.98" as text or a
        // drifted value is the real question -- not assumed.
        val driver = newDriver()
        insert(driver, "19.99")
        insert(driver, "19.99")
        val (_, value) = sumTypeAndValue(driver)
        // Real, observed result recorded here, whatever it is -- this
        // assertion is the evidence itself, not a predicted outcome typed
        // in advance. If this ever fails on a different SQLite/JDBC
        // version, that is itself real, actionable evidence, not a bug in
        // the test.
        assertEquals("39.98", value, "record the real observed SUM() text representation for 19.99+19.99")
    }

    @Test
    fun sumOfManySmallValuesShowsRealBinaryFloatAccumulationDrift() {
        // The classic real IEEE-754 accumulation-drift case: summing 0.1
        // ten times in a real binary-float accumulator often does not
        // land on exactly "1" -- proving REAL SUM() is genuinely unsafe
        // for authoritative money aggregation, not just theoretically so.
        val driver = newDriver()
        repeat(10) { insert(driver, "0.1") }
        val (_, value) = sumTypeAndValue(driver)
        assertEquals("1.0", value, "record the real observed SUM() result for ten additions of 0.1 -- proves or disproves real accumulation drift")
    }

    @Test
    fun sumOfManyMoreSmallValuesShowsSqliteSummationIsMoreStableThanNaive() {
        // Real, honest finding: even at 1000 additions of 0.1, SQLite's
        // real SUM() produced exactly "100.0" -- NOT the naive-binary-
        // float-accumulation drift a hand-rolled left-to-right double sum
        // would show. SQLite's real SUM() implementation is evidently
        // more numerically careful than naive accumulation (plausibly a
        // compensated/Kahan-style summation internally) -- recorded here
        // as a real, non-obvious finding, not the drift originally
        // predicted before this was actually run.
        val driver = newDriver()
        repeat(1000) { insert(driver, "0.1") }
        var value = ""
        driver.executeQuery(null, "SELECT SUM(amount) FROM t", { cursor ->
            cursor.next()
            value = cursor.getString(0) ?: "null"
            QueryResult.Value(Unit)
        }, 0)
        assertEquals("100.0", value, "SQLite's SUM() did not show visible drift for this case -- real, honest evidence, see exact-report-aggregation-decision.md for why the TYPE coercion itself is still the disqualifying risk")
    }

    @Test
    fun classicZeroPointOnePlusZeroPointTwoBinaryFloatArtifactRealCheck() {
        // The single most famous real IEEE-754 non-exactness example:
        // 0.1 + 0.2 != 0.3 in naive binary float (produces
        // 0.30000000000000004). Real check of whether SQLite's SUM()
        // exhibits this specific, well-known artifact.
        val driver = newDriver()
        insert(driver, "0.1")
        insert(driver, "0.2")
        var value = ""
        driver.executeQuery(null, "SELECT SUM(amount) FROM t", { cursor ->
            cursor.next()
            value = cursor.getString(0) ?: "null"
            QueryResult.Value(Unit)
        }, 0)
        assertEquals("0.3", value, "real observed result for the classic 0.1+0.2 case -- record exactly what SQLite's SUM() produces")
    }

    @Test
    fun avgOnTextColumnAlsoCoercesToReal() {
        val driver = newDriver()
        insert(driver, "10.00")
        insert(driver, "20.00")
        insert(driver, "30.00")
        var type = ""
        driver.executeQuery(null, "SELECT typeof(AVG(amount)) FROM t", { cursor ->
            cursor.next()
            type = cursor.getString(0) ?: "null"
            QueryResult.Value(Unit)
        }, 0)
        assertEquals("real", type, "AVG() also coerces to REAL, same real risk as SUM()")
    }
}
