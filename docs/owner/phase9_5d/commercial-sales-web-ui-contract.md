# Phase 9.5D — Milestone 19: Commercial-Sales Web UI Contract

`app/commercial_sales/routes.py` (blueprint `commercial_sales_web`, no url_prefix — top-level paths like `/quotes`, `/orders`, matching how `/leads`/`/customers` are top-level) + 16 templates under `app/templates/commercial_sales/`. Follows `app/leads/routes.py`'s exact conventions: Post/Redirect/Get, no destructive GET, CSRF via the global `csrf.init_app(app)`, fail-closed ownership (`_model_or_none()` mirrors `_lead_or_none()`), error codes carried via `?error=CODE` query string and localized only at render time via `localize_commercial_sales_error()`/`localize_commission_error()` (Milestone 19 prep, committed earlier this milestone) — never in the service layer.

## Pages

Quotes (list/new/detail with inline line-add/remove/submit/cancel/customer-decision/approval-decide), Orders (list/detail with confirm/cancel/fulfill/create-invoice), Invoices (list/detail with issue/void/allocate-payment/reverse-allocation/request-refund), Payments (list/new/detail with confirm/reject), Refunds (list/detail with approve/confirm/void), Commissions (list with approve), Commission payout batches (list/create/approve/pay-entry), and the two Milestone 17 dashboards (`/commercial-dashboard`, `/commercial-dashboard/finance`).

Nav links added to `layout/base.html`'s shared tab bar, unconditional for any logged-in staff member — matching every other nav item's existing convention in this codebase (the route's own `@require_permission` decorator is the actual enforcement point, not nav visibility).

## Scope reduction: payment allocation and refund source-payment lookup are free-text UUID fields

The invoice detail page's "Allocate payment"/"Request refund" forms take a raw payment UUID text input rather than a scoped dropdown of the customer's confirmed payments. A proper customer-scoped payment picker is real, additional UI work with no service-layer counterpart yet (no `list_confirmed_payments_for_customer()` query exists) — deferred as a known, documented UX gap rather than either blocking this milestone or inventing an unreviewed query function under time pressure. The underlying operation (`allocate_payment()`/`create_refund()`) is fully correct and tested regardless of how the payment ID reaches it.

## Test coverage

`tests/test_phase9_5d_web_commercial_sales.py` — 3 real HTTP-layer tests using form-encoded POSTs (matching actual browser submission, not JSON): the full Quote→Order→Invoice→Payment→Allocation chain end to end through the web routes (proving Post/Redirect/Get and template rendering, not just route existence), an IDOR check (peer Quote → 404 on the web route, mirroring the API's own IDOR proof), and a permission-denial check (finance dashboard → 403 for a SALES-only actor). Full real-browser (multi-viewport, RTL) validation is Milestone 25's dedicated job, matching Phase 9.5C's own "automated tests alone are not sufficient" precedent — not duplicated here.
