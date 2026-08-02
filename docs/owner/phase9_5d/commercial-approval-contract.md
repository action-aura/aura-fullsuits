# Phase 9.5D — Milestone 6: Commercial Approval Contract

`app/commercial_sales/approvals.py`, on the `CommercialApproval` model (migration `b7e4a2c91f30`) — a separate, per-document approval record, matching the existing `RenewalRequest`/`PendingActivation`/`PilotRecord` convention, deliberately not folded into `Quote.status` (Milestone 1's audit).

## Functions

- `create_approval_request(*, target_type, target_id, target_version_at_request, reason_code, requested_values, original_values, requested_by_staff_user_id) -> CommercialApproval` — `PENDING`.
- `decide_approval(approval, *, approved, decision_reason, decided_by_staff_user_id, current_target_version) -> CommercialApproval` — `PENDING`→`APPROVED`/`REJECTED`. Enforces, in this order: self-approval block (Non-Negotiable Principle 12 — checked in the service layer regardless of permission grant, since "UI hiding is not authorization"), valid transition, reason-required-on-reject, staleness (`target_version_at_request != current_target_version`).
- `cancel_approval_request(approval, ...)` — `PENDING`→`CANCELLED` (e.g. the requester edits the line to remove the exception before anyone decides).
- `unresolved_approvals_for_targets(target_type, target_ids) -> list[CommercialApproval]` — returns every `PENDING`/`REJECTED` approval for the given targets; `APPROVED`/`CANCELLED`/`EXPIRED` are excluded (resolved, not blocking). Used by the Quote acceptance gate.

## Integration with the Quote domain

`quotes.py::add_quote_line()` calls `catalog_for_sales.requires_line_approval()` after computing the line; if true, auto-creates a `PENDING` `CommercialApproval(target_type="QUOTE_LINE", target_id=line.id, ...)` with `reason_code` derived from which condition triggered (`PRICE_OVERRIDE`, `ZERO_PRICE_LINE`, `DISCOUNT_ABOVE_LIMIT`). `quotes.py::record_customer_decision(accepted=True)` calls `unresolved_approvals_for_targets("QUOTE_LINE", <this quote's line ids>)` and raises `APPROVAL_REQUIRED` if anything is still `PENDING`/`REJECTED` — an accepted Quote can never have an unresolved exception underneath it.

## Real bug found and fixed: version pinned to the wrong row

The first implementation pinned `target_version_at_request` to the **Quote's** `version`. Since `Quote.version` increments on every mutation — including `submit_quote()` and adding an unrelated sibling line — every pending approval was immediately, incorrectly marked stale before a decider could ever act on it (caught by `test_accept_succeeds_after_approval_granted`, which failed with `APPROVAL_STALE` on the very first correct-usage attempt). Fixed by giving `QuoteLine` its own `version` column (migration `c92d5f18a4e6`, additive — matching the existing per-child-row optimistic-lock pattern already used by `LeadContact.version`/`CustomerContact.version`/`CustomerLocation.version`) and pinning the approval to the **line's** version instead. Since this milestone has no `update_quote_line()` (line editing is out of scope — reserved for Milestone 18/19's `PATCH /quote-lines/{id}`), `QuoteLine.version` never actually changes after creation in the current codebase, so staleness never fires today — correct, since nothing has actually changed. The mechanism itself is proven correct by `test_stale_approval_rejected_after_quote_changed`, which manually bumps `line.version` to simulate a future edit and confirms `decide_approval()` still rejects it as stale.

## Test coverage

`tests/test_phase9_5d_approvals.py` — 8 tests: normal line creates no approval, price-override line creates a pending approval with the right reason code, acceptance blocked while pending, acceptance succeeds after a different staff user approves, self-approval forbidden (zero-price-line trigger), reject-requires-reason, staleness (simulated), double-decision rejected (`INVALID_APPROVAL_TRANSITION` on a second decision against an already-`APPROVED` row).
