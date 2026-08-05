# Platform Authority Audit (M7.8)

Real canonical platform values, iOS readiness classification, and
proof that no ALL-platform/unknown-platform-fallback/case-insensitive
alias bypass exists.

## Real canonical platform values

`Platform` is a real DB-backed table (`app/models/catalog.py:27-31`,
`owner_platforms`), not a bare string. Real seeded rows today —
**exactly two**: `WINDOWS`, `ANDROID`
(`app/catalog/services.py:32`, `_CANONICAL_PLATFORMS`). Confirmed
historically canonical (no change across the Owner codebase's own
migration history — the initial migration
`62e4adb0a7b9_initial_owner_schema.py:50-58` already defines the
table with no seed data of its own; seeding happens exclusively via
`catalog/services.py`, a single source of truth).

## Every real enforcement point that constrains platform to these two values

1. `contracts/activation-request-v1.schema.json` —
   `"platform": {"type":"string","enum":["WINDOWS","ANDROID"]}`,
   `additionalProperties: false` on the whole request object.
2. `contracts/installation-registration-v1.schema.json` — same
   enum, on the not-yet-live registration contract.
3. `License.allowed_platforms` (`licensing.py:25`) — comma-separated
   string set at issuance from a form default of `"WINDOWS,ANDROID"`
   (`app/licensing/routes.py:59`, `app/templates/licensing/new.html:
   13`).
4. `activation.py:121-122` — `PLATFORM_NOT_ALLOWED` check: the
   submitted `platform` string must appear in
   `license_row.allowed_platforms.split(",")`.
5. `ProductPlatform` (`catalog.py:34-45`) — catalog-level "is this
   product offered on this platform at all", seeded 1:1 for
   WINDOWS/ANDROID per product (`catalog/services.py:95-102`,
   confirmed exactly 2 rows per product by
   `tests/test_catalog.py:16-23`).

## iOS trace — exhaustive, real, none wired to licensing

A case-insensitive grep across all of `owner/` for `ios`, plus a
literal `\bIOS\b` word-boundary grep, returns exactly these matches
and no others:

1. `app/models/activation_governance.py:17-20` —
   `DEVICE_POLICY_PLATFORM_CATEGORIES = ("WINDOWS", "ANDROID", "IOS",
   "MOBILE")`. A device-count-*policy category* constant, consumed
   only by `app/commercial_sales/device_policy.py::resolve_device_
   policy()` (a sales-quoting resolver operating on `Subscription`,
   not `License`/`Installation`) and exercised in
   `tests/test_phase9_5a_device_policy.py` — which in practice only
   ever instantiates `"WINDOWS"` and `"MOBILE"` categories, never
   `"IOS"` alone. This constant is **not** referenced anywhere in
   `app/licensing_service/`, `app/licensing/`, or `app/installations/`
   — confirmed by grepping those three directories for
   `activation_governance` imports (zero matches). It never joins to
   `owner_platforms`.
2. `app/models/employees.py:26` — `PRESENCE_PLATFORMS = ("WEB",
   "ANDROID", "IOS")`, used only by the **staff** presence-heartbeat
   feature (`app/api_operations/routes.py:149-168`) — tracks which
   client an internal Owner employee's own session runs on. Entirely
   unrelated to customer product licensing.
3. No other occurrence exists anywhere under `owner/` (verified by a
   second, independent lowercase-substring grep — zero additional
   hits beyond the two above).

## iOS readiness classification

**`SCHEMA_READY_BUT_UNSEEDED`**, with an important qualifier: the
schema readiness is *partial*, not full.

Reasoning:
- `owner_platforms` is a real, generic table (`platform_code`, `name`)
  — adding an `IOS` row is structurally trivial (no migration needed,
  just a new seeded row) → schema-ready at the `Platform` table level.
- `ProductPlatform` is equally generic → schema-ready.
- **However**, `License.allowed_platforms` and the JSON-schema
  contracts (`activation-request-v1.schema.json`,
  `installation-registration-v1.schema.json`) hard-enum to
  `["WINDOWS","ANDROID"]` at the *validation* layer, independent of
  the `owner_platforms` table's own flexibility. Adding an `IOS`
  `Platform` row alone would **not** make iOS activation work — the
  JSON-schema `enum` and any staff-UI default string would also need
  additive changes. This is real, additive Owner-server work
  (`OWNER_SERVER` in `licensing-gap-ownership-matrix.md`), not
  something achievable purely by seeding data.
- Not `ALREADY_CANONICAL` (iOS is not accepted today).
- Not `CONTRACT_READY_BUT_NOT_DATABASE_READY` (the reverse is true:
  the database/table shape is more flexible than the contract's fixed
  enum).
- Not `REQUIRES_ADDITIVE_OWNER_CHANGE` alone, because that label
  undersells how localized the change is — it's specifically an enum
  update in ≤3 real files, not a schema redesign.
- Not `BLOCKED_BY_RELEASE_AUTHORITY` — no release-manifest or
  version-gating logic references platform enums (see
  `mobile-release-version-contract.md`).
- Not `UNKNOWN_PENDING_EVIDENCE` — this reading is conclusive, not
  uncertain.

## Proof: no ALL-platform / unknown-platform-fallback / case-insensitive bypass exists

- `PLATFORM_NOT_ALLOWED` check (`activation.py:121-122`) does an exact
  membership test against `allowed_platforms.split(",")` — no
  wildcard, no `"ALL"` sentinel value was found anywhere in
  `app/licensing/`, `app/licensing_service/`, or the JSON schemas
  (grepped for `"ALL"`, `"*"`, `"ANY"` as platform-adjacent literals —
  no matches tied to platform logic).
- JSON-schema `enum` validation is case-sensitive by construction
  (JSON Schema `enum` performs exact string equality) — a lowercase
  `"windows"` or `"Android"` submission is rejected outright by schema
  validation before reaching `activation.py` at all, confirmed by the
  schema's own `enum: ["WINDOWS","ANDROID"]` (exact-case values only,
  no case-folding step in the request-validation pipeline was found).
- No `try/except`-style fallback to a default platform was found in
  `activation.py` around the platform check — an unrecognized platform
  value fails schema validation first (400) and never reaches the
  license-lookup/device-limit logic at all.

## Real test coverage

`tests/test_catalog.py::test_product_platform_mapping_seeded`,
`tests/test_phase6_activation_protocol.py` ("wrong product/platform
rejected" case).
