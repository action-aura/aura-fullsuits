# Phase 9.5D — Milestone 1: Existing Commercial Authority Audit

Real repository audit (not assumption from memory), file:line-referenced. Central finding: **Phase 9.5A already modeled the entire Quote → SalesOrder → CommercialInvoice → Payment → Refund → Commission shape as database tables and RBAC permissions only.** Almost nothing in that chain has a service layer, route, or UI. Phase 9.5D is overwhelmingly an *activation* project — wiring services/routes/UI onto existing, unused schema — not a schema-design project.

## Catalog / pricing (all FULLY IMPLEMENTED AND REUSABLE)

- **Product** — `app/models/catalog.py:14`, service+routes in `app/catalog/`.
- **ProductPlatform** — `app/models/catalog.py:34`.
- **Plan** — `app/models/catalog.py:81`, `create_plan()` in `catalog/services.py:209`.
- **PlanPrice** (the price-history authority — there is no separately named "PlanPriceHistory" table) — `app/models/catalog.py:105`, `add_plan_price()` at `catalog/services.py:187`. Dated rows, `effective_until IS NULL` = current, never overwritten. **This is the price authority Quote/SalesOrder/Invoice lines must snapshot against (`price_version_id`) — do not build a second price-history mechanism.**
- **AddOn** — `app/models/catalog.py:118`, `set_addon_availability()`.
- **EntitlementDefinition/PlanEntitlement/AddonEntitlement** — `app/models/catalog.py:132-154`, resolved at runtime by `app/licensing_service/entitlements.py`.

## Fulfillment-adjacent authorities (all FULLY IMPLEMENTED AND REUSABLE)

- **Subscription + lifecycle** — `app/models/subscriptions.py:14`; `app/subscriptions/services.py` `create_subscription()`/`transition_subscription()`. **Gotcha**: `transition_subscription()`'s table deliberately blocks `EXPIRED`→`ACTIVE` (a security fix, not a renewal-workflow gap) — any 9.5D flow reviving a subscription must go through `commercial_ops/renewal_requests.py`'s `apply_renewal_request()`, never call `transition_subscription()` directly for a revival. `Subscription.sales_order_id` FK already exists (Phase 9.5A, additive) for fulfillment traceability.
- **RenewalRequest workflow** — `app/models/commercial_ops.py:49`; full DRAFT→QUOTED→AWAITING_CONFIRMATION→AWAITING_PAYMENT→PAYMENT_RECORDED→APPROVED→APPLIED state machine in `commercial_ops/renewal_requests.py`. **This is the closest existing analogue to Quote→Order→Invoice approval — study its transition-table + separate approve/apply-function pattern before designing 9.5D's own state machines.**
- **License + issuance** — `app/models/licensing.py:14`; `issue_license_key()` in `app/licensing/services.py` is the sole issuance authority (idempotent via `LicenseKeyIssuanceEvent`, plaintext key returned once only, never persisted, audits without the secret). **9.5D fulfillment must call this exact function — never construct a `License` row or write key fields directly.**
- **Installation** — `app/models/installations.py:17`, `register_installation()`/`transition_installation()`.
- **Pilot** — `app/models/commercial_ops.py:334`, full lifecycle in `commercial_ops/pilot_lifecycle.py`. Conversion-to-paid is deliberately routed through a real `RenewalRequest`, not a standalone action.
- **EmergencyExtension** — `app/models/commercial_ops.py:456`, `commercial_ops/emergency_extensions.py`. Short-lived, MFA-gated, checked after REVOKED precedence.
- **DeviceSlotException** (real enforcement) — `app/models/activation_governance.py:108`, enforced via `commercial_ops/device_slot_ops.py`. **DevicePolicyProfile resolver** (`activation_governance.py:142`, `commercial_sales/device_policy.py`) is FOUNDATION ONLY — reporting/preview only, `License.device_limit` remains the sole live enforcement authority.

## Payments (mixed)

- **PaymentRecord** — `app/models/subscriptions.py:127`, `record_payment()`/`correct_payment()` in `subscriptions/services.py`; corrections tracked via immutable `PaymentCorrectionHistory` (`commercial_ops.py:171`). Already has a nullable `commercial_invoice_id` FK (Phase 9.5A, additive) for invoice-linked payments. **FULLY IMPLEMENTED AND REUSABLE for storage/correction.**
- **Payment confirmation flow** — `payments.confirm` is a seeded, FINANCE-assigned permission with **zero code checking it and no `confirm_payment()` function anywhere**. **SERVICE PRESENT (storage) / ORCHESTRATION MISSING** — this is core net-new work: a `confirm_payment()` that marks `PaymentRecord` CONFIRMED, flips the linked `CommercialInvoice` toward PAID/PARTIALLY_PAID, and triggers commission posting. Build on top of `record_payment`/`correct_payment`, do not duplicate the table.

## Core people/audit/idempotency authorities (all FULLY IMPLEMENTED AND REUSABLE)

- **Employee ownership pattern** — `app/leads/ownership.py:24` `apply_ownership_filter(stmt, model, actor_employee_profile_id, *, all_permission_held)`. Currently branches only for `Lead`/`Customer`, `raise NotImplementedError` otherwise. **9.5D must add branches to this same function for Quote/SalesOrder/etc. — never write parallel ownership-filter logic** (the module's own docstring warns against exactly this duplication).
- **Customer** — `app/models/customers.py:14`; `Quote.customer_id`/`SalesOrder.customer_id`/`CommercialInvoice.customer_id` already FK straight to it.
- **Lead** + conversion — `app/leads/conversion.py`'s `convert()` confirmed (by import list) to never create Subscription/License/Installation/Quote/Invoice/Payment/Commission — only a Customer.
- **Audit** — `app/audit/services.py` `record()` is the sole permitted `AuditLog` writer (tamper-evident hash chain, auto-redaction, request-context-optional). Every 9.5D service must call this exact function.
- **Idempotency** — `app/models/commercial_sales.py:175` `CommercialOperationsIdempotencyKey` (unique on `(idempotency_key, operation_code)`), already used by `leads/conversion.py` (`OPERATION_CODE = "LEAD_CONVERSION"`), modeled on `issue_license_key()`'s own idempotency pattern. **One shared ledger across all commercial ops by design — mint a new `OPERATION_CODE` per new mutating operation, do not create a second idempotency table.**
- **StableCodeError pattern** — base class `app/commercial_ops/errors.py:19`. Real examples: `LeadError`/`CustomerCrmError` in `app/leads/errors.py` (both correctly subclass the shared base). **Known pre-existing inconsistency**: `commercial_ops/renewal_requests.py:75`'s `InvalidRenewalTransitionError` hand-rolls the same shape as a plain `ValueError` instead of subclassing `StableCodeError` (predates the Phase 9.5B-R3 extraction) — not to be copied as a template; use `LeadError`'s pattern instead.
- **RBAC permission registry** — `app/staff/seed_data.py`. **The full Phase 9.5D permission set is already seeded and role-assigned**: `quotes.create/approve`, `orders.create/approve`, `invoices.create/issue`, `pricing.override`, `payments.confirm`, `refunds.create/approve`, `commissions.view_own/view_all/calculate/approve/pay/reverse`. SALES gets create-side permissions + `commissions.view_own`; FINANCE gets confirm/issue/approve/pay/reverse; `pricing.override` granted to no role except via SUPER_ADMIN wildcard. **Do not add new permission codes for these core verbs — they exist and are role-mapped; wire routes to check them.**

## Commercial-sales document schema (the central finding — all FOUNDATION ONLY)

All in `app/models/commercial_sales.py`, migration `3c0d51d82d8c`, **zero services/routes/templates reference any of them** (grep-confirmed):

- **Quote / QuoteLine** (`:34`, `:58`) — `QUOTE_STATUSES = (DRAFT, SENT, ACCEPTED, REJECTED, EXPIRED, CANCELLED)`. Full schema: customer, creating employee, status, `quote_number` (unique, unpopulated), currency, subtotal/discount_total/total, `valid_until`, versioning, timestamps. Lines snapshot `price_version_id`, support price override + discount, CHECK forces exactly one of `plan_id`/`addon_id`.
- **SalesOrder / SalesOrderLine** (`:76`, `:96`) — `SALES_ORDER_STATUSES = (DRAFT, CONFIRMED, CANCELLED, FULFILLED)`. Optional `quote_id` FK (order can originate from quote or stand alone), `order_number` unique. `Subscription.sales_order_id` already points back here.
- **CommercialInvoice / CommercialInvoiceItem** (`:114`, `:136`) — `INVOICE_STATUSES = (DRAFT, ISSUED, PARTIALLY_PAID, PAID, VOID, REFUNDED, PARTIALLY_REFUNDED)`. Optional `sales_order_id` FK, `invoice_number` unique, subtotal/discount_total/tax_total/total, `due_date`. `PaymentRecord.commercial_invoice_id` already points here — the intended reuse point. Note: `commercial_ops` renewals create **no** invoice document today (RenewalRequest → apply, no invoice).
- **CommercialRefund** (`:156`) — `REFUND_STATUSES = (DRAFT, APPROVED, PAID, VOID)`. Required `commercial_invoice_id` FK, optional `payment_record_id` FK, amount/currency/reason, creator + approver fields.

## Commission foundation (MODEL PRESENT, POSTING/APPROVAL/PAYOUT SERVICE MISSING)

`app/models/commissions.py`: `CommissionPlan`, `CommissionRuleVersion`, `EmployeeCommissionPlanAssignment`, `CommissionLedgerEntry` (append-only, partial-unique-indexed one-EARNED-entry-per-payment), `CommissionPayoutBatch`, `CommissionPayoutLine`. Service `app/commissions/services.py`: `calculate_commission(rule, base_amount)` implements real Decimal-exact `PERCENTAGE_OF_PAYMENT`/`FIXED_AMOUNT` — **`PERCENTAGE_FIRST_SALE`/`PERCENTAGE_RENEWAL` raise `NotImplementedError`**, genuinely unimplemented despite being declared in `COMMISSION_RULE_TYPES`. `preview_commission()` is explicitly preview-only — never writes a `CommissionLedgerEntry`. **No function anywhere posts, approves, reverses, or pays out a real ledger entry.** This is core net-new work for Milestone 15.

## Gaps requiring genuinely new authority

- **Document numbering** — no sequence generator exists anywhere; `quote_number`/`order_number`/`invoice_number` columns are unique-constrained but nothing populates them. Nearest analogues: `license_keys.generate_license_key()` (deterministic prefix + version format) and `EmployeeProfile.employee_number` (caller supplies it, DB enforces uniqueness — no generator). **Concurrency-safety of whatever generator is built must be verified explicitly — nothing existing demonstrates this pattern.**
- **Generic Approval workflow** — no `Approval`/`ApprovalRequest` model exists. Established codebase convention instead: one `status` enum + dedicated `approve_*()` function + `*StatusHistory` table **per document type** (RenewalRequest, PendingActivation, PilotRecord all follow this). Quote/SalesOrder's status enums already exist waiting for exactly this service layer. **Building a shared generic Approval table would itself be the duplication-risk anti-pattern this audit exists to prevent — follow the per-document convention instead.**
- **Currency validation** — every commercial-sales currency column is a free `String(3)`/`String(8)`, not DB-enforced ISO 4217. Only `Lead.estimated_value`'s paired currency is actually validated (`app/leads/validation.py:24`, 3-char length check). Reuse that validation pattern, wire it into every new commercial-sales create path (currently wired nowhere for these models).
- **Tax** — `CommercialInvoice.tax_total` is the *only* tax field in the entire codebase; no per-line tax_rate/tax_amount, no jurisdiction/rule concept. FOUNDATION ONLY at header-aggregate level; REQUIRES NEW AUTHORITY for anything more granular.
- **Discount** — `Quote.discount_total`/`QuoteLine.discount_amount`/`CommercialInvoice.discount_total`/`CommercialInvoiceItem.discount_amount` exist; **`SalesOrder` has no header discount_total column** (only `SalesOrderLine.discount_amount`) — confirm this asymmetry is intentional before Milestone 8 adds order-level discount logic. No service anywhere enforces `subtotal - discount_total = total`.

## `commercial_ops/` module — precise boundary

Confirmed by reading every file: `renewal_requests.py`, `pilot_lifecycle.py`, `emergency_extensions.py`, `activation_policy.py`, `device_slot_ops.py`, `commercial_policy.py`, `expiry_scan.py`, `reconciliation.py`, `queues.py`, `timeline.py`, `state_resolution.py`, `assertion_fields.py`, `errors.py`, `preflight.py`. **Nothing here touches Quote/SalesOrder/CommercialInvoice/CommercialRefund/Commission** — this module is exclusively the Phase 8 renewal/pilot/emergency/device-governance domain. `app/commercial_sales/` (Phase 9.5A's own module name for this exact domain) currently contains only `device_policy.py` — **this is the intended home for all new Quote/SalesOrder/Invoice/Refund service+route code**, not `commercial_ops/`, not a new top-level package.

## OpenAPI

`docs/owner/phase9_5a/openapi.yaml` (997 lines) documents `POST /quotes`, `POST /commercial-invoices/{id}/issue`, `POST /payments/{id}/confirm`, `GET /commissions/own`, `POST /commissions/{id}/approve` with request/response schemas — **every path marked `x-status: planned`, none routed**. Reuse these schemas as the design contract for Quote/Invoice/Payment/Commission. **No path exists at all for SalesOrder or CommercialRefund, even as planned** — draft those from scratch matching the existing style.

## Preflight

`app/commercial_ops/preflight.py`'s `run_preflight()` has no commercial-sales-domain integrity check yet (parallel gap to the CRM one before Phase 9.5C added `_check_crm_domain_integrity`). Milestone 22 should add `_check_commercial_sales_domain_integrity` following that exact template.

## Modules read in full (verification depth)

- `app/licensing/services.py::issue_license_key()` — confirmed idempotent-replay-first, DRAFT-only precondition, single-transaction key+history+event write, secret never re-persisted or logged.
- `app/subscriptions/services.py` + `app/commercial_ops/renewal_requests.py` — confirmed `apply_renewal_request()` deliberately inlines subscription-mutation logic rather than calling `record_renewal()`, to keep the whole apply one transaction; 9.5D fulfillment reviving a subscription should match this "one transaction" discipline rather than composing two independent calls.
- Payment confirmation — confirmed no such function exists pre-9.5D; `record_payment()`/`correct_payment()` are the only two functions, neither invoice- nor commission-aware.
