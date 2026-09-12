"""AUDIT-owner-cross-screen: one typed contract per dashboard metric
payload that more than one screen reads.

Why this module exists. Every dashboard payload in Owner CC used to be a
bare dict, and every consumer -- Jinja template, JSON API twin, another
service -- bound to it by loose string key. That makes a producer-side
rename completely silent in both directions:

  * Jinja's default Undefined renders as the empty string, so when
    finance_commercial_dashboard()'s "outstanding_invoice_total" became
    the per-currency "outstanding_invoice_totals", the Finance
    dashboard's "Outstanding invoice total" row went permanently blank
    while the JSON twin and the Management dashboard kept showing real
    money. Nothing raised. Nothing logged. The screens simply disagreed.
  * jsonify() on the producer side is just as happy to ship a key nobody
    reads, so a dead key can linger for a whole phase.

The fix is deliberately small: each payload below is a frozen dataclass
whose FIELD SET IS THE CONTRACT.

  * Producers build the dataclass and return ``.to_payload()``. A missing
    field or a typo'd/renamed one is a TypeError raised at the producing
    call itself -- at import/first-request time in a test run, not as a
    blank <dd> in production.
  * Consumers are checked against ``field_names()`` by
    tests/test_owner_metric_contracts.py, which scans each consuming
    template for the key it reads and checks each JSON route's real
    response keys. A rename a consumer missed fails a test instead of
    shipping a blank number.

    Precisely what the template scan covers, because a control against
    silent renames must not overstate its own reach: the three ways a
    Jinja template can name a key on the payload it was rendered with --
    ``data.key``, ``data["key"]`` and ``data.get("key")``. What it does
    NOT cover is a key named by something other than a literal:
    ``data[some_var]``, a payload re-bound to another name before it is
    read (``{% set d = data %}``), or a key assembled at runtime. Those
    are not used by any template in TEMPLATE_CONTRACTS today, and
    introducing one would step outside this guarantee -- so don't,
    without widening the scan in the same change.

Deliberately NOT a framework: no registry, no runtime validation layer,
no serializer, no base-class magic beyond two three-line helpers. Four
dataclasses covering exactly the payloads this pass touched. Adding a
fifth is a copy of eight lines; that is the intended cost.

Money values keep the shape each payload already shipped (Decimal for
the commercial dashboards, pre-stringified str for the operational ones,
whose JSON routes jsonify the payload directly) -- this module documents
the existing contract, it does not silently re-serialize anything.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal


@dataclass(frozen=True)
class _MetricPayload:
    """Shared conversion helpers only -- declares no fields of its own, so
    subclasses stay ordinary frozen dataclasses."""

    def to_payload(self) -> dict:
        # Shallow by design: the nested dicts are already plain,
        # JSON-serializable values built by the producer, and a deep copy
        # (dataclasses.asdict) would needlessly rebuild every one of them
        # on every dashboard request.
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def field_names(cls) -> frozenset[str]:
        return frozenset(f.name for f in fields(cls))


@dataclass(frozen=True)
class EmployeeCommercialDashboard(_MetricPayload):
    """app.commercial_sales.dashboards.employee_commercial_dashboard().

    Consumed by templates/commercial_sales/employee_dashboard.html and by
    api_operations/commercial_sales.py::employee_commercial_dashboard_route.

    `currency` is the single currency the four scalar commission figures
    are scoped to (they were blended across currencies before this pass --
    a USD earning plus a JOD earning added up to one meaningless number).
    `commission_totals_by_currency` carries every currency this employee
    actually has entries in, so scoping the scalars hides nothing: it is
    the same numbers, keyed by currency, and the scalars are derived from
    it rather than re-queried, so the two can never drift apart.
    """

    currency: str
    quotes_by_status: dict[str, int]
    orders_by_status: dict[str, int]
    invoices_by_status: dict[str, int]
    own_pending_approval_requests: int
    commission_earned_unapproved: Decimal
    commission_approved_unpaid: Decimal
    commission_paid_total: Decimal
    commission_reversed_total: Decimal
    commission_totals_by_currency: dict[str, dict[str, Decimal]]


@dataclass(frozen=True)
class FinanceCommercialDashboard(_MetricPayload):
    """app.commercial_sales.dashboards.finance_commercial_dashboard().

    Consumed by templates/commercial_sales/finance_dashboard.html and by
    api_operations/commercial_sales.py::finance_commercial_dashboard_route.

    `outstanding_invoice_totals` is per-currency (never one blended
    scalar) -- the rename that this contract exists to make loud.
    """

    quotes_by_status: dict[str, int]
    orders_by_status: dict[str, int]
    invoices_by_status: dict[str, int]
    quote_approvals_pending: int
    refunds_pending_approval: int
    commissions_pending_approval: int
    commissions_approved_unpaid: int
    payout_batches_pending_approval: int
    outstanding_invoice_totals: dict[str, Decimal]


@dataclass(frozen=True)
class ManagementOperationalDashboard(_MetricPayload):
    """app.operational_reports.dashboards.management_operational_dashboard().

    Consumed by templates/operations_ui/dashboard_management.html and by
    api_operations/expenses_and_operations.py::management_operational_dashboard_route.

    Every figure here is scoped to `currency` -- including the two queue
    counts that were company-wide before this pass.
    """

    currency: str
    expense_totals_by_category: dict[str, str]
    expense_totals_by_employee: dict[str, str]
    approval_queue_count: int
    duplicate_review_queue_count: int
    approved_unpaid_count: int
    partially_paid_count: int
    closing_variance_queue_count: int
    confirmed_collections: str
    confirmed_refunds: str
    recorded_commission_payouts: str
    paid_expenses: str
    net_operational_cash_movement: str
    outstanding_receivables: str
    overdue_invoices: int
    fulfillment_exceptions: int
    management_notes_requiring_action: int


@dataclass(frozen=True)
class FinanceOperationalDashboard(_MetricPayload):
    """app.operational_reports.dashboards.finance_operational_dashboard().

    Consumed by templates/operations_ui/dashboard_finance.html and by
    api_operations/expenses_and_operations.py::finance_operational_dashboard_route.
    """

    currency: str
    expense_approvals_pending: int
    approved_unpaid_expenses: int
    expense_payment_queue: int
    partial_settlements: int
    daily_cash_closing: dict | None
    closing_variance_queue_count: int
    payment_confirmations_pending: int
    unallocated_payments_total: str
    refund_queue_count: int
    commission_payout_queue_count: int
    net_operational_cash_movement: str
