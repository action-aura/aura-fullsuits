# Phase 9.5D — Customer Acceptance Contract

## `quotes.py::record_customer_decision(quote, *, accepted, reason=None, ...)`

Already implemented in Milestone 5, gated by Milestone 6's approval-resolution check. Captures:

- `accepted: bool` — the decision itself.
- `reason: str | None` — required on rejection (`REASON_REQUIRED`), optional on acceptance.
- Implicit: `actor_staff_user_id` (who recorded the decision — the employee, on the customer's behalf, since no direct customer-facing portal exists this phase), `accepted_at`/`rejected_at` timestamp, and the audit trail's own `actor`/`timestamp`/`reason` fields.

No electronic-signature provider is built (explicitly out of scope). "Acceptance method" / "customer contact" / "reference" / "safe evidence attachment" fields mentioned in the governing spec's Milestone 7 text are not separately modeled this phase — `Quote.notes` (free text, already existing) is the only place such detail can currently be recorded; a dedicated structured acceptance-evidence model was not identified as a proven gap by Milestone 1's audit (no existing "secure attachment system" was found to reuse, and building a new one is explicitly out of scope: "Do not build an electronic-signature provider").

## What acceptance does NOT do

Per Non-Negotiable Rule 1 and the funnel contract's explicit "forbidden side effects" column: `record_customer_decision(accepted=True)` creates no Payment, Subscription, License, Installation, or Commission. It only transitions `Quote.status` to `ACCEPTED` (after the Milestone 6 approval gate passes). The Lead-to-Customer boundary conversion (Milestone 7's `resolve_customer_for_accepted_quote()`) is a **separate, explicit call** — not triggered automatically inside `record_customer_decision()` — made by Milestone 8's Order-creation flow, keeping "acceptance" and "customer resolution" as distinct, individually-auditable steps rather than one opaque transaction.

## Rejection is terminal for the Quote, not the relationship

A `REJECTED` Quote cannot create a Sales Order (`QUOTE_TRANSITIONS["SENT"]` allows no path back once rejected). The underlying Lead/Customer relationship is untouched — a new Quote (a fresh `create_quote()` call) can always be created against the same Customer, or the same Lead if it was never converted (Lead status remains whatever it was; rejection of a Quote does not change Lead/Customer lifecycle state at all).
