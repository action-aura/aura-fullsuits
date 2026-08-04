package com.actionaura.retail.importing

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs

/**
 * M5.8.19 -- real, executed proof of the deferred import-authorization
 * integration boundary's one real behavior: resolving a requested scope
 * against a granted `ImportAccessContext`. Mirrors
 * `ReportingAccessContextTest` (M5.6.18) exactly.
 */
class ImportAccessContextTest {

    @Test
    fun aRequestedBranchWithinTheAllowedSetResolvesToARealScope() {
        val context = ImportAccessContext(1L, allowedBranchIds = setOf(10L, 20L), grantedCapabilities = setOf(ImportCapability.COMMIT_IMPORT))

        val result = context.authorize(ImportCapability.COMMIT_IMPORT, requestedBranchId = 10L)

        assertIs<ImportResult.Success<ImportAuthorizedScope>>(result)
        assertEquals(ImportAuthorizedScope(1L, 10L), result.value)
    }

    @Test
    fun aRequestedBranchOutsideTheAllowedSetIsRejected() {
        val context = ImportAccessContext(1L, allowedBranchIds = setOf(10L, 20L), grantedCapabilities = setOf(ImportCapability.COMMIT_IMPORT))

        val result = context.authorize(ImportCapability.COMMIT_IMPORT, requestedBranchId = 999L)

        assertIs<ImportResult.Failure>(result)
        assertIs<ImportError.AccessDenied>(result.error)
    }

    @Test
    fun nullAllowedBranchIdsMeansEveryBranchIsPermittedARealAttestedAllGrant() {
        val context = ImportAccessContext(1L, allowedBranchIds = null, grantedCapabilities = setOf(ImportCapability.COMMIT_IMPORT))

        val result = context.authorize(ImportCapability.COMMIT_IMPORT, requestedBranchId = 999L)

        assertIs<ImportResult.Success<ImportAuthorizedScope>>(result)
    }

    @Test
    fun aCapabilityNotGrantedIsRejectedEvenWithAnAllowedBranch() {
        val context = ImportAccessContext(1L, allowedBranchIds = setOf(10L), grantedCapabilities = setOf(ImportCapability.VIEW_SCHEMAS))

        val result = context.authorize(ImportCapability.COMMIT_IMPORT, requestedBranchId = 10L)

        assertIs<ImportResult.Failure>(result)
        assertIs<ImportError.AccessDenied>(result.error)
    }

    @Test
    fun theResolvedScopesCompanyIdAlwaysComesFromTheContextNeverFromARequestedParameter() {
        val context = ImportAccessContext(1L, allowedBranchIds = null, grantedCapabilities = setOf(ImportCapability.COMMIT_IMPORT))

        val result = context.authorize(ImportCapability.COMMIT_IMPORT, requestedBranchId = null)

        assertIs<ImportResult.Success<ImportAuthorizedScope>>(result)
        assertEquals(1L, result.value.companyId, "there is no parameter through which a caller could request a different company's scope")
    }
}
