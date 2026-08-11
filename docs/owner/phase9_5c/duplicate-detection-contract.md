# Phase 9.5C — Milestone 5: Duplicate Detection Contract

## Engine (reused, extended in Milestone 3)

`app.customers.services.find_duplicate_candidates(legal_name,
commercial_registration_reference, email, phone)` — deterministic,
explainable rules only, no opaque model:

- Exact `legal_name` match (case-insensitive).
- Exact `commercial_registration_reference` match.
- Exact contact `business_email` match (case-insensitive).
- Exact normalized-phone match against any contact's `business_phone`
  (digits-only comparison; added this phase — was a dead parameter
  before Milestone 3).

Returns a flat list of `Customer` candidates — **not** categorized into
exact/likely/possible/no-match tiers by the engine itself; the *caller*
decides what to do with a non-empty result (this phase does not add a
scored-confidence layer, since every current match rule is already an
exact match on a normalized field — there is no "fuzzy" tier to
represent yet, and inventing one without a real fuzzy-matching rule
behind it would be a false precision claim).

## Where it's called from

1. `customers/routes.py:create()` — existing, unchanged, non-blocking
   (shows candidates, requires `confirm_duplicate` to proceed).
2. `leads/conversion.py:convert()` — existing, unchanged, blocking
   (`DuplicateCustomerError` raised, conversion aborted) unless
   `existing_customer_id` is explicitly supplied (link-to-existing path).

## Privacy requirement — bounded response for an inaccessible match

Both existing call sites currently return the **full** candidate
`Customer` objects (name, etc.) to whichever employee triggered the
check. This is correct when the actor already has `customers.view_all`
or the candidate happens to be one they're assigned to — but is a real
information-disclosure risk when an employee with only
`customers.view_own` triggers a duplicate check that matches a Customer
they cannot otherwise see (a competitor-facing sales employee could
learn another employee's customer's name/contact exists, even without
opening its detail page).

**Decision**: candidate filtering happens at the presentation
boundary, not inside `find_duplicate_candidates()` itself (which stays a
pure, reusable data query). A new helper,
`app.customers.services.describe_duplicate_candidates_for_actor()`
(Milestone 16 route-layer wiring), partitions the raw candidate list:

- Candidates the actor can already see (per `_customer_visible_to()`,
  the same Milestone 3 helper) are returned in full, exactly as today.
- Candidates the actor cannot see are collapsed into a single bounded
  marker: `POSSIBLE_EXISTING_RECORD_REQUIRES_MANAGEMENT_REVIEW` — no
  name, phone, email, location, or UUID of the inaccessible record is
  ever included in the response the employee's browser receives.

A `customers.view_all` holder (management) reviewing the same duplicate
warning sees the full candidate, since they have general visibility.

## Override

An authorized management actor (holding `leads.convert` +
`customers.view_all`, or a new `customers.override_duplicate` permission
if a finer split is wanted — deferred to Milestone 16's actual route
implementation, not decided in the abstract here) may proceed with
conversion into a duplicate-flagged Lead by supplying
`existing_customer_id` explicitly (the conversion service's existing
link-to-existing path) — this already requires a reason and is already
audited (`LEAD_CONVERTED` with `existing_customer_id` in the before/after
state). No new override mechanism is needed; the existing explicit-link
path already satisfies "authorized override with reason + audit +
explicit confirmation."

## Not automatically blocking every possible match

Confirmed: neither the existing `customers/routes.py:create()` flow nor
this milestone's addition makes duplicate detection a hard block by
default — `customers/routes.py` already only warns (non-blocking,
`confirm_duplicate` to proceed); `conversion.py` blocks specifically for
conversion (a higher-stakes, harder-to-undo action creating a permanent
Customer record) but even then via the explicit override path, not an
unconditional rejection.
