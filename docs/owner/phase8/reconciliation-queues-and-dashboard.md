# Phase 8 — Reconciliation Engine, Operational Queues, Dashboard (Parts Q/S/T, Milestone 6)

## Reconciliation engine (Part Q)

`commercial_ops/reconciliation.py`'s `run_reconciliation()` mirrors `expiry_scan.py`'s shape
exactly: report-only by default, CLI-runnable (`flask commercial reconcile [--apply]`), dry-run
computes and returns every finding without writing anything. It never mutates any commercial record
itself -- every finding is either an INCONSISTENCY or a STALLED workflow surfaced for a human,
never something this engine "fixes."

Three independent checks in one pass:

1. **STATE_INCONSISTENT** -- reuses the existing, unmodified `resolve_commercial_state()`
   (Milestone 1) as the single definition of "consistent," rather than re-deriving a second one
   here. Walks every `License` with `status != "REPLACED"` (a replaced license is *expected* to
   look inconsistent against its now-superseded subscription state -- that's normal, not a
   finding), resolves the state, and flags any pair that lands on `CommercialState.INVALID`.
   `REVOKED` is its own dedicated branch in that function (always wins) and is never `INVALID`, so
   a revoked license is correctly never flagged here.
2. **STALE_APPROVED_RENEWAL** -- a `RenewalRequest` sitting in `APPROVED` (separation-of-duties and
   payment already cleared) for more than `stale_approved_renewal_days` (default 7) without being
   applied.
3. **STALE_PENDING_ACTIVATION** -- a `PendingActivation` sitting in `PENDING_REVIEW` for more than
   `stale_pending_activation_hours` (default 48) -- a manual-approval queue item nobody has acted
   on (Milestone 5).

`as_of` (date-precision) governs check 1, matching `expiry_scan.py`'s own convention; `now`
(datetime-precision, independently overridable) governs checks 2-3, since "approved 47 hours ago"
needs real precision, not a date boundary. Findings that write (non-dry-run) reuse Milestone 3's
`create_notification()` — deduped by a check-specific key so a scan that runs late or repeatedly
never spams the same finding twice.

## Operational queues (Part S)

`commercial_ops/queues.py`'s `get_queue_for_role(role_code)` is a pure read/aggregation layer --
never creates, transitions, or decides anything. Returns a `QueueSnapshot` (plain dicts, not ORM
objects) so a future route/UI layer can serialize it directly, the same reasoning
`dashboard/services.py` already follows for its own summary dict.

Per-role composition:

- **SALES** -- renewal requests still in the pre-finance pipeline (`DRAFT`/`QUOTED`/
  `AWAITING_CONFIRMATION`/`AWAITING_PAYMENT`), pilots needing action (`DRAFT`/`APPROVED`),
  notifications assigned to `SALES`.
- **FINANCE** -- renewal requests in `PAYMENT_RECORDED` (awaiting the separation-of-duties
  approval), notifications assigned to `FINANCE`.
- **SUPPORT** -- pending activations, active emergency extensions, active device-slot exceptions,
  notifications assigned to `SUPPORT`.
- **SUPER_ADMIN** -- the union of every category above, plus every open notification regardless of
  assigned role (oversight, not a restricted view).
- **VIEWER** -- **counts only, never items**. The role has no permission to act on any of these, so
  listing actionable items would be dead weight at best, confusing at worst.

No routes/UI yet -- service-layer only, the same discipline Milestone 4/5 applied to their own new
capability, deferred to Milestone 7's "full internal Owner UI workflow set."

## Dashboard extensions (Part T)

`dashboard/services.py`'s `get_dashboard_summary()` already existed (Phase 5/6, "commercial-
metadata only -- never Retail sales/inventory or Clinic patient/medical data") and is the correct,
established extension point rather than a new dashboard module. Six new counts added, all
aggregate, all already-legitimate Owner-domain data (nothing new crosses the product/Owner
boundary): open notifications, renewals awaiting finance approval, pilots needing action, pending
activations, active emergency extensions, active device-slot exceptions. Rendered as one new card
in the existing `dashboard/index.html` template, same style as the other summary cards.

## What Milestone 6 deliberately does not do yet

- No routes/CLI for viewing a role's queue interactively (only the Python service layer) -- part of
  Milestone 7's UI work.
- No scheduler/advisory-lock wiring for `reconcile`/`device-limit-scan`/`expiry-scan` running
  automatically -- CLI-runnable only, per the spec's own instruction (Milestone 3's Part R note
  applies identically here).
- No drill-down UI on the new dashboard counts (e.g. click "pending activations" to see the list) --
  the counts are real live aggregates today; the interactive queue views that back them are
  Milestone 7's job.
