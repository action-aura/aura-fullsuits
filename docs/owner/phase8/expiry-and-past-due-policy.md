# Phase 8 — Expiry, Past-Due, and Notification Design (Parts G/H/I/J/R, Milestone 3)

## Commercial policy vs. technical offline policy — kept strictly separate

`CommercialPolicy` (`owner_commercial_policies`) governs *commercial* timing: expiry-warning
offsets, when a subscription becomes `PAST_DUE`, how long its payment grace lasts, and whether the
scan job may auto-transition it to `EXPIRED` once grace is exhausted. This is a completely
different table and concept from the existing `owner_offline_policies` (Phase 6/7): check-in
interval, offline grace, warning-before-restricted at the *installation* level. Part I is explicit
these must never be conflated — an Owner outage is not unpaid status, an unpaid status is not a
cryptographic failure, an expired assertion may still be refreshable during a valid subscription,
and a valid cached assertion may continue offline per the signed technical policy regardless of
commercial state. Nothing in `CommercialPolicy` reaches the product side directly; it only drives
`Subscription.status` transitions and notification generation on the Owner side. Entitlements and
assertions still flow through the unchanged Phase 6/7 pipeline.

## Policy resolution

`resolve_policy_for_subscription()`: product-specific active policy first (`product_id` matches,
`effective_date <= as_of < retired_date`), falling back to the global default
(`product_id IS NULL`). Returns `None` if neither exists — the expiry scan treats that as
`SKIPPED_NO_POLICY` for that subscription, never as license to guess (deny-by-default). No
per-subscription policy assignment column exists yet (deliberately — see the model's own
docstring): policy resolution is a live lookup, so correcting a policy's rules retroactively
affects every subscription using it without a migration touching every subscription row.

## The expiry scan job

`run_expiry_scan(as_of, dry_run, actor_staff_user_id)` walks every subscription in `ACTIVE` or
`PAST_DUE` status with a set `end_date`, and for each one:

1. **Warning notifications** (Part G): for every offset in the policy's `warning_offsets_days`
   that `days_until_end` has crossed, create (or, on a repeat run, no-op against) a notification
   keyed by `dedup_key = f"SUBSCRIPTION_EXPIRY_WARNING:{subscription_id}:{offset}"`. A scan that
   runs late or was skipped for a few days still catches up on every offset it missed, without
   ever duplicating one it already created — the `UNIQUE` constraint on `dedup_key` is the actual
   guarantee, not an application-level check-then-insert (which would itself race).
2. **Status transitions** (Part I): purely date-driven.
   - `ACTIVE` past its `end_date` by at least `past_due_start_days`: transitions to `PAST_DUE`, or
     straight to `EXPIRED` if `past_due_start_days == 0` (no `PAST_DUE` interim state for that
     policy).
   - `PAST_DUE` for at least `payment_grace_days` since entering that state, **and**
     `auto_expire_after_grace` is true: transitions to `EXPIRED`. If a policy sets
     `auto_expire_after_grace = False`, the subscription simply stays `PAST_DUE` indefinitely until
     a human acts (renewal, manual suspension, or a policy change) — the job never invents a
     transition path a policy didn't explicitly authorize.

Report-only by default (`dry_run=True`): computes and returns every finding without writing
anything, so a CLI operator or a test can see exactly what *would* happen. `--apply` is required to
actually write.

## Why an automated job is allowed to write `EXPIRED` transitions at all

This looks, at first glance, like exactly the kind of "automated commercial decision" the
governing spec is wary of. It's safe here specifically because of what these three transitions
are and aren't:

- `ACTIVE → PAST_DUE`, `ACTIVE → EXPIRED`, `PAST_DUE → EXPIRED` are **purely date-driven, objective
  facts** — "the term ended" — not discretionary trust decisions the way approving a renewal or
  lifting a suspension are.
- They only ever **restrict** future commercial operation (per `resolve_commercial_state()`,
  Milestone 1) — they never touch customer data (Principle 1: read/backup/restore/export stay
  available in every state) and never grant anything.
- They're implemented by calling the **existing, unmodified** `transition_subscription()` — this
  milestone does not add any new entry to the shared `VALID_TRANSITIONS` table (all three
  transitions were already permitted there before Phase 8). Milestone 2's security finding was
  about *widening* that table for a *reviving* transition reachable through a loosely-gated route;
  this job only exercises transitions that already existed and that only ever narrow what's
  possible.

Reviving a subscription (`EXPIRED → ACTIVE`) remains staff-only, MFA-gated, and impossible to reach
outside `apply_renewal_request()` — this job never attempts it, and couldn't even if it tried,
since `EXPIRED` is terminal in the shared table.

## Notification center

`InternalNotification` — internal-only (Part H is explicit: no WhatsApp/SMS/email delivery in this
milestone; a future outbound adapter may be defined but must stay inactive). Lifecycle:
`OPEN → IN_PROGRESS → RESOLVED`, or `OPEN → ACKNOWLEDGED → RESOLVED`, or `→ DISMISSED`. Every
transition is audited. `assign_notification()` also flips `OPEN` to `IN_PROGRESS`, since an
assigned-but-untouched notification isn't meaningfully "open" anymore for queue-visibility
purposes (Milestone 6's role-based queues will read off `status` directly).

## CLI

```
flask commercial expiry-scan              # dry-run, prints full findings as JSON
flask commercial expiry-scan --apply      # writes notifications + transitions
flask commercial expiry-scan --as-of 2026-08-01 --apply   # explicit evaluation date, for testing/backfill
```

Covers what the governing spec calls both `expiry-scan` and `notification-scan` in one command —
the same per-subscription walk needs the same policy resolution and date arithmetic for both, so
splitting them into two passes would mean resolving each subscription's policy twice for no
benefit. No scheduler daemon is wired up in this milestone, per the spec's own instruction; the job
is idempotent and CLI-runnable today, and safe to put behind a cron/scheduled-task trigger later
without any code change.

## What Milestone 3 deliberately does not do yet

- No job-run history table / advisory-lock-based scheduler safety (Part R's fuller scope) — this
  milestone proves the job itself is idempotent (dedup keys, and a transitioned subscription
  leaves the eligible-status scan scope so it's never revisited); making *concurrent scan
  processes* safe against each other via a Postgres advisory lock is deferred to whichever
  milestone actually introduces a scheduler.
- No UI for viewing/acknowledging notifications or managing policies yet (Milestone 6/7).
- No Scenario 3 (Part AB, "past due") physical end-to-end validation against a real product
  installation yet — deferred to Milestone 8 alongside the other Part AB scenarios, same reasoning
  as Milestone 2's renewal scenarios.
