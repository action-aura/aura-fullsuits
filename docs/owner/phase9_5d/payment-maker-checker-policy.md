# Phase 9.5D — Payment Maker-Checker Policy

## The rule

A sales employee may **submit** payment information (`submit_payment()`, requires `payments.create`, pre-seeded and granted to FINANCE per Milestone 1's audit — not to SALES by default, matching the existing RBAC posture, though the spec's narrative describes sales staff "submitting evidence" as a real workflow role Milestone 16/19 should account for at the route/permission-grant level, not this service). Only an authorized Finance/management user may **confirm** it (`payments.confirm`, seeded, FINANCE-assigned, no role except SUPER_ADMIN's wildcard holds it otherwise). The two actions are structurally distinct function calls (`submit_payment()` vs. `confirm_payment()`), never a single "record-and-auto-confirm" path.

## Enforced in the service layer, not just by permission grant

`confirm_payment()`'s `SELF_CONFIRMATION_FORBIDDEN` check is unconditional — it does not consult role or permission state at all, and fires even for a hypothetical account that holds both `payments.create` and `payments.confirm`. This mirrors `CommercialApproval`'s self-approval block exactly: a permission grant expresses "you may confirm payments," never "you may confirm payments except your own," so that second half must be a service-layer rule, not a route-layer permission check.

## Idempotency-by-rejection, not idempotency-by-replay

Unlike `Quote`/`SalesOrder`/`CommercialInvoice` creation (which use the shared `CommercialOperationsIdempotencyKey` ledger for true replay-returns-same-result idempotency), `confirm_payment()` uses a simpler, sufficient mechanism: a second confirmation attempt against an already-`CONFIRMED` payment is rejected outright (`PAYMENT_ALREADY_CONFIRMED`), not silently treated as a successful no-op. This is deliberate — confirmation has no multi-step creation side effects to replay (no child rows created), so the idempotency ledger's "return the original result" semantics aren't needed; "reject the redundant call with a clear, distinguishable error" is the simpler and equally safe choice for a single-field status transition.

## Cancellation after confirmation is forbidden — use Refund

`PaymentRecord.status` has no `CONFIRMED`→`VOIDED` transition available through this module (`reject_payment()` only operates on `PENDING`). A confirmed payment can only be undone through a `CommercialRefund` (Milestone 12), matching Non-Negotiable Rule 7 and the existing `PAYMENT_STATUSES` enum's own shape (`VOIDED` is reachable only from pre-confirmation states in real usage).
