package com.actionaura.retail.reporting

import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.RepositoryError

/**
 * M5.6.18 -- the future integration boundary between a real Milestone
 * 7-10 authorization authority and the reporting layer. NOT wired into
 * `ReportingRepository`/`DashboardRepository` yet -- those still accept
 * a plain `ReportScope` directly, and continue to for the rest of M5.6.
 * This type exists so a later milestone's real authority has a stable
 * shape to construct and a real, tested resolver function to call,
 * without redesigning the reporting layer then
 * (`reporting-authorization-integration-boundary.md`).
 *
 * Authorization is `DEFERRED_TO_MILESTONES_7_TO_10`: today, this class
 * is constructed only by tests and internal wiring, never by anything
 * resembling a real session/permission check -- there is no real
 * session/permission concept anywhere in this codebase yet
 * (`product-inventory-authorization.md`'s own cited finding, reused
 * here rather than re-audited).
 */
data class ReportingAccessContext(
    val companyId: Long,
    /** `null` = every Branch under this company the real authority grants ("all," an explicit attested grant) -- never "unchecked." */
    val allowedBranchIds: Set<Long>?,
    val grantedCapabilities: Set<ReportingCapability>,
)

enum class ReportingCapability {
    VIEW_SALES_SUMMARY,
    VIEW_SALES_TREND,
    VIEW_TOP_PRODUCTS,
    VIEW_DASHBOARD,
}

/**
 * Resolves a caller's requested Branch/Category into a real `ReportScope`,
 * or rejects it -- this is the one real behavior a future use-case layer
 * needs from this boundary. Never trusts a UI-supplied `companyId`: the
 * resolved scope's `companyId` always comes from the context, never from
 * a parameter, so a caller cannot request a different company's data no
 * matter what it passes as `requestedBranchId`/`requestedCategoryId`.
 */
fun ReportingAccessContext.resolveScope(
    requiredCapability: ReportingCapability,
    requestedBranchId: Long?,
    requestedCategoryId: Long?,
): DomainResult<ReportScope> {
    if (requiredCapability !in grantedCapabilities) {
        return DomainResult.Failure(RepositoryError.AccessDenied("reporting", "capability $requiredCapability not granted"))
    }
    if (requestedBranchId != null && allowedBranchIds != null && requestedBranchId !in allowedBranchIds) {
        return DomainResult.Failure(RepositoryError.AccessDenied("reporting", "branch $requestedBranchId not in allowed branches"))
    }
    return DomainResult.Success(ReportScope(companyId, requestedBranchId, requestedCategoryId))
}
