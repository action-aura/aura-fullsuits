# Phase 8 — Subscription/License Governance (Part B, Milestone 1)

## What this document covers

The authoritative state-resolution service implemented in
`owner/app/commercial_ops/state_resolution.py`, and the renewal date-calculation rules in
`owner/app/commercial_ops/renewal_dates.py`. Both are pure functions — no I/O, no database
session — following the same "pure decision function over already-known inputs" pattern already
proven in `commercial_runtime/licensing_contracts/capability_guard.py`.

## Why a separate `CommercialState` from the product-local `LicenseState`

`commercial_runtime/licensing_contracts/state_machine.py`'s `LicenseState` already exists and is
correct — it answers "what should this specific installation's local product be doing right now,"
computed from elapsed offline time against a signed assertion. `CommercialState` (this document)
answers a different, narrower question: "what does Owner's own commercial record — subscription
status, license status — say right now, independent of any particular installation's offline
bookkeeping." One `CommercialState` of `ACTIVE` can correspond to several different
`LicenseState`s across different installations of the same license (one online, one in `WARNING`
because it hasn't checked in in a while) — that's expected and correct, not a state-model bug.

## The state matrix (as implemented)

| Subscription status | License status | `CommercialState` | May issue assertion | May activate new | May check in existing |
|---|---|---|---|---|---|
| any | `REVOKED` | `REVOKED` | No | No | No |
| `ACTIVE`/`PILOT` | `ACTIVE`/`ISSUED` | `ACTIVE`/`PILOT_ACTIVE` | Yes | Yes | Yes |
| `ACTIVE`/`PILOT` | `None` | `NO_LICENSE` | No | No | No |
| `ACTIVE`/`PILOT` | anything else (e.g. `DRAFT`) | `INVALID` | No | No | No |
| `PAST_DUE` | `ACTIVE`/`ISSUED` | `PAST_DUE` | Yes | No | Yes |
| `PAST_DUE` | `SUSPENDED` | `SUSPENDED` (suspended license wins) | No | No | Yes |
| `SUSPENDED` | any | `SUSPENDED` | No | No | Yes |
| `EXPIRED` | any | `EXPIRED` | No | No | Yes |
| any | `EXPIRED` | `EXPIRED` | No | No | Yes |
| `CANCELLED` | any | `CANCELLED`, within/past `cancellation_effective_date` | Yes/No depending on effective date | No | Yes/No depending on effective date |
| `COMPLETED` | any | `PILOT_COMPLETED` | No | No | No |
| `DRAFT` | any | `INVALID` | No | No | No |
| unrecognized | any | `INVALID` | No | No | No |

Notes on specific rows:

- **REVOKED always wins**, checked first, regardless of subscription status — an explicit signed
  security decision is never silently overridden by an otherwise-active subscription (spec Part I).
- **`SUSPENDED`/`EXPIRED` still allow "check-in existing installation"** even though no assertion
  is issued. This is deliberate: a rejected check-in is what drives the product's own
  reevaluate-on-failure pipeline (Phase 7V-A's Part I fix — `/_internal/reevaluate`) toward
  `RESTRICTED`. "May check in" here means "the check-in attempt is meaningful and should be
  honored by the caller," not "a fresh assertion will be issued."
- **`PAST_DUE` does not automatically block anything** except new activation — matching spec Part
  I's explicit instruction that past-due must not automatically mean revoked. A future commercial
  policy (Milestone 3, Part I) will narrow this further as time in `PAST_DUE` accumulates past a
  configured grace; this function only encodes the immediate, unconditional part of the rule
  today. The `applicable_policy_code` field on `CommercialStateDecision` is reserved (always
  `None` until Milestone 3) for exactly that later narrowing, so the output shape does not need to
  change when it's built.
- **`CANCELLED`'s immediate-vs-end-of-term distinction** is driven entirely by
  `cancellation_effective_date` compared against `as_of` — never by `Subscription.cancellation_date`
  alone (that column records *when cancellation was recorded*, not *when it takes effect*).
  Milestone 2's cancellation workflow is responsible for setting `cancellation_effective_date`
  explicitly.
- **Inconsistent combinations return `INVALID`, never a guessed-at active state** — e.g. an
  `ACTIVE` subscription whose license is still `DRAFT`. This is the deny-by-default backstop the
  spec's Part B explicitly requires ("do not allow independent arbitrary states to drift without
  reconciliation"). `INVALID` decisions are exactly what Milestone 6's reconciliation engine is
  built to surface and report on.

## Renewal date rules (Part D)

Implemented in `renewal_dates.py`, five pure functions:

- `is_early_renewal(current_term_end, as_of)` — the *only* automatic classification this module
  performs (early vs. late), and it never decides *which* late-renewal anchor to use.
- `calculate_early_renewal(...)` — new term starts from the existing `current_term_end`; remaining
  paid time is never discarded.
- `calculate_late_renewal(rule, ...)` — requires the caller to explicitly pass one of three named
  rules (`LATE_RENEWAL_FROM_PREVIOUS_END`, `LATE_RENEWAL_FROM_APPROVAL_DATE`,
  `LATE_RENEWAL_FROM_PAYMENT_CONFIRMED_DATE`); raises rather than guessing if the rule's required
  anchor date is missing.
- `calculate_pilot_conversion(...)` — paid term start is always an explicit input, never inherited
  from pilot dates; credited pilot time is an explicit `credited_days` adjustment to the *end*
  date only, never a backdated start.
- `add_interval(base, months=.., days=..)` — the shared month/day arithmetic, handling leap years
  and month-length differences by clamping (Jan 31 + 1 month → Feb 28/29, never overflowing into
  March).

All 24 test cases in `test_commercial_ops_renewal_dates.py` (leap years, month/year boundaries,
same-day renewal, multiple consecutive renewals, determinism) pass. All 24 test cases in
`test_commercial_ops_state_resolution.py` (every state-matrix row above, both orderings of
governing-record precedence, deny-by-default paths) pass.

## What Milestone 1 deliberately does not do yet

- No renewal *workflow* engine (create/approve/apply) — only the data model
  (`RenewalRequest`/`RenewalRequestStatusHistory`, see the migration
  `8646da2df010_phase_8_milestone_1_renewal_workflow_.py`) and the pure date rules exist so far.
  The atomic apply-transaction, idempotency-key enforcement, and optimistic-lock concurrency tests
  are Milestone 2.
- No route or UI wiring for any of this yet.
- No consumption of `resolve_commercial_state()` by the existing activation/check-in routes yet —
  it exists and is tested standalone; wiring it in as the actual gate those routes consult is a
  deliberate Milestone 2+ decision, not an oversight, since that *is* a behavior change to a route
  Phase 7V-A already physically validated, and should happen with its own dedicated review and
  regression pass rather than bundled into "add a pure function module."
