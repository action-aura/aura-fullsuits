# Phase 9.5A — Gate Matrix (initial; finalized at Milestone 24+)

| Gate | Plan |
|---|---|
| Existing-domain audit before design | DONE — `existing-owner-capability-audit.md` |
| No duplicate customer/payment/product/subscription/license/audit system | Enforced by design per `duplication-risk-report.md` |
| Multi-device licensing policy — additive, does not weaken Phase 8 | Enforced — new layer sits above `resolve_effective_device_limit()`, never replaces it |
| Employee profile separate from auth account | Enforced by design (Milestone 4) |
| Lead separate from Customer | Enforced by design (Milestone 6), zero schema change to existing `Customer` |
| Record-level ownership/IDOR prevention | Real tests planned (Milestone 24) |
| GPS explicit, no background tracking | Enforced by design — no periodic/background capture field or job |
| Server-side authority for money/licensing/commission | Enforced by design — no client-supplied price/commission acceptance path |
| Everything important audited | New `action_code` values added to the existing real audit authority |
| Mobile-ready, not mobile-built | API contracts + OpenAPI only; no Flutter/Android/iOS project created |
| Real migration tested (empty + populated DB) | Planned Milestone 21 |
| Final regression | Planned, end of phase |
