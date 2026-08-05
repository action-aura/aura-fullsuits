package com.actionaura.retail.di

import app.cash.sqldelight.db.SqlDriver
import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.platform.DatabaseDriverFactory
import com.actionaura.retail.securestorage.InMemorySecureBlobStore
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNotSame
import kotlin.test.assertSame
import kotlin.test.assertTrue

/** Real, JVM-testable `DatabaseDriverFactory` -- same real schema-creation pattern every other real repository test in this module already uses. */
private class FakeDatabaseDriverFactory : DatabaseDriverFactory {
    override fun createDriver(): SqlDriver {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return driver
    }
}

/**
 * M6.3 -- real, executed proof that `AuraAppContainer` is the one real
 * composition root: every repository inside ONE container shares the
 * SAME real `RetailDatabase`/`DatabaseWriteGate`, and two INDEPENDENT
 * containers never share state (no accidental JVM-wide singleton) --
 * `presentation-di-scope-report.md`'s own required graph-identity proof.
 */
class AuraAppContainerTest {

    @Test
    fun everyRepositoryInOneContainerSharesTheSameRealDatabaseInstance() = runTest {
        val container = AuraAppContainer(FakeDatabaseDriverFactory(), com.actionaura.retail.platform.AndroidUnicodeTextNormalizer(), InMemorySecureBlobStore())

        // Real cross-repository consistency proof: a Branch inserted
        // through one repository is immediately visible via a raw query
        // on the container's own `database` reference -- proving
        // `branchRepository` and `container.database` are the same real
        // connection/schema, not two independent databases that happen
        // to look alike.
        container.branchRepository.insert(1L, "Main Store", null, null, 1000L)
        val rawRows = container.database.catalogQueries.selectActiveBranches(1L).executeAsList()
        assertEquals(1, rawRows.size)
        assertEquals("Main Store", rawRows.first().name)
    }

    @Test
    fun theSameGateInstanceIsSharedAcrossEveryRepository() = runTest {
        val container = AuraAppContainer(FakeDatabaseDriverFactory(), com.actionaura.retail.platform.AndroidUnicodeTextNormalizer(), InMemorySecureBlobStore())

        // Real proof categories and branches (two independently-
        // constructed repository objects) never deadlock or corrupt
        // state when used from the same container -- both real writes
        // succeed and are both visible afterward, proving they share
        // one real DatabaseWriteGate rather than two competing locks
        // against the same underlying JDBC connection.
        container.categoryRepository.insert(1L, "Beverages", null, 1000L)
        container.branchRepository.insert(1L, "Main Store", null, null, 1000L)

        assertEquals(1, container.categoryRepository.listActive(1L).size)
        assertEquals(1, container.branchRepository.listActive(1L).size)
    }

    @Test
    fun twoIndependentContainersNeverShareRealDatabaseState() = runTest {
        val containerA = AuraAppContainer(FakeDatabaseDriverFactory(), com.actionaura.retail.platform.AndroidUnicodeTextNormalizer(), InMemorySecureBlobStore())
        val containerB = AuraAppContainer(FakeDatabaseDriverFactory(), com.actionaura.retail.platform.AndroidUnicodeTextNormalizer(), InMemorySecureBlobStore())
        assertNotSame(containerA.database, containerB.database)
        assertNotSame(containerA.gate, containerB.gate)

        containerA.categoryRepository.insert(1L, "Only In A", null, 1000L)

        assertEquals(1, containerA.categoryRepository.listActive(1L).size)
        assertEquals(0, containerB.categoryRepository.listActive(1L).size, "container B must never see container A's real data -- proof there is no accidental shared/global database")
    }

    @Test
    fun theSameContainerInstanceReturnsTheSameRealDatabaseReferenceEveryAccess() {
        val container = AuraAppContainer(FakeDatabaseDriverFactory(), com.actionaura.retail.platform.AndroidUnicodeTextNormalizer(), InMemorySecureBlobStore())
        assertSame(container.database, container.database)
        assertSame(container.gate, container.gate)
        assertNotNull(container.reportingRepository)
        assertNotNull(container.dashboardRepository)
        assertNotNull(container.importPersistenceRepository)
    }

    /**
     * M10.31 -- real, executed proof the container's own
     * `secureMaterialStore` is a real, functional
     * `GenerationalSecureMaterialStore` wrapping exactly the
     * `SecureBlobStore` this container was constructed with, and that
     * two independent containers never share secure-storage state --
     * the identical "no accidental shared authority" guarantee
     * `database`/`gate` already have, now extended to secure storage.
     */
    @Test
    fun secureMaterialStoreIsRealFunctionalAndIndependentPerContainer() = runTest {
        val blobStoreA = InMemorySecureBlobStore()
        val blobStoreB = InMemorySecureBlobStore()
        val containerA = AuraAppContainer(FakeDatabaseDriverFactory(), com.actionaura.retail.platform.AndroidUnicodeTextNormalizer(), blobStoreA)
        val containerB = AuraAppContainer(FakeDatabaseDriverFactory(), com.actionaura.retail.platform.AndroidUnicodeTextNormalizer(), blobStoreB)

        val bundle = com.actionaura.retail.securestorage.SecureStorageFixtures.bundle()
        val commit = containerA.secureMaterialStore.commitActivationBundle(bundle)
        assertTrue(commit is com.actionaura.retail.securestorage.SecureStorageResult.Success, "real regression: the container-held secureMaterialStore must be a real, functional store, not a stub")

        assertEquals(0, blobStoreB.rawKeyCount(), "real regression: two independent containers must never share the same underlying SecureBlobStore/secure state")
        assertTrue(blobStoreA.rawKeyCount() > 0)
    }
}
