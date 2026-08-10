# Phase 9.5D — Milestone 5: Quote Domain Contract

`app/commercial_sales/quotes.py`, built directly on the existing `Quote`/`QuoteLine` schema (Phase 9.5A, `app/models/commercial_sales.py`) — no new Quote model, per Milestone 1's audit.

## Functions

- `create_quote(fields, *, actor_employee_profile_id, actor_staff_user_id) -> Quote` — `DRAFT`, allocates a real number via `numbering.allocate_document_number("QUOTE")`, validates currency, defaults `valid_until` to +30 days.
- `add_quote_line(quote, *, plan_id, addon_id, quantity, ...) -> QuoteLine` — only on `DRAFT`; resolves the catalog item via `catalog_for_sales.describe_plan_for_sale`/`describe_addon_for_sale` (rejects inactive/expired/nonexistent items, rejects currency mismatch between the Quote and the catalog item); snapshots `price_version_id`/`unit_price`/`description` at creation time; computes the line's own total directly via `calculator.calculate_line()` (not by matching against a full-document recompute — see "real bug" below); recomputes the Quote header's `subtotal`/`discount_total`/`total` via `calculator.calculate_document()`.
- `remove_quote_line(quote, line, ...)` — only on `DRAFT`; recomputes header totals.
- `submit_quote(quote, ...)` — `DRAFT`→`SENT`; rejects an empty document (`EMPTY_DOCUMENT`).
- `cancel_quote(quote, *, reason, ...)` — `DRAFT`/`SENT`→`CANCELLED`; reason required.
- `record_customer_decision(quote, *, accepted, reason=None, ...)` — `SENT`→`ACCEPTED`/`REJECTED`; reason required on rejection; rejects if `valid_until` has already passed (an expired Quote cannot be accepted, full stop — not merely "cannot silently expire after acceptance," which is the separate guarantee `expire_stale_quotes()` provides).
- `expire_stale_quotes(as_of=None) -> int` — server-evaluated; only touches still-`SENT` Quotes past `valid_until`; an `ACCEPTED` Quote is structurally untouched (the query filters on `status == "SENT"`) even if its `valid_until` has since passed.

## Ownership

`app/leads/ownership.py::apply_ownership_filter()` extended with a `Quote` branch: creator-only (`created_by_employee_profile_id`) — Quote has no separate assignee concept, unlike Lead. Reuses the exact same shared helper every other CRM/commercial query uses; no parallel filter implementation.

## Real bug found and fixed during implementation

The first draft of `add_quote_line()` tried to find "this line's" computed total by matching `sort_order` against the full-document recompute's per-line results (`next(lr for lr in result.lines if lr.sort_order == sort_order)`). This is ambiguous whenever two lines share the same `sort_order` value — the default for every line unless the caller explicitly sets distinct values, which the tests didn't. Fixed by computing the new line's own total directly via `calculate_line()` against its own inputs (correct, since this Quote flow never applies a document-level discount — only per-line `discount_amount` is exposed — so a line's `line_net` is fully determined by its own inputs, independent of sibling lines). The full-document recompute is still used, but only for the Quote header's aggregate `subtotal`/`discount_total`/`total`.

## Test coverage

`tests/test_phase9_5d_quotes.py` — 11 tests: number generation + defaults, line-add total recomputation, DRAFT-only line mutation enforcement, empty-quote submit rejection, full accept lifecycle, reject-requires-reason, accepted-quote-cannot-be-cancelled, stale-version rejection, currency-mismatch rejection, inactive-catalog-item rejection, and the expiry boundary (only `SENT` quotes expire, an `ACCEPTED` quote past its `valid_until` is untouched).
