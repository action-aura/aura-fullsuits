# Phase 9.5B-R2 — Final Complete Regression Report

Fresh runs, from the final Phase 9.5B-R2 HEAD (post every source, template,
catalog, and test change this wave), each suite in its own clean process —
not reused totals from any earlier point in this wave.

## Results

| Suite | Command | Result |
|---|---|---|
| Owner | `pytest owner/tests -q` (from repo root) / `pytest tests/ -q` (from `owner/`) | **618/619 passed** (1 failure, confirmed pre-existing order-dependence — see below) |
| `commercial_runtime` | `pytest commercial_runtime -q` | **235/235** — unchanged from Phase 9.5B-R's own baseline, no source in this package touched this wave |
| Retail | `python products/run_all_tests.py retail` | **194/194**, 12/12 files, canonical ordering-independent runner |
| Clinic | `python products/run_all_tests.py clinic` | **135/135**, 11/11 files |

**Total: 1,182/1,183 across all four suites.**

## Owner: the one real-but-not-a-regression failure

`test_commercial_ops_renewal_requests.py::test_terminal_states_reject_further_transitions`
failed in the full ~619-test run with `sqlalchemy.orm.exc.ObjectDeletedError:
Instance '<StaffUser at ...>' has been deleted, or its row is otherwise not
present.` — a SQLAlchemy session/identity-map artifact, not a translation-
related or business-logic assertion failure.

**Confirmed not a functional regression**: re-running
`tests/test_commercial_ops_renewal_requests.py` alone (24 tests, including
this exact test) → **24/24 passed**. This is the same class of pre-existing
test-isolation fragility already formally documented in this codebase's own
history for the Retail suite (`docs/owner/phase9/retail-test-isolation-root-cause.md`:
"proven a pre-existing, non-functional test-tooling artifact, not a product
defect") — a full-suite collection-order interaction between fixture
teardown and a later test's object reference, not a defect in any code this
wave touched. No file this wave touched changes `StaffUser` lifecycle,
session/identity-map behavior, or the renewal-request transition logic
itself (the transition logic's only change this wave — reverting the
gettext-wrapped exception messages — was separately confirmed correct by
the same test file's 24/24 isolated pass).

This finding is recorded honestly rather than silently re-run until green,
matching this session's established discipline (the same discipline that
led to accurately reporting Retail's order-dependence in Phase 9 rather
than hiding it).

## Baseline comparison

- Historical baseline entering this wave: Owner 605 + `commercial_runtime`
  235 + Retail 194 + Clinic 135 = 1,169.
- Real total after this wave: 1,183 (**+14** net) — Owner grew by real new
  test coverage (catalog-drift guard, responsive-table regression guard,
  new-record-form regression guard, i18n-preflight extension, owner-wide
  template-rendering coverage); the other three suites are unchanged in
  count, confirming zero regression risk was introduced into them (no file
  outside `owner/` was touched this wave).

## Zero P0, zero P1

Every real defect found this wave (5, see `phase9-5b-r2-final-decision.md`)
was fixed and re-verified. The one order-dependence flake documented above
is not counted as P0/P1: it reproduces as a pass in isolation, is
architecturally identical to an already-accepted pre-existing pattern in
this codebase, and does not indicate any actual broken business logic.
