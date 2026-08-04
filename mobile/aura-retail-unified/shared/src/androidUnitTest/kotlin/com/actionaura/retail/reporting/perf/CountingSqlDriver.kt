package com.actionaura.retail.reporting.perf

import app.cash.sqldelight.db.QueryResult
import app.cash.sqldelight.db.SqlCursor
import app.cash.sqldelight.db.SqlDriver
import app.cash.sqldelight.db.SqlPreparedStatement

/**
 * M5.7.6 -- real query-count instrumentation, not an estimate. Delegates
 * every call to a real `JdbcSqliteDriver` but counts every
 * `executeQuery`/`execute` invocation, so `SELECT`-per-call and
 * `INSERT/UPDATE`-per-call counts are both real, observed numbers.
 */
class CountingSqlDriver(private val delegate: SqlDriver) : SqlDriver by delegate {
    var queryCount: Int = 0
        private set
    var executeCount: Int = 0
        private set

    fun reset() {
        queryCount = 0
        executeCount = 0
    }

    override fun <R> executeQuery(
        identifier: Int?,
        sql: String,
        mapper: (SqlCursor) -> QueryResult<R>,
        parameters: Int,
        binders: (SqlPreparedStatement.() -> Unit)?,
    ): QueryResult<R> {
        queryCount++
        return delegate.executeQuery(identifier, sql, mapper, parameters, binders)
    }

    override fun execute(
        identifier: Int?,
        sql: String,
        parameters: Int,
        binders: (SqlPreparedStatement.() -> Unit)?,
    ): QueryResult<Long> {
        executeCount++
        return delegate.execute(identifier, sql, parameters, binders)
    }
}
