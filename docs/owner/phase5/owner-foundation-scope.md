# Phase 5 -- Aura Owner Foundation Scope

## What this phase builds
An independent internal web application ("Aura Owner Control Center", product code `AURA_OWNER`) under `owner/`, with its own PostgreSQL database, that lets Action Aura staff manage: staff accounts/roles, the commercial product/platform/version catalog, plans/prices/add-ons/entitlements, customer organizations/contacts, subscriptions, manual renewal/payment records, licenses (with secure one-time-reveal key issuance), installation/device records, activation-event records, an append-only audit log, an internal dashboard, and Owner's own PostgreSQL backup/restore.

## What this phase explicitly does NOT build
No connection to Retail or Clinic (no shared runtime import, no shared database, no live API call in either direction). No license **enforcement** inside either product. No public/external activation endpoint (kept behind `OWNER_EXTERNAL_API_ENABLED=false`). No VPS/cloud deployment. No telemetry ingestion. No WhatsApp/SMS delivery. No customer-facing portal. No Aura Core integration. Future-phase API contract *schemas* are prepared (Part R) as inactive specifications only.

## Authorization basis
Per Wave 1C's `docs/release/wave1c/owner-platform-entry-decision.md`: **YES, Owner Foundation Only** -- Aura Clinic cleared Controlled Paid Pilot (conditionally), zero unresolved P0/P1, Owner requires no product/patient data access by construction. This phase begins exactly, and only, the scope that decision authorized.

## Repository boundaries
- `aura-fullsuits/owner/` -- new, independent application (this phase).
- `aura-fullsuits/products/*`, `aura-fullsuits/commercial_runtime/*`, `aura-fullsuits/android/*` -- untouched.
- `AuraEnterprise/` (original monorepo) -- untouched, read-only, not referenced by Owner code.
- `owner_control_center/` -- a pre-existing empty scaffold (0 files, likely from earlier architecture planning under the product's official name "Aura Owner Control Center"). Left in place, untouched; not used as the build location because this phase's spec explicitly names `owner/` as the path. `pyproject.toml`'s stale `owner_control_center/tests` testpath entry is corrected to `owner/tests` as part of this phase (a config fix, not a Retail/Clinic change).

## Data Owner must never touch
Retail sales/invoice lines/inventory quantities/customer records; Clinic patient records/appointments/prescriptions/medical notes/invoices/payments; any local product `.db` file. Enforced structurally (no import path exists from `owner/` to `products/*` or `commercial_runtime/*`) and by explicit tests (Part W).

## Phase boundary
Stop completely after Phase 5. No Phase 6 (Licensing & Activation Service, live enforcement) begins without new, explicit authorization.
