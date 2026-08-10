# Phase 9.5C — Milestone 6: List Query Contract

Reuses the existing `app.services.pagination.paginate()` helper
unchanged (same `DEFAULT_PAGE_SIZE=25`, same `page`/`page_size`/`total`/
`total_pages`/`has_prev`/`has_next` shape every other Owner list already
returns). No second pagination implementation was introduced.

Bounded page size enforced at the API layer
(`min(request.args.get("page_size", ...), 100)` in
`api_operations/crm.py`) — the web routes use the default only (no
user-controlled `page_size` on the HTML list page in this wave).

Deterministic ordering: `Lead.created_at.desc()` throughout — every list
query in `app.leads.services`/`app.leads.dashboard` specifies an
explicit `.order_by()`, never relies on implicit database row order.

Ownership-safe counts: `apply_ownership_filter()` runs before
`paginate()`'s `SELECT COUNT(*)` subquery in every actor-scoped list
function, so `total` always reflects the actor's real visible set.
