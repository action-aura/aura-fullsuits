# Phase 9.5C — Milestone 5: Duplicate Detection Privacy Model

## Real, tested guarantee

`app.customers.services.describe_duplicate_candidates_for_actor()` —
verified by `tests/test_phase9_5c_duplicate_detection.py`:

- `test_duplicate_candidate_invisible_to_unauthorized_actor_is_masked`:
  an actor holding only `customers.view_own` (not the assignee, not
  `view_all`) receives exactly `{"visible": False, "marker":
  "POSSIBLE_EXISTING_RECORD_REQUIRES_MANAGEMENT_REVIEW"}` for a matching
  Customer they don't own — the test asserts the Customer's real name and
  UUID are **not present anywhere** in the returned structure (`assert
  "Secret Corp" not in str(described)`).
- `test_duplicate_candidate_visible_to_owner_and_to_view_all_holder`:
  the assigned owner and any `customers.view_all` holder both receive
  the real candidate detail — the masking only applies to genuinely
  unauthorized viewers, not universally.

## Phone-matching normalizer — real, documented limitation

`normalize_phone()` strips all non-digit characters and compares the
result. This correctly matches formatting variants of the *same*
digit string (`"+962-79-123-4567"` vs `"962 79 123 4567"`) but does
**not** understand that a leading national trunk prefix (`0791234567`)
and its E.164 form (`962791234567`) represent the same real phone
number — that requires a real phone-number library (`libphonenumber`-
style country-code parsing), which this phase deliberately does not add
(bounded, explainable, deterministic scope per the governing spec — "do
not implement an opaque AI duplicate model," and a full E.164 parser is
its own scoped dependency decision, not silently bundled in here).
Documented as a known limitation, not silently hidden — verified by the
test's own comment and by choosing digit-identical (not just
"same real number") variants in the test itself.

## Where masking is NOT yet wired to a live route

`describe_duplicate_candidates_for_actor()` is real, tested, and ready,
but no web/API route calls it yet — that wiring happens in Milestone 16
when the `POST /leads` (create) and `POST /leads/<uuid>/convert` routes
are built. Both existing pre-9.5C call sites
(`customers/routes.py:create()` and `leads/conversion.py:convert()`)
still return unmasked candidates as before, unchanged by this milestone,
since neither currently has an unauthorized-viewer scenario: `customers/
routes.py:create()`'s duplicate check runs against the actor's own
about-to-be-created record with no access restriction on read at that
point, and `conversion.py:convert()`'s `DuplicateCustomerError` is
raised to the same actor performing the conversion (who must already
hold `leads.convert`), not rendered to a wider audience.
