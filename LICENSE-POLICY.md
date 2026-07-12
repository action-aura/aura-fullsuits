# License Policy

Aura FullSuits is proprietary commercial software owned by Action Aura. This file describes the *licensing model*, not open-source terms — there is no open-source license grant here.

## Current state (foundational, not enforcement)

As of this document, per-customer license *enforcement* is not yet implemented. What exists today:

- `commercial_runtime/identity/` — identity contracts (`customer_id`, `tenant_id`, `installation_id`, `device_id`, `product_code`, `edition_code`, `license_id`, `environment`). See `docs/architecture/identity-and-tenancy.md`.
- `commercial_runtime/licensing_contracts/` — interfaces reserved for the future license-issuance system. No enforcement logic lives here yet.

Do not represent any build of this software as having working commercial license enforcement until `docs/handover/NEXT-PHASE-LICENSING-AND-PACKAGING.md` items are implemented and tested.

## Rules that apply regardless of enforcement status

- No shared/hardcoded license key across installations.
- No single shared `tenant_id`, `company_id`, or encryption key baked into a build.
- Every installation gets its own generated identity (`installation_id`, `device_id`) at first run.
- One customer's installation must never be able to read or act on another customer's data or license state.

## Editions

`edition_code` values are reserved: `BASIC`, `PRO`, `ENTERPRISE`. No entitlement behavior is hardcoded against these values yet — that requires the policy layer described in `docs/commercial/future-license-generation-contract.md`.
