# Phase 9.5A — Domain Dependency Rules

1. A context's models live in their own `owner/app/models/*.py` file; a service function in context A
   may import and query context B's models (read dependency), but must never import context B's
   *service* module in a way that creates a cycle (verified informally per bounded-context-map.md's
   dependency diagram — no automated cycle-detection tool introduced this phase, consistent with
   "foundation only").
2. Routes call exactly one context's service layer for the primary action; if an action genuinely spans
   contexts (e.g. lead conversion touches Sales Pipeline + Customer Management), a dedicated
   cross-context service (`LeadConversionService`) owns that transaction, not the route handler.
3. No context may bypass another context's service to mutate its models directly (e.g. Commissions must
   call a Commercial Sales query function to read invoice/payment state, never write to
   `commercial_invoices` directly).
4. Money and licensing authority is never delegated to a dependent context: Commissions reads
   Commercial Sales' state but Commercial Sales never asks Commissions whether a sale is allowed.
   Licensing Operations reads Commercial Catalog but Commercial Catalog has no licensing concept.
5. Audit is written to by every context via the one shared `app.audit.services.record()` call — no
   context maintains its own audit trail.
6. Reporting (daily snapshot) only ever reads; it must never be a context another module depends on for
   correctness (a missing/late snapshot must never block a real business action).
