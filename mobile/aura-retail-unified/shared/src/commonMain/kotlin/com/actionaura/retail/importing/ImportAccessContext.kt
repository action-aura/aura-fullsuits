package com.actionaura.retail.importing

/**
 * M5.8.19 -- the future integration boundary between a real Milestone
 * 7-10 authorization authority and the Import Center, same pattern as
 * `reporting.ReportingAccessContext` (M5.6.18). NOT wired into
 * `ImportCommitExecutor`/any future parse-or-detect use case yet -- those
 * still accept a plain `companyId`/`branchId` directly. This type exists
 * so a later milestone's real authority has a stable shape to construct
 * and a real, tested resolver function to call, without redesigning the
 * Import Center layer then (`import-authorization-boundary.md`).
 *
 * Authorization is `DEFERRED_TO_MILESTONES_7_TO_10`: today, this class is
 * constructed only by tests and internal wiring, never by anything
 * resembling a real session/permission check -- there is no real
 * session/permission concept anywhere in this codebase yet, the same
 * real, cited finding `ReportingAccessContext`'s own KDoc already
 * established (`product-inventory-authorization.md`), reused here rather
 * than re-audited.
 */
data class ImportAccessContext(
    val companyId: Long,
    /** `null` = every Branch under this company the real authority grants ("all," an explicit attested grant) -- never "unchecked." */
    val allowedBranchIds: Set<Long>?,
    val grantedCapabilities: Set<ImportCapability>,
)

enum class ImportCapability {
    VIEW_SCHEMAS,
    PARSE_FILE,
    CREATE_DRY_RUN,
    COMMIT_IMPORT,
    VIEW_PROVENANCE,
}

/** The one real, narrow fact a future use-case layer needs after authorization: which company/branch to act within. */
data class ImportAuthorizedScope(val companyId: Long, val branchId: Long?)

/**
 * Resolves a caller's requested Branch into a real `ImportAuthorizedScope`,
 * or rejects it -- mirrors `ReportingAccessContext.resolveScope`'s own
 * real behavior exactly. Never trusts a UI-supplied `companyId`: the
 * resolved scope's `companyId` always comes from the context, never from
 * a parameter, so a caller cannot request a different company's import
 * no matter what it passes as `requestedBranchId`.
 */
fun ImportAccessContext.authorize(
    requiredCapability: ImportCapability,
    requestedBranchId: Long?,
): ImportResult<ImportAuthorizedScope> {
    if (requiredCapability !in grantedCapabilities) {
        return ImportResult.Failure(ImportError.AccessDenied("capability $requiredCapability not granted"))
    }
    if (requestedBranchId != null && allowedBranchIds != null && requestedBranchId !in allowedBranchIds) {
        return ImportResult.Failure(ImportError.AccessDenied("branch $requestedBranchId not in allowed branches"))
    }
    return ImportResult.Success(ImportAuthorizedScope(companyId, requestedBranchId))
}
