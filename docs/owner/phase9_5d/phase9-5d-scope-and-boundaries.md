# Phase 9.5D — Scope and Boundaries

## Business objective

Support the complete internal sales lifecycle for Action Aura products, plans, subscriptions, add-ons, and licenses:

Lead or Customer → Quote → Required Approval → Quote Acceptance → Customer Confirmation → Sales Order → Commercial Invoice → Payment Recording → Payment Confirmation → Subscription and License Fulfillment → Commission Earning → Commission Approval → Commission Payout Recording.

Separation preserved throughout: pricing / Quote / Sales Order / Commercial Invoice / Payment / Refund / Subscription / License / Installation / Commission — no object may silently impersonate another.

## Explicitly allowed this phase

Audit and reuse of Product, Platform, Plan, Price History, Add-on, and Entitlement Definition authorities; Quote lifecycle and versioning; pricing snapshots; discounts; approval workflows; customer acceptance/rejection; Sales Orders; Commercial Invoices; invoice payment status; payment recording/confirmation/allocation; refunds; commercial fulfillment (new subscription sale, renewal sale, add-on sale where existing models support it); license issuance through canonical services; commission policies/entries/approvals/payout recording/reversals; sales + management dashboards; `/api/operations/v1` commercial APIs; web forms/workflows; English/Arabic/RTL; audit; migrations when required; indexes + `EXPLAIN ANALYZE`; browser testing; financial-property testing; dependency scan; secret scan; infrastructure regression; full cross-product regression; final Phase 9.5D tag.

## Explicitly forbidden this phase

Expenses/expense approvals/petty cash; general ledger; chart of accounts; accounting journals; financial statements; bank reconciliation; payroll; supplier purchasing; inventory allocation; warehouse fulfillment; statutory tax engine; governmental e-invoicing; payment gateway; automatic bank API; credit-card processing; chargeback automation; shared management notes; WhatsApp; SMS; automatic email sending; e-signature provider; Flutter; Aura Owner Android; Aura Owner iOS; Phase 9R; remote deployment; public domain; real customer data (synthetic only).

Also explicitly out of scope per the phase title line: expense management, payroll, general ledger, journal entries, accounts payable, bank reconciliation, statutory tax accounting, governmental e-invoicing integration, legal fiscal receipt integration, inventory fulfillment, warehouse operations, management shared notes, payment gateway integration, automatic bank verification, WhatsApp, SMS, external email delivery, Aura Owner Android/iOS, Phase 9R, remote deployment, public production operation.

## The 20 Non-Negotiable Commercial Principles (verbatim intent)

1. **Quote is not an Order** — approval/acceptance creates no revenue, Subscription, License, or Installation.
2. **Order is not an Invoice** — not proof payment was received.
3. **Invoice is not Payment** — outstanding vs. collected stay separate.
4. **Payment recording is not confirmation** — sales staff may submit; only authorized Finance/management confirms.
5. **Confirmation is not direct DB fulfillment** — Subscription/License fulfillment must use existing canonical services; no direct row inserts from sales routes.
6. **Commission earned only from confirmed, allocated, non-refunded payment** — never from Quote/Order/Invoice totals.
7. **Refunds must reverse commercial consequences** — balances, commission reversal, entitlement consequence, all auditable.
8. **Financial totals are server-authoritative** — never trust client subtotal/tax/discount/total/paid/commission/balance.
9. **Decimal only** — no binary float for money.
10. **Price snapshots are immutable** — historical documents don't move when catalog price changes.
11. **One document, one currency** — no hidden FX conversion.
12. **Sales staff cannot self-approve exceptions** — price override, excessive discount, zero-price, refund, commission adjustment.
13. **Customer required before order fulfillment** — Lead-based Quote acceptance must convert/link through the canonical Phase 9.5C conversion service before an Order exists.
14. **No customer data leakage** — across Quote/Order/Invoice/Payment/Refund/Commission UUIDs, numbering, search, counts, filters, approval queues.
15. **No statutory claims** — these are operational commercial documents, not government-certified tax invoices / legally compliant fiscal invoices / Jordanian e-invoices / accounting journal entries.
16. **No hard delete of commercial history** — issued/approved/accepted/confirmed/paid/refunded/fulfilled/commissioned records stay auditable.
17. **Service layers stay request-independent** — stable machine errors in services, localization only at presentation boundary (the exact `StableCodeError` pattern already proven in Phase 9.5C).
18. **English and Arabic from the first commit** — no deferred Arabic.
19. **Audit sensitive actions** — pricing overrides, approvals, invoice issue/void, payment confirmation, refunds, fulfillment, commission approval/payout.
20. **No hardcoded Bahaa/Awab logic** — authenticated accounts/roles/permissions/employee profiles/management authorities only.

## Relationship to Phase 9.5C

Phase 9.5D builds strictly on top of the CRM foundation just closed. Retained and protected without modification unless a genuine extension is required: employee accounts/MFA/sessions/lifecycle, RBAC enforcement, EN/AR/RTL, stable service-error architecture, Postgres advisory-lock test isolation, Leads/Lead lifecycle, Customers, CRM ownership/isolation/duplicate-detection, contacts/interactions/follow-ups/notes, location capture/privacy, Lead-to-Customer conversion, CRM dashboards, `/api/operations/v1` CRM endpoints, migration `a1f9c3d76e02`'s index set, audit-chain authority, dependency/secret scanning, legacy-repository preservation discipline.

The canonical Lead-to-Customer conversion service (`app/leads/conversion.py`) is a hard dependency for Milestone 7 (Customer Acceptance / Lead-Quote-Customer Boundary) — it must be called, never reimplemented.
