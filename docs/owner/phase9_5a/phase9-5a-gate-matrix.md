# Phase 9.5A — Gate Matrix (final)

| Gate | Status | Evidence |
|---|---|---|
| Existing-domain audit before design | **PASS** | `existing-owner-capability-audit.md`, `existing-domain-reuse-matrix.md` |
| No duplicate customer/payment/product/subscription/license/audit system | **PASS** | `duplication-risk-report.md`; `PaymentRecord`/`Customer`/`InternalNotification` reused via additive columns only, zero parallel systems built |
| Multi-device licensing policy — additive, does not weaken Phase 8 | **PASS** | `resolve_device_policy()` (Milestone 22) never calls or modifies `resolve_effective_device_limit()`; explicit enforcement-wiring boundary honored (not wired into `licensing_service/activation.py` this phase); 4 real tests (`test_phase9_5a_device_policy.py`) |
| Employee profile separate from auth account | **PASS** | `EmployeeProfile` (own table, unique `staff_user_id` FK) — `StaffUser` untouched except 4 additive mobile-session columns |
| Lead separate from Customer | **PASS** | `Lead` genuinely new table; `Customer.lifecycle_status="LEAD"` default left untouched; `LeadConversionService.convert()` is the only code path that ever writes `Customer.lifecycle_status="ACTIVE"` |
| Record-level ownership/IDOR prevention | **PASS (foundation layer)** | `apply_ownership_filter()` real, tested (4 tests, `test_phase9_5a_ownership_idor.py`) — proves the building block every future route must use; full HTTP-layer IDOR testing NOT VERIFIED (no routes exist yet, honestly scoped in `milestone-24-security-tests.md`) |
| GPS explicit, no background tracking | **PASS** | Structural tests confirm no background-tracking-shaped column exists on `CustomerLocation`/`EmployeePresenceSession`; no periodic job created; `capture_location()` is the only write path, always an explicit call |
| Server-side authority for money/licensing/commission | **PASS** | `calculate_commission()` server-computed only; `CommercialInvoiceItem.line_total` server-computed, never client-supplied (schema-enforced, `price-authority-rules.md`); `resolve_device_policy()` report-only |
| Everything important audited | **PASS** | 10 real `action_code` values emitted by real service code, each with a passing test asserting the exact `AuditLog` row (`audit-event-catalog.md`); 18 reserved codes named for future routes |
| Mobile-ready, not mobile-built | **PASS** | API contracts + OpenAPI 3.0 (validated) only; zero Flutter/Android/iOS project files created this phase |
| Real migration tested (empty + populated DB) | **PASS** | Milestone 21 — 36 new tables, 4 real bugs found+fixed, tested against fresh `aura_owner_test`, dev, and the real populated `aura_owner_staging` (Phase 9 restore-drill data confirmed intact) |
| Real service layer, not routes-with-embedded-logic | **PASS** | 11 service modules, zero Flask routes added this phase — matches the spec's own explicit "do not put business logic in routes" instruction by simply not building routes yet |
| Final regression | **PASS** | 474/474 (423 pre-existing + 51 new Phase 9.5A), zero failures, zero skips |

## Real vs. reserved — final honest count

37 real, test-backed security/financial/privacy requirements (Milestone 24). 8 explicitly NOT VERIFIED,
all because the corresponding HTTP route or posting service does not exist yet — never because a claim
was asserted without running it. Full breakdown in `milestone-24-security-tests.md`.
