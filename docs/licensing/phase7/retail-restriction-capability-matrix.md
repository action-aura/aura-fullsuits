# Phase 7 -- Retail Restriction Capability Matrix

Built directly from every `@retail_bp.route` in `products/retail/backend/api/retail_api.py` (43 routes, grepped during Part A). Implemented only after Clinic's capability guard is built and passes (Part R's explicit sequencing) -- this document is written now, alongside Clinic's, so both matrices are reviewed together for consistency, but the Retail *code* is not started until `phase7-implementation-plan.md`'s Clinic-first milestone is met.

## Route -> capability mapping

| Route | Method | Capability | Allowed when RESTRICTED/EXPIRED/SUSPENDED/REVOKED? |
|---|---|---|---|
| `/dashboard/stats` | GET | `retail.records.read` | Yes |
| `/categories` | GET | `retail.records.read` | Yes |
| `/categories` | POST | `retail.product.create` | **No** |
| `/products` | GET | `retail.records.read` | Yes |
| `/products` | POST | `retail.product.create` | **No** |
| `/products/<id>` | PATCH | `retail.product.update` | **No** |
| `/products/<id>` | DELETE | `retail.product.update` | **No** |
| `/products/<id>/stock-adjust` | POST | `retail.stock.adjust` | **No** |
| `/customers` | GET | `retail.records.read` | Yes |
| `/customers` | POST | `retail.staff.manage`-adjacent -- actually customer records, mapped to `retail.records.read` for GET / a new `retail.customer.create` capability for POST (not in the spec's suggested list; added because the route exists and must be classified, not left ungoverned) | **No** for POST |
| `/customers/<id>` | PATCH | `retail.customer.create` (same capability as creation -- both are customer-record writes) | **No** |
| `/customers/<id>/sales` | GET | `retail.records.read` | Yes |
| `/suppliers` | GET | `retail.records.read` | Yes |
| `/suppliers` | POST | `retail.supplier.manage` | **No** |
| `/suppliers/<id>` | PATCH | `retail.supplier.manage` | **No** |
| `/purchase-orders` | GET | `retail.records.read` | Yes |
| `/purchase-orders` | POST | `retail.purchase.create` | **No** |
| `/purchase-orders/<id>` | GET | `retail.records.read` | Yes |
| `/purchase-orders/<id>/receive` | POST | `retail.purchase.create` | **No** |
| `/sales` | POST | `retail.sale.create` | **No** |
| `/sales/recent` | GET | `retail.records.read` | Yes |
| `/sales/<id>` | GET | `retail.records.read` | Yes |
| `/returns` | GET | `retail.records.read` | Yes |
| `/returns` | POST | `retail.return.create` | **Policy decision required -- see below** |
| `/held-sales` | GET | `retail.records.read` | Yes |
| `/held-sales` | POST | `retail.sale.create` (feat/pos-hold-resume-sale -- same gate as `/sales` POST; holding a cart is part of the same new-sale workflow, not a distinct capability) | **No** |
| `/held-sales/<id>/resume` | POST | `retail.sale.create` (same reasoning) | **No** |
| `/held-sales/<id>` | DELETE | `retail.sale.create` (same reasoning -- discarding a held draft is still part of the gated new-sale workflow, consistent with every other mutation route in this file carrying a capability decorator) | **No** |
| `/printer/test` | POST | `retail.printer.test` (2026-09-08, the ESC/POS hardware test: it prints a fixed sample and can fire the drawer, so it is a mutation of the physical till even though it persists no row) | **No, as shipped -- MEASURED, not assumed: `retail.printer.test` is absent from `RETAIL_RESTRICTED_ALLOWLIST`, so a restricted or expired install is refused with `LICENSE_INACTIVE`.** Whether that is right is a **policy decision, like `/returns` POST below**, and it is deliberately not being made here: the argument for allowing it is that checking a printer and a cash drawer is exactly what a shop does while it waits for a key to land, and refusing it strands someone who has already paid; the argument against is that it drives real hardware and is not read-only. Allowing it is one entry in that allowlist and one line in `test_pre_activation_states_deny_new_mutation`'s expectations -- do not add it without deciding on purpose |
| `/printer/devices` | GET | none today -- it reads the OS printer list and touches no shop data, so it carries only the session/capability decorators, not `require_license_capability` | n/a |
| `/reports/sales-trend` | GET | `retail.report.view` | Yes |
| `/reports/top-products` | GET | `retail.report.view` | Yes |
| `/reports/payment-methods` | GET | `retail.report.view` | Yes |
| `/reports/summary` | GET | `retail.report.view` | Yes |
| `/branches` | GET | `retail.records.read` | Yes |
| `/branches` | POST | `retail.settings.update` | **No** |
| `/settings/credit` | GET | `retail.records.read` | Yes |
| `/settings/credit` | POST | `retail.settings.update` | **No** |
| `/settings/tax` | GET | `retail.records.read` | Yes |
| `/settings/tax` | POST | `retail.settings.update` | **No** |
| `/payment-methods` | GET | `retail.records.read` | Yes |
| `/payment-methods` | POST | `retail.settings.update` | **No** |
| `/customers/receivables` | GET | `retail.report.view` | Yes |
| `/customers/<id>/statement` | GET | `retail.report.view` | Yes |
| `/customers/<id>/payments` | POST | `retail.customer.create`-adjacent -- payment recording, new capability `retail.customer.payment.record` (not in spec's suggested list, added for completeness) | **Same policy question as returns/refunds below** |
| `/suppliers/payables` | GET | `retail.report.view` | Yes |
| `/suppliers/<id>/statement` | GET | `retail.report.view` | Yes |
| `/suppliers/<id>/payments` | POST | `retail.supplier.manage` | **No** |
| `/purchase-orders/<id>/pay` | POST | `retail.purchase.create` | **No** |
| `/reports/daily-cash` | GET | `retail.report.view` | Yes |
| `/reports/aging` | GET | `retail.report.view` | Yes |
| `/payments/<id>/void` | POST | `retail.settings.update` (financial-correction action, admin-grade) | **No** |
| `/demo-wipe` | DELETE | dev/onboarding only | **No** |
| `/demo-seed` | POST | dev/onboarding only | **No** |

Not yet present as routes, reserved per the spec's suggested capability list for the guard decorator (`retail.shift.open`, `retail.shift.close`, `retail.receipt.reprint`, `retail.backup.create`, `retail.backup.restore`, `retail.data.export`, `retail.receipt.print`, `retail.barcode.scan`, `retail.license.manage`): no backend route currently models shift open/close as a distinct concept in `retail_api.py` (confirmed by the full route grep -- if shift lifecycle exists, it's client-side state, not a guarded backend mutation today; re-checked at Part R implementation time before assuming this capability is a no-op). Backup/restore again live in the separate `commercial_runtime.backup` blueprint. Barcode scanning and receipt printing/reprinting are Android/Windows client-side operations with no dedicated backend mutation route to guard (the scan result flows into `/sales` POST, already covered).

## Decision (resolved by reading `retail_api.py`'s actual handler bodies during Part R implementation)

- `POST /returns` (`create_return`) is a server-authoritative REVERSAL of an existing, already-recorded sale -- every client-supplied price/discount/tax figure is ignored; refund amounts are always recomputed from the original `sale_items` row, and the requested quantity is capped at what remains returnable against that specific prior sale. It creates no new pricing power and no new stock beyond restoring what was already sold. Matches option (a) from the spec's two documented choices: blocking a lawful refund obligation could itself cause customer harm. **Allowed** (`retail.return.create`).
- `POST /customers/<id>/payments` (`customer_payment`) reduces a customer's outstanding credit balance -- money coming IN, paying down debt the customer already owes. Cash-flow positive for the business and directly goodwill-critical (a customer who shows up specifically to pay their balance must not be turned away by a licensing lapse). **Allowed** (`retail.customer.payment.record`, new capability code).
- `POST /suppliers/<id>/payments` (`supplier_payment`) sends money OUT at staff discretion -- not a customer-facing continuity obligation the way the two lines above are. **Blocked** (`retail.supplier.manage`), consistent with the general principle that discretionary outbound financial activity is the more conservative default.
- `POST /purchase-orders/<id>/pay` is the same outbound-payment shape as supplier payments. **Blocked** (`retail.purchase.create`).

## Baseline restricted-mode policy (applies regardless of how the two open questions above resolve)

**Always allowed**: every `retail.records.read` / `retail.report.view` route, backup/restore/export (once those Part T routes exist), licensing status/activation/deactivation, normal staff login, and safe closing of an already-open shift if/when shift lifecycle is confirmed to exist as a real backend concept.

**Always blocked**: new ordinary sales, new purchases, unauthorized stock adjustment, new product/supplier/customer creation, new branch/staff/settings changes, `/demo-wipe` and `/demo-seed`. The license guard performs no financial calculation of its own and never touches `retail_api.py`'s pricing/tax logic -- it only decides whether a route may begin executing at all, before any of that logic runs (Part R: "must not change financial calculations").
