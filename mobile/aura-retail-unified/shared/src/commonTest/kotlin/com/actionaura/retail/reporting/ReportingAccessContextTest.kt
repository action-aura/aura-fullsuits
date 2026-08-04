package com.actionaura.retail.reporting

import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.RepositoryError
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs

/**
 * M5.6.18 -- real, executed proof of the deferred reporting-authorization
 * integration boundary's one real behavior: resolving a requested scope
 * against a granted `ReportingAccessContext`.
 */
class ReportingAccessContextTest {

    @Test
    fun aRequestedBranchWithinTheAllowedSetResolvesToARealScope() {
        val context = ReportingAccessContext(1L, allowedBranchIds = setOf(10L, 20L), grantedCapabilities = setOf(ReportingCapability.VIEW_SALES_SUMMARY))

        val result = context.resolveScope(ReportingCapability.VIEW_SALES_SUMMARY, requestedBranchId = 10L, requestedCategoryId = null)

        assertIs<DomainResult.Success<ReportScope>>(result)
        assertEquals(ReportScope(1L, 10L, null), result.value)
    }

    @Test
    fun aRequestedBranchOutsideTheAllowedSetIsRejected() {
        val context = ReportingAccessContext(1L, allowedBranchIds = setOf(10L, 20L), grantedCapabilities = setOf(ReportingCapability.VIEW_SALES_SUMMARY))

        val result = context.resolveScope(ReportingCapability.VIEW_SALES_SUMMARY, requestedBranchId = 999L, requestedCategoryId = null)

        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.AccessDenied>(result.error)
    }

    @Test
    fun nullAllowedBranchIdsMeansEveryBranchIsPermittedARealAttestedAllGrant() {
        val context = ReportingAccessContext(1L, allowedBranchIds = null, grantedCapabilities = setOf(ReportingCapability.VIEW_SALES_SUMMARY))

        val result = context.resolveScope(ReportingCapability.VIEW_SALES_SUMMARY, requestedBranchId = 999L, requestedCategoryId = null)

        assertIs<DomainResult.Success<ReportScope>>(result)
    }

    @Test
    fun aCapabilityNotGrantedIsRejectedEvenWithAnAllowedBranch() {
        val context = ReportingAccessContext(1L, allowedBranchIds = setOf(10L), grantedCapabilities = setOf(ReportingCapability.VIEW_SALES_SUMMARY))

        val result = context.resolveScope(ReportingCapability.VIEW_DASHBOARD, requestedBranchId = 10L, requestedCategoryId = null)

        assertIs<DomainResult.Failure>(result)
    }

    @Test
    fun theResolvedScopesCompanyIdAlwaysComesFromTheContextNeverFromARequestedParameter() {
        val context = ReportingAccessContext(1L, allowedBranchIds = null, grantedCapabilities = setOf(ReportingCapability.VIEW_SALES_SUMMARY))

        val result = context.resolveScope(ReportingCapability.VIEW_SALES_SUMMARY, requestedBranchId = null, requestedCategoryId = null)

        assertIs<DomainResult.Success<ReportScope>>(result)
        assertEquals(1L, result.value.companyId, "there is no parameter through which a caller could request a different company's scope")
    }
}
