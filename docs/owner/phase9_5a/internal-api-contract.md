# Phase 9.5A Milestone 19 — Internal API Contract

## Namespace decision

`/api/operations/v1` — new, distinct from `/api/licensing/v1` (external product licensing, Phase 6/8,
untouched) and `/api/v1` (`external_api` blueprint, unrelated general prefix, Milestone 1's audit).
Employee/customer/commercial-operations concerns never share a URL prefix with the licensing API.

## Status this phase: contract only

Every endpoint below is a **planned contract** (see `openapi.yaml`'s `x-status: planned` on every
path) — none are routed in `owner/app/__init__.py` this phase, matching the "mobile-ready, not
mobile-built" / "foundation only" boundary. This document exists so a later phase implements against a
stable, reviewed contract rather than inventing shapes ad hoc.

## Endpoint groups (full detail in `openapi.yaml`; cross-referenced to the design doc that defines each)

- **Auth**: `mobile-session-contract.md` — login/MFA/refresh/logout/sessions/heartbeat.
- **Employees**: `employee-domain-model.md`, `employee-presence-contract.md` — list/detail/create/
  update/suspend/terminate/roles/presence.
- **Leads**: `lead-customer-domain-model.md`, `lead-conversion-contract.md`,
  `customer-card-api-view-model.md` — list/create/detail/update/status/assign/interactions/followups/
  notes/locations/convert.
- **Customers**: `lead-customer-domain-model.md`, `customer-detail-api-view-model.md` — list/detail/
  contacts/locations/notes/interactions/followups/commercial-history/licensing-summary.
- **Catalog**: `catalog-and-pricing-contract.md` — products/plans/price-versions/platform-device-rules
  (read-only for employees; management-only write, existing `catalog.manage_*` permissions).
- **Sales**: `commercial-document-lifecycle.md`, `payment-and-fulfillment-contract.md` —
  quotes/orders/invoices/payments/refunds.
- **Commissions**: `commission-domain-design.md` — own summary/management summary/ledger/approval/
  payout.
- **Expenses**: `mini-financial-ledger-design.md` — categories/create/submit/approve/pay/void.
- **Licensing control**: `multi-device-policy-design.md`, `device-policy-api-contract.md` —
  subscription device policy/installations/replacement/activation-review/exceptions/device-history
  (the last four are the **existing** real endpoints under `commercial_ops`/`licensing_admin` — this
  namespace does not duplicate them; a future phase may proxy/alias, not rebuild).
- **Dashboards/reports**: `admin-dashboard-metric-contract.md`, `daily-reporting-contract.md`.
- **Management notes**: `management-notes-design.md`.

## Standards applied to every endpoint (real, checked in `openapi.yaml`)

Public UUIDs only (no internal integer/DB-sequence IDs ever exposed — every model in this phase uses
`UUIDPKMixin`, matching the existing codebase-wide convention); versioned path (`/v1`); role-safe
response fields (per-endpoint, documented in the relevant view-model doc); pagination
(`page`/`page_size`/`total` envelope, Milestone 9); filtering/sorting query params documented per list
endpoint; ISO 8601 timezone-aware timestamps everywhere (`datetime.now(timezone.utc).isoformat()`
shape, matching Phase 9's own structured-logging convention); `Decimal` amounts serialized as JSON
strings, never floats (`"amount": "1200.00"`, never `1200.0`) — prevents float-precision corruption of
money in transit; optimistic-lock `version` field on every mutable resource; `Idempotency-Key` header
required on every sensitive write (issuance, payment confirmation, conversion, commission
approval/payout); `X-Correlation-Id` request/response header (reuses the exact real mechanism Phase 9
already built in `owner/app/observability/logging_config.py` — not a new correlation-ID system).

## What's explicitly not exposed

No endpoint returns a raw database auto-increment ID, a stack trace, a secret, or Clinic/Retail
customer-domain data — the last is structurally impossible (Owner holds none), not merely filtered.
