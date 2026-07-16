# Safe to Defer Until After Revenue

Everything in this list is Wave 2 or Wave 3 from `26`, plus items explicitly
out of scope for every phase to date. Deferring these is a reasonable
business decision **once** every item in `27` is actually done — this list
is not a license to skip `27`.

## Wave 2 — safe to defer, but not indefinitely

- **AUDIT-010** (test-pollution fix) — annoying for ongoing development, not
  customer-visible. Fix before it causes a real regression to slip through
  unnoticed, but doesn't block a sale.
- **AUDIT-015** (missing invoice states) — a real operational gap, tolerable
  short-term with a manual workaround (e.g. a support-team-only DB fix for
  the rare erroneous invoice) until fixed properly.
- **AUDIT-021** (`FLAG_SECURE`) — real but lower-severity; acceptable to defer
  a few weeks, not acceptable to defer indefinitely for Clinic given patient
  data.
- **AUDIT-026** (missing indexes) — safe to defer only as long as customer
  catalogs/patient counts stay small; must be revisited before onboarding any
  customer approaching thousands of products or patients.
- **AUDIT-027, 014, 025** — cosmetic/hygiene, safe to batch into routine
  maintenance.
- **The full-scale (50,000+ row) performance test** — reasonable to defer
  until a real customer's data volume approaches that scale, provided
  AUDIT-026's indexes are in place first as a baseline safeguard.

## Wave 3 — safe to defer well into a post-revenue phase

- Fine-grained RBAC beyond what exists today.
- Structured observability/metrics/health endpoints.
- Controlled/automatic update mechanism.
- Formal, rehearsed disaster-recovery drills (beyond the basic backup/restore
  capability required in Wave 0/`27`).
- Accessibility review and remediation.
- `commercial_runtime/licensing_contracts/` enforcement wiring.
- AUDIT-024, 028, 029 (low-severity hardening items) — real, worth fixing,
  but genuinely low-impact; bundle into any convenient future security pass.

## Explicitly out of scope for both this audit and any near-term phase

Per the task's own exclusions, repeated here for completeness: Owner Control
Center implementation, final license issuance, online activation enforcement,
subscription-expiration enforcement, customer-specific package generation,
payment-gateway integration, production VPS deployment, automatic remote
update execution, and Aura Core integration. None of these should begin until
at minimum everything in `27` is complete for the product being sold.
