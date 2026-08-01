# Phase 9.5C — Milestone 10: Follow-Up Domain Contract

## Schema decision: minimal additive extension, not a rebuild

`LeadFollowup`/`CustomerFollowup` (Phase 9.5A) had `due_at`,
`completed_at`, `notes`, and an assignee (`employee_profile_id`) —
already covers most of the governing spec's field list functionally.
One genuinely missing capability: **no way to distinguish "still open"
from "cancelled"** (both would otherwise show as `completed_at IS NULL`).
Fixed with one additive column per table: `cancelled_at` (nullable
timestamp), added via `migrations/versions/fe581c2bb967_*.py`.

**Deliberately not added**: a separate `title` field, a `priority`
column, or a persisted `status` enum. `notes` already serves as the
free-text description; a due-soon reminder system genuinely doesn't need
a separate one-line title distinct from its notes field for this phase's
real usage (staff working a pipeline, not a full task-management
product); and `status` is fully derivable (see below) — the governing
spec's own instruction ("do not persist OVERDUE when a derived state is
safer") is applied consistently to the whole status concept, not just
the OVERDUE case it names explicitly.

## Status — fully derived, never stored

```python
def followup_status(followup) -> str:
    if followup.cancelled_at is not None:
        return "CANCELLED"
    if followup.completed_at is not None:
        return "COMPLETED"
    return "OPEN"

def is_overdue(followup, *, as_of=None) -> bool:
    return followup_status(followup) == "OPEN" and followup.due_at < (as_of or utcnow())
```

Verified by test: `test_overdue_is_derived_not_stored` — asserts a
correctly-computed `is_overdue()` result for both a past-due and a
future-due row with no persisted `is_overdue`/`status` column or
property shortcut on the model class itself.

## Lifecycle rules (implemented, tested)

- `complete_lead_followup()`/`complete_customer_followup()`: rejects an
  already-cancelled follow-up (`FOLLOWUP_ALREADY_CANCELLED`); completing
  an already-completed one is a **safe idempotent no-op**, not an error
  (`test_followup_lifecycle_open_complete_idempotent`).
- `cancel_lead_followup()`/`cancel_customer_followup()`: requires a
  non-empty reason (`REASON_REQUIRED_FOR_FOLLOWUP_CANCEL`); rejects an
  already-completed follow-up (`FOLLOWUP_ALREADY_COMPLETED` — completion
  is terminal, cannot un-complete into cancelled); cancelling an
  already-cancelled one is idempotent.
- Concurrent completion: both functions read-then-write within a single
  `db_session.commit()`; the idempotent-no-op behavior means two
  concurrent "complete" calls both succeed with the same end state
  rather than racing into an error — an intentionally safer choice than
  an optimistic-version conflict for this specific low-stakes action
  (unlike Lead status transitions, completing a follow-up twice has no
  harmful effect to guard against).

## Queries

`list_own_lead_followups_due_today()` / `list_own_lead_followups_overdue()`
— both scoped to `employee_profile_id == actor` (real per-employee
isolation, verified by `test_due_today_and_overdue_queries_scoped_to_actor`
— employee B's follow-up never appears in employee A's due-today/overdue
results), paginated via the existing shared `paginate()` helper, ordered
by `due_at`. Customer-side equivalents (`list_own_customer_followups_*`)
are a direct mechanical mirror, added when Milestone 14's dashboard
needs them (not duplicated here speculatively).

## Timezone

`due_at`/`occurred_at` are `DateTime(timezone=True)` (already the case
in the Phase 9.5A model) — stored and compared as real timezone-aware
UTC instants throughout `engagement.py`. Business-day display
conversion to `Asia/Amman` (per the governing spec's explicit mention)
is a presentation-layer concern for Milestone 17's templates, not a
storage concern — see `follow-up-timezone-rules.md`.

## No external reminder delivery

Confirmed: zero code in `engagement.py` sends an email/SMS/push/webhook.
Follow-ups are visible only through the due-today/overdue query
functions above, consumed by a dashboard (Milestone 14) or list route
(Milestone 16) — pull, not push, per this phase's explicit scope
boundary.
