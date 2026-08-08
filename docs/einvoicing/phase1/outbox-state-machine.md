# Outbox state machine

`commercial_runtime/einvoicing/outbox.py`.

```
    QUEUED --claim--> SUBMITTING --CLEARED----> CLEARED           (terminal)
       ^                   |
       |                   +--REJECTED----> FAILED_PERMANENT      (terminal)
       |                   +--RETRY-------> QUEUED   (backoff, attempt_count++)
       |                   +--PENDING-----> AWAITING_CLEARANCE
       |                   +--UNKNOWN-----> SUBMITTING_UNKNOWN
       |                                         |
       |                                         +--check_status: cleared --> CLEARED
       |                                         +--check_status: not received --> QUEUED
       +-------------------------------------------------+
    AWAITING_CLEARANCE --poll: cleared--> CLEARED
    AWAITING_CLEARANCE --poll: rejected--> FAILED_PERMANENT
    any non-terminal --kill switch / operator--> CANCELLED         (terminal,
                                                   sequence number retained)
```

Exhaustively tested: `test_state_machine.py` checks every `(from, to)`
pair in the full state set against the exact expected-legal set — nothing
legal is missing, nothing illegal sneaks through.

## Four independent layers make double-submission structurally impossible

1. **UNIQUE index on `invoice_ref`.** `enqueue()` is `INSERT OR IGNORE`; a
   duplicate enqueue is a silent no-op.
2. **`claim_due()` is a conditional `UPDATE ... WHERE status='QUEUED'`.**
   Only proceeds when exactly one row was actually updated. Proven under a
   real 20-thread concurrent race in `test_outbox_repository.py`.
3. **`submit_started_at` is written and committed before the network
   call**, by the caller (`worker.py`). A crash mid-flight leaves an
   unambiguous marker.
4. **An expired `SUBMITTING` lease reclaims to `SUBMITTING_UNKNOWN`, never
   straight back to `QUEUED`.** The worker must get an explicit
   `check_status()` answer — "not received" — before the row is ever
   claimable again. An `UNKNOWN` answer holds the row exactly where it is.
   Proven in `test_worker.py::test_crash_recovery_lease_reclaim_resolves_via_check_status_not_a_resubmit`,
   which asserts `submit_invoice` is never called during reclaim recovery.

## Stale vs. illegal transitions

`_transition()` distinguishes two failure modes:

- **Stale** (row already settled into a terminal state by a race or an
  earlier call): returns `False`, does not raise. Calling `mark_cleared()`
  twice on the same row — the second call arriving after the first already
  committed — is a normal, expected race, not a bug.
- **Illegal** (the row is in a normal, active, non-terminal state this
  method was never meant to fire from — e.g. `mark_cleared()` on a row
  still `QUEUED`, never claimed): raises `IllegalTransitionError`. This is
  a genuine caller bug, not a race, and must not be swallowed silently.

## Reconciliation

`reconcile_missing_sales` / `reconcile_missing_invoices` (product-side,
`einvoice_adapter.py`) find sales/invoices with no outbox row at all — the
enqueue hook is deliberately best-effort and can fail — and enqueue them.
Scoped by `enabled_at` so a sale/invoice from before the feature was ever
turned on is never retroactively submitted.

**Real bug found and fixed while wiring this (Steps 13/14):** `enabled_at`
is stored as UTC ISO-8601, but `sales.created_at` is naive local time and
`clinic_invoices.created_at` is naive UTC (SQLite's `CURRENT_TIMESTAMP`
default) — two different conventions, neither matching the stored
`enabled_at` format. A raw SQL string comparison between them was silently
always false (or always true, depending on direction) due to format
mismatch alone. Each product's adapter now converts `enabled_at` into that
specific product's own timestamp convention before it ever reaches SQL —
see `einvoice_adapter.py`'s `reconcile_missing_sales` /
`reconcile_missing_invoices` docstrings for the exact conversion and why
Retail's and Clinic's differ.
