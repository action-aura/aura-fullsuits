package com.actionaura.retail.usecases.branch

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightBranchRepository
import com.actionaura.retail.data.sqldelight.SqlDelightSettingsRepository
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.Cart
import com.actionaura.retail.financial.TaxMode
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertNull

/** M5.4 -- real, executed proof of the Branch domain's use-case layer. */
class BranchUseCasesTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    @Test
    fun ensureDefaultBranchCreatesExactlyOneMainBranchOnFirstCall() = runTest {
        val repo = SqlDelightBranchRepository(newDb(), DatabaseWriteGate())
        val useCase = EnsureDefaultBranchUseCase(repo)

        val first = useCase.execute(1L, 1000L)
        assertEquals("Main Branch", first.name)
        assertEquals(1, repo.listActive(1L).size)

        // Idempotent -- a second call with a branch already present returns
        // the SAME branch, never creates a second one.
        val second = useCase.execute(1L, 2000L)
        assertEquals(first.id, second.id)
        assertEquals(1, repo.listActive(1L).size)
    }

    @Test
    fun ensureDefaultBranchReturnsLowestIdActiveBranchWhenSeveralExist() = runTest {
        val repo = SqlDelightBranchRepository(newDb(), DatabaseWriteGate())
        val second = repo.insert(1L, "Second", null, null, 1000L)
        val first = repo.insert(1L, "First", null, null, 2000L) // inserted second, but note: id ordering, not creation-time ordering, is the deterministic rule
        // "Second" was inserted first, so it has the lower id -- the
        // deterministic rule is lowest id, not earliest/latest created_at.
        val default = EnsureDefaultBranchUseCase(repo).execute(1L, 3000L)
        assertEquals(second.id, default.id)
        assertEquals("Second", default.name)
    }

    @Test
    fun currentBranchDefaultsToDeterministicFallbackWhenNoneSelected() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val settingsRepo = SqlDelightSettingsRepository(db, gate)
        val branch = branchRepo.insert(1L, "Main", null, null, 1000L)

        val current = GetCurrentBranchUseCase(branchRepo, settingsRepo).execute(1L)
        assertEquals(branch.id, current?.id)
    }

    @Test
    fun setCurrentBranchThenGetReturnsIt() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val settingsRepo = SqlDelightSettingsRepository(db, gate)
        branchRepo.insert(1L, "First", null, null, 1000L)
        val second = branchRepo.insert(1L, "Second", null, null, 2000L)

        val setResult = SetCurrentBranchUseCase(branchRepo, settingsRepo).execute(1L, second.id)
        assertIs<DomainResult.Success<Unit>>(setResult)

        val current = GetCurrentBranchUseCase(branchRepo, settingsRepo).execute(1L)
        assertEquals(second.id, current?.id)
    }

    @Test
    fun setCurrentBranchRejectsArchivedBranch() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val settingsRepo = SqlDelightSettingsRepository(db, gate)
        val first = branchRepo.insert(1L, "First", null, null, 1000L)
        branchRepo.insert(1L, "Second", null, null, 2000L)
        DeactivateBranchUseCase(branchRepo).execute(1L, first.id)

        val result = SetCurrentBranchUseCase(branchRepo, settingsRepo).execute(1L, first.id)
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.ValidationFailed>(result.error)
    }

    @Test
    fun currentBranchFallsBackWhenPreviouslySelectedBranchWasArchived() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val settingsRepo = SqlDelightSettingsRepository(db, gate)
        val first = branchRepo.insert(1L, "First", null, null, 1000L)
        val second = branchRepo.insert(1L, "Second", null, null, 2000L)
        SetCurrentBranchUseCase(branchRepo, settingsRepo).execute(1L, second.id)
        DeactivateBranchUseCase(branchRepo).execute(1L, second.id)

        val current = GetCurrentBranchUseCase(branchRepo, settingsRepo).execute(1L)
        assertEquals(first.id, current?.id, "archived selection must fall back to the deterministic default, not be returned as-is")
    }

    @Test
    fun deactivateUseCasePropagatesLastActiveProtectedFromRepository() = runTest {
        val repo = SqlDelightBranchRepository(newDb(), DatabaseWriteGate())
        val only = repo.insert(1L, "Only", null, null, 1000L)

        val result = DeactivateBranchUseCase(repo).execute(1L, only.id)
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.LastActiveProtected>(result.error)
    }

    @Test
    fun activateUnknownBranchReturnsNotFound() = runTest {
        val repo = SqlDelightBranchRepository(newDb(), DatabaseWriteGate())
        val result = ActivateBranchUseCase(repo).execute(1L, 999L)
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.NotFound>(result.error)
    }

    @Test
    fun activateIsIdempotent() = runTest {
        val repo = SqlDelightBranchRepository(newDb(), DatabaseWriteGate())
        repo.insert(1L, "First", null, null, 1000L)
        val second = repo.insert(1L, "Second", null, null, 2000L)

        assertIs<DomainResult.Success<Unit>>(ActivateBranchUseCase(repo).execute(1L, second.id))
        assertIs<DomainResult.Success<Unit>>(ActivateBranchUseCase(repo).execute(1L, second.id))
    }

    @Test
    fun currentBranchIsNullWhenNoBranchesExistAtAll() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val current = GetCurrentBranchUseCase(SqlDelightBranchRepository(db, gate), SqlDelightSettingsRepository(db, gate)).execute(1L)
        assertNull(current)
    }

    // ------------------------------------------------------------------
    // M5.5 mandatory follow-up (Cart Branch identity) -- "current-Branch
    // switch is blocked while Cart is active." `activeCart` is caller-
    // supplied (SetCurrentBranchUseCase.kt's own KDoc explains why: no
    // real CartRepository/persistence exists yet, M5.1 boundary stub).
    // ------------------------------------------------------------------

    @Test
    fun setCurrentBranchBlockedWhileCartActiveForDifferentBranch() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val settingsRepo = SqlDelightSettingsRepository(db, gate)
        val originatingBranch = branchRepo.insert(1L, "Front Counter", null, null, 1000L)
        val otherBranch = branchRepo.insert(1L, "Warehouse", null, null, 2000L)
        val activeCart = Cart(branchId = originatingBranch.id, lines = emptyList(), mode = TaxMode.AFTER_DISCOUNT)

        val result = SetCurrentBranchUseCase(branchRepo, settingsRepo).execute(1L, otherBranch.id, activeCart)
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.ValidationFailed>(result.error)
    }

    @Test
    fun setCurrentBranchAllowedWhenCartActiveForTheSameBranch() = runTest {
        // Not a real "switch" at all -- selecting the branch the active
        // cart already belongs to must not be spuriously blocked.
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val settingsRepo = SqlDelightSettingsRepository(db, gate)
        val branch = branchRepo.insert(1L, "Front Counter", null, null, 1000L)
        val activeCart = Cart(branchId = branch.id, lines = emptyList(), mode = TaxMode.AFTER_DISCOUNT)

        val result = SetCurrentBranchUseCase(branchRepo, settingsRepo).execute(1L, branch.id, activeCart)
        assertIs<DomainResult.Success<Unit>>(result)
    }

    @Test
    fun setCurrentBranchAllowedWhenNoCartActive() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val settingsRepo = SqlDelightSettingsRepository(db, gate)
        branchRepo.insert(1L, "Front Counter", null, null, 1000L)
        val otherBranch = branchRepo.insert(1L, "Warehouse", null, null, 2000L)

        val result = SetCurrentBranchUseCase(branchRepo, settingsRepo).execute(1L, otherBranch.id, activeCart = null)
        assertIs<DomainResult.Success<Unit>>(result)
    }
}
