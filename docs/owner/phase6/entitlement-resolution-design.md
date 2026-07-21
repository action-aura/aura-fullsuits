# Phase 6 -- Entitlement Resolution Design

## Precedence (exactly Part O's recommended order, implemented as a strict override chain)
1. System safety limits (hard-coded sane bounds -- e.g. device count never negative, never absurdly large -- independent of any data row, a last-resort backstop).
2. Product constraints (platform/release-channel allowance from `owner_product_platforms`/the license's `allowed_platforms`/`allowed_release_channel_id`).
3. Plan entitlements (`owner_plan_entitlements` for the subscription's plan).
4. Active add-on entitlements (`owner_subscription_addons` joined to `owner_addon_entitlements`, **only** where the add-on's `availability_status == "AVAILABLE"` -- a DRAFT/PLANNED add-on's entitlement is never applied, per Part O's explicit instruction, closing the same "unbuilt feature silently enabled" risk Phase 5's catalog design already guarded against for the admin UI).
5. Authorized license-specific override (`owner_license_entitlements`, staff-set, highest precedence short of safety limits).
6. Deny-by-default fallback (an entitlement code with no resolved value anywhere in the chain resolves to its type's safe default: `false` for boolean, `0` for integer, empty list/string, `None` for date -- never silently `true`/enabled).

## Typed resolution
Each `EntitlementDefinition.value_type` (`boolean`/`integer`/`string`/`list`/`date`) is enforced at resolution time -- a stored JSONB value that doesn't match its definition's declared type is rejected (`resolve_entitlements` raises `EntitlementResolutionError`, logged and audited, never silently coerced).

## Determinism and snapshotting
`resolve_entitlements(license, at_timestamp)` is a pure function of its inputs (license, subscription, plan, active add-ons, license overrides, product/platform, timestamp) -- no hidden global state, no random ordering (dict keys sorted). The output is embedded verbatim as the assertion's `entitlements` field and never recomputed for the lifetime of that assertion, matching Part H's "immutable entitlement snapshot" requirement; a later entitlement change only takes effect on the *next* issued assertion (next check-in), never retroactively mutating an already-issued one.

## Rejected inputs (Part O's explicit reject list)
Unknown entitlement code referenced by a plan/add-on/override row (defensive -- shouldn't happen given FK constraints, but checked); invalid list-typed value (non-JSON-array); negative device limit; a retired (`RETIRED` availability status... not currently a modeled add-on status, mapped to non-`AVAILABLE`) add-on's entitlement; an unavailable feature whose definition exists but whose owning add-on is not `AVAILABLE`; a platform/release-channel constraint conflicting with the license's own `allowed_platforms`/`allowed_release_channel_id` (the narrower of the two always wins, never the broader).

## Source attribution for internal diagnostics only
Each resolved entitlement's `_source` (which precedence level produced the final value) is available to the internal "entitlement resolution preview" admin view (Part R) for staff debugging, but is never included in the client-facing assertion payload -- matching Part O's "without exposing unnecessary internal data to clients."
