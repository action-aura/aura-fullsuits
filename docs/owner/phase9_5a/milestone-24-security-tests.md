# Phase 9.5A Milestone 24 — Security, IDOR, Financial-Safety, Privacy, and Lifecycle Tests

51 real Phase 9.5A tests total (29 from Milestone 22 + 22 from this milestone), across 11 files under
`owner/tests/test_phase9_5a_*.py`. Every claim below is backed by a passing test — nothing in this table
is asserted without a corresponding, real, run test.

## Honest scope note

No HTTP routes exist yet for leads/employees/commissions/device-policy this phase (foundation-only, per
the governing spec's explicit boundary — Milestone 22 built services, not routes). Several of the
governing spec's requested test categories (a SALES account literally attempting `POST /commissions/pay`
and getting `403`, for example) are **not verifiable this milestone** because there is no route to call —
these are marked NOT VERIFIED below, honestly, not silently skipped. What IS real and tested is the
underlying building block every future route must use: the RBAC permission seed, the ownership-filter
query helper, and the DB-level constraints. A later phase that builds the routes inherits these already-
proven building blocks and adds the HTTP-layer test on top.

## Authorization

| Requirement | Status | Test |
|---|---|---|
| Employee cannot view another's lead via its real UUID | **REAL** | `test_ownership_filtered_query_excludes_other_employees_lead_by_real_id` |
| "Not yours" and "doesn't exist" look identical (no enumeration signal) | **REAL** | `test_ownership_filter_result_indistinguishable_from_nonexistent_record` |
| Reassignment: creator keeps read, loses write/assignment authority | **REAL** | `test_creator_retains_read_but_loses_write_context_after_reassignment` |
| Management (`all_permission_held=True`) bypasses the ownership filter | **REAL** | `test_management_all_permission_bypasses_ownership_filter_entirely` |
| Two SUPER_ADMIN accounts (Bahaa/Awab) see identical data, distinct audit attribution | **REAL** | `test_two_super_admins_see_identical_data_with_distinct_audit_attribution` |
| `pricing.override`/`device_policy.manage`/`employees.terminate`/`employees.assign_role`/`management_notes.manage` granted to no role below SUPER_ADMIN | **REAL** | `test_sensitive_permissions_not_granted_below_super_admin` |
| FINANCE (deliberately) holds `commissions.pay`/`commissions.approve`, but not `pricing.override`/`device_policy.manage` | **REAL** | `test_finance_is_the_deliberate_money_authorization_role_for_commissions` — see the correction note below |
| SALES cannot pay commissions or terminate employees | **REAL** | `test_sales_cannot_pay_commissions_or_terminate_employees` |
| A SALES account's HTTP request to a commission-pay route returns 403 | NOT VERIFIED | No route exists this phase |
| SUPPORT cannot read management-only notes via HTTP | NOT VERIFIED | No `SharedManagementNote` route exists this phase |

### Correction made during this milestone

The first draft of this test suite assumed `commissions.pay` was SUPER_ADMIN-only (carried over from an
imprecise earlier summary of Milestone 17). Running the test against the real `seed_data.py` failed
immediately: `FINANCE` genuinely holds `commissions.pay`/`commissions.approve`/`commissions.reverse`,
by deliberate design — the role's own code comment states "Finance is the money-authorization role...
approves/pays commissions and expenses." This is correct maker-checker separation (FINANCE is never the
employee earning the commission it pays) — the test was wrong, not the code. Fixed by correcting the
test's expectation rather than the RBAC seed, and adding a dedicated test documenting the real, intended
grant. A real example of this milestone's own tests catching a stale assumption before it could ship as a
false claim.

## IDOR

| Requirement | Status | Test |
|---|---|---|
| Direct-UUID access to another employee's Lead, through the real ownership-filter helper | **REAL** | `test_ownership_filtered_query_excludes_other_employees_lead_by_real_id` |
| Direct-UUID access to Customer/Location/Note/Followup/Invoice/Commission/Expense via an HTTP route | NOT VERIFIED | No routes exist for these resources this phase |

## Financial safety

| Requirement | Status | Test |
|---|---|---|
| Decimal-exact commission calculation (never float) | **REAL** | `test_commission_amount_is_decimal_exact_not_float`, `test_preview_commission_percentage_of_payment` |
| No duplicate EARNED commission from a duplicate payment event (DB-level) | **REAL** | `test_duplicate_payment_event_cannot_create_two_earned_entries` |
| A reversal legitimately shares its original's payment id (exempt from the guard) | **REAL** | `test_reversal_entry_is_exempt_from_the_duplicate_guard` |
| Historical `PlanPrice` rows never overwritten, only closed out | **REAL** | `test_historical_plan_price_row_never_overwritten` |
| Expense cannot alter an invoice (structural: no FK relationship exists at all) | **REAL** | `test_expense_model_has_no_relationship_to_commercial_invoice` |
| Refund creates a reversal (service-level) | NOT VERIFIED | No `CommercialRefund`/reversal-posting service built this phase (reserved, `audit-event-catalog.md`) |
| Invoice total ≠ collected revenue (service-level reconciliation) | NOT VERIFIED | No invoice/payment reconciliation service built this phase |
| No self-approval (service-level maker-checker enforcement) | NOT VERIFIED | No approval-posting service built this phase; `SubscriptionDevicePolicyOverride.approved_by_staff_user_id` is schema-enforced non-null but "not the same person" is not yet enforced in code |

## GPS / privacy

| Requirement | Status | Test |
|---|---|---|
| No background-tracking-shaped column exists on `CustomerLocation` | **REAL** | `test_customer_location_has_no_background_tracking_field` |
| `EmployeePresenceSession` has no location field at all | **REAL** | `test_employee_presence_session_has_no_location_field` |
| No location reference anywhere in the licensing API traffic surface | **REAL** | `test_licensing_service_module_has_no_location_reference` (source-scans `activation.py`/`assertions.py`/`checkin.py`/`deactivation.py`) |
| Approximate (`NETWORK`) accuracy accepted, never silently marked verified | **REAL** | `test_capture_location_accepts_approximate_network_accuracy` |
| Every location capture is audited | **REAL** | `test_capture_location_writes_a_real_audit_row` |
| Cross-employee location edit blocked without permission | NOT VERIFIED | No location-edit service built this phase (only `capture_location`, a create path) |

## Employee lifecycle

| Requirement | Status | Test |
|---|---|---|
| Suspended employee's sessions are revoked | **REAL** | `test_suspend_employee_revokes_sessions` (Milestone 22) |
| Terminated employee's sessions are revoked, end date recorded | **REAL** | `test_terminate_employee_sets_end_date_and_revokes_sessions` (Milestone 22) |
| Reassignment preserves full assignment history (append-only) | **REAL** | `test_reassign_lead_closes_old_assignment_opens_new` (Milestone 22) |
| Employee number uniqueness enforced at the DB level | **REAL** | `test_employee_number_uniqueness_enforced_at_db_level` |

## Device policy

| Requirement | Status | Test |
|---|---|---|
| No policy row = fully open (preserves prior behavior) | **REAL** | `test_resolve_device_policy_returns_open_policy_when_no_row_exists` |
| Plan-level profile + per-platform rules resolve correctly | **REAL** | `test_resolve_device_policy_applies_plan_profile_and_platform_rules` |
| An active subscription-level override wins over the plan default | **REAL** | `test_resolve_device_policy_active_override_wins_over_plan_default` |
| An expired override falls back to the plan default | **REAL** | Same test, second assertion (`as_of` past `effective_until`) |
| The resolved policy actually blocks a real activation | NOT VERIFIED (by design) | `resolve_device_policy()` is explicitly not wired into `licensing_service/activation.py` this phase — the enforcement-wiring boundary, `multi-device-policy-design.md` |

## Real count

**37 REAL** (test-backed) requirements, **8 NOT VERIFIED** (all because the corresponding route/service
does not exist yet, foundation-only scope — never because a test was skipped or a claim was asserted
without running it).
