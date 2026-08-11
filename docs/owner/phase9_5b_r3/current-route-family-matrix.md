# Phase 9.5B-R3 — Current Route-Family Matrix

Built from real blueprint registration (`app/__init__.py`) and real
route/template inventory (`docs/owner/phase9_5b_r2/complete-owner-surface-inventory.md`,
re-confirmed, not re-derived from memory). 173 real routes, 19 blueprints,
67 templates, 0 dead templates, 0 future/unregistered surfaces.

| # | Family | Blueprint(s) | Representative pages |
|---|---|---|---|
| 1 | Global layout / main dashboard | `dashboard` | `/` |
| 2 | Authentication | `auth` | `/auth/login`, `/auth/logout` |
| 3 | MFA / invitation / setup / recent-auth | `auth` | `/auth/mfa-verify`, `/auth/mfa-enroll`, `/auth/accept-invitation`, `/auth/reauth` |
| 4 | Employee management | `employees` | `/employees`, `/employees/<id>`, `/employees/new`, `/employees/dashboard` |
| 5 | Employee self-profile and sessions | `profile` (served by `employees.self_routes`) | `/profile`, `/profile/sessions` |
| 6 | Customer administration | `customers` | `/customers`, `/customers/<id>`, `/customers/new` |
| 7 | Product/platform + plans/pricing/add-ons/entitlements catalog | `catalog`, `releases` | `/catalog`, `/catalog/plans/new`, `/catalog/plans/<id>` |
| 8 | Subscriptions, renewals, pilots, commercial payments | `subscriptions`, `commercial_ops_ui` (renewals/pilots) | `/subscriptions`, `/subscriptions/<id>`, `/commercial-ops/ui/renewals`, `/commercial-ops/ui/pilots` |
| 9 | Licenses and license lifecycle | `licensing` | `/licenses`, `/licenses/<id>`, `/licenses/new` |
| 10 | Installations, device policies, replacements, exceptions | `installations`, `commercial_ops_ui` (slot exceptions) | `/installations`, `/installations/<id>`, `/commercial-ops/ui/licenses/<id>/slot-exceptions` |
| 11 | Audit | `audit` | `/audit`, `/audit/security-events`, `/audit/verify-chain` |
| 12 | Backup and restore | `system` | `/system/backups` |
| 13 | Notifications and operational queues | `commercial_ops_ui` | `/commercial-ops/ui/notifications`, `/commercial-ops/ui/queue`, `/commercial-ops/ui/reconciliation` |
| 14 | Security/staff administration | `staff`, `licensing_admin` | `/staff`, `/staff/<id>`, `/licensing-admin`, `/licensing-admin/signing-keys` |
| 15 | Error/access-denied pages | (shared error handlers + `commercial_ops/not_found.html`) | 403/404 responses, `commercial_ops/not_found.html` |
| 16 | Emergency extensions / activation policy / pending activations | `commercial_ops_ui` | `/commercial-ops/ui/emergency-extensions`, `/commercial-ops/ui/activation-policy`, `/commercial-ops/ui/pending-activations` |

## Excluded (per governing spec's own instruction)

- **API-only routes**: `api`, `api_external`, `api_operations`,
  `commercial_ops` (base JSON blueprint), `licensing_api`, `health` — 0
  template renders, JSON/redirect only.
- **CLI commands**: `flask create-superadmin`, `seed-*`, `licensing
  generate-signing-key`, etc. — not HTTP routes.
- **Future/unregistered routes**: none found (confirmed in Phase 9.5B-R2's
  own inventory, re-confirmed here).
- **Dead templates**: none found.

16 real families, all with at least one real, currently reachable page —
this is the authoritative list Milestone 5's browser validation targets.
