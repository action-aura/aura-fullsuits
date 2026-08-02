# Phase 9.5D — Milestone 16: Commercial-Sales Segregation-of-Duties Matrix

RBAC enforcement in this codebase is a **route-layer** concern (`require_permission(...)` decorators, per every existing `routes.py`) — service functions never check permission codes directly. No commercial-sales/commission routes exist yet (Milestones 18/19 build them). Milestone 16's real work is therefore: verify and, where necessary, fix the underlying permission catalog (`app/staff/seed_data.py`) *before* those routes are built on top of it, so the routes inherit a correct segregation-of-duties model for free instead of encoding one ad hoc.

## The real gap this milestone found and closed

`quotes.approve` and `orders.approve` were pre-seeded in Phase 9.5A (per Milestone 1's audit) but never granted to any concrete role — only `SUPER_ADMIN` (via the `*` wildcard) could ever confirm a Sales Order or approve a quote/pricing exception. Fixed by granting both to `FINANCE`, matching the same money-authorization mandate FINANCE already holds for `invoices.issue`/`refunds.approve`/`commissions.approve`/`commissions.pay` (see `seed_data.py`'s own comment at the grant site). A second, smaller inconsistency was also found and fixed: `commercial-funnel-contract.md`'s "Record customer decision" row cited a permission code (`quotes.record_customer_decision`) that was never actually seeded — corrected to `quotes.create` (own) / `quotes.approve` (any), matching the pattern of every other own-quote action in that same table.

## The two-tier model

| Tier | Verbs | Who |
|---|---|---|
| Creation / pipeline-ownership | `quotes.create`, `orders.create`, `invoices.create` | `SALES` only |
| Money-authorization / approval | `quotes.approve`, `orders.approve`, `invoices.issue`, `refunds.create`, `refunds.approve`, `pricing.override`, `commissions.approve`, `commissions.pay`, `commissions.reverse` | `FINANCE` (all except `pricing.override`, which stays `SUPER_ADMIN`-only via the wildcard — a catalog-price bypass is a stronger authority than a discount approval) |
| Evidence submission | `payments.create` | **Both** `SALES` and `FINANCE` (Milestone 18 addendum — see below) |

## Milestone 18 addendum: `payments.create` is dual-granted, deliberately

Unlike every other creation-tier verb, `payments.create` is granted to both `SALES` and `FINANCE` — not SALES-exclusive. This is not a maker-checker violation: `payments.create` only records "money was received," it never itself confirms anything (`payments.confirm` is the real authorization gate, `FINANCE`-only, never granted to `SALES`). A salesperson submitting evidence of a payment they personally collected in the field is the documented real-world workflow (`submit_payment()`'s own docstring: "Sales-employee-facing 'I received this payment, please confirm it' action"); FINANCE may also record a payment it received directly (e.g., a bank transfer with no salesperson involved). Either way, the same individual who submitted a payment can never also confirm it (`confirm_payment()`'s unconditional `SELF_CONFIRMATION_FORBIDDEN` check, `payment-maker-checker-policy.md`) — the real separation guarantee is, once again, enforced at the actor level, not merely by which role can create vs. approve.

`SALES` never holds any permission from the approval tier. `FINANCE` never holds any permission from the creation tier (it is not a sales-pipeline role — it does not own Quotes/Orders/Invoices, it authorizes them). `SUPPORT` and `VIEWER` hold neither tier.

## Why `refunds.create` + `refunds.approve` coexisting on `FINANCE` is not a violation

Unlike Quote/Order, `CommercialRefund` creation and approval are **both** money-authorization actions (a refund is initiated by Finance reviewing a customer request, not by the salesperson who made the original sale) — so both verbs legitimately sit on the same role. The real separation-of-duties guarantee here is enforced one level down, at the **individual actor** level, not the role level: `refunds.py::approve_refund()` unconditionally blocks the refund's own creator from approving it (`SELF_APPROVAL_FORBIDDEN`), regardless of what role grants they hold — proven in Milestone 12's test suite. The same actor-level pattern (not role-level) is how every other self-approval rule in this phase works: `CommercialApproval.decide_approval()`, `payments.py::confirm_payment()`, `commissions/ledger.py::approve_commission_entry()`/`approve_payout_batch()`. Role-level separation (this milestone's fix) and actor-level self-approval blocks (Milestones 6/10/12/15, already built) are complementary, not redundant: role separation stops a *class* of person from ever touching the approval step; the self-block stops the *specific individual* who made the request from being the one who signs off on it, which a coarser role split alone cannot guarantee (two different FINANCE employees must always be involved, but which two is never fixed in advance).

## Test coverage

`tests/test_phase9_5d_commercial_sales_rbac.py` — 6 static-data tests over `app.staff.seed_data.ROLES`, extending `test_phase9_5a_rbac_restrictions.py`'s established pattern: SALES never holds an approval-tier permission; FINANCE never holds a creation-tier permission; `quotes.approve`/`orders.approve` are now real (granted to FINANCE); no non-SUPER_ADMIN role holds both a create and an approve verb for the same document type; SUPPORT/VIEWER hold neither tier; the full `commissions.approve`/`pay`/`reverse` trio (not just `pay`, which the Phase 9.5A test already covered) is never SALES-grantable.
