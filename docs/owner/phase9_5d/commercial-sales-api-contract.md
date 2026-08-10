# Phase 9.5D — Milestone 18: Commercial-Sales Operations API Contract

`app/api_operations/commercial_sales.py` — a second Blueprint on the same `/api/operations/v1` prefix as `app/api_operations/crm.py` (Flask allows several blueprints sharing one url_prefix, same pattern Phase 9.5C established). Delegates entirely to the existing `app/commercial_sales/` and `app/commissions/` services built in Milestones 3–17 — no business logic in this file, only request parsing, permission checks, ownership-scoped fetch-or-404, and response serialization.

## Route surface

| Domain | Routes |
|---|---|
| Quotes | `GET/POST /quotes`, `GET /quotes/<id>`, `POST /quotes/<id>/lines`, `DELETE /quotes/<id>/lines/<line_id>`, `POST /quotes/<id>/submit`, `POST /quotes/<id>/cancel`, `POST /quotes/<id>/decision` |
| Approvals | `POST /approvals/<id>/decide` |
| Orders | `GET/POST /orders`, `GET /orders/<id>`, `POST /orders/<id>/confirm`, `POST /orders/<id>/cancel`, `POST /orders/<id>/fulfill` |
| Invoices | `GET/POST /invoices`, `GET /invoices/<id>`, `POST /invoices/<id>/issue`, `POST /invoices/<id>/void` (+ `require_recent_auth`) |
| Payments | `POST /payments`, `GET /payments/<id>`, `POST /payments/<id>/confirm`, `POST /payments/<id>/reject` |
| Allocations | `POST /allocations`, `POST /allocations/<id>/reverse` |
| Refunds | `POST /refunds`, `GET /refunds/<id>`, `POST /refunds/<id>/approve`, `POST /refunds/<id>/confirm`, `POST /refunds/<id>/void` |
| Commissions | `GET /commissions`, `POST /commissions/<id>/approve` |
| Payouts | `POST /commission-payout-batches`, `POST /commission-payout-batches/<id>/approve`, `POST /commission-payout-batches/<id>/pay/<entry_id>` |
| Dashboards | `GET /commercial/dashboard/employee`, `GET /commercial/dashboard/finance` (Milestone 17) |

Every mutating route requires the exact permission the funnel contract (`commercial-funnel-contract.md`) specifies for that action; list/detail routes accept either the create-tier or approve-tier permission (`require_any_permission`) and ownership-scope the result set accordingly, matching `crm.py`'s established `_own_or_all` pattern.

## Real gap found and closed this milestone

`payments.create` was granted to `FINANCE` only (Phase 9.5A's original seed). `submit_payment()`'s own docstring calls it a "sales-employee-facing" action, and `payment-maker-checker-policy.md` (Milestone 10) explicitly deferred the SALES-grant decision to "Milestone 16/19 ... at the route/permission-grant level" — this is that moment. Granted to `SALES` too; `payments.confirm` remains `FINANCE`-only, preserving the maker-checker separation. See `commercial-sales-sod-matrix.md` for the updated model.

## JSON boundary coercion

Every service function in `commercial_sales`/`commissions` expects real `Decimal`/`date` instances — none of them perform internal coercion (unlike `leads/validation.py`'s `estimated_value`, which does `Decimal(str(value))` itself). Since `request.get_json()` never produces `Decimal`, every monetary field is explicitly coerced via a `_to_decimal()` helper (`Decimal(str(value))`, never `Decimal(value)` directly — floats have no exact binary representation, so `Decimal(0.1) != Decimal("0.1")`) and every date field via `date.fromisoformat(...)`, matching the existing `datetime.fromisoformat()` precedent in `crm.py`/`leads/routes.py`.

## Fail-closed IDOR pattern (reused, not reinvented)

`_model_or_404()` mirrors `crm.py::_lead_or_404()` exactly: an actor holding neither the `*_all`/approve-tier permission nor a real `EmployeeProfile` is denied — never granted by default because the ownership check had nothing to compare against. A peer's Quote/Order/Invoice returns `404`, never `403` — existence itself is not confirmed to an unauthorized actor.

## Test coverage

`tests/test_phase9_5d_api_commercial_sales.py` — 5 real HTTP-layer tests: the full Quote→Order→Invoice→Payment→Allocation chain end to end through the test client (proving route wiring, not just service-layer correctness, which Milestones 3–17 already proved), an IDOR check (peer Quote → 404), two permission-denial checks (`orders.approve`/`payments.confirm`/`commissions.view_all` all correctly reject a `SALES`-only actor with 403), and a stale-version 409. Full HTTP-layer IDOR/segregation coverage across every route is Milestone 23's dedicated job, not duplicated here.
