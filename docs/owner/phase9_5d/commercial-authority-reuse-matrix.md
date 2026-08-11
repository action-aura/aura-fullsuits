# Phase 9.5D — Commercial Authority Reuse Matrix

Classification per the spec's 8-way taxonomy. Drives every later milestone's build-vs-reuse decision — cite this table before introducing any new model or service.

| # | Capability | Classification | 9.5D action |
|---|---|---|---|
| 1 | Product | FULLY IMPLEMENTED AND REUSABLE | Reuse as-is |
| 2 | ProductPlatform | FULLY IMPLEMENTED AND REUSABLE | Reuse as-is |
| 3 | Plan | FULLY IMPLEMENTED AND REUSABLE | Reuse as-is |
| 4 | PlanPrice (price history) | FULLY IMPLEMENTED AND REUSABLE | Snapshot `price_version_id` on every line; never build a second price-history mechanism |
| 5 | AddOn | FULLY IMPLEMENTED AND REUSABLE | Reuse as-is |
| 6 | EntitlementDefinition/PlanEntitlement/AddonEntitlement | FULLY IMPLEMENTED AND REUSABLE | Reuse as-is |
| 7 | Subscription + lifecycle | FULLY IMPLEMENTED AND REUSABLE | Reuse; never call `transition_subscription()` to revive EXPIRED — route through renewal pipeline |
| 8 | RenewalRequest workflow | FULLY IMPLEMENTED AND REUSABLE | Study as the state-machine template for Quote/Order approval flows |
| 9 | PaymentRecord (storage/correction) | FULLY IMPLEMENTED AND REUSABLE | Reuse `record_payment`/`correct_payment` |
| 9b | Payment confirmation orchestration | REQUIRES SAFE EXTENSION (net-new orchestration atop existing storage) | Build `confirm_payment()` in Milestone 10, wired to `payments.confirm` permission (already seeded) |
| 10 | License + issuance service | FULLY IMPLEMENTED AND REUSABLE | Call `issue_license_key()` directly; never write License rows |
| 11 | Installation | FULLY IMPLEMENTED AND REUSABLE | Reuse as-is; not directly in the fulfillment chain |
| 12 | Generic CommercialOperation entity | REQUIRES NEW AUTHORITY, if wanted — convention says don't | Do not build; follow one-table-per-document-type convention instead |
| 13 | Pilot | FULLY IMPLEMENTED AND REUSABLE | Reuse as-is |
| 14 | EmergencyExtension | FULLY IMPLEMENTED AND REUSABLE | Reuse as-is |
| 15a | DeviceSlotException | FULLY IMPLEMENTED AND REUSABLE | Reuse as-is |
| 15b | DevicePolicyProfile resolver | FOUNDATION ONLY (reporting only) | Do not treat as live enforcement; `License.device_limit` is authoritative |
| 16 | Employee ownership (`apply_ownership_filter`) | FULLY IMPLEMENTED, REQUIRES SAFE EXTENSION | Add Quote/SalesOrder/CommercialInvoice/CommercialRefund/CommissionLedgerEntry branches to this exact function |
| 17 | Customer | FULLY IMPLEMENTED AND REUSABLE | Reuse as-is |
| 18 | Lead + conversion | FULLY IMPLEMENTED AND REUSABLE | Call `conversion.convert()` for the Milestone 7 boundary; never reimplement |
| 19 | Audit service | FULLY IMPLEMENTED AND REUSABLE | Every new service function calls `record()` |
| 20 | Idempotency ledger | FULLY IMPLEMENTED AND REUSABLE | Mint new `OPERATION_CODE` values, reuse the one table |
| 21 | Generic Approval workflow | REQUIRES NEW AUTHORITY, but follow existing per-document convention | Per-document `status` enum + `approve_*()` + `*StatusHistory`, not a shared generic model |
| 22 | Document numbering | REQUIRES NEW AUTHORITY | Design one generator (Milestone 5), verify concurrency-safety explicitly |
| 23 | Currency handling | FOUNDATION ONLY / REQUIRES SAFE EXTENSION | Reuse `String(3)` column convention + `leads/validation.py`'s 3-char check pattern; wire into every new create path |
| 24 | Tax fields | FOUNDATION ONLY (header total) / REQUIRES NEW AUTHORITY (per-line) | Header-level only this phase unless a genuine need for per-line tax surfaces |
| 25 | Discount fields | FOUNDATION ONLY | Columns exist (except SalesOrder header — confirm intentional); build the enforcement service |
| 26 | Commission calculation | MODEL PRESENT, POSTING SERVICE MISSING | `calculate_commission()` reusable for 2 of 4 rule types; posting/approval/payout/reversal is net-new (Milestone 15) |
| 27 | Quote / QuoteLine | FOUNDATION ONLY | Build service+routes+UI on top of this exact schema — do not create a second Quote model |
| 28 | SalesOrder / SalesOrderLine | FOUNDATION ONLY | Same — build on top, no second model |
| 29 | CommercialInvoice / CommercialInvoiceItem | FOUNDATION ONLY | Same — build on top, no second model |
| 30 | CommercialRefund | FOUNDATION ONLY | Same — build on top, no second model |
| 31 | `commercial_ops/` module | FULLY IMPLEMENTED (its actual scope) / OUT OF SCOPE (as a home for new code) | New Quote/Order/Invoice/Refund/Commission code lives in `app/commercial_sales/`, not `commercial_ops/` |
| 32 | RBAC permission registry | FULLY IMPLEMENTED AND REUSABLE — permissions pre-seeded, unused | Wire routes/services to check existing codes; add none of the core verbs |
| 33 | StableCodeError pattern | FULLY IMPLEMENTED AND REUSABLE | Subclass `commercial_ops/errors.py::StableCodeError` directly, following `LeadError`'s pattern (not `InvalidRenewalTransitionError`'s) |
| 34 | OpenAPI spec | FOUNDATION ONLY | Reuse Quote/Invoice/Payment/Commission schemas; draft SalesOrder/Refund from scratch in matching style |
| 35 | Preflight | FULLY IMPLEMENTED, REQUIRES SAFE EXTENSION | Add `_check_commercial_sales_domain_integrity` following `_check_crm_domain_integrity`'s template |

## Net-new work this matrix identifies (the real 9.5D scope)

1. Document numbering generator (concurrency-safe).
2. Per-document approval service layer (Quote submit/approve, discount/price-override exceptions) — new `status` + `approve_*()` + history pattern, not a generic Approval model.
3. Quote/SalesOrder/CommercialInvoice/CommercialRefund service + route + UI layer — the models already exist and must not be duplicated.
4. `confirm_payment()` orchestration (Payment→Invoice status→commission trigger).
5. Payment allocation (Milestone 11 — genuinely new, no existing analogue found).
6. Refund service layer + entitlement consequence policy.
7. Fulfillment orchestration service calling `issue_license_key()` / subscription create-or-renewal pipeline — thin wrapper, no direct row writes.
8. Commission posting/approval/payout/reversal service (the ledger and 2/4 rule-type calculators exist; nothing writes a real entry yet). `PERCENTAGE_FIRST_SALE`/`PERCENTAGE_RENEWAL` remain `NotImplementedError` — confirm whether 9.5D's scope requires closing that gap.
9. `apply_ownership_filter()` extension for the 5 new models.
10. Currency validation wired into every new create path.
11. `_check_commercial_sales_domain_integrity` preflight check.
12. `/api/operations/v1` routes for all of the above (OpenAPI already partially specifies the shape for Quote/Invoice/Payment/Commission).
