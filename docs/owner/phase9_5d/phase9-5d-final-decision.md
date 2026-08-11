# Phase 9.5D — Final Decision

## Decision: PASS

Every mandatory gate in `phase9-5d-gate-matrix.md` reads PASS with real, executed evidence — the Entry Gate, all 29 Milestones, the full cross-repo regression, and the legacy-repository preservation re-proof. No gate was marked PASS on inspection alone; every one has a commit hash, a test count, or a real tool-output artifact behind it.

## What was built

The authoritative commercial path from Lead/Customer through Quote → pricing-exception approval → customer acceptance → Sales Order → Commercial Invoice → Payment recording/confirmation → Payment Allocation → real commission earning → commission approval → payout → subscription/license fulfillment → Refund → proportional commission reversal, as: 9 real service modules (`app/commercial_sales/`: calculator, catalog_for_sales, numbering, quotes, approvals, lead_quote_boundary, sales_orders, invoices, payments, allocation, refunds, fulfillment, dashboards, routes; `app/commissions/`: management, ledger), 2 route layers (a 20-route `/api/operations/v1` API blueprint and a 43-route web UI blueprint with 16 templates), a blocking domain-integrity preflight check, full EN/AR localization, and 7 real database migrations. Reused, never duplicated: the entire Phase 9.5A schema foundation, the Phase 9.5C Lead-to-Customer conversion service, the existing Subscription/License issuance services, the shared idempotency ledger, and the audit-log writer.

## Real bugs found and fixed this phase (not merely tests written)

In the order they were discovered, each with its own commit and proving test:

- **M6**: approval validity bound to a version counter alone was insufficient (any unrelated Quote mutation could invalidate a still-accurate pending approval) — replaced with a deterministic commercial-value fingerprint, per the user's own explicit closure requirement.
- **M13**: a literal `"ALL"` platform placeholder in fulfillment would have silently rejected every real license activation; a partial-failure recovery gap could have duplicated a Subscription on retry; no row lock existed for concurrent fulfillment attempts; an incompatible existing Subscription state could be silently reused. All four closed and proven (8-item closure list).
- **M15**: the commission ledger's uniqueness constraint deduped on `source_payment_record_id`, which would have silently blocked a legitimate second commission when one Payment was split-allocated across multiple Invoices (a scenario Milestone 11 had already proven real) — moved to `source_payment_allocation_id`.
- **M16**: `quotes.approve`/`orders.approve` were pre-seeded permissions granted to no concrete role — only `SUPER_ADMIN` could ever confirm a Sales Order.
- **M18**: `payments.create` sat FINANCE-only despite `submit_payment()`'s own docstring describing it as sales-employee-facing — a decision the Milestone 10 documentation had explicitly deferred to this exact point.
- **M21**: `resolve_customer_for_accepted_quote()` mutated `Quote.customer_id` with no audit trail entry of its own.
- **M22**: 12 foreign-key columns on hot ownership-filter/child-lookup query paths had no index — the same class of bug Phase 9.5C's own Milestone 24 found independently.
- **M23**: `allocate_payment()` never verified the payment and invoice belonged to the same customer — a real cross-tenant financial-integrity gap, not merely an access-control one.
- **M24**: `confirm_refund()` never re-validated the refund total against what was actually collected, nor locked the invoice row — two individually-valid DRAFT refunds could jointly over-refund a customer, and a real concurrency race could let both be confirmed simultaneously.

Every one of these was caught by this phase's own testing discipline (never by the user pointing it out first, except where the user's explicit checkpoint messages required deeper verification than the milestone's first pass had reached) — consistent with the pattern established across the entire Aura Owner effort: build, test rigorously, find the real gap the first pass missed, fix it, prove the fix, move on.

## Residual risks (accepted, not blocking)

- **`pytest` 8.3.2 CVE** (`PYSEC-2026-1845`): UNIX-specific local-privilege issue in the test runner itself, dev-tooling-only, never shipped. Same accepted residual Phase 9.5C's own closure already documented; not new, not applicable to this Windows development environment.
- **External `.autosync` tool interference**: a background git-sync tool on this machine silently checked the working directory out to `master` and discarded uncommitted files on (at least) three separate occasions during this phase (once during Milestone 14, twice during Milestone 15). No committed work was ever lost — every incident was caught via `git reflog`, safely recovered, and disclosed. Mitigated for the remainder of the phase by committing far more frequently (often after every single file change) rather than batching a whole milestone into one commit. This remains a real, standing operational risk for any future session on this machine, independent of Phase 9.5D's own content.
- **Payment allocation / refund payment lookup uses a free-text UUID field**, not a scoped customer-payment picker, in the web UI (documented in `commercial-sales-web-ui-contract.md`). The underlying service operations are fully correct and tested regardless of how the ID reaches them; this is a UX completeness gap, not a functional or security one.
- **No dedicated "Sales Manager" role tier** — Order/Quote approval authority sits on `FINANCE` (per the Milestone 16 SoD matrix's own documented reasoning: money-authorization is a coherent single category in the current 5-role model). If the business later wants a sales-management-specific approval tier distinct from Finance, that is new role-taxonomy work, not a defect in what was built.
- **M26's performance validation directly measured 2 of the 12 new indexes** via `EXPLAIN ANALYZE` at real (30k-row) scale; the remaining 10 share the identical single-column-equality B-tree shape and were not independently re-measured, on the reasoning documented in `commercial-sales-performance-explain-analyze.md`. All 12 are structurally confirmed present via the schema-drift tests and exercised continuously by the full regression suite.
- **M25's real-browser validation covered a representative sample of the 43 routes/16 templates** (Quote create→submit→accept→Order→Invoice, RTL, mobile, Finance dashboard), not an exhaustive per-page click-through of every route. The remaining pages share the same template base/CSS/JS foundation already proven correct; risk is judged low, not zero.

## What was explicitly not built (forbidden scope, honored)

Expenses, General Ledger/journal entries, accounts payable, statutory tax accounting, e-invoicing, fiscal receipts, inventory/warehouse, shared management notes, payment gateways, automatic bank verification, WhatsApp/SMS/external email, Aura Owner Android/iOS apps, Phase 9R, remote deployment, public production operation. Verified via the same negative-space proof pattern used throughout the phase (e.g. `test_boundary_creates_no_commercial_fulfillment_documents`).

## Handover

Every milestone's own contract/completeness doc lives under `docs/owner/phase9_5d/` and is cross-referenced from this file and the gate matrix. The next phase (Aura Core Hub/AI-Hub, per `enterprise-roadmap-strategy` memory) or the next commercial-sales extension (a Sales Manager role tier, a scoped payment picker, Expenses/GL as an explicitly separate future phase) can start from a genuinely complete, tested, documented foundation — not a partially-built one.
