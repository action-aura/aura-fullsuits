# Phase 9.5D — Milestone 7: Lead/Quote/Customer Boundary

## Real gap found: Quote had no way to represent a Lead origin

Non-Negotiable Rule 13: "A Quote may originate from: a qualified Lead, a confirmed Customer." Milestone 1's audit confirmed the Quote schema (Phase 9.5A) as authoritative and not to be duplicated — but on inspection, `Quote.customer_id` was `NOT NULL` with no `lead_id` column at all. There was structurally no way to create a Lead-based Quote. This is a proven, explicitly-mandated gap (not speculative), closed with an additive migration (`d15e6a3b7c92`): `customer_id` relaxed to nullable, `lead_id` added, CHECK constraint `(customer_id IS NOT NULL) + (lead_id IS NOT NULL) >= 1` (at least one, never neither).

**Not "exactly one," deliberately**: once a Lead-based Quote's boundary conversion runs, `customer_id` gets populated while `lead_id` is retained permanently as the historical origin marker — the same pattern already established by `Customer.converted_from_lead_id` in Phase 9.5C. `SalesOrder.customer_id` stays `NOT NULL`, unchanged — Non-Negotiable Rule 13's second half ("A Sales Order and all downstream documents require a confirmed Customer") means conversion must already be resolved by Order-creation time, which Milestone 8 enforces by calling this boundary before ever writing a `SalesOrder` row.

## `app/commercial_sales/lead_quote_boundary.py::resolve_customer_for_accepted_quote()`

The single call site for the boundary — Milestone 8's Order creation calls this, never `app/leads/conversion.py::convert()` directly.

1. Requires `quote.status == "ACCEPTED"` (`QUOTE_NOT_ACCEPTED` otherwise).
2. If `quote.customer_id` is already set (direct Customer-based Quote, or an already-converted Lead-based Quote), returns that Customer immediately — trivially idempotent by construction.
3. Otherwise, calls the canonical `app/leads/conversion.py::convert()` — the exact same function Phase 9.5C's CRM uses, never reimplemented. `DuplicateCustomerError` is surfaced as-is (not swallowed) so the route layer can present the same duplicate-review flow the CRM already has, never silently picking a candidate or leaking another employee's Customer details.
4. On success, sets `quote.customer_id` to the new Customer's id, bumps `quote.version`, commits.

## Retry-safety without a single spanning transaction

`convert()` commits its own transaction internally (Phase 9.5C's existing idempotency-key design); the boundary's own `quote.customer_id` update is a separate, subsequent commit. If a process crashes between the two, `convert()`'s own idempotency-key replay path (unchanged) ensures a retry with the same key returns the *same* Customer rather than creating a duplicate — the retry then successfully completes the Quote-linking step. This matches the same idempotent-retry discipline Milestone 13's fulfillment orchestration will need.

## Negative-space proof

`test_boundary_creates_no_commercial_fulfillment_documents` — matching Phase 9.5C's own precedent test style — confirms zero `Subscription`/`License`/`PaymentRecord`/`CommissionLedgerEntry` rows exist after a full Lead-based Quote acceptance-and-conversion cycle. The boundary creates a Customer and nothing else (Non-Negotiable Rule 1).

## Test coverage

`tests/test_phase9_5d_lead_quote_boundary.py` — 5 tests: full Lead-based conversion on acceptance (Lead ends `CONFIRMED`, Quote gets `customer_id` while retaining `lead_id`), rejection of a not-yet-accepted Quote, a Customer-based Quote returns its existing Customer directly (never touches conversion), idempotent replay (second call is a no-op read), and the negative-space fulfillment-document proof.
