package com.actionaura.retail.data.sqldelight

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.db.RetailDatabase
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

/** M5.1 -- real, executed proof of the SQLDelight-backed `SettingsRepository`, including doc_sequences' atomic increment. */
class SettingsRepositoryTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    @Test
    fun getSetSettingRoundTrip() = runTest {
        val repo = SqlDelightSettingsRepository(newDb(), DatabaseWriteGate())
        assertNull(repo.getSetting(1L, "tax_calculation_mode"))

        repo.setSetting(1L, "tax_calculation_mode", "after_discount")
        assertEquals("after_discount", repo.getSetting(1L, "tax_calculation_mode"))
        assertEquals(mapOf("tax_calculation_mode" to "after_discount"), repo.getAllSettings(1L))
    }

    @Test
    fun nextDocumentNumberStartsAtOneAndIncrementsPerCompanyAndDocType() = runTest {
        val repo = SqlDelightSettingsRepository(newDb(), DatabaseWriteGate())

        assertEquals(1L, repo.nextDocumentNumber(1L, "sale"))
        assertEquals(2L, repo.nextDocumentNumber(1L, "sale"))
        assertEquals(3L, repo.nextDocumentNumber(1L, "sale"))

        // Different doc_type under the same company starts its own sequence.
        assertEquals(1L, repo.nextDocumentNumber(1L, "return"))

        // Different company under the same doc_type also starts its own sequence.
        assertEquals(1L, repo.nextDocumentNumber(2L, "sale"))

        // Original sequence is undisturbed by the other two.
        assertEquals(4L, repo.nextDocumentNumber(1L, "sale"))
    }
}
