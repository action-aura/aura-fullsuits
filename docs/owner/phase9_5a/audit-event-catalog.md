# Phase 9.5A Milestone 23 — Audit Event Catalog

`app.audit.services.record()` takes a free-text `action_code` (no fixed enum/allowlist gates it — confirmed
by reading `owner/app/audit/services.py` in full: `record()` accepts any string). This catalog is the
documented, canonical set of `action_code` values Phase 9.5A code emits, so every future caller reuses the
same string rather than inventing a near-duplicate. All entries below are real, already-emitted by the
Milestone 22 service code (`owner/app/employees/services.py`, `owner/app/leads/services.py`,
`owner/app/leads/conversion.py`) unless marked **(reserved)** — a code a later milestone/phase's route will
emit once the corresponding write path exists, listed now so this catalog does not need silent revision.

## Employees (`owner/app/employees/services.py`)

| action_code | Emitted by | entity_type |
|---|---|---|
| `EMPLOYEE_PROFILE_CREATED` | `create_employee_profile()` | `employee_profile` |
| `EMPLOYEE_PROFILE_UPDATED` | `update_employee_profile()` | `employee_profile` |
| `EMPLOYEE_SUSPENDED` | `suspend_employee()` | `employee_profile` |
| `EMPLOYEE_TERMINATED` | `terminate_employee()` | `employee_profile` |
| `EMPLOYEE_ROLE_CHANGED` **(reserved)** | future RBAC-assignment route | `employee_profile` |
| `EMPLOYEE_SESSIONS_REVOKED` **(reserved)** | already covered implicitly — `suspend_employee()`/`terminate_employee()` call the real `revoke_all_sessions_for_staff()` directly; a distinct audit row is reserved only if a future route revokes sessions independently of a status change |

## Leads (`owner/app/leads/services.py`, `owner/app/leads/conversion.py`)

| action_code | Emitted by | entity_type |
|---|---|---|
| `LEAD_CREATED` | `create_lead()` | `lead` |
| `LEAD_ASSIGNED` | `assign_lead()` (no prior assignee) | `lead` |
| `LEAD_REASSIGNED` | `assign_lead()` (had a prior assignee) | `lead` |
| `LEAD_STATUS_CHANGED` | `change_lead_status()` | `lead` |
| `LEAD_CONVERTED` | `conversion.convert()` | `lead` |
| `LEAD_NOTE_ADDED` | `add_lead_note()` | `lead` |

## Customer location (`owner/app/leads/services.py`)

| action_code | Emitted by | entity_type |
|---|---|---|
| `CUSTOMER_LOCATION_CAPTURED` | `capture_location()` | `lead` or `customer` |
| `CUSTOMER_LOCATION_CORRECTED` **(reserved)** | future edit-in-place-within-window route (`customer-location-contract.md`'s own-capture edit window) |

## Customer note / interaction **(reserved — no write path built this milestone)**

`CUSTOMER_NOTE_ADDED` already exists and is real (Phase 5, `owner/app/customers/services.py`'s
`add_note()` → `CUSTOMER_NOTE_ADDED`) — reused unchanged, not duplicated.
`CUSTOMER_INTERACTION_ADDED` is reserved for the `CustomerInteraction` write path (Milestone 22 built the
model, not yet a service function — foundation-phase scope).

## Commercial documents **(reserved — schema-only this phase, no service layer built)**

`QUOTE_CREATED`, `SALES_ORDER_CREATED`, `COMMERCIAL_INVOICE_CREATED`, `PAYMENT_RECORDED`,
`PAYMENT_CONFIRMED`, `REFUND_RECORDED`. Reserved because Milestone 22 explicitly does not implement the
complete sales/invoice/payout workflow (governing spec's own boundary) — the 36-table migration
(Milestone 21) and the contract docs (Milestones 9–11) are real; the service layer that would emit these
is future work.

## Commissions

| action_code | Emitted by | entity_type |
|---|---|---|
| `COMMISSION_EARNED` **(reserved)** | future `CommissionEligibilityService.evaluate()` — posts a `CommissionLedgerEntry` on payment confirmation; not built this phase (only the pure, non-posting `preview_commission()` is) |
| `COMMISSION_APPROVED` **(reserved)** | future approval route |
| `COMMISSION_PAID` **(reserved)** | future payout-batch route |
| `COMMISSION_REVERSED` **(reserved)** | future reversal route |

## Device policy

| action_code | Emitted by | entity_type |
|---|---|---|
| `DEVICE_POLICY_CHANGED` **(reserved)** | future `DevicePolicyProfile`/platform-rule write route — Milestone 22 built only the read-side `resolve_device_policy()`, per the explicit enforcement-wiring boundary (`multi-device-policy-design.md`) |
| `SUBSCRIPTION_DEVICE_POLICY_OVERRIDE_CREATED` **(reserved)** | future override-creation route |

## Expenses, management notes, daily reports **(reserved — schema-only this phase)**

`EXPENSE_CREATED`, `EXPENSE_APPROVED`, `MANAGEMENT_NOTE_CREATED`, `MANAGEMENT_NOTE_UPDATED`,
`DAILY_REPORT_GENERATED`, `DAILY_REPORT_REGENERATED` — models exist (Milestone 21); no service/route layer
built this phase for these six, consistent with Milestone 22's explicit minimum-proving-service list (which
names `DailySnapshotDefinitionService` only as a *structure* generator, not the real generation job).

## Pricing

| action_code | Emitted by | entity_type |
|---|---|---|
| `PRICE_OVERRIDDEN` **(reserved)** | future line-item override path (`pricing.override` permission, `price-authority-rules.md` rule 4) — not built this phase; `read_active_catalog()` (Milestone 22) is read-only, no override capability exists yet |

## Real vs. reserved — honest count

**10 real, already-emitted** action codes (verified: every one has a passing test in
`owner/tests/test_phase9_5a_*.py` asserting the exact `AuditLog` row exists — see
`owner/tests/test_phase9_5a_employee_services.py::test_update_employee_profile_bumps_version_and_audits`
for the pattern). **18 reserved** codes, named now so a later phase's route reuses this exact string instead
of drifting into a near-duplicate (`LEAD_STATUS_CHANGE` vs. `LEAD_STATUS_CHANGED`, etc.) — the real risk
this catalog exists to prevent.
