package com.actionaura.retail.data.sqldelight

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.db.RetailDatabase
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** M5.1 -- real, executed proof of the SQLDelight-backed Category/Branch repositories, run through a real in-memory SQLite database (same pattern as RetailDatabaseSchemaTest). */
class CategoryBranchRepositoryTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    @Test
    fun categoryInsertListGetAndArchiveRoundTrip() = runTest {
        val repo = SqlDelightCategoryRepository(newDb(), DatabaseWriteGate())

        val created = repo.insert(1L, "Beverages", "Cold drinks", 1000L)
        assertEquals("Beverages", created.name)
        assertTrue(created.isActive)
        assertEquals(0L, created.productCount)

        val listed = repo.listActive(1L)
        assertEquals(1, listed.size)
        assertEquals(created.id, listed.first().id)

        repo.setActive(1L, created.id, false)
        assertTrue(repo.listActive(1L).isEmpty(), "archived category must not appear in listActive")
        val fetched = repo.getById(1L, created.id)
        assertFalse(fetched!!.isActive, "getById must still return an archived row -- unlike listActive, it is not status-filtered")
    }

    @Test
    fun categoryIsScopedByCompanyId() = runTest {
        val repo = SqlDelightCategoryRepository(newDb(), DatabaseWriteGate())
        val created = repo.insert(1L, "Beverages", null, 1000L)
        assertNull(repo.getById(2L, created.id), "a category created under company_id=1 must not be readable under company_id=2")
    }

    @Test
    fun branchInsertListAndCountActive() = runTest {
        val repo = SqlDelightBranchRepository(newDb(), DatabaseWriteGate())
        repo.insert(1L, "Main", "123 St", "555-0100", 1000L)
        repo.insert(1L, "Second", null, null, 2000L)

        assertEquals(2L, repo.countActive(1L))
        assertEquals(2, repo.listActive(1L).size)
    }

    @Test
    fun lastActiveBranchCannotBeDeactivated() = runTest {
        val repo = SqlDelightBranchRepository(newDb(), DatabaseWriteGate())
        val only = repo.insert(1L, "Only Branch", null, null, 1000L)

        val result = repo.setActive(1L, only.id, false)
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.LastActiveProtected>(result.error)
        assertEquals(1L, repo.countActive(1L), "the branch must remain active after a rejected deactivation")
    }

    @Test
    fun secondToLastActiveBranchCanBeDeactivated() = runTest {
        val repo = SqlDelightBranchRepository(newDb(), DatabaseWriteGate())
        val first = repo.insert(1L, "Main", null, null, 1000L)
        repo.insert(1L, "Second", null, null, 2000L)

        val result = repo.setActive(1L, first.id, false)
        assertIs<DomainResult.Success<Unit>>(result)
        assertEquals(1L, repo.countActive(1L))
    }

    @Test
    fun deactivatingAlreadyInactiveBranchIsIdempotentNoOp() = runTest {
        val repo = SqlDelightBranchRepository(newDb(), DatabaseWriteGate())
        val first = repo.insert(1L, "Main", null, null, 1000L)
        repo.insert(1L, "Second", null, null, 2000L)
        repo.setActive(1L, first.id, false)

        // Now only "Second" is active. Deactivating "first" again (already
        // inactive) must be a no-op success, not a spurious
        // LastActiveProtected failure -- it is not itself an active branch.
        val result = repo.setActive(1L, first.id, false)
        assertIs<DomainResult.Success<Unit>>(result)
    }
}
