# Phase 5 -- Owner RBAC Matrix (Part G)

46 permission codes across 9 categories (STAFF, CATALOG, CUSTOMERS, SUBSCRIPTIONS, FINANCE, LICENSES, INSTALLATIONS, AUDIT, SYSTEM) -- full list in `owner/app/staff/seed_data.py::PERMISSIONS`. Every route is decorated with `@require_permission("<code>")`; there is no role-name check anywhere in route code (`grep -rn "role ==" owner/app` returns nothing).

## Roles

| Role | Grant basis | Notable exclusions |
|---|---|---|
| **SUPER_ADMIN** | All permissions (both via `StaffUser.is_super_admin` bypass in `get_staff_permission_codes()` AND an explicit full role-permission grant, belt-and-suspenders) | None. Mandatory MFA (`mfa_required=True` set at creation). |
| **SALES** | `catalog.view`, `pricing.view`, `customers.*` (view/create/update/archive/contacts/notes), `subscriptions.*` except suspend, `licenses.view`+`licenses.create`, `installations.view` | No staff administration (`staff.*`), no pricing changes, no license issuance/suspend/revoke/replace, no database restore |
| **SUPPORT** | `catalog.view`, `customers.view`+`manage_notes`, `subscriptions.view`, `licenses.view`, `installations.*` (view/register/update/suspend) | No pricing/payment changes, no staff role changes, no license issuance |
| **FINANCE** | `catalog.view`+`manage_prices`, `pricing.view`, `customers.view`, `subscriptions.view`+`renew`, `payments.*` | No license-secret issuance, no installation changes, no staff administration |
| **VIEWER** | `catalog.view`, `customers.view`, `subscriptions.view`, `licenses.view`, `installations.view` (read-only) | Every write permission |

## Recent-auth-gated actions (Part F)
Independent of role permissions, these routes additionally require `@require_recent_auth` (MFA confirmation within `OWNER_RECENT_AUTH_SECONDS`, default 600s): staff role changes, staff disablement, license issuance, license status transitions, license replacement, Owner database backup, Owner database restore, audit export.

## Test evidence
`owner/tests/test_rbac.py` (8/8 passing): direct-API-call attempts by under-permissioned roles, Viewer read-only confirmed, Support blocked from pricing, Sales blocked from staff management, Finance blocked from license issuance, unauthenticated redirect, Super Admin bypass. `owner/tests/test_security.py::test_privilege_escalation_role_change_requires_recent_auth` additionally proves a role change is rejected without a fresh MFA confirmation even for an authenticated Super Admin.
