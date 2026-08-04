# Import Authorization Boundary (M5.8.19)

Real, deferred integration boundary between a future Milestone 7-10
authorization authority and the Import Center — exact structural
mirror of `reporting.ReportingAccessContext` (M5.6.18,
`reporting-authorization-integration-boundary.md`), applied to
Import Center's own result type. Proven by `ImportAccessContextTest.kt` (5/5).

## `ImportAccessContext`

`companyId`, `allowedBranchIds: Set<Long>?` (`null` = every branch —
an explicit, attested "all" grant, never "unchecked"),
`grantedCapabilities: Set<ImportCapability>`.

## `ImportCapability`

`VIEW_SCHEMAS, PARSE_FILE, CREATE_DRY_RUN, COMMIT_IMPORT,
VIEW_PROVENANCE` — real, distinct capabilities matching the real route
boundaries the legacy authority itself exposed (`/schemas`, `/parse`
and `/detect`, dry-run creation, `/execute`/`/smart-execute`, and a new
provenance-read capability with no legacy analogue, since provenance
itself is new this milestone).

## `ImportAccessContext.authorize`

Same two real checks as `ReportingAccessContext.resolveScope`: the
required capability must be granted, and a non-null requested branch
must be in `allowedBranchIds` when that set is non-null. Returns an
`ImportAuthorizedScope(companyId, branchId)` — the resolved
`companyId` always comes from the CONTEXT, never from a caller-supplied
parameter (`theResolvedScopesCompanyIdAlwaysComesFromTheContextNeverFromARequestedParameter`
proves no parameter exists through which a caller could request a
different company's scope).

## `DEFERRED_TO_MILESTONES_7_TO_10`

Not wired into `ImportCommitExecutor` or any parse/detect use case this
milestone — those still accept a plain `companyId`/`branchId` directly.
This type is constructed only by tests and internal wiring today; there
is no real session/permission concept anywhere in this codebase yet
(the same real, cited finding `ReportingAccessContext`'s own KDoc
already established, reused here rather than re-audited). A future
milestone's real authority gets a stable shape to construct and a real,
tested resolver to call without redesigning the Import Center layer
then.
