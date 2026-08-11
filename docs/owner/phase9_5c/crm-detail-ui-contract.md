# Phase 9.5C — Milestone 7: Detail Page Contract

## `leads/detail.html` — sections implemented

1. **Overview** — identity, contact fields, source, priority, creator/
   assignee (assignee shown in assignment history), status, created/
   updated timestamps.
2. **Interactions** — list + log-new form (call/email/meeting/WhatsApp-
   manual-note/other).
3. **Follow-ups** — list with derived status badge + overdue badge,
   complete action (via the shared `crm_shared.complete_followup` route),
   create form.
4. **Notes** — visibility-filtered list (Milestone 11's
   `list_lead_notes_visible_to()`), visibility selector on the add form.
5. **Location** — capture button (real one-shot
   `navigator.geolocation.getCurrentPosition()`, no watch loop), manual-
   address fallback form, per-location verify action for unverified rows.
6. **Status history** — from/to/reason/actor/timestamp table.
7. **Assignment history** — assignee/reason/timestamp table.
8. **Conversion** — status-conditional "Convert to customer" action
   (shown only when `status == "QUALIFIED"`), duplicate-candidate display
   (privacy-masked per Milestone 5) when conversion is blocked by a
   possible match.
9. **Commercial summary** — not applicable to Leads (Customer-only per
   the governing spec); not present on this page.

No Quotes/Invoices/Payments/licensing-fulfillment UI exists anywhere on
this page — confirmed by the fact that `leads/detail.html` never
references any commercial-sales/licensing template block or route.

## `customers/detail.html` — extended this milestone

Added: interactions section, follow-ups section, visibility-filtered
notes (replacing a **real pre-existing bug**: the template previously
iterated `customer.notes` — the raw, unfiltered SQLAlchemy relationship
— completely bypassing Milestone 11's visibility rules the moment they
existed; now uses the `notes` context variable populated by
`list_customer_notes_visible_to()`), location section (capture +
verify), assignment-history section, and a reassignment form.

**Commercial summary for Customers**: not added this milestone —
`Customer`'s existing subscription/license relationships were not
wired into this template. This is a real, acknowledged gap (the spec
explicitly wants a Customer's *existing, already-authorized* commercial
summary shown) left for a follow-up pass given this wave's time budget;
critically, **nothing new was built** to fill it (no new Quote/Invoice/
Payment/fulfillment code), so the phase's hard boundary is not at risk
— the gap is an omission of a read-only display, not a scope violation.

## Confirmed: no forbidden commercial features added

Grep-verified: `app/leads/` and `app/customers/` import zero
`commissions`/`expenses`/`management_notes` modules, and the **only**
`commercial_sales` import anywhere in this phase's code is
`CommercialOperationsIdempotencyKey` — the shared idempotency-key ledger
table (used for `create_lead`/`convert`'s idempotency, documented since
Phase 9.5A as reused, not a Quote/Order/Invoice/Payment/Commission model
or service). No Quote/Order/Invoice/Payment/Commission model, service,
or route is imported or created anywhere in this phase's code.
