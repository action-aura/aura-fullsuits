# Local Authorization vs. License Entitlement (M7.19)

Defines the real, separate composition of local user authorization vs.
commercial entitlement — two concepts that must never be merged into
one check, per the governing checkpoint's own instruction. This
document is a design definition built on the real findings of M7.2-
M7.16; it does not implement anything, and no production wiring in
this repository hard-codes full access.

## Two genuinely independent axes

1. **Local Retail user authorization** — the existing Retail app's own
   employee/PIN/role model (unrelated to Owner, already real,
   pre-dates M7, out of this document's audit scope). Answers: "is
   *this person*, on *this device*, allowed to open the Categories
   screen / post a sale / view the Reporting dashboard?"
2. **Commercial license entitlement** — proven purely at the *device*
   level against Owner, per `customer-authentication-gap-analysis.md`'s
   own real finding that no customer login exists. Answers: "is *this
   installation* currently covered by a valid, unexpired, non-revoked
   license for this product/platform, and does that license's
   entitlement set include the feature being requested?" — evaluated
   locally via the signed assertion (`installation-credential-
   contract.md`) and `OfflinePolicyEvaluator`
   (`offline-license-lease-contract-audit.md`), not by any per-user
   check.

## Real composition rule

```
future_access_to_feature_X =
      commercial_access(installation, product, X)   // from the signed assertion + offline policy
  AND local_permission(user, X)                       // from Retail's own existing RBAC/PIN model
  AND business_or_branch_scope(user, X)                // from Retail's own existing scoping
  AND feature_availability(X, subscription_entitlements) // from LicenseEntitlement/PlanEntitlement, per subscription-entitlement-license-map.md
```

All four terms are independently `AND`-ed — a `true` on one axis never
substitutes for a `false` on another. In particular:
- A fully-licensed installation with an over-permissioned local user
  is still gated by `local_permission`.
- A perfectly-authorized local user on an installation whose license
  has gone `SUSPENDED`/`EXPIRED`/`REVOKED`
  (`license-state-machine-contract.md`) is still gated by
  `commercial_access`.
- `feature_availability` is entitlement-driven (real
  `LicenseEntitlement`/`PlanEntitlement` rows,
  `subscription-entitlement-license-map.md`), independent of both
  local role and raw license validity — a valid license with an
  entitlement explicitly excluding a feature must not grant it.

## Why this must never be merged into one check

- Merging would mean a single boolean flag conflating "is licensed"
  with "is this specific user allowed" — a real security regression
  risk: any code path that checks only commercial validity would
  silently grant every local user full access the moment the device
  activates, and any code path that checks only local role would keep
  working after a license is revoked, since Owner has no way to force
  a local re-check without the client itself distinguishing the two
  axes.
- The governing M6 checkpoint's own `deferred-authorization-ui-boundary.md`
  already established that authorization remains explicitly deferred
  in the presentation layer — this document extends that same
  discipline to the licensing layer without contradicting it: neither
  layer may claim to speak for the other.

## Confirmed: no production wiring hard-codes full access

Grepped the M7.17 shared contract package (`shared/src/commonMain/
kotlin/com/actionaura/retail/licensing/`) and the M6 presentation DI
graph (`AuraAppContainer`) for any unconditional `true`/allow-all
authorization shortcut — none exists; M7.17 introduces only inert data
models with no authorization decision logic at all (per M7's own scope
restriction — "Do not implement HTTP execution yet," and by extension
no authorization *decision* logic either, since it would have nothing
real to evaluate against without HTTP execution).

## Real gap for a later milestone

Wiring `commercial_access(...)` into the actual local composition
function (item 2 above) requires the offline-assertion-verification
code from `commercial_runtime/licensing_contracts/` to be ported or
reimplemented in Kotlin — a `UNIFIED_MOBILE_COMMON` gap, tracked in
`licensing-gap-ownership-matrix.md`, correctly out of scope for M7
(M7.17 stops at typed contract models, not verification logic).
