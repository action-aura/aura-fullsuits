# Licensing Authority Source Map (M7.1)

Real evidence-priority order actually followed for the M7 audit, and
what was found at each tier. Every downstream M7 document cites the
tier-1/tier-2 sources below directly (file:line); tier-8/9 sources
(current mobile client, docs) are read for context only and never
override tier 1-2.

## Tier 1 — Executable Owner code (`owner/app/`)

Real, authoritative. Read in full or by targeted grep for this audit:

- `owner/app/models/` — `licensing.py`, `installations.py`,
  `customers.py`, `subscriptions.py`, `catalog.py`, `staff.py`,
  `licensing_service.py`, `activation_governance.py`, `employees.py`.
- `owner/app/licensing/` — staff-facing License CRUD/issue/transition/
  replace (`services.py`, `routes.py`).
- `owner/app/licensing_service/` — the real activation/check-in/
  deactivation/device-identity/assertion-signing engine
  (`activation.py`, `checkin.py`, `deactivation.py`,
  `device_identity.py`, `assertions.py`, `signing.py`,
  `idempotency.py`, `reason_codes.py`).
- `owner/app/licensing_admin/` — signing-key/device-key/offline-policy
  staff console (`routes.py`).
- `owner/app/installations/` — staff-facing Installation CRUD/
  transition (`services.py`, `routes.py`).
- `owner/app/commercial_ops/` — `device_slot_ops.py`,
  `activation_policy.py` (manual-approval gating), `expiry_scan.py`,
  `renewal_requests.py`, `pilot_lifecycle.py`.
- `owner/app/api_external/routes.py` — the real, versioned external
  device protocol (`/api/licensing/v1/*`).
- `owner/app/api/routes.py` — a separate, explicitly prototype-only,
  disabled-by-default "future product integration" surface
  (`/api/v1/*`); read and cited, never treated as live.
- `owner/app/auth/`, `owner/app/security/` — staff auth/MFA/RBAC/
  password/token modules gating every sensitive licensing route.
- `owner/app/customers/`, `owner/app/subscriptions/`,
  `owner/app/catalog/` — CRM/subscription/catalog services.

## Tier 2 — Migrations and constraints (`owner/migrations/versions/`)

Read as ground truth for real column types, nullability, uniqueness,
and check constraints wherever the ORM model alone was ambiguous —
principally `62e4adb0a7b9_initial_owner_schema.py` (the baseline
schema for products/platforms/plans/customers/subscriptions/licenses/
installations) plus the additive migrations that introduced
`SigningKey`/`DeviceKeyMetadata`/`SignedAssertion` and later CRM/
commercial-ops columns (`60f363ee66e8_phase_6_licensing_activation_
service_.py`, `3c0d51d82d8c_phase_9_5a_commercial_operations_.py`,
`911ac2a05c12_phase_9_5c_crm_ownership_history_.py`).

## Tier 3 — API routes

Enumerated directly from the tier-1 route files above (`licensing/
routes.py`, `licensing_admin/routes.py`, `installations/routes.py`,
`api_external/routes.py`, `api/routes.py`) — see
`remote-licensing-api-contract-map.md`.

## Tier 4 — `commercial_runtime` (real, deployable offline-verification package)

`commercial_runtime/licensing_contracts/` — the real, existing
client-side lease/assertion-verification core (`assertion_verifier.py`,
`policy_evaluator.py`, `trust_store.py`, `canonical.py`,
`device_identity.py`, `client.py`, `reason_codes.py`,
`trust_anchor.json`), plus its own 24-file real test suite. This is
tier 4, not tier 1, because it is a separate deployable from Owner
itself — but it is real, executed, tested code, not a design document,
so it ranks above `licensing_contracts` docs and above the current
Retail/Clinic client implementations.

## Tier 5 — `commercial_runtime/licensing_contracts` supporting docs

None found as a separate doc tree distinct from the code itself in
tier 4 — the module docstrings inside tier-4 files serve this role
(e.g. `assertion_verifier.py`'s own module docstring, `canonical.py`'s
"no import across deployables" rationale). Treated as tier-4 evidence,
not separately ranked.

## Tier 6 — Executed tests

`owner/tests/` (License/Installation/activation/check-in/deactivation/
concurrency/manual-approval/customer/subscription/catalog test files)
and `commercial_runtime/licensing_contracts/tests/` (24 files) — both
read and cited throughout M7.2-M7.16 as real, executed evidence of
actual behavior, ranked below the source they test but above any
tooling or prose description of the same behavior.

## Tier 7 — Release-manifest tooling

`owner/app/catalog/services.py::import_release_manifest`,
`owner/app/licensing_service/signing.py::export_signed_keyset_manifest`
— read for `mobile-release-version-contract.md` and the key-rotation
finding in `offline-license-lease-contract-audit.md`.

## Tier 8 — Current Retail/Clinic client implementations

The legacy Android app's own licensing client
(`LicensingCoordinator`/`OwnerClient`/`DeviceIdentity`/`Canonical`),
already audited in M6's own `presentation-authority-audit.md` item 5.
Cited by reference where relevant (installation-identity/credential
docs) rather than re-audited from scratch — no new finding in this
tier contradicts anything found in tiers 1-4.

## Tier 9 — Documentation (supporting only, never authoritative)

`docs/owner/phase5/owner-license-key-design.md`,
`docs/owner/phase6/external-api-forbidden-data-report.md` — read and
cited only to corroborate tier-1/tier-4 code findings (both docs
match the real code exactly; no doc-vs-code conflict was found).
`aura-fullsuits-phase9r` docs were not read for M7 — Phase 9R is
out of scope per the governing checkpoint's own instruction, and no
M7 finding depends on it.

## Deliberate absence noted

No `owner/app/api_external/` route, model, or schema file was found
to reference `IOS` as a valid platform anywhere (see
`platform-authority-audit.md`). No customer-facing authentication
model exists in any tier-1 file (see
`customer-authentication-gap-analysis.md`). Both are real, confirmed
absences, not omissions in this map.
