# Phase 9.5B-R2 — Domain Label Coverage Report

## Coverage method

Every status/enum value rendered in the 50 newly translated templates was
verified against its real source (service-layer `VALID_TRANSITIONS` dicts,
model column literals, or template filter-option lists) — not guessed.
Three real corrections were made during this process where an initial
assumption proved wrong (renewal statuses: 10 real values, not 6 assumed;
pilot statuses: 7 real values including EXTENDED, not 5; pending-activation
status: `PENDING_REVIEW` not `PENDING`) — each caught by reading the actual
template's filter-dropdown option list or the actual service module, not by
assumption.

## No stored value ever translated

Verified two ways: (1) every `*_label()` function's dict maps FROM the raw
code TO a translated string, never mutates the code itself; (2) the
bilingual functional-parity test creates a real customer and reads back its
`lifecycle_status` column directly via the ORM, confirming it is stored as
`"LEAD"` regardless of request locale.

## Safe-fallback verification

Every one of the ~30 new functions passes `code` itself as the fallback
(`.get(code, code)`) — verified by direct code review of every function in
`complete-domain-label-catalog.md`'s table, and exercised implicitly by
every render test that passes real (not synthetic-only) status values
through these functions without any function ever raising.
