# Owner Server Change Specification — iOS Platform + Multi-Device Reconciliation

**Not applied in this branch.** This is an exact, implementation-ready
specification for a future, separately-approved Owner change. No file
under `owner/` was created, modified, or migrated to produce this
document — every real citation below is read-only evidence from M7/M8.

## Suggested future Owner branch

```
feat/owner-ios-multidevice-policy
```

Suggested starting point (real, current Phase 9R HEAD, confirmed
unchanged across M7 and M8 entry — `external-workspace-entry-
fingerprints-m8.md`):

```
8c35768393d9407f72ef34aa93705f9c454453f7
```

This commit and its existing Phase 9R worktree are not modified by
this specification or by any command in this milestone — the branch
above does not exist yet and is not created here.

## 1. iOS platform catalog seed

Add `("IOS", "iOS")` to `_CANONICAL_PLATFORMS` in
`owner/app/catalog/services.py:32` (currently `[("WINDOWS", "Windows"),
("ANDROID", "Android")]`). A new Alembic migration (naming convention
matches real existing files, e.g.
`owner/migrations/versions/f5959fdb9738_phase_9_5e_management_notes_
missing_.py`) seeds one new `owner_platforms` row via the same
idempotent seed pattern `test_catalog.py::test_seed_is_idempotent`
already verifies for the existing two rows — re-running the seed must
remain a no-op on an already-seeded database.

## 2. AURA_RETAIL + IOS `ProductPlatform` mapping

Extend the seed loop at `owner/app/catalog/services.py:95-102` (which
currently creates a `ProductPlatform(supported=True)` row for every
Product × {WINDOWS, ANDROID} pair) to include IOS for `AURA_RETAIL`.
Real open decision, not resolved by this spec: whether `AURA_CLINIC`
also gets an IOS `ProductPlatform` row at the same time, or is seeded
`supported=False` until Clinic's own iOS readiness is separately
evaluated — flagged for the Product Owner, not decided unilaterally
here.

## 3. Release-manifest iOS validation

`import_release_manifest()` (`owner/app/catalog/services.py:133`)
currently only iterates `("WINDOWS", "windows"), ("ANDROID",
"android")`. Add `("IOS", "ios")` to that iteration, and extend the
`ProductVersion.platform_id` resolution to accept the new seeded row
from item 1. No change to `artifact_checksum_sha256` handling — same
mechanism, one more platform value.

## 4. Canonical platform API serialization

No route currently serializes a platform list directly except via the
`enum` in the JSON schemas (item 5) and `entitlement-response-v1.
schema.json`/`product-version-check-v1.schema.json` where a platform
string appears. Audit those two schemas for the same
`additionalProperties`-style enum constraint and extend identically to
item 5, so no response schema silently permits `IOS` while a request
schema still rejects it (an asymmetry that would itself be a new bug).

## 5. Activation acceptance for IOS only when mapped

Extend the `enum` in `owner/contracts/activation-request-v1.schema.json`
and `owner/contracts/installation-registration-v1.schema.json` from
`["WINDOWS","ANDROID"]` to `["WINDOWS","ANDROID","IOS"]`. Critically,
schema acceptance alone is **not** sufficient for real activation
eligibility — `activation.py:121-122`'s `PLATFORM_NOT_ALLOWED` check
against `License.allowed_platforms` must continue to gate real
issuance; no existing `License` row gets `IOS` added to
`allowed_platforms` merely because the schema now permits it. Real
per-customer opt-in remains a staff action via the existing license-
issuance UI (`app/licensing/routes.py:46-64`), unchanged.

## 6. Final-slot transaction locking

No change required. `activation.py:175-177`'s `SELECT ... FOR UPDATE`
row lock and `resolve_effective_device_limit()` are already
platform-agnostic (confirmed in `multi-device-license-policy-audit.md`
scenario 1) — adding a third platform value does not require new
locking logic, since the real enforced cap has never been per-platform.

## 7. iOS audit events

No schema change required — `ActivationEvent`
(`app/models/installations.py:89-107`) already stores `event_type`/
`result`/`reason_code` generically, and `Installation.platform_id`
already resolves through the same `owner_platforms` FK item 1 extends.
Confirm (not assumed) during implementation that no staff-facing
report/dashboard template hard-codes a `WINDOWS`/`ANDROID`-only
platform filter — a real, testable regression to check for, not
guaranteed absent by this spec alone.

## 8. iOS rate-limit keys

`RATE_LIMITED`/`Retry-After` behavior (`remote-licensing-api-contract-
map.md`) is keyed however Owner's existing rate limiter keys requests
today (not audited in M7/M8 — out of this spec's read evidence).
Real requirement: confirm the rate-limit key does not implicitly
assume a 2-value platform enum (e.g. a fixed-size bucket array indexed
by platform) before shipping — a real implementation-time check, not
resolved here since the rate-limiter's own internals were not part of
the M7/M8 audit scope.

## 9. iOS release-channel behavior

No change required — `ReleaseChannel`/`License.allowed_release_
channel_id` are channel-scoped, not platform-scoped
(`mobile-release-version-contract.md`); a channel already works
identically regardless of platform.

## 10. Backward compatibility for WINDOWS and ANDROID

Every change above is additive (new enum value, new seeded row, new
iteration entry) — no existing `WINDOWS`/`ANDROID` behavior, route, or
schema field is removed, renamed, or reinterpreted. Real regression
requirement: re-run `test_catalog.py`, `test_phase6_activation_
protocol.py`, and `test_phase6_concurrency.py` unmodified after the
change and confirm all still pass — a real, required, not-yet-executed
verification step for whoever implements this spec.

## 11. No ALL-platform behavior

This spec adds exactly one new discrete value (`IOS`) to every real
enum/list found in `platform-authority-audit.md` and
`platform-contract-reconciliation-m8.md` — it does not add a wildcard,
`ALL`, or `ANY` value anywhere, matching M7.8's own proof that no such
bypass exists today and must continue not to exist after this change.

## 12. Migration apply/rollback

The new migration (item 1) must define both `upgrade()` (seed the
`IOS` row + `ProductPlatform` rows) and `downgrade()` (remove them),
matching every other real Owner migration's own two-way pattern
(e.g. `60f363ee66e8_phase_6_licensing_activation_service_.py`'s own
`create_unique_constraint`/drop pair, cited in
`owner-licensing-authority-audit.md`).

## 13. Clean database seed

A brand-new database running every migration from scratch must end
with the `IOS` row present and `supported=True` for `AURA_RETAIL` (and
whatever the Product Owner decides for `AURA_CLINIC`, item 2) —
verified by extending `test_catalog.py::test_product_platform_mapping_
seeded`'s own real assertion (currently "exactly 2 rows per product")
to the new expected count.

## 14. Populated upgrade

An already-running Owner database (with existing Customers/
Subscriptions/Licenses using only `WINDOWS`/`ANDROID`) must apply this
migration without touching any existing row — the migration only
inserts new `owner_platforms`/`owner_product_platforms` rows, never
updates or deletes existing ones. No existing `License.allowed_
platforms` string is rewritten by this migration.

## 15. Duplicate-seed idempotency

The seed function (item 1/2) must remain idempotent under the same
real discipline `test_catalog.py::test_seed_is_idempotent` already
proves for the existing two platforms — re-running it against an
already-migrated database must not create a duplicate `owner_platforms`
row or violate the real `UniqueConstraint("product_id", "platform_id")`
on `ProductPlatform` (`app/models/catalog.py:34-45`).

## 16. Owner preflight updates

If Owner has a startup/preflight consistency check (not directly
audited in M7/M8 — real, open item), it must be extended to include
the new platform the same way it presumably already covers `WINDOWS`/
`ANDROID` — flagged for the implementer to locate and verify, not
assumed present or absent here.

## 17. Test matrix (for the future implementer)

- Seed idempotency (item 15).
- Clean-DB seed count (item 13).
- Populated-DB upgrade leaves existing rows untouched (item 14).
- Downgrade removes exactly the rows the upgrade added (item 12).
- Activation with `platform="IOS"` against a License whose
  `allowed_platforms` does NOT include `IOS` still real-rejects with
  `PLATFORM_NOT_ALLOWED` (proves item 5's two-layer gate).
- Activation with `platform="IOS"` against a License whose
  `allowed_platforms` DOES include `IOS`, on a Product/Platform with a
  real seeded `ProductPlatform(supported=True)` row, succeeds through
  the existing `process_activation()` logic unmodified.
- Existing `WINDOWS`/`ANDROID` activation tests (item 10) still pass
  unmodified.
- Release-manifest import accepts an `IOS` artifact (item 3).
- Rate-limit behavior confirmed platform-count-agnostic (item 8).

## 18. Remote validation requirements

Before this spec is implemented against a real, live Owner deployment,
the future implementer must independently re-verify every citation
above against the *then-current* `owner/` source — this specification
is only as current as M7/M8's own read-only audit; if Owner's schema
has changed since, the line numbers and exact function names above
must be re-confirmed, not assumed still accurate.

## Device-limit reconciliation (near-term rule, real and binding)

Per the M7-acceptance checkpoint's own instruction: do not perform a
destructive migration casually.

**Required near-term rule** (already true today, and must remain true
after this iOS change): `License.device_limit` remains the only
enforced runtime authority; the three other real, unreconciled columns
(`Plan.max_device_count`/`included_device_count`, `Subscription.
device_allowance`, the generic `max_devices` `PlanEntitlement`) are
detected by preflight (a real, future check the implementer must add
if one doesn't already exist — not confirmed present in M7/M8's own
audit) rather than silently trusted; every API response to a client
returns one resolved canonical `ResolvedDevicePolicy`-shaped value
(`resolved-device-policy-contract.md`), never the raw competing
columns.

**Required long-term options** (to be evaluated by the Product Owner,
not decided by this specification):
1. Deprecate the three unused columns outright (mark nullable/
   unused in a future migration, stop writing to them).
2. Migrate their historical values into one canonical policy model
   (e.g. a new `LicenseDevicePolicy` table) and stop dual-writing.
3. Remove them in a future, separately-reviewed destructive migration
   once (1) or (2) is complete and verified safe.
4. Preserve them permanently as historical commercial metadata only
   (e.g. for sales reporting on what was originally quoted), explicitly
   documented as non-authoritative from that point forward.

This specification does not select among these four options — that is
a real Product Owner decision requiring input beyond what M7/M8's own
read-only code audit can determine.
