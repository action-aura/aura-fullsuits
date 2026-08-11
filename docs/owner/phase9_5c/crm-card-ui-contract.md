# Phase 9.5C — Milestone 6: Card UI Contract

## Implementation

`leads/list.html` renders each Lead as a `.crm-card` inside a
`.responsive-cards` CSS grid (`repeat(auto-fill, minmax(260px, 1fr))`) —
reflows naturally at any viewport with zero separate mobile stylesheet,
logical-properties-only CSS (mirrors correctly under RTL automatically).
Card fields: name, status badge, phone/email, priority, source,
next-follow-up date. No child-record data (contacts/interactions/notes)
is loaded for the list view — `list_own_leads()`/`list_all_leads()`
select only `Lead` rows, no eager-loaded relationships, avoiding N+1 by
simply not fetching what the card doesn't display.

## Employee vs. management view

Both use the same template; the only difference is which service
function backs the query (`list_own_leads()` — ownership-filtered — vs.
`list_all_leads()` — unfiltered, `leads.view_all` only), decided in
`leads/routes.py:list_leads()`. No separate "employee card"/"management
card" component exists — the data returned already reflects the correct
scope, so nothing further needs hiding at render time.

## Pagination, filters, sorting

Server-side pagination via the existing `paginate()` helper
(`page`/`page_size`/`total`/`total_pages`), status filter via query
string, deterministic ordering (`Lead.created_at.desc()`, unchanged from
the Phase 9.5A service functions). Employee-facing filters (status,
priority, follow-up due) are query-string driven in the current minimal
template; the fuller filter set the spec lists (product interest,
location-available, own/assigned/shared) is not yet built into the list
UI — tracked as a follow-up refinement, not silently claimed complete.

## No unauthorized count/metadata leakage

`list_own_leads()` applies `apply_ownership_filter()` *before*
`paginate()` computes its `total` count — the total reflects only
records the actor can see, never a global count with a filtered/smaller
row set (which would itself leak "there are N more records you can't
see").

## Customer cards

Not yet built as a separate `.crm-card` grid — `customers/list.html`
(pre-existing, from earlier Owner phases) still renders a plain table.
Retrofitting it to the card layout is a small, low-risk template change
deferred past this wave's time budget; the ownership-filtering fix
(Milestone 3) is the same regardless of which markup renders the rows,
so this does not affect security or the correctness of what's already
shipped, only visual presentation.

## RTL / English-Arabic

Every string in `leads/list.html` goes through `_()`/the shared
`lead_*_label()` helpers (Milestone 18); no hardcoded English text.
`<bdi dir="ltr">` wraps phone/email exactly as every other bidirectional
identifier in this codebase already does.
