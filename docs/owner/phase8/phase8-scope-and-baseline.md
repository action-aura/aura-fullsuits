# Phase 8 — Scope and Baseline (Part A)

## Starting point, verified

- Final Phase 7 tag: `aura-product-licensing-phase7-validation-complete`, dereferences to commit
  `1e14bdc8782c0114fc5f7197abb3088009253842` (2026-07-26 00:36:33 +0300).
- `HEAD` is at the same commit, branch `master`, working tree clean.
- The Phase 7V-A ad-hoc loopback Owner validation process (PID 30944, port 19101, left running
  from the prior session) was stopped before running the test suites below — it held the same
  Postgres test database the `owner/tests/` fixtures target, and a live process sharing that DB is
  what corrupted it once already earlier in this project. Nothing of value was lost: it only held
  synthetic Phase 7V-A validation data.

## Test baseline (this session, post-cleanup)

| Suite | Result | Notes |
|---|---|---|
| `owner/tests/` | **187 passed** | Full suite, one process, no isolation issues |
| `commercial_runtime/licensing_contracts/tests/` | **212 passed** | Full suite, one process |
| `products/retail/tests/` | **110 passed / 73 failed / 11 errors as one combined run; every individual file/test passes standalone** (spot-checked `wave1c_financial_gate_test.py` — the financial-manipulation, idempotency, and double-return tests — 4/4 clean alone) | Pre-existing cross-file test-isolation issue, not a Phase 7 regression |
| `products/clinic/tests/` | **33 passed / 83 failed / 19 errors as one combined run; every individual file/test passes standalone** (spot-checked `clinic_capability_guard_test.py::test_restricted_allows_appointment_checkin` alone) | Same characteristic as Retail |

**Conclusion on the product suites:** Retail and Clinic's test files are not safe to run as one
large combined `pytest` invocation across the whole `tests/` directory — something (a fixed test
port, a shared temp SQLite path, or module-level state) bleeds across files when many run in the
same process. Running `products/retail/tests` and `products/clinic/tests` **together** in one
invocation additionally produces outright collection errors (module name collisions between
generically-named test files in the two packages). This is a real, pre-existing characteristic of
the test infrastructure — not introduced by this session, not a Phase 7 regression, and not
something Phase 8 needs to fix to proceed, but worth fixing eventually (each test file should run
standalone or with a properly isolated fixture, e.g. via `pytest-xdist` process isolation or a
per-test unique port/tmp-path fixture) since it currently means "run the whole product suite" is
not a meaningful CI gate as written — only "run each file" is. Recorded here rather than silently
worked around.

**Recommendation for Phase 8's own CI/regression story:** run each product test file individually
(or use `pytest --forked` / per-file subprocess invocation) rather than a single combined
invocation, until this isolation issue is separately fixed.

## Current products (unchanged, to remain unchanged)

- Aura Retail Windows 1.0.0-rc.2, `versionCode` unrelated (Windows).
- Aura Retail Android 1.0.0-rc.2, `versionCode 3`.
- Aura Clinic Windows 1.0.0-rc.2.
- Aura Clinic Android 1.0.0-rc.2, `versionCode 3`.
- Android signing certs (reconfirmed throughout Phase 7): Clinic
  `35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2`, Retail
  `cae6b18450c14a71eba47545e5b3ba52089eef0397d5881f4c1dc7d5a4b7d32d`. Neither is to be touched in
  Phase 8.

## Owner module map (as it exists today)

```
owner/app/
├── api/                 -- internal Owner web UI API
├── api_external/         -- external product-facing API (activations, check-ins, deactivations)
├── audit/                -- audit hash chain (Part... Phase 5/6)
├── auth/                 -- staff auth, MFA
├── catalog/               -- products, platforms, plans, prices, add-ons
├── customers/             -- customers, contacts
├── dashboard/             -- internal dashboard
├── installations/         -- device installations, status transitions
├── licensing/              -- license issuance + subscription-adjacent license service
├── licensing_admin/        -- internal admin routes over licensing
├── licensing_service/       -- Phase 6 activation/check-in/deactivation protocol implementation
├── models/                 -- SQLAlchemy models (catalog, customers, subscriptions, licensing,
│                              licensing_service, installations, staff, audit, base)
├── releases/                -- release-channel metadata
├── security/                 -- signing keys, license-key hashing
├── services/                  -- cross-cutting services
├── staff/                      -- staff user management
├── system/                      -- system/health
└── templates/                   -- Owner web UI templates
```

## Existing subscription/license domain (Phase 5/6/7 foundation Phase 8 builds on)

`owner/app/models/subscriptions.py` already defines:

- `Subscription` — status (`DRAFT|PILOT|ACTIVE|PAST_DUE|SUSPENDED|EXPIRED|CANCELLED|COMPLETED`,
  matches the spec's canonical list exactly), `start_date`/`end_date`, `billing_cycle`,
  `device_allowance`, `renewal_date`, `cancellation_date`/`cancellation_reason`, sales/support
  owner references, `SubscriptionItem`/`SubscriptionAddon` children.
- `SubscriptionStatusHistory` — append-only, no `delete-orphan` cascade (deliberate, per its own
  comment: an audit trail must never vanish as a side effect of deleting its parent).
- `RenewalRecord` — a single flat record per renewal application: previous/new end date,
  previous/new plan, approving staff, linked payment record. **This is the "apply" step only —
  there is no renewal request/workflow state machine yet** (no DRAFT/QUOTED/AWAITING_PAYMENT/
  APPROVED pipeline; `record_renewal()` in `subscriptions/services.py` mutates the subscription
  directly and commits in one step, with no idempotency key, no optimistic lock, no separate
  approval gate). This is exactly the gap Part C-E of the Phase 8 spec targets.
- `PaymentRecord` — status `PENDING|CONFIRMED|FAILED|REFUNDED|VOIDED` (matches spec exactly),
  `recorded_by`/`verified_by` staff references, `internal_note`. `correct_payment()` exists for
  status corrections but does **not** create a separate correction-history row — it mutates the
  row in place (verified_by + status only), which conflicts with Part F's "no hard overwrite of
  verified financial metadata... correcting a payment must create a correction record or history
  entry." This needs a real history table if Phase 8 wants to honor that rule strictly.

`owner/app/subscriptions/services.py`'s `VALID_TRANSITIONS` for `Subscription` already matches the
spec's canonical state list exactly (`DRAFT→{PILOT,ACTIVE,CANCELLED}`,
`PILOT→{ACTIVE,COMPLETED,CANCELLED}`, `ACTIVE→{PAST_DUE,SUSPENDED,EXPIRED,CANCELLED}`,
`PAST_DUE→{ACTIVE,SUSPENDED,EXPIRED,CANCELLED}`, `SUSPENDED→{ACTIVE,CANCELLED,EXPIRED}`,
`EXPIRED|CANCELLED|COMPLETED→{}`). `transition_subscription()` follows the same
audited-history-row + `audit_record()` pattern already proven correct in `owner/app/licensing/
services.py`'s `transition_license()` (the function Phase 7V-A's Part L physically exercised
this session for `SUSPENDED`↔`ACTIVE`).

`owner/app/models/licensing.py`'s `License.status` (`DRAFT|ISSUED|ACTIVE|SUSPENDED|EXPIRED|
REVOKED|REPLACED`) and `owner/app/models/installations.py`'s `Installation.status` (default
`REGISTERED`, transitions tracked via `InstallationStatusHistory`) are the other two legs of the
four-way relationship (subscription / license / installation / product-local state) Part B asks to
be formalized — today each has its own `VALID_TRANSITIONS` table and its own history table, but
there is **no single deterministic state-resolution service** that reads all three plus the
product-local `LicenseState` and returns one authoritative answer. That service is Part B's actual
deliverable and does not exist yet.

## Product-side state machine (already mature, Phase 8 builds signed-assertion inputs into it, does not replace it)

`commercial_runtime/licensing_contracts/state_machine.py`'s `LicenseState` enum already includes
every state Part B/I/J need: `ACTIVE_ONLINE`, `ACTIVE_OFFLINE`, `WARNING`, `GRACE_PERIOD`,
`RESTRICTED`, `SUSPENDED`, `REVOKED`, `EXPIRED`, `DEVICE_DEACTIVATED`, `DEVICE_REPLACED`, plus
`ACTIVE_FAMILY` and `DATA_PRESERVED_FAMILY` frozensets consumed by `capability_guard.py`. Phase 7V-
A's Part I fix (the `/_internal/reevaluate` route) already proved this evaluator and its
elapsed-time bookkeeping work correctly on real hardware. Phase 8's job on this side is almost
entirely about **what goes into a signed assertion** (Part W's proposed new fields:
`commercial_policy_version`, `renewal_status`, `plan_code`, `term_start`/`term_end`,
`past_due_since`, `commercial_grace_end`, `pilot_status`, `emergency_extension_id`, etc.) and
**assertion refresh triggers** (Part X) — not building a new local state machine or a second
enforcement path. This matches the spec's own Part J instruction ("do not create a second
enforcement path") exactly, and the existing code already makes that easy: `evaluate_capability()`
already takes `current_state` + `restricted_mode_allowlist` + `entitlements` as pure inputs with no
I/O, so new commercial fields just need to flow into `entitlements`/`AssertionEvidence` the same
way existing ones do.

## What Phase 8 is really building, in one sentence

A governed **request → approve → apply → refresh** workflow layer (renewals, plan/add-on changes,
pilot lifecycle, emergency extensions, manual activation approval, device-slot administration) on
top of an already-correct **subscription/license/installation state machine and signed-assertion
pipeline** that Phase 5-7 already built and Phase 7V-A already physically validated end-to-end —
plus the internal operational tooling (notifications, queues, reconciliation, dashboards) to run
that workflow layer safely.

## Scale note

The full Phase 8 specification (Parts A through at least AC, truncated in transmission beyond
that point) describes on the order of 15+ new database tables, a dozen+ new service modules, new
Owner UI workflows across 4 role-based queues, signed-assertion schema extensions consumed
identically by Kotlin and Python, full Windows+Android product UX work, and end-to-end physical
validation of 7 commercial lifecycle scenarios across 4 build targets. This is not a single-session
implementation. See `phase8-implementation-plan.md` for a proposed phased breakdown.
