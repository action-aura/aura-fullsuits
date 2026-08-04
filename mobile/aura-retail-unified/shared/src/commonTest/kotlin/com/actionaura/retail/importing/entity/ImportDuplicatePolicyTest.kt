package com.actionaura.retail.importing.entity

import com.actionaura.retail.importing.ImportDuplicateDecision
import com.actionaura.retail.importing.ImportDuplicateScope
import com.actionaura.retail.importing.ImportEntityType
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class ImportDuplicatePolicyTest {

    @Test
    fun theFirstRowToClaimAKeyIsNeverADuplicate() {
        val seen = mutableMapOf<String, Long>()
        val result = ImportDuplicatePolicy.classifyWithinFile(ImportEntityType.PRODUCTS, 2, "sku-1", seen)
        assertNull(result)
        assertEquals(2L, seen["sku-1"])
    }

    @Test
    fun aLaterRowWithTheSameKeyIsARealWithinFileDuplicate() {
        val seen = mutableMapOf("sku-1" to 2L)
        val result = ImportDuplicatePolicy.classifyWithinFile(ImportEntityType.PRODUCTS, 5, "sku-1", seen)
        assertEquals(ImportDuplicateScope.WITHIN_FILE, result?.scope)
        assertEquals(ImportDuplicateDecision.SKIP, result?.decision)
        assertEquals(5L, result?.rowNumber)
    }

    @Test
    fun blankKeysNeverCollideWithEachOtherWithinFile() {
        val seen = mutableMapOf<String, Long>()
        val first = ImportDuplicatePolicy.classifyWithinFile(ImportEntityType.CUSTOMERS, 2, "", seen)
        val second = ImportDuplicatePolicy.classifyWithinFile(ImportEntityType.CUSTOMERS, 3, "", seen)
        assertNull(first)
        assertNull(second, "two customers with no email must never be treated as duplicates of each other")
    }

    @Test
    fun productMatchingAnExistingSkuIsUpdateExisting() {
        val result = ImportDuplicatePolicy.classifyAgainstDatabase(ImportEntityType.PRODUCTS, 2, "sku-1", existingId = 99L)
        assertEquals(ImportDuplicateDecision.UPDATE_EXISTING, result?.decision)
    }

    @Test
    fun customerMatchingAnExistingEmailIsUpdateExisting() {
        val result = ImportDuplicatePolicy.classifyAgainstDatabase(ImportEntityType.CUSTOMERS, 2, "a@b.com", existingId = 99L)
        assertEquals(ImportDuplicateDecision.UPDATE_EXISTING, result?.decision)
    }

    @Test
    fun categoryMatchingAnExistingNameIsSkippedNeverSilentlyOverwritten() {
        val result = ImportDuplicatePolicy.classifyAgainstDatabase(ImportEntityType.CATEGORIES, 2, "drinks", existingId = 99L)
        assertEquals(ImportDuplicateDecision.SKIP, result?.decision)
    }

    @Test
    fun supplierAndBranchMatchesAreAlsoSkipped() {
        assertEquals(ImportDuplicateDecision.SKIP, ImportDuplicatePolicy.classifyAgainstDatabase(ImportEntityType.SUPPLIERS, 2, "abc", 1L)?.decision)
        assertEquals(ImportDuplicateDecision.SKIP, ImportDuplicatePolicy.classifyAgainstDatabase(ImportEntityType.BRANCHES, 2, "main", 1L)?.decision)
    }

    @Test
    fun noExistingMatchProducesNoDuplicateRecord() {
        val result = ImportDuplicatePolicy.classifyAgainstDatabase(ImportEntityType.PRODUCTS, 2, "sku-1", existingId = null)
        assertNull(result)
    }

    @Test
    fun dedupKeyFieldMatchesTheRealAuditedLegacyBehaviorPerEntity() {
        assertEquals("sku", ImportDuplicatePolicy.dedupKeyField(ImportEntityType.PRODUCTS))
        assertEquals("email", ImportDuplicatePolicy.dedupKeyField(ImportEntityType.CUSTOMERS))
        assertEquals("name", ImportDuplicatePolicy.dedupKeyField(ImportEntityType.SUPPLIERS))
        assertEquals("name", ImportDuplicatePolicy.dedupKeyField(ImportEntityType.BRANCHES))
        assertEquals("name", ImportDuplicatePolicy.dedupKeyField(ImportEntityType.CATEGORIES))
    }
}
