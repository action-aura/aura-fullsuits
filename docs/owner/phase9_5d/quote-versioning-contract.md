# Phase 9.5D — Quote Versioning Contract

## Optimistic locking

Every mutation (`add_quote_line`, `remove_quote_line`, `submit_quote`, `cancel_quote`, `record_customer_decision`) increments `Quote.version`. `submit_quote`/`cancel_quote`/`record_customer_decision` accept an optional `expected_version`; when supplied and mismatched against the current row, `STALE_VERSION` is raised before any state change — matching the exact pattern already proven in Phase 9.5C's Lead/Customer version checks. Route handlers (Milestone 19) always supply the version the client last saw (a hidden form field / request body field), never optional.

## "Material revision" — Quote immutability once submitted

Per the funnel contract: line mutation (`add_quote_line`/`remove_quote_line`) is only permitted while `status == "DRAFT"`. Once `submit_quote()` moves a Quote to `SENT`, its lines are locked against further edits — there is no "edit a sent quote in place" operation. A genuine revision after submission is always a **new Quote** (a fresh `create_quote()` call, optionally referencing the superseded Quote's number in its `notes` field) — the old Quote is `CANCELLED`, never resurrected or silently mutated. This matches `commercial-funnel-contract.md`'s "SUPERSEDED" mapping: not a modeled `Quote.status` value, but a real Quote row (old) marked `CANCELLED` alongside a new Quote row, connected only by convention (a note), not a schema FK — the simplest correct implementation given the existing 6-value status enum is authoritative.

## Approval invalidation on revision (forward reference to Milestone 6)

`CommercialApproval.target_version_at_request` pins the exact `Quote.version` an approval request was made against. Milestone 6's approval-resolution logic checks this against the Quote's *current* version before honoring an `APPROVED` decision — if the Quote changed (new version) after the approval was requested, the approval is treated as `APPROVAL_STALE` and a fresh request is required. This is why `CommercialApproval` carries `target_version_at_request` as a first-class column rather than only a UUID reference.

## What is NOT versioned

Individual `QuoteLine` rows have no `version` column of their own — line-level concurrency is handled at the parent `Quote.version` level (any line mutation bumps the parent's version), matching the existing `LeadContact`/`CustomerContact` pattern precedent... except those *do* have their own `version` for primary-flag-demotion races specifically. QuoteLine has no equivalent race condition to guard (line creation/removal is always evaluated against `DRAFT` status alone, and two concurrent line-adds to the same Quote don't conflict with each other's data — each line is its own row); the parent Quote's version increment is sufficient to detect "something about this Quote changed since I last read it" for any UI reload/stale-form scenario.
