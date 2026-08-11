# Phase 9.5A — Existing Domain Reuse Matrix

| Proposed capability | Classification | Real evidence |
|---|---|---|
| Staff authentication/MFA/sessions/RBAC | ALREADY EXISTS AND REUSABLE | `owner/app/models/staff.py`, `owner/app/staff/seed_data.py` |
| Employee profile (phone/job title/department) | MISSING | `StaffUser` has no such fields |
| Employee presence/heartbeat | MISSING | No presence model anywhere |
| Customer record, contacts, addresses, notes | ALREADY EXISTS AND REUSABLE | `owner/app/models/customers.py` |
| Customer sales/support ownership assignment | ALREADY EXISTS AND REUSABLE | `Customer.assigned_sales_staff_id`/`assigned_support_staff_id` |
| Lead / pre-qualification pipeline | EXISTS BUT HAS WRONG DOMAIN OWNERSHIP | `Customer.lifecycle_status="LEAD"` conflates lead+customer into one table, no real pipeline (statuses/history/follow-ups) exists |
| Lead status history, assignment history, interactions, follow-ups | MISSING | No such tables |
| Lead → Customer conversion workflow | MISSING | No conversion service exists; `Customer.lifecycle_status` is never programmatically transitioned past `"LEAD"`/`"ARCHIVED"` today |
| Customer location capture | MISSING | `CustomerAddress` is a static mailing address, not a GPS/accuracy/captured-by-employee model |
| Product/plan/pricing/add-on catalog | ALREADY EXISTS AND REUSABLE | `owner/app/models/catalog.py` |
| Device allowance / device limit enforcement | ALREADY EXISTS AND REUSABLE (do not touch) | `Subscription.device_allowance`, `License.device_limit`, `DeviceSlotException`, `resolve_effective_device_limit()` |
| Per-platform device-limit policy (Windows max 1, mobile max 1, etc.) | MISSING (the actual new layer this phase adds) | No platform-specific sub-limits exist anywhere — only a single total `device_limit` |
| Approval-required vs automatic activation | EXISTS BUT REQUIRES EXTENSION | `PendingActivation`/`ActivationPolicy` (migration `0f8d55b753ed`) already model manual-approval activation at the license level; extending to a plan-level default is additive |
| Payments | ALREADY EXISTS AND REUSABLE | `PaymentRecord` + `PaymentCorrectionHistory` |
| Quote | MISSING | No model |
| Sales Order | MISSING | No model |
| Commercial Invoice (distinct from raw payment) | MISSING | `PaymentRecord` records a payment, not an invoice with line items/snapshot pricing |
| Refund | MISSING (only a status value, not a document) | No dedicated refund record |
| Commission plans/ledger | MISSING | No model |
| Expenses | MISSING | No model |
| Internal notification / alert queue | ALREADY EXISTS AND REUSABLE | `InternalNotification` — reusable for dashboard/daily-report alerting, not rebuilt |
| Management shared notes | EXISTS BUT HAS WRONG DOMAIN OWNERSHIP (partial) | `CustomerNote` exists but is customer-scoped only; a standalone, non-customer-bound management note is a genuinely different, missing concept |
| Daily activity snapshot | MISSING | No model; real scheduler infrastructure to run it on already exists (Phase 9) |
| Admin dashboard | EXISTS BUT REQUIRES EXTENSION | `owner/app/dashboard/services.py` already exists with real (if partly dead-code, see baseline doc) queries — extended, not replaced |
| Audit log + hash chain | ALREADY EXISTS AND REUSABLE | `owner/app/audit/services.py` — extended with new `action_code` values only |
| Scheduler (locking, timeout, structured output) | ALREADY EXISTS AND REUSABLE | `deploy/staging/run_scheduled_ops.py` (Phase 9) |
| Backup/restore | ALREADY EXISTS AND REUSABLE | `owner/app/system/backup.py` — new tables automatically covered by the existing full-database `pg_dump`, no change needed |
| Health/readiness | ALREADY EXISTS AND REUSABLE | `owner/app/health.py` — untouched |
| Structured/redacted logging | ALREADY EXISTS AND REUSABLE | `owner/app/observability/logging_config.py` (Phase 9) |
| Mobile authentication | MISSING (architecture decision only this phase) | No token-based auth exists; current auth is cookie-session only |
| `/api/operations/v1` namespace | MISSING | Only `/api/licensing/v1` and `/api/v1` (external_api, unrelated general prefix) exist |

## Deferred to a later phase (explicitly, not silently dropped)

Full commission payout execution, full invoice/order UI, mobile app implementation, background
location (forbidden entirely), full accounting/GL, e-invoicing, payment-gateway integration.
