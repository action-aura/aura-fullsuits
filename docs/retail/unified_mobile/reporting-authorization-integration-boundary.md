# Reporting Authorization Integration Boundary (M5.6.18)

Real, honest scope finding, reused rather than re-audited:
`product-inventory-authorization.md` (M5.5.13) already established that
**no Kotlin-side RBAC/authorization system exists anywhere in this
codebase today** — `LicensingRepository`/`UserRepository`/
`SessionRepository` remain empty M5.1 boundary stubs
(`RepositoryBoundaries.kt`), because Milestones 7-10 (Owner licensing
authority audit, multi-device activation, secure credential storage,
offline lease enforcement) have not been reached. That finding still
holds; this document does not re-derive it.

## Disposition: `DEFERRED_TO_MILESTONES_7_TO_10`

Same disposition as M5.5.13, applied to the reporting layer. Per the
governing checkpoint's explicit instruction, M5.6 does **not** create a
new RBAC authority and does **not** build a new user-facing permission
check. It defines only the integration *boundary* a real Milestone
7-10 authority will construct and hand to a future reporting use-case
layer.

## The real, implemented boundary shape

`reporting/ReportingAccessContext.kt`:

```kotlin
data class ReportingAccessContext(
    val companyId: Long,
    val allowedBranchIds: Set<Long>?,       // null = every branch a real authority attests as granted, never "unchecked"
    val grantedCapabilities: Set<ReportingCapability>,
)

enum class ReportingCapability { VIEW_SALES_SUMMARY, VIEW_SALES_TREND, VIEW_TOP_PRODUCTS, VIEW_DASHBOARD }

fun ReportingAccessContext.resolveScope(
    requiredCapability: ReportingCapability,
    requestedBranchId: Long?,
    requestedCategoryId: Long?,
): DomainResult<ReportScope>
```

- **`companyId`** always comes from the context, never from a
  caller-supplied parameter — `resolveScope` has no `companyId`
  parameter at all, so there is no code path through which a UI could
  ask for a different company's data. Proven by
  `theResolvedScopesCompanyIdAlwaysComesFromTheContextNeverFromARequestedParameter`.
- **`allowedBranchIds`** rejects a `requestedBranchId` outside the
  granted set with `RepositoryError.AccessDenied` (new error case added
  to `RepositoryError.kt` this milestone). `null` is a real, explicit
  "every branch this authority grants" attestation, not an unchecked
  bypass — proven by
  `nullAllowedBranchIdsMeansEveryBranchIsPermittedARealAttestedAllGrant`.
- **`grantedCapabilities`** rejects a request for a capability the
  context does not list, independent of Branch — proven by
  `aCapabilityNotGrantedIsRejectedEvenWithAnAllowedBranch`.

Proven by `ReportingAccessContextTest.kt` (5/5,
`shared/src/commonTest/kotlin/com/actionaura/retail/reporting/ReportingAccessContextTest.kt`,
run cross-platform since it lives in `commonTest`, not `androidUnitTest`).

## What is deliberately NOT done this milestone

- `ReportingRepository`/`DashboardRepository` are **not** changed to
  accept `ReportingAccessContext` — every method still takes a plain
  `ReportScope` directly, as it has since M5.6.3. Wiring the boundary in
  is a future use-case-layer concern once a real caller exists to
  construct the context from a real session.
- `ReportingAccessContext` is constructed **only** by
  `ReportingAccessContextTest.kt` today — no production code path
  builds one. There is no fabricated "default" or "always-allow"
  instance shipped anywhere.
- No new persistence, no new table, no new query — this is a pure
  in-memory boundary type.

## What already functions as defense-in-depth, independent of this boundary

Same two structural properties `product-inventory-authorization.md`
already identified, now proven again for the reporting layer
specifically:

- Every reporting query is `company_id`-scoped and independently
  checked — cross-business leakage is structurally rejected regardless
  of `ReportingAccessContext` even existing yet
  (`sales-trend-contract.md`'s `requestingAnotherCompanysBranchIdNeverLeaksThatCompanysSales`,
  `top-products-contract.md`'s cross-business Category proof).
- License-capability gating remains a real, server-side Python control
  for as long as this mobile client talks to that backend
  (pre-Milestone-11 offline-lease work) — unaffected by this milestone.

## Final mobile release gate (unchanged from the M5.5 checkpoint)

Final mobile release remains blocked until a real Milestone 7-10
authority constructs `ReportingAccessContext` from real session state,
wires it into the reporting use-case layer, and negative-authorization
tests (a denied capability/Branch actually blocking a real call) pass
against that real wiring — not against this milestone's boundary-only
proof.
