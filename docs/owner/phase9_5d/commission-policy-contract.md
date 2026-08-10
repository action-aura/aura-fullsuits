# Phase 9.5D — Milestone 14: Commission Policy Contract

`app/commissions/management.py` — closes the real gap Milestone 1's audit identified: `CommissionPlan`/`CommissionRuleVersion`/`EmployeeCommissionPlanAssignment` were MODEL PRESENT, SERVICE MISSING. Lives in the existing `app/commissions/` package (Phase 9.5A's own module boundary for commission logic), not `app/commercial_sales/` — that module is for the new Quote/Order/Invoice/Refund document chain specifically.

## Functions

- `create_commission_plan(fields, actor_staff_user_id) -> CommissionPlan`.
- `create_commission_rule_version(plan, *, rule_type, rate_percentage=None, fixed_amount=None, currency=None, product_id=None, effective_from, actor_staff_user_id) -> CommissionRuleVersion` — **append-only**, matching `add_plan_price()`'s exact pattern (Milestone 1's audit precedent): closes the current open rule's `effective_until`, inserts a new row. A rate change never retroactively alters a prior version's own stored rate.
- `assign_employee_commission_plan(employee_profile_id, plan, *, effective_from, actor_staff_user_id) -> EmployeeCommissionPlanAssignment` — same append-only pattern; an employee has at most one *open* assignment at any time, but every historical assignment is retained (never deleted).

## Real validation — "do not allow unrestricted employee-entered rates"

- `PERCENTAGE_OF_PAYMENT`/`PERCENTAGE_FIRST_SALE`/`PERCENTAGE_RENEWAL`: `rate_percentage` must be `> 0` and `<= 100` (`INVALID_COMMISSION_RATE`).
- `FIXED_AMOUNT`: `fixed_amount` must be `> 0` with a valid 3-letter currency (`INVALID_COMMISSION_FIXED_AMOUNT`).
- An unrecognized `rule_type` string is rejected (`COMMISSION_RULE_TYPE_NOT_IMPLEMENTED`). `PERCENTAGE_FIRST_SALE`/`PERCENTAGE_RENEWAL` **can** be created as reserved configuration (the schema supports them, per Phase 9.5A's own design) — `calculate_commission()` (unchanged) still raises `NotImplementedError` if one is ever actually *used*, since the distinguishing logic (first-sale vs. renewal detection) isn't implemented. Creation and use are deliberately separate gates.

## New error class: `CommissionError`

`app/commissions/errors.py::CommissionError(StableCodeError)` — a real, previously-missing per-domain error class, matching `LeadError`'s pattern exactly (not crammed into the unrelated `CommercialSalesError`, and not hand-rolled like the older, pre-9.5B-R3 `InvalidRenewalTransitionError`). Carries every stable code Milestones 14 and 15 (ledger) need.

## Test coverage

`tests/test_phase9_5d_commission_management.py` — 9 tests: plan creation, valid percentage rule, invalid rate rejection (too high, exactly zero), fixed-amount-requires-currency rejection, unimplemented-rule-type rejection, append-only rule versioning (prior version's own rate stays untouched), employee assignment + `resolve_active_rule()`/`calculate_commission()` integration proof, and reassignment correctly closes the prior open assignment while retaining both rows.

## Real external interference during this milestone (honestly recorded)

Mid-milestone, an external "aura-sync" tool running on this machine silently checked the working directory out to `master` and removed all uncommitted files, without any git commands issued by this session. No committed work was lost (the branch's commit history was untouched, confirmed via `git reflog`), but this milestone's uncommitted files (this module, its tests, and these docs) had to be recreated from the session's own record before being committed. Flagged for the user's awareness — an automated tool mutating a git working directory mid-session is a real operational risk independent of this phase's actual content.
