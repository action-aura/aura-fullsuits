# Phase 9.5C — Milestone 1: Existing CRM Foundation Audit

Real, source-level audit (not doc-level) of what Phase 9.5A actually
built. Every classification below is backed by a file citation, verified
by direct reads of the cited files (not delegated blindly).

| # | Capability | Classification | Evidence |
|---|---|---|---|
| 1 | Lead model + CRUD | SERVICE EXISTS, ROUTE MISSING | `app/models/leads.py:32-56` (Lead); `app/leads/services.py:23-67` has `create_lead`, `list_own_leads`, `list_all_leads`. No `update_lead`, no single-get helper beyond `db_session.get`, no route. |
| 2 | Lead status lifecycle | SERVICE EXISTS, ROUTE MISSING, **REQUIRES SAFE EXTENSION** | `change_lead_status()` `app/leads/services.py:70-99` appends history but has **no transition-validity guard** — any status accepts any status today. Milestone 2 must add the transition matrix. |
| 3 | Lead assignment/reassignment | SERVICE EXISTS, ROUTE MISSING, REQUIRES SAFE EXTENSION | `assign_lead()` `app/leads/services.py:102-137` — real append-only close/open pattern, correct audit codes. Missing: destination-employee-ACTIVE check, `reason` param, no route. |
| 4 | Lead product-interest linking | MODEL EXISTS, SERVICE MISSING | `LeadProductInterest` `app/models/leads.py:59-63`; zero references anywhere else. |
| 5 | Lead interactions | MODEL EXISTS, SERVICE MISSING | `LeadInteraction` `app/models/leads.py:97-106`; no create/list function exists. |
| 6 | Lead follow-ups | MODEL EXISTS, SERVICE MISSING | `LeadFollowup` `app/models/leads.py:109-118`; no create/complete/cancel/overdue-query function exists. |
| 7 | Lead notes | SERVICE EXISTS, ROUTE MISSING, REQUIRES SAFE EXTENSION | `add_lead_note()` `app/leads/services.py:140-151` works. `LeadNote` (`app/models/leads.py:121-131`) has **no visibility column** — Milestone 11 needs an additive migration. |
| 8 | Customer model | FULLY IMPLEMENTED AND REUSABLE | `app/models/customers.py:14-44` — full column set, full CRUD service + route + templates already live from earlier Owner phases. |
| 9 | Customer contacts | FULLY IMPLEMENTED AND REUSABLE | `CustomerContact` model, `add_contact()` service, `POST /customers/<id>/contacts` route — all already live. |
| 10 | Customer notes | FULLY IMPLEMENTED AND REUSABLE, REQUIRES SAFE EXTENSION for visibility | `CustomerNote` `app/models/customers.py:78-89` — genuinely separate table from `LeadNote` (different author FK type: `staff_user_id` vs `employee_profile_id`), correctly not duplicated. Same visibility gap as item 7. |
| 11 | Customer interactions/follow-ups | MODEL EXISTS, SERVICE MISSING | `CustomerInteraction`/`CustomerFollowup` `app/models/leads.py:165-191` — pure unused scaffolding, zero service references. |
| 12 | CustomerLocation | SERVICE EXISTS, ROUTE MISSING, REQUIRES SAFE EXTENSION | `capture_location()` `app/leads/services.py:154-184` — real exactly-one-owner guard, real audit. **No input validation** (lat/long bounds, accuracy, NaN/timestamp skew all unvalidated today — the DB CHECK constraint only guards the lead/customer exclusivity, not value ranges). No route anywhere calls it. |
| 13 | Lead-to-Customer conversion | FULLY IMPLEMENTED (backend), UNWIRED, REQUIRES SAFE EXTENSION | `app/leads/conversion.py:38-107` — real transaction, real idempotency-key reuse of the existing `CommercialOperationsIdempotencyKey` ledger, real duplicate-check before create, preserves `converted_from_lead_id` lineage. Gaps found by direct read: **does not append a `LeadStatusHistory` row** on conversion (status flips to `CONFIRMED` with no history entry — inconsistent with every other status change); **no optimistic-version check** against a caller-supplied expected version before mutating; **`find_duplicate_candidates()` accepts `phone` but the function itself never uses it** — phone-based duplicate matching is dead code today. Not wired to any route. |
| 14 | Lead ownership/access control | FULLY IMPLEMENTED AND REUSABLE | `apply_ownership_filter()` `app/leads/ownership.py:24-52` — one shared helper (correctly avoiding the "reimplemented per-route" IDOR risk the Phase 9.5A docs themselves warned about), used by `list_own_leads()`. Deliberately raises `NotImplementedError` for any other model, forcing every future query type to be an explicit, reviewed addition. |
| 15 | Duplicate detection | FULLY IMPLEMENTED, REQUIRES SAFE EXTENSION | `find_duplicate_candidates()` `app/customers/services.py:12-25` matches legal_name (case-insensitive), commercial_registration_reference, contact business_email. Phone matching is an accepted-but-unused parameter — real gap for Milestone 5. |
| 16 | Employee-to-employee data isolation | FULLY IMPLEMENTED AND REUSABLE | Real query-level filtering (not just RBAC permission gating) via item 14, exercised by an existing passing test (`tests/test_phase9_5a_lead_services.py`). |
| 17 | Web routes (leads/customers CRM) | OUT OF PHASE (leads) / FULLY IMPLEMENTED (customers) | No `leads` blueprint, no `/leads` route anywhere (`app/__init__.py` blueprint list has no leads entry). `customers` blueprint fully live: list/new/detail/archive/contacts/notes. |
| 18 | Templates | OUT OF PHASE (leads) / FULLY IMPLEMENTED (customers) | `app/templates/customers/{list,new,detail}.html` exist. Zero `*lead*` templates anywhere. |
| 19 | `/api/operations/v1` namespace | FOUNDATION EXISTS, OPERATION MISSING | Namespace registered (`app/api_operations/routes.py`, `url_prefix="/api/operations/v1"`), currently serving only `/me`, `/me/sessions*`, `/presence/heartbeat`, `/employees*`. Zero CRM endpoints. |
| 20 | Migrations | FULLY IMPLEMENTED — already at head | `migrations/versions/3c0d51d82d8c_phase_9_5a_commercial_operations_*.py` creates every `owner_lead_*`/`owner_customer_{followups,interactions,locations}` table. This revision is an ancestor of the current `338d06dece44` head — `alembic upgrade head` already creates these tables. Models are not orphaned from schema. |
| 21 | Existing tests | FULLY IMPLEMENTED for what they cover | `tests/test_phase9_5a_lead_conversion.py` (5 tests), `tests/test_phase9_5a_lead_services.py` (6 tests) — create+history, ownership filtering, status-history append, reassignment, location exactly-one-owner, conversion idempotency/duplicate/reject-lost/link-existing. Zero coverage for interactions, followups, product-interest, notes route, or any route/API layer (none exist yet). |
| 22 | Localization | REQUIRES SAFE EXTENSION | Only a single `"LEAD": _("Lead")` label exists (`app/i18n_labels.py:152`, `translations/en/LC_MESSAGES/messages.po:263`) — likely for `Customer.lifecycle_status`, not the 9 real `LEAD_STATUSES`/5 `LEAD_SOURCES`/3 `LEAD_PRIORITIES`/4 `LOCATION_SOURCES`/5 `INTERACTION_TYPES` enum values defined in `app/models/leads.py:22-29`, none of which are translated yet. |
| 23 | Audit events | REQUIRES SAFE EXTENSION (no catalog file to pre-register against) | `app/audit/services.py`'s `record()` takes a free-form `action_code: str` — no enum to update. Codes already in real use: `LEAD_CREATED`, `LEAD_STATUS_CHANGED`, `LEAD_ASSIGNED`/`LEAD_REASSIGNED`, `LEAD_NOTE_ADDED`, `CUSTOMER_LOCATION_CAPTURED`, `LEAD_CONVERTED`. No codes exist yet for interactions/followups/product-interest since those services don't exist. |
| 24 | Permissions | FULLY IMPLEMENTED AND REUSABLE | `app/staff/seed_data.py` already seeds `leads.{create,view_own,view_all,update_own,update_all,assign,convert,archive}` and `customers.{view_own,view_all,update_own,update_all,assign,capture_location,verify_location}`, already assigned to SALES/SUPPORT/management roles. New codes needed only for interactions/followups (e.g. `leads.log_interaction`, `leads.manage_followups`) once those services exist. |

## Summary

**Reusable foundation (do not rebuild)**: Lead/Customer models, migrations
(already at head), the shared ownership-filter helper, duplicate
detection (name/registration/email), append-only status-history and
assignment patterns, the conversion service, lead-note service, location-
capture service, the full Customer CRUD+contacts+notes stack, permission
codes, the free-form audit action-code pattern, 11 existing tests.

**Real gaps Phase 9.5C must fill**: LeadInteraction/LeadFollowup/
LeadProductInterest/CustomerInteraction/CustomerFollowup services (models
exist, zero service code); phone-based duplicate matching; note
visibility columns (additive migration); transition-validity guard on
`change_lead_status`; destination-ACTIVE check + reason on `assign_lead`;
status-history + optimistic-version check on `convert()`; location input
validation; the entire `leads` blueprint/routes/UI/templates; wiring
`conversion.py` and `capture_location()` to any route; CRM surface under
`/api/operations/v1`; i18n labels for every enum; dashboards (no lead/
customer dashboard code exists anywhere).
