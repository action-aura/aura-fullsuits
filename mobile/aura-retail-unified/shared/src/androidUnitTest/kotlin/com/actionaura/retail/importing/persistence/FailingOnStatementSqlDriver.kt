package com.actionaura.retail.importing.persistence

import app.cash.sqldelight.db.QueryResult
import app.cash.sqldelight.db.SqlDriver
import app.cash.sqldelight.db.SqlPreparedStatement

/**
 * M5.8.15/M5.8.16 -- real, driver-level fault injection against the
 * REAL `JdbcSqliteDriver`, matching `CountingSqlDriver`'s own established
 * decorator precedent (`reporting-query-count-report.md`). Throws a
 * real exception on the Nth `execute()` call whose SQL text contains
 * `sqlContains` -- everything up to that point is a real statement
 * against the real SQLite engine; the throw happens INSIDE the real
 * `db.transactionWithResult` block, so the real SQLite ROLLBACK
 * machinery is what undoes it, not a mock or a hand-waved assertion.
 */
class FailingOnStatementSqlDriver(
    private val delegate: SqlDriver,
    private val sqlContains: String,
    private val failOnOccurrence: Int,
) : SqlDriver by delegate {
    private var occurrence = 0

    override fun execute(
        identifier: Int?,
        sql: String,
        parameters: Int,
        binders: (SqlPreparedStatement.() -> Unit)?,
    ): QueryResult<Long> {
        if (sql.contains(sqlContains)) {
            occurrence++
            if (occurrence == failOnOccurrence) {
                throw RuntimeException("FailingOnStatementSqlDriver: injected failure on occurrence $occurrence of statements containing '$sqlContains'")
            }
        }
        return delegate.execute(identifier, sql, parameters, binders)
    }
}
