# Phase 9.5D — Gate Matrix (living document)

This file is updated as each milestone completes with real, executed evidence — never marked PASS on inspection alone. Stub created at phase entry; populated incrementally.

| Gate | Status | Evidence |
|---|---|---|
| Entry Gate | PASS | `phase9-5d-baseline.md` — branch created from exact tag, all 7 historical tags verified, legacy repo confirmed read-only and unchanged |
| M1 — existing commercial authority audit | PASS | `existing-commercial-authority-audit.md` + `commercial-authority-reuse-matrix.md` + `commercial-duplication-risk-report.md`, commit `ff71341`; spot-verified against real seed_data.py |
| M2 — canonical commercial funnel | PASS | `commercial-funnel-contract.md` + `commercial-state-transition-matrix.md`, commit `00aa6e2`; built on verified existing Phase 9.5A design docs, not re-derived |
| M3 — financial calculation authority | PASS | `app/commercial_sales/calculator.py` + `errors.py`, 40/40 tests passing, commit `c64114a` |
| M4 — catalog/pricing for sales | PASS | `app/commercial_sales/catalog_for_sales.py`, 13/13 tests, commit `66070c5` |
| M5 — Quotes | PASS | `app/commercial_sales/numbering.py` (12-thread race proof) + `quotes.py`, migration `b7e4a2c91f30`, 6+11 tests, commits `6d541b3`/`4cf9265`/`0286c0e` |
| M6 — pricing/discount approvals | PASS | `app/commercial_sales/approvals.py`, deterministic commercial fingerprint (not version-counter, per explicit user closure requirement), migrations `c92d5f18a4e6`/`e4a8c1f6b0d3`, 31 tests incl. material-change-invalidates/unrelated-action-does-not, commits `ba43b5a`/`f014aa6` |
| M7 — customer acceptance / lead boundary | PASS | `app/commercial_sales/lead_quote_boundary.py`, canonical `leads/conversion.py` reuse, migration `d15e6a3b7c92` (Quote.lead_id), 5/5 tests, commit `3b60c73` |
| M8 — Sales Orders | PASS | `app/commercial_sales/sales_orders.py`, 9/9 tests, commit `0f36d15` |
| M9 — Commercial Invoices | PASS | `app/commercial_sales/invoices.py`, 8/8 tests incl. no-statutory-claim-fields structural proof, commit `1510675` |
| M10 — payment recording/confirmation | PASS | `app/commercial_sales/payments.py`, maker-checker self-confirmation block, 9/9 tests, commit `ed041af` |
| M11 — payment allocation | PASS | `app/commercial_sales/allocation.py`, real `SELECT...FOR UPDATE`, 10/10 tests incl. reversal-frees-balance, commit `3e90506` |
| M12 — refunds | PASS | `app/commercial_sales/refunds.py` + `entitlement_consequence.py`, self-approval block, migration `f2b7d4e91a63`, 12/12 tests, commit `f83f28d` |
| M13 — subscription/license fulfillment | PASS | `app/commercial_sales/fulfillment.py`, real row lock + incompatible-state guard + entitlement-consequence wiring, 15/15 tests (8 explicit closure items), commits `d85aad2`/`d79414a` |
| M14 — commission policy | PASS | `app/commissions/management.py` + `errors.py`, append-only rule/assignment versioning, 9/9 tests, commit `b07e5c7` |
| M15 — commission ledger | PASS | `app/commissions/ledger.py`, migration `a3c8e5d29f47` (allocation-basis uniqueness fix), wired into `allocation.py`/`refunds.py`, 13/13 tests (one per Commission Non-Negotiable Rule incl. real 8-thread concurrency race), commit `34b62b0` |
| M16 — RBAC/segregation of duties | PASS | `commercial-sales-sod-matrix.md`, FINANCE granted `quotes.approve`/`orders.approve` (real gap, previously SUPER_ADMIN-only), 6/6 SoD tests + 6/6 existing Phase 9.5A RBAC tests, `flask seed-rbac` applied to dev DB, commits `1aa3c9b`/`113c96f`. Full-suite regression (846 pass / 2 fail on M15's schema change, both fixed and reverified 5/5 green) |
| M17 — commercial dashboards | PASS | `app/commercial_sales/dashboards.py` (employee + finance scope, no generic management scope per Milestone 16 SoD), `commercial-dashboards-contract.md`, 3/3 tests incl. cross-employee isolation proof, commits `10eaaaa`/pending fix commit |
| M18 — operations API | PENDING | |
| M19 — web routes/UI | PENDING | |
| M20 — i18n/RTL | PENDING | |
| M21 — audit events | PENDING | |
| M22 — DB/migrations/indexes/numbering | PENDING | |
| Full regression (must meet/exceed 673 Owner + 46 files Retail/Clinic/commercial_runtime/licensing_contracts) | PENDING | |
| Legacy repo preservation (exit check) | PENDING | |
| Final tag | BLOCKED — tag name not received (spec truncated before it was stated) | |
