# Phase 9.5B — Employee Management Portal — Handover

## What this phase is

The first real operational layer on top of the Phase 9.5A commercial-operations foundation: real employee
accounts, profiles, secure onboarding, full lifecycle (activate/suspend/reactivate/terminate/archive),
session management, presence, a management portal (list/detail/dashboard), employee self-service, and the
employee/presence subset of `/api/operations/v1`. Foundation-only items explicitly NOT touched: leads,
customers, quotes/orders/invoices, commissions, expenses, management notes, mobile apps, remote deployment.

## Where things live

| Concern | Path |
|---|---|
| Employee lifecycle/profile/presence services | `owner/app/employees/services.py`, `presence.py`, `queries.py`, `dashboard.py` |
| Management web routes | `owner/app/employees/routes.py` (`/employees/*`) |
| Self-service web routes | `owner/app/employees/self_routes.py` (`/profile/*`) |
| Operations API | `owner/app/api_operations/routes.py` (`/api/operations/v1/*`) |
| Onboarding (extends the existing invitation flow) | `owner/app/staff/services.py` (`create_employee_invitation`, `reissue_employee_invitation`) |
| First-login activation hook | `owner/app/auth/session.py::create_session()` |
| Last-usable-SUPER_ADMIN guard | `owner/app/security/super_admin_guard.py` |
| Templates | `owner/app/templates/employees/`, `owner/app/templates/profile/` |
| Preflight extension | `owner/app/commercial_ops/preflight.py::_check_employee_domain_integrity` |
| Migration | `owner/migrations/versions/af7831a6dc4d_*.py` (one additive column) |
| Tests | `owner/tests/test_phase9_5b_*.py` (12 files, 74 tests) |
| Design/evidence docs | `docs/owner/phase9_5b/` (this directory) |

## Real bugs found and fixed this phase (see `final-residual-risk-register.md` for full list)

1. Suspension/termination didn't block new login, only revoked existing sessions.
2. No last-usable-SUPER_ADMIN protection existed anywhere in the codebase.
3. `EmployeePresenceSession` rows survived suspension/termination (separate table from `StaffSession`).
4. Duplicate `employee_number` reservable by two concurrent open invitations.
5. Preflight false-failed on any fresh, staff-less database.

## One honest, documented scope reduction

Arabic/English/RTL localization — deferred, with reasoning, not silently dropped. See
`phase9-5b-scope-and-boundaries.md`.

## What a future phase (9.5C+) inherits, ready to build on

- A complete, tested employee/session/presence/lifecycle service layer and RBAC-enforced routes.
- A real operations API surface (`/me`, sessions, presence, employee CRUD+lifecycle, roles) ready for a
  real mobile client once one exists (mobile auth routes remain the one deliberately-unbuilt piece).
- A preflight check surface that already catches the most likely employee-domain integrity regressions.
- Two synthetic-tested management accounts proving the shared-authority model works end to end.

## Verdict

See `phase9-5b-final-decision.md` and `final-gate-matrix.md`: **PASS**, 27/28 dimensions unconditional,
1 honestly deferred (i18n), 0 failed.
