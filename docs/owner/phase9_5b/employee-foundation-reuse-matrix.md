# Phase 9.5B Milestone 1 — Employee Foundation Reuse Matrix

| Capability | Classification | Real source |
|---|---|---|
| Staff auth account (email/password/MFA/sessions/lockout) | ALREADY IMPLEMENTED | `owner/app/models/staff.py::StaffUser`, `StaffSession`, `MfaCredential`, `LoginAttempt` |
| Employee profile model | ALREADY IMPLEMENTED | `owner/app/models/employees.py::EmployeeProfile` |
| Employee presence model | ALREADY IMPLEMENTED | `owner/app/models/employees.py::EmployeePresenceSession` |
| Employment-status enum | ALREADY IMPLEMENTED | `EMPLOYMENT_STATUSES` (`employees.py`) — PENDING/ACTIVE/SUSPENDED/TERMINATED/ARCHIVED, matches spec exactly |
| Manager self-relationship | ALREADY IMPLEMENTED | `EmployeeProfile.manager_employee_profile_id` |
| Commission-plan assignment | ALREADY IMPLEMENTED | `EmployeeProfile.commission_plan_id` |
| Profile optimistic version | ALREADY IMPLEMENTED | `EmployeeProfile.version` |
| Public UUIDs | ALREADY IMPLEMENTED | `UUIDPKMixin`, universal |
| create/update/suspend/terminate profile service | ALREADY IMPLEMENTED | `owner/app/employees/services.py` (Phase 9.5A M22) |
| reactivate/activate/archive profile service | FOUNDATION EXISTS, OPERATION MISSING | Same module — added this phase |
| presence heartbeat *service* (`touch_presence`) | ALREADY IMPLEMENTED | `owner/app/employees/services.py::touch_presence` |
| presence *state derivation* (ONLINE/RECENTLY_ACTIVE/OFFLINE) | FOUNDATION EXISTS, OPERATION MISSING | Model has `last_seen_at`; no derivation function existed — added this phase |
| RBAC/permission seed for employee actions | ALREADY IMPLEMENTED | `owner/app/staff/seed_data.py` (Phase 9.5A M17), 125 permissions incl. `employees.*` |
| one-time secure setup token | ALREADY IMPLEMENTED (different domain, directly reusable) | `owner/app/models/staff.py::StaffInvitation` + `owner/app/staff/services.py` + `owner/app/auth/routes.py` accept-invitation flow (Phase 5, pre-9.5A) |
| MFA enrollment flow | ALREADY IMPLEMENTED | `owner/app/auth/routes.py::mfa_enroll_form/submit` (Phase 4/6) |
| session revocation mechanism | ALREADY IMPLEMENTED | `owner/app/auth/session.py::revoke_all_sessions_for_staff`, `revoke_session` |
| last-usable-SUPER_ADMIN protection | OUT OF PHASE 9.5A, real gap | Not built anywhere — added this phase (Milestone 2) |
| employee list/detail routes+UI | FOUNDATION EXISTS, ROUTE+UI MISSING | Model/service ready; no route/template — added this phase |
| employee self-profile route+UI | FOUNDATION EXISTS, ROUTE+UI MISSING | Added this phase |
| management employee dashboard | FOUNDATION EXISTS, ROUTE+UI MISSING | Metrics computable from existing tables; no route/template — added this phase |
| `/api/operations/v1` employee/presence routes | FOUNDATION EXISTS (contract only), ROUTE MISSING | `openapi.yaml` has the contract (`x-status: planned`); zero Flask routes exist — added this phase |
| role assignment UI/route | ALREADY IMPLEMENTED | `owner/app/staff/routes.py::update_roles` (Phase 4) — reused as-is, hardened with last-SUPER_ADMIN guard |
| session list/revoke UI (management) | FOUNDATION EXISTS, UI MISSING | `revoke_all_sessions_for_staff` exists; no per-session list/single-revoke UI — added this phase |
| self session list/revoke | OUT OF PHASE 9.5A, real gap | Not built anywhere — added this phase |
| Leads/customers/quotes/invoices/commissions UI | OUT OF PHASE | Explicitly forbidden this phase |
| Mobile app / mobile auth routes | OUT OF PHASE | Explicitly deferred (ADR, Phase 9.5A) and not in 9.5B's own endpoint list |
