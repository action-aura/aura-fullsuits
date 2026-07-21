# Phase 5 -- Aura Owner Domain Map

## Bounded contexts (`owner/app/*`)
| Package | Owns | Depends on |
|---|---|---|
| `security` | password hashing, MFA/TOTP, session cookies, CSRF, rate limiting, headers | nothing (leaf) |
| `auth` | login/logout, session model, bootstrap CLI, invitations | `security`, `models`, `audit` |
| `staff` | staff users, role assignment | `auth`, `models`, `audit` |
| `catalog` | products, platforms, versions, release channels, plans, prices, add-ons, entitlements | `models`, `audit` |
| `customers` | customer orgs, contacts, notes | `models`, `audit` |
| `subscriptions` | subscriptions, items, add-ons, status history, renewals, payments | `models`, `catalog`, `customers`, `audit` |
| `licensing` | licenses, license status history, entitlements, key issuance | `models`, `subscriptions`, `security`, `audit` |
| `installations` | installations, devices, activation events | `models`, `licensing`, `audit` |
| `audit` | append-only hash-chained audit log, security events | `models` (leaf among domain packages) |
| `dashboard` | read-only aggregation queries across the above | all read-only |
| `api` | future-contract JSON Schemas + `OWNER_EXTERNAL_API_ENABLED` gate (inactive) | `contracts/` |
| `services` | cross-cutting helpers (idempotency keys, pagination, CSV export) | leaf |

## Dependency rule
Arrows point one way: `auth`/`staff`/`catalog`/`customers` -> `subscriptions` -> `licensing` -> `installations`. No package imports "downward" (e.g. `catalog` never imports `licensing`). `audit` is called by everyone, calls no one back.

## Entity relationship summary
```
owner_staff_users --(assigned via)--> owner_staff_role_assignments --> owner_roles --> owner_role_permissions --> owner_permissions
owner_customers --> owner_customer_contacts / owner_customer_addresses / owner_customer_notes
owner_customers --> owner_subscriptions --> owner_subscription_items --> owner_plans --> owner_products / owner_platforms
owner_subscriptions --> owner_subscription_addons --> owner_addons
owner_subscriptions --> owner_renewal_records / owner_payment_records
owner_subscriptions --> owner_licenses --> owner_license_entitlements / owner_license_status_history / owner_license_key_issuance_events
owner_licenses --> owner_installations --> owner_device_records
owner_installations --> owner_activation_events
(everything sensitive) --> owner_audit_log (hash-chained, append-only)
```

## API-first note
Every domain package exposes a plain-Python service module (`catalog/services.py`, `licensing/services.py`, ...) with functions taking/returning dataclasses -- Flask routes and CLI commands both call the same services. This is what "reusable by the future activation API without coupling to the current UI" means concretely: the future Phase 6/7 activation endpoints will call `licensing/services.py` and `installations/services.py` directly, not scrape HTML or reimplement logic.
