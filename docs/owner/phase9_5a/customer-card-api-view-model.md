# Phase 9.5A Milestone 9 — Customer/Lead Card API View Model

Applies identically to Lead and Customer list/summary endpoints — the same collapsed-card shape, since
the future UI treats them as one visual pipeline (a card just carries a `record_type: "LEAD"|"CUSTOMER"`
discriminator).

## Collapsed card response (list/summary endpoint)

```json
{
  "id": "uuid",
  "record_type": "LEAD",
  "name": "string",
  "status": "QUALIFIED",
  "status_badge_color": "amber",
  "phone": "string|null",
  "area": "string|null",
  "assigned_employee": {"id": "uuid", "display_name": "string"},
  "last_interaction_at": "2026-08-01T00:00:00Z|null",
  "next_follow_up_at": "2026-08-02T00:00:00Z|null",
  "product_interest": ["AURA_RETAIL"],
  "value": {"amount": "1200.00", "currency": "USD"},
  "overdue_follow_up": true,
  "quick_actions": ["call_logged", "status_change", "convert"]
}
```

`status_badge_color` and `quick_actions` are server-computed (not left to the client to derive from
raw status strings) — a role-appropriate, pre-filtered action list (e.g. `convert` only appears if the
caller holds `leads.convert` AND the lead is in a convertible status), matching Non-Negotiable
Principle 8 (server authority) applied to UI affordances, not just data.

## Pagination/filtering/sorting (required, standard across every list endpoint this phase contracts)

Query params: `page`, `page_size` (bounded max, e.g. 50), `status`, `assigned_employee_id` (management
only — an employee's own list endpoint ignores this param, always self-scoped), `search` (name/phone/
email, case-insensitive), `sort` (`next_follow_up_at`/`last_interaction_at`/`created_at`, `asc`/`desc`).
Response envelope: `{"items": [...], "page": 1, "page_size": 50, "total": 137}` — `total` is computed
from the same ownership-filtered query as `items` (never a separate unfiltered count — Milestone 7's
own anti-enumeration rule).

## No N+1

The card's `assigned_employee` and `product_interest` fields are populated via a single joined/batched
query per page (SQLAlchemy `selectinload`/explicit join), never one query per row — a concrete
implementation requirement for Milestone 22's real service, not just a nice-to-have.
