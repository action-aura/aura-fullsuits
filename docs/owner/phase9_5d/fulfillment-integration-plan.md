# Phase 9.5D — Fulfillment Integration: Plan

## Governing constraint

Non-Negotiable Principle 5: payment confirmation is not direct database fulfillment. Subscription and License fulfillment must call the existing canonical services (whatever Milestone 1's audit finds under `owner/app/` for Subscription creation/renewal and License issuance) — never insert `Subscription`/`License`/`Installation`/`Entitlement`/license-audit-chain rows directly from a sales/commercial route or service.

## Approach

1. **Milestone 1's audit is the hard dependency here.** The fulfillment orchestration service (Milestone 13) is written only after the audit identifies the exact existing entry points (function signatures, required arguments, side effects, idempotency behavior) for subscription creation/renewal and license issuance. If the audit finds these are FOUNDATION ONLY (model exists, no callable service), that changes the milestone's scope honestly rather than silently building a parallel path.
2. **A thin orchestration layer, not a reimplementation.** `commercial_fulfillment.py` (or equivalent) checks eligibility (confirmed Customer, confirmed Order, issued Invoice, confirmed+allocated payment meeting the default `FULL_CONFIRMED_PAYMENT_REQUIRED` policy, no blocking refund, required approval complete, idempotency key unused) and then calls into the canonical service — it does not duplicate that service's own validation or persistence logic.
3. **Idempotency and retry-safety**: reuses the same `CommercialOperationsIdempotencyKey` pattern already proven in Phase 9.5C's `app/leads/conversion.py` — one idempotency key per fulfillment attempt, replay returns the prior result rather than double-fulfilling.
4. **No Installation side effect**: fulfilling a sale creates/renews a Subscription and issues/updates a License only — Installation remains a separate, later device-activation event, explicitly not triggered by this phase's fulfillment path.
5. **Failure recovery**: if the canonical service call fails partway (e.g. license issuance succeeds but the fulfillment record's own commit fails), the fulfillment record must be recoverable/retryable without creating a duplicate Subscription/License — this is why the eligibility check includes "no previous successful fulfillment for the same line" before ever calling the canonical service.

## What this rules out concretely

No `db_session.add(Subscription(...))` or `db_session.add(License(...))` anywhere under a `quotes/`, `orders/`, `invoices/`, `payments/`, or `commercial_fulfillment/` module. Every such row is created by calling into the audited canonical service, confirmed by grep before this milestone is marked done (matching the exact verification method Phase 9.5C used to prove `conversion.py` never touches commercial-fulfillment models).
