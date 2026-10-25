"""The one-screen sale: a licence sold in one form, paid for, credited.

Step 0 (2026-10-24). The one-screen issuance (/licenses/issuance,
licensing/issuance.py::issue_license_direct) issued a licence with NO
payment and NO sales owner -- measured on 829c4cde: a licence and its key
out, zero PaymentRecord rows, zero SalesOrder rows, so the seller's
commission could never be counted and nothing tied the licence to money.
The owner's rules (Aura Growth Plan, "Your answers", 2026-10-24):
  - sellers are employees registered in Owner and never take cash; the
    customer pays the company (CliQ or bank transfer);
  - a manager -- not the seller, not whoever recorded it -- confirms each
    payment, and the licence key is issued only after that;
  - commission goes to the sale's seller, counted by the payment-confirmed
    date.

This module does not build a second money path. It only shortens how the
sale's documents are made; every money step is the existing pipeline's own
service, unchanged:

  record_direct_sale   (the seller's form; orders.create + payments.create)
      Customer (inline: assigned to the seller), SalesOrder DRAFT with
      catalog-priced lines, PaymentRecord PENDING (subscriptions.services
      .record_payment). NO invoice, NO licence. There is no Quote: a
      one-screen sale is a counter sale at the catalog price, no discount,
      no override, so nothing a Quote approval exists for can occur.
  confirm_direct_sale  (a manager; payments.confirm + orders.approve +
                        invoices.issue -- FINANCE / SUPER_ADMIN)
      confirm_payment -> confirm_order -> create_invoice_from_order (credited
      to the seller) -> issue_invoice -> allocate_payment (THIS posts the
      seller's commission, commissions/ledger.py) -> fulfill_order (the
      licence and its key, shown once).
  reject_direct_sale   reject_payment + cancel_order.

Why the invoice is made at confirmation and not at the seller's submit: an
invoice needs a CONFIRMED order (invoices.py), and confirming an order is
orders.approve, which SALES deliberately never holds (staff/seed_data.py,
commercial-sales-sod-matrix.md). Every authorising step happens in the
manager's action, never in the seller's.

The seller is SalesOrder.created_by_employee_profile_id. That column flows
on to CommercialInvoice.created_by_employee_profile_id -- the exact column
the commission ledger credits (ledger.py: post_earning_for_allocation) --
and to Subscription.sales_owner_staff_user_id (fulfillment.py). The
payment<->order link is a row in the shared ledger table, see
sale_attribution.py. No schema change.

Exactly once. Submitting the same form twice, or concurrently, is one
sale: the same three pieces issuance.py and fulfillment.py use, for the
same reason (every service called here commits on its own, so this cannot
be one outer transaction):
  1. every check runs before the first write -- a refused form writes nothing;
  2. a per-key advisory lock on a DEDICATED connection (one on db_session
     would be released by the first nested commit -- see
     docs/corrections/owner/fulfillment-lock-released-by-nested-commits-root-cause-analysis.md);
  3. the customer, order and payment ids are derived from the form's key
     (uuid5), so a replay finds the original rows and a retry after a crash
     part-way reuses what was committed; the order, its lines and the
     payment link commit in ONE transaction. A replay whose details differ
     from what the key was first used for is refused, never resolved to
     either sale.
"""
from __future__ import annotations

import hashlib
import uuid
from contextlib import contextmanager
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import String, cast, func, select, text
from sqlalchemy.exc import OperationalError

from app.audit.services import record as audit_record
from app.commercial_sales.allocation import allocate_payment
from app.commercial_sales.calculator import LineInput, calculate_document, calculate_line
from app.commercial_sales.catalog_for_sales import (
    DEVICES_PER_UNIT_BY_ADDON_CODE,
    EXTRA_DEVICE_ADDON_CODE,
    EXTRA_DEVICE_PAIR_ADDON_CODE,
    describe_addon_for_sale,
    describe_plan_for_sale,
)
from app.commercial_sales.errors import CommercialSalesError
from app.commercial_sales.fulfillment import fulfill_order
from app.commercial_sales.invoices import create_invoice_from_order, issue_invoice
from app.commercial_sales.numbering import allocate_document_number
from app.commercial_sales.payments import confirm_payment, reject_payment
from app.commercial_sales.sale_attribution import DIRECT_SALE_PAYMENT_LINK, direct_sale_payment_id_for_order
from app.commercial_sales.sales_orders import cancel_order, confirm_order
from app.customers.services import create_customer, customer_visible_to_actor
from app.extensions import db_session, get_engine
from app.licensing.issuance import compose_activation_whatsapp_message
from app.models.base import utcnow
from app.operational_reports.periods import jordan_today
from app.models.catalog import Addon, Plan
from app.models.commercial_sales import (
    CommercialInvoice,
    CommercialOperationsIdempotencyKey,
    PaymentAllocation,
    SalesOrder,
    SalesOrderLine,
)
from app.models.customers import Customer
from app.models.employees import EmployeeProfile
from app.models.licensing import License
from app.models.subscriptions import PaymentRecord, Subscription
from app.push.outbox import enqueue_sale_waiting
from app.subscriptions.services import lock_payment_for_update, record_payment

# Sellers never take cash: the customer pays the company by CliQ or bank
# transfer (owner, 2026-10-24). CliQ is a bank transfer in Jordan and the
# shared PAYMENT_METHODS list has no separate value for it, so the form
# shows BANK_TRANSFER as "CliQ / bank transfer". Widen this tuple -- not the
# check -- if the owner ever accepts another channel.
DIRECT_SALE_PAYMENT_METHODS = ("BANK_TRANSFER",)

# Seller-picking: a caller holding this may record a sale for any active
# employee (a manager entering a seller's sale). Anyone else -- SALES --
# can only ever be the seller of their own sale.
CHOOSE_SELLER_PERMISSION = "orders.approve"

# What confirming a one-screen sale needs: issuance_routes.confirm_sale's own
# decorators. Step 1 (2026-10-25): who is told a sale is waiting -- the push
# notification, the badge -- is derived from THIS set (app/push/recipients.py),
# and tests/test_step1_push.py proves a role holding exactly these can confirm
# and a role missing any one cannot, so "notified" and "may confirm" cannot
# drift apart.
CONFIRM_SALE_PERMISSIONS = frozenset({"payments.confirm", "orders.approve", "invoices.issue"})

# A one-screen sale is waiting for a manager while its order is not yet
# fulfilled or cancelled and its payment is recorded and not rejected -- the
# list below and the confirm both act on exactly these.
WAITING_ORDER_STATUSES = ("DRAFT", "CONFIRMED")
WAITING_PAYMENT_STATUSES = ("PENDING", "CONFIRMED")

_RECORD_LOCK_NAMESPACE = "commercial_sales.record_direct_sale"
_CONFIRM_LOCK_NAMESPACE = "commercial_sales.confirm_direct_sale"

# Fixed forever: a retry after a deploy that changed it would derive
# different ids and stop finding the rows its failed attempt left behind.
_ROW_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "urn:aura-owner:commercial_sales:direct_sale")

_MAX_REFERENCE_LENGTH = 128  # PaymentRecord.reference is String(128)

DEVICES_PER_PAIR = DEVICES_PER_UNIT_BY_ADDON_CODE[EXTRA_DEVICE_PAIR_ADDON_CODE]

# Step 0 fix round 3 -- sizes checked BEFORE any write. Measured on
# 580cd65a (verifier probe P3): extra_devices=10**9 on a package with no
# maximum reached the database as "numeric field overflow" -- a 500 -- with
# the inline customer already committed. A one-screen sale is a counter
# sale: this cap is a typo guard, far above any real device count (bigger
# deals go through a quote). The total cap is the money columns' own
# capacity (Numeric(12, 2): below 10^10), so no line, order, invoice or
# payment of a sale that passed this check can overflow later.
# Step 0 packages: the screen sells PAIRS (2 devices each), so the cap is
# half the old 1000 single devices -- the same device ceiling.
MAX_EXTRA_DEVICE_PAIRS_PER_SALE = 500
MAX_SALE_TOTAL = Decimal("9999999999.99")

# English, request-context-free, keyed by a stable code -- the
# CommercialSalesError pattern (commercial_ops/errors.py StableCodeError):
# str(exc) is this English text; the screens show
# i18n_labels.localize_direct_sale_error(exc.code, **exc.params), which holds
# the same sentences as translatable literals. Keep the two tables in step
# (tests/test_issuance_sale_hardening.py::test_every_direct_sale_message_is_translated).
DIRECT_SALE_MESSAGES = {
    "AMOUNT_NOT_A_NUMBER": "The amount paid must be a number, like 250.00.",
    "AMOUNT_NOT_POSITIVE": "The amount paid must be a number greater than zero.",
    "OWN_PROFILE_REQUIRED": "Recording a sale needs your own employee profile. Ask a manager to add one.",
    "SELLER_MUST_BE_SELF": "You can only record a sale as its seller yourself.",
    "SELLER_REQUIRED": "Pick the seller (sales owner) of this sale.",
    "SELLER_NOT_FOUND": "The selected seller was not found.",
    "SELLER_NOT_ACTIVE": "The seller must be an active employee.",
    "CUSTOMER_NOT_FOUND": "Selected customer was not found.",
    "CUSTOMER_ARCHIVED": "Selected customer is archived.",
    "CUSTOMER_REQUIRED": "Pick an existing customer or type the legal name of a new one.",
    "PACKAGE_NOT_FOUND": "Selected package was not found.",
    "PACKAGE_NOT_SELLABLE": "Selected package is not currently sellable (check its effective date and price).",
    "PACKAGE_NO_PLATFORMS": "Selected package has no supported platforms configured.",
    "EXTRA_PAIRS_NOT_A_NUMBER": "Extra device pairs must be a whole number.",
    "EXTRA_PAIRS_NEGATIVE": "Extra device pairs must not be negative.",
    "EXTRA_PAIRS_TOO_MANY": "One sale can add at most %(max)s extra device pairs. Check the number you typed.",
    "DEVICE_COUNT_OVER_PACKAGE_MAX": "Requested device count (%(requested)s) exceeds this package's maximum (%(max)s).",
    "EXTRA_PAIRS_NOT_ON_SALE": (
        "Extra device pairs are not on sale for this package yet: the Extra Device Pair add-on needs a price "
        "and must be Available in the catalog."
    ),
    "EXTRA_PAIR_CURRENCY_MISMATCH": "The Extra Device Pair add-on is priced in a different currency than this package.",
    "TOTAL_TOO_LARGE": "This sale's total is too large to record (at most %(max)s). Check the number of extra device pairs.",
    "ZERO_PRICE": "This package costs nothing, so there is no payment to record. Use a no-charge licence instead.",
    "METHOD_NOT_ALLOWED": "The customer pays the company by CliQ or bank transfer; sellers never take cash.",
    "REFERENCE_REQUIRED": "Type the CliQ or transfer reference number.",
    "REFERENCE_TOO_LONG": "The reference is too long (at most %(max)s characters).",
    "PAYMENT_DATE_REQUIRED": "Type the date the money arrived.",
    "PAYMENT_DATE_IN_FUTURE": "The payment date cannot be in the future.",
    "AMOUNT_NOT_FULL_PRICE": "The amount paid must be the full price of this sale: %(total)s %(currency)s.",
    "KEY_REQUIRED": "An idempotency key is required to record a sale.",
    "KEY_USED_FOR_DIFFERENT_SALE": (
        "This sale form was already used to record a different sale. Open a fresh form to record another one."
    ),
    "RECORD_IN_PROGRESS": "This sale is still being recorded from an earlier click. Wait a moment, then check the sales list.",
    "CONFIRM_IN_PROGRESS": "This sale is already being confirmed by someone else. Wait a moment, then reload the list.",
    "REJECT_IN_PROGRESS": "This sale is being confirmed by someone else right now. Reload the list.",
    "SALE_NOT_FOUND": "Sale not found.",
    "NOT_A_ONE_SCREEN_SALE": "This order was not recorded from the one-screen sale form.",
    "PAYMENT_NEVER_RECORDED": (
        "This sale's payment was never recorded (the form was interrupted). The seller should submit the same form again."
    ),
    "ORDER_NOT_CONFIRMABLE": "This sale is %(order_status)s; it cannot be confirmed.",
    "PAYMENT_NOT_CONFIRMABLE": "This sale's payment is %(payment_status)s; it cannot be confirmed.",
    "PAYMENT_DOES_NOT_MATCH_TOTAL": "This sale's payment does not match its total; it cannot be confirmed.",
    "PACKAGE_NO_LONGER_SELLABLE": "This sale's package is no longer sellable; it cannot be confirmed as it stands.",
    "REJECT_PAYMENT_CONFIRMED": (
        "This sale's payment is already confirmed; it can no longer be rejected (use a refund instead)."
    ),
    "ORDER_NOT_REJECTABLE": "This sale is %(order_status)s; it cannot be rejected.",
    "REJECT_MONEY_ALLOCATED": "This sale's payment was already allocated to an invoice; it can no longer be rejected here.",
    "REJECT_INVOICE_EXISTS": (
        "An invoice was already made for this sale; it can no longer be rejected here. Void the invoice first."
    ),
}


class DirectSaleError(ValueError):
    """An operator-facing refusal of the one-screen sale, with a stable
    code (DIRECT_SALE_MESSAGES) so the screens can show it in the reader's
    language. str(exc) is the English sentence. Errors raised by the reused
    commercial_sales services keep their own CommercialSalesError codes."""

    def __init__(self, code: str, **params):
        self.code = code
        self.params = params
        template = DIRECT_SALE_MESSAGES[code]
        super().__init__(template % params if params else template)


def _advisory_key(namespace: str, value: str) -> int:
    digest = hashlib.sha256(f"{namespace}|{value}".encode("utf-8")).hexdigest()
    return int(digest[:15], 16)  # fits in a Postgres bigint


@contextmanager
def _hold_advisory_lock(namespace: str, value: str, busy_code: str):
    """fulfillment.py::_hold_fulfillment_lock's mechanism, not a new one
    (read its docstring): a transaction-scoped advisory lock on a dedicated
    connection survives db_session's commits and is released when this
    exits -- or by Postgres itself if the process dies."""
    conn = get_engine().connect()
    try:
        txn = conn.begin()
        try:
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _advisory_key(namespace, value)})
        except OperationalError as exc:
            txn.rollback()
            if getattr(getattr(exc, "orig", None), "sqlstate", None) == "55P03":  # lock_timeout
                raise DirectSaleError(busy_code) from exc
            raise
        try:
            yield
        finally:
            txn.rollback()
    finally:
        conn.close()


def _row_id_for(idempotency_key: str, kind: str) -> uuid.UUID:
    return uuid.uuid5(_ROW_ID_NAMESPACE, f"{kind}|{idempotency_key}")


def extra_device_pair_offer(product_id) -> dict | None:
    """The sellable EXTRA_DEVICE_PAIR add-on for this product, or None. Its
    price is the only price an extra pair ever gets on this screen -- never a
    typed one. The owner sells extra devices ONLY as a pair (1 desktop + 1
    phone, 50 JOD once), so this screen never falls back to the single
    EXTRA_DEVICE add-on, even when that one is on sale (before the packages
    it sold single devices at that add-on's price). addon_code is unique
    catalog-wide, so only the product that add-on belongs to (Aura Retail)
    can sell extra pairs (named limitation)."""
    addon = db_session.execute(
        select(Addon).where(Addon.addon_code == EXTRA_DEVICE_PAIR_ADDON_CODE, Addon.product_id == product_id)
    ).scalars().first()
    if addon is None:
        return None
    return describe_addon_for_sale(addon.id)


def parse_amount(raw) -> Decimal:
    """Form text -> a finite, positive Decimal, or DirectSaleError (never a
    500: the legacy record route's NaN/Infinity/InvalidOperation lesson,
    829c4cde item 5)."""
    try:
        amount = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError) as exc:
        raise DirectSaleError("AMOUNT_NOT_A_NUMBER") from exc
    if not amount.is_finite() or amount <= 0:
        raise DirectSaleError("AMOUNT_NOT_POSITIVE")
    return amount


def _resolve_seller(sales_owner_employee_profile_id, *, actor_staff_user_id, actor_permission_codes: set[str]) -> EmployeeProfile:
    own = db_session.execute(
        select(EmployeeProfile).where(EmployeeProfile.staff_user_id == actor_staff_user_id)
    ).scalars().first()
    may_choose = CHOOSE_SELLER_PERMISSION in actor_permission_codes
    if not may_choose:
        # A seller can only sell as themselves: crediting another employee,
        # or being credited for someone else's sale, is a manager's call.
        if own is None:
            raise DirectSaleError("OWN_PROFILE_REQUIRED")
        if sales_owner_employee_profile_id and str(sales_owner_employee_profile_id) != str(own.id):
            raise DirectSaleError("SELLER_MUST_BE_SELF")
        seller = own
    else:
        if not sales_owner_employee_profile_id:
            raise DirectSaleError("SELLER_REQUIRED")
        try:
            seller = db_session.get(EmployeeProfile, uuid.UUID(str(sales_owner_employee_profile_id)))
        except ValueError:
            seller = None
        if seller is None:
            raise DirectSaleError("SELLER_NOT_FOUND")
    if seller.employment_status != "ACTIVE" or seller.archived_at is not None:
        raise DirectSaleError("SELLER_NOT_ACTIVE")
    return seller


def _resolve_customer(customer_id, new_customer_legal_name, *, actor_staff_user_id, actor_permission_codes):
    if customer_id:
        try:
            customer = db_session.get(Customer, uuid.UUID(str(customer_id)))
        except ValueError:
            customer = None
        # Not visible = not found: never confirms another seller's customer
        # exists. Visibility is the shared CRM ownership rule, so a seller
        # cannot hang a sale -- and its commission -- on someone else's customer.
        if customer is None or not customer_visible_to_actor(customer, actor_staff_user_id, actor_permission_codes):
            raise DirectSaleError("CUSTOMER_NOT_FOUND")
        if customer.archived_at is not None:
            raise DirectSaleError("CUSTOMER_ARCHIVED")
        return customer, ""
    legal_name = (new_customer_legal_name or "").strip()
    if not legal_name:
        raise DirectSaleError("CUSTOMER_REQUIRED")
    return None, legal_name


def _package_line_description(plan_item: dict) -> str:
    if plan_item["setup_fee"]:
        return f"{plan_item['name']} -- first period + {plan_item['setup_fee']} {plan_item['currency']} setup fee"
    return plan_item["name"]


def _price_sale(plan: Plan, extra_device_pairs: int) -> tuple[dict, dict | None, list[dict], Decimal, str]:
    """Catalog price of the sale, through the one shared calculator
    (calculator.py). Returns (plan item, add-on item, lines, total,
    currency); each line is {unit_price, quantity, addon_id,
    price_version_id, description}.

    Step 0 packages (2026-10-24). The package line is the package's
    FIRST-SALE price -- base_price + setup_fee (catalog_for_sales:
    first_sale_unit_price): the owner's Package 1 is 250 the first year =
    100 a year + 150 first time. Measured on 7571c3df: this priced
    base_price alone, refused 250 with "The amount paid must be the full
    price of this sale: 100.00 JOD." and sold Package 1 for 100. The setup
    fee stays ON the package line (its description says so), not a line of
    its own: one plan line per order is one package sold, which is what the
    commission ladder will count.

    Extra devices are sold only as PAIRS (EXTRA_DEVICE_PAIR, quantity =
    pairs); each pair is 2 devices against the package maximum. On 7571c3df
    a posted pair count was ignored: P1 at 100 with 2 pairs was recorded as
    one 100.00 package line and the licence had the included 2 devices."""
    plan_item = describe_plan_for_sale(plan.id)
    if plan_item is None:
        raise DirectSaleError("PACKAGE_NOT_SELLABLE")
    if not plan_item["supported_platform_codes"]:
        raise DirectSaleError("PACKAGE_NO_PLATFORMS")
    if extra_device_pairs < 0:
        raise DirectSaleError("EXTRA_PAIRS_NEGATIVE")
    if extra_device_pairs > MAX_EXTRA_DEVICE_PAIRS_PER_SALE:
        raise DirectSaleError("EXTRA_PAIRS_TOO_MANY", max=MAX_EXTRA_DEVICE_PAIRS_PER_SALE)
    device_limit = plan.included_device_count + extra_device_pairs * DEVICES_PER_PAIR
    if plan.max_device_count is not None and device_limit > plan.max_device_count:
        raise DirectSaleError("DEVICE_COUNT_OVER_PACKAGE_MAX", requested=device_limit, max=plan.max_device_count)

    lines = [{
        "unit_price": plan_item["first_sale_unit_price"], "quantity": 1, "addon_id": None,
        "price_version_id": plan_item["price_version_id"], "description": _package_line_description(plan_item),
    }]
    addon_item = None
    if extra_device_pairs > 0:
        addon_item = extra_device_pair_offer(plan.product_id)
        if addon_item is None:
            raise DirectSaleError("EXTRA_PAIRS_NOT_ON_SALE")
        if addon_item["currency"] != plan_item["currency"]:
            raise DirectSaleError("EXTRA_PAIR_CURRENCY_MISMATCH")
        lines.append({
            "unit_price": addon_item["unit_price"], "quantity": extra_device_pairs,
            "addon_id": uuid.UUID(addon_item["addon_id"]), "price_version_id": addon_item["price_version_id"],
            "description": addon_item["name"],
        })

    document = calculate_document(
        [LineInput(quantity=line["quantity"], unit_price=line["unit_price"], sort_order=i) for i, line in enumerate(lines)]
    )
    if document.total <= 0:
        raise DirectSaleError("ZERO_PRICE")
    if document.total > MAX_SALE_TOTAL:
        # Every line is at most the total, so this one check covers the
        # order, its lines, the invoice and the payment columns alike.
        raise DirectSaleError("TOTAL_TOO_LARGE", max=MAX_SALE_TOTAL)
    return plan_item, addon_item, lines, document.total, plan_item["currency"]


def _validate_payment(*, amount: Decimal, currency: str, total: Decimal, method: str, reference: str, payment_date, as_of: date):
    if method not in DIRECT_SALE_PAYMENT_METHODS:
        raise DirectSaleError("METHOD_NOT_ALLOWED")
    if not reference:
        raise DirectSaleError("REFERENCE_REQUIRED")
    if len(reference) > _MAX_REFERENCE_LENGTH:
        raise DirectSaleError("REFERENCE_TOO_LONG", max=_MAX_REFERENCE_LENGTH)
    if payment_date is None:
        raise DirectSaleError("PAYMENT_DATE_REQUIRED")
    if payment_date > as_of:
        raise DirectSaleError("PAYMENT_DATE_IN_FUTURE")
    if amount != total:
        # A one-screen sale is paid in full. A part payment would leave the
        # invoice PARTIALLY_PAID, which fulfillment rightly refuses (full
        # payment required) -- the operator would be told "recorded" and the
        # licence would never come. Refused here instead, with the real total.
        raise DirectSaleError("AMOUNT_NOT_FULL_PRICE", total=total, currency=currency)


def _addon_quantity_on(order: SalesOrder, addon_code: str) -> int:
    rows = db_session.execute(
        select(SalesOrderLine.quantity)
        .join(Addon, Addon.id == SalesOrderLine.addon_id)
        .where(SalesOrderLine.sales_order_id == order.id, Addon.addon_code == addon_code)
    ).scalars().all()
    return sum(rows)


def _extra_pairs_on(order: SalesOrder) -> int:
    return _addon_quantity_on(order, EXTRA_DEVICE_PAIR_ADDON_CODE)


def _plan_id_on(order: SalesOrder):
    return db_session.execute(
        select(SalesOrderLine.plan_id).where(SalesOrderLine.sales_order_id == order.id, SalesOrderLine.plan_id.is_not(None))
    ).scalars().first()


def _check_same_sale(order: SalesOrder, *, idempotency_key, customer, legal_name, plan_id, extra_device_pairs, seller) -> None:
    """A key names one sale: the same customer choice, package, pairs and
    seller every time (issuance.py's replay rule). Refused otherwise."""
    if customer is not None:
        same_customer = order.customer_id == customer.id
    else:
        expected = _row_id_for(idempotency_key, "customer")
        derived = db_session.get(Customer, expected)
        same_customer = order.customer_id == expected and derived is not None and derived.legal_name == legal_name
    same = (
        same_customer
        and str(_plan_id_on(order)) == str(plan_id)
        and _extra_pairs_on(order) == extra_device_pairs
        # A sale recorded before the pairs (single EXTRA_DEVICE lines) is
        # never the same sale as a form that sells pairs.
        and _addon_quantity_on(order, EXTRA_DEVICE_ADDON_CODE) == 0
        and order.created_by_employee_profile_id == seller.id
    )
    if not same:
        raise DirectSaleError("KEY_USED_FOR_DIFFERENT_SALE")


def _check_same_payment(payment: PaymentRecord, *, amount, method, reference, payment_date) -> None:
    if (Decimal(payment.amount), payment.method, payment.reference, payment.payment_date) != (amount, method, reference, payment_date):
        raise DirectSaleError("KEY_USED_FOR_DIFFERENT_SALE")


def _result(order: SalesOrder, payment_id, *, replayed: bool) -> dict:
    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "customer_id": order.customer_id,
        "payment_id": payment_id,
        "total": order.total,
        "currency": order.currency,
        "replayed": replayed,
    }


def record_direct_sale(
    *,
    customer_id=None,
    new_customer_legal_name: str | None = None,
    plan_id,
    extra_device_pairs: int = 0,
    sales_owner_employee_profile_id=None,
    amount: Decimal,
    method: str,
    reference: str | None,
    payment_date: date | None,
    idempotency_key: str,
    actor_staff_user_id: uuid.UUID,
    actor_permission_codes: set[str],
    as_of: date | None = None,
) -> dict:
    """Records the sale and its PENDING payment. Never issues a licence --
    that waits for a manager (confirm_direct_sale). Returns {order_id,
    order_number, customer_id, payment_id, total, currency, replayed};
    replayed=True means this key's sale was already recorded and these are
    its original rows."""
    if not idempotency_key:
        raise DirectSaleError("KEY_REQUIRED")
    # Jordan's date (Step 0 fix round): at 00:30 in Amman, utcnow().date() is
    # still yesterday and money that arrived today was refused as "in the
    # future" (verifier probe v13).
    as_of = as_of or jordan_today()
    reference = (reference or "").strip()
    method = (method or "").strip()

    with _hold_advisory_lock(
        _RECORD_LOCK_NAMESPACE, idempotency_key, "RECORD_IN_PROGRESS",
    ):
        # -- read-only until every check has passed --
        seller = _resolve_seller(
            sales_owner_employee_profile_id,
            actor_staff_user_id=actor_staff_user_id, actor_permission_codes=actor_permission_codes,
        )
        customer, legal_name = _resolve_customer(
            customer_id, new_customer_legal_name,
            actor_staff_user_id=actor_staff_user_id, actor_permission_codes=actor_permission_codes,
        )
        order_id = _row_id_for(idempotency_key, "order")
        payment_id = _row_id_for(idempotency_key, "payment")

        order = db_session.get(SalesOrder, order_id)
        if order is not None:
            # Replay, or a retry after a crash between the order and the
            # payment. Checked BEFORE the catalog: a recorded sale stays
            # replayable even if its package has since stopped being sellable.
            _check_same_sale(
                order, idempotency_key=idempotency_key, customer=customer, legal_name=legal_name,
                plan_id=plan_id, extra_device_pairs=extra_device_pairs, seller=seller,
            )
            payment = db_session.get(PaymentRecord, payment_id)
            if payment is not None:
                _check_same_payment(payment, amount=amount, method=method, reference=reference, payment_date=payment_date)
                return _result(order, payment_id, replayed=True)
            _validate_payment(
                amount=amount, currency=order.currency, total=order.total, method=method,
                reference=reference, payment_date=payment_date, as_of=as_of,
            )
            _record_sale_payment(order, payment_id, amount=amount, method=method, reference=reference,
                                 payment_date=payment_date, seller=seller, actor_staff_user_id=actor_staff_user_id)
            return _result(order, payment_id, replayed=False)

        plan = None
        try:
            plan = db_session.get(Plan, uuid.UUID(str(plan_id))) if plan_id else None
        except ValueError:
            plan = None
        if plan is None:
            raise DirectSaleError("PACKAGE_NOT_FOUND")
        plan_item, _addon_item, lines, total, currency = _price_sale(plan, extra_device_pairs)
        _validate_payment(
            amount=amount, currency=currency, total=total, method=method,
            reference=reference, payment_date=payment_date, as_of=as_of,
        )

        # An inline customer an interrupted attempt with this key already
        # created is reused only for the same name; picking a different,
        # existing customer now is a different sale (issuance.py's
        # _resumable_rows rule) -- refused before any write, never orphaned.
        derived_customer = db_session.get(Customer, _row_id_for(idempotency_key, "customer"))
        if derived_customer is not None:
            if customer is None and derived_customer.legal_name != legal_name:
                raise DirectSaleError("KEY_USED_FOR_DIFFERENT_SALE")
            if customer is not None and customer.id != derived_customer.id:
                raise DirectSaleError("KEY_USED_FOR_DIFFERENT_SALE")

        # -- writes start here -- only after EVERY check above, sizes
        # included (_price_sale): the inline customer commits on its own
        # (create_customer), so a check that ran after it would leave a
        # customer behind for a refused form -- measured on 580cd65a with
        # extra_devices=10**9 (probe P3). Past this line only the database
        # itself can refuse, and a retry with the same key reuses the
        # key-derived customer it left.
        if customer is None:
            customer = derived_customer or create_customer(
                {
                    "id": _row_id_for(idempotency_key, "customer"),
                    "legal_name": legal_name,
                    # The seller owns the customer they sold to (CRM own-scoping).
                    "assigned_sales_staff_id": seller.staff_user_id,
                },
                actor_staff_user_id,
            )

        # Order, its lines and the payment link in ONE transaction: either
        # the whole sale document exists or none of it does.
        order = SalesOrder(
            id=order_id,
            quote_id=None,
            customer_id=customer.id,
            created_by_employee_profile_id=seller.id,
            status="DRAFT",
            order_number=allocate_document_number("SALES_ORDER"),
            currency=currency,
            total=total,
            version=1,
        )
        db_session.add(order)
        db_session.flush()
        for sort_order, item in enumerate(lines):
            line = calculate_line(LineInput(quantity=item["quantity"], unit_price=item["unit_price"], sort_order=sort_order))
            db_session.add(
                SalesOrderLine(
                    sales_order_id=order.id,
                    plan_id=plan.id if item["addon_id"] is None else None,
                    addon_id=item["addon_id"],
                    price_version_id=uuid.UUID(item["price_version_id"]),
                    description=item["description"],
                    quantity=item["quantity"],
                    unit_price=item["unit_price"],
                    discount_amount=Decimal("0"),
                    line_total=line.line_net,
                    sort_order=sort_order,
                )
            )
        db_session.add(
            CommercialOperationsIdempotencyKey(
                idempotency_key=str(payment_id), operation_code=DIRECT_SALE_PAYMENT_LINK, result_reference_id=order.id
            )
        )
        try:
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

        audit_record(
            actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="ORDER_CREATED",
            entity_type="sales_order", entity_public_id=str(order.id),
            after_state={
                "order_number": order.order_number, "customer_id": str(customer.id), "source": "DIRECT_SALE",
                "seller_employee_profile_id": str(seller.id), "plan_id": str(plan.id),
                "extra_device_pairs": extra_device_pairs, "total": str(total), "currency": currency,
            },
        )
        _record_sale_payment(order, payment_id, amount=amount, method=method, reference=reference,
                             payment_date=payment_date, seller=seller, actor_staff_user_id=actor_staff_user_id)
        return _result(order, payment_id, replayed=False)


def _record_sale_payment(order, payment_id, *, amount, method, reference, payment_date, seller, actor_staff_user_id):
    # The existing storage writer, PENDING -- record_payment itself refuses
    # CONFIRMED (two-person rule, 16cbc323). The managers' push-notification
    # outbox row is written in the payment's OWN transaction (Step 1): the sale
    # becomes confirmable at exactly this commit, so this is the one commit
    # that may announce it. A replay never gets here (record_direct_sale
    # returns early when the payment exists), and the outbox row is unique per
    # sale, so one sale is announced once.
    record_payment(
        {
            "id": payment_id,
            "customer_id": order.customer_id,
            "amount": amount,
            "currency": order.currency,
            "method": method,
            "payment_date": payment_date,
            "reference": reference,
            "status": "PENDING",
        },
        actor_staff_user_id,
        in_same_transaction=lambda payment: enqueue_sale_waiting(order_id=order.id, payment_id=payment.id),
    )
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="DIRECT_SALE_RECORDED",
        entity_type="sales_order", entity_public_id=str(order.id),
        after_state={
            "order_number": order.order_number, "payment_record_id": str(payment_id),
            "seller_employee_profile_id": str(seller.id), "amount": str(amount), "currency": order.currency,
        },
    )


# --------------------------------------------------------- the manager side --

def direct_sale_payment(order: SalesOrder) -> PaymentRecord | None:
    payment_id = direct_sale_payment_id_for_order(order.id)
    return db_session.get(PaymentRecord, payment_id) if payment_id is not None else None


def list_sales_awaiting_confirmation(limit: int = 200) -> list[dict]:
    """One-screen sales not yet finished (order DRAFT/CONFIRMED), oldest first."""
    rows = db_session.execute(
        select(SalesOrder, CommercialOperationsIdempotencyKey.idempotency_key)
        .join(CommercialOperationsIdempotencyKey, CommercialOperationsIdempotencyKey.result_reference_id == SalesOrder.id)
        .where(
            CommercialOperationsIdempotencyKey.operation_code == DIRECT_SALE_PAYMENT_LINK,
            SalesOrder.status.in_(("DRAFT", "CONFIRMED")),
        )
        .order_by(SalesOrder.created_at)
        .limit(limit)
    ).all()
    sales = []
    for order, payment_key in rows:
        payment = db_session.get(PaymentRecord, uuid.UUID(payment_key))
        seller = db_session.get(EmployeeProfile, order.created_by_employee_profile_id)
        sales.append({
            "order": order,
            "payment": payment,
            "customer": db_session.get(Customer, order.customer_id),
            "seller": seller,
            "plan": db_session.get(Plan, _plan_id_on(order)),
            "extra_device_pairs": _extra_pairs_on(order),
            # Single extra devices on a sale recorded before the pairs.
            "extra_devices": _addon_quantity_on(order, EXTRA_DEVICE_ADDON_CODE),
        })
    return sales


def _waiting_sales_select(*columns):
    """One-screen sales waiting for a manager, as a SELECT of `columns`. The
    link row stores the payment id as TEXT (CommercialOperationsIdempotencyKey
    .idempotency_key), so the payment is joined by comparing text -- never by
    casting that column to uuid, which would fail on other operations' keys."""
    link = CommercialOperationsIdempotencyKey
    return (
        select(*columns)
        .select_from(SalesOrder)
        .join(link, link.result_reference_id == SalesOrder.id)
        .join(PaymentRecord, cast(PaymentRecord.id, String) == link.idempotency_key)
        .outerjoin(EmployeeProfile, EmployeeProfile.id == SalesOrder.created_by_employee_profile_id)
        .where(
            link.operation_code == DIRECT_SALE_PAYMENT_LINK,
            SalesOrder.status.in_(WAITING_ORDER_STATUSES),
            PaymentRecord.status.in_(WAITING_PAYMENT_STATUSES),
        )
    )


def count_sales_awaiting_for(staff_user_id) -> int:
    """How many waiting sales THIS person could confirm: every waiting sale
    except the ones they recorded or sold (the two-person rule,
    _guard_confirmer). The managers' badge and the number in a push
    notification (Step 1)."""
    stmt = _waiting_sales_select(func.count()).where(
        (PaymentRecord.recorded_by_staff_user_id.is_(None)) | (PaymentRecord.recorded_by_staff_user_id != staff_user_id),
        (EmployeeProfile.staff_user_id.is_(None)) | (EmployeeProfile.staff_user_id != staff_user_id),
    )
    return int(db_session.execute(stmt).scalar_one())


def sale_is_waiting(order_id) -> bool:
    """True while this one-screen sale can still be confirmed."""
    return db_session.execute(
        _waiting_sales_select(SalesOrder.id).where(SalesOrder.id == order_id)
    ).first() is not None


def _guard_confirmer(order: SalesOrder, payment: PaymentRecord, actor_staff_user_id) -> None:
    """The two-person rule for the WHOLE sale, checked before any write.
    confirm_payment/correct_payment check it again for the payment itself,
    but this action also confirms the order and issues the licence, and the
    payment may already be CONFIRMED (someone confirmed it from the payments
    screen) -- the recorder or the seller must not be able to finish the
    sale and reveal its key either way."""
    if payment.recorded_by_staff_user_id == actor_staff_user_id:
        raise CommercialSalesError("SELF_CONFIRMATION_FORBIDDEN")
    seller = db_session.get(EmployeeProfile, order.created_by_employee_profile_id)
    if seller is not None and seller.staff_user_id == actor_staff_user_id:
        raise CommercialSalesError("SELLER_CONFIRMATION_FORBIDDEN")


def _load_sale(order_id) -> tuple[SalesOrder, PaymentRecord]:
    """The sale's order (re-read) and its payment, re-read UNDER A ROW LOCK
    (lock_payment_for_update) held until this transaction's next commit --
    so the status checks below and the payment's own confirm/reject decide
    on one and the same current row. The per-order advisory lock already
    serializes this screen's confirm and reject; this row lock is what
    makes the payments screen's confirm/reject (which take no advisory
    lock) wait instead of slipping in between check and write (580cd65a,
    verifier probe P2). Lock order: advisory lock, then payment row --
    see lock_payment_for_update."""
    order = db_session.get(SalesOrder, order_id)
    if order is None:
        raise DirectSaleError("SALE_NOT_FOUND")
    db_session.refresh(order)
    payment_id = direct_sale_payment_id_for_order(order.id)
    if payment_id is None:
        raise DirectSaleError("NOT_A_ONE_SCREEN_SALE")
    payment = db_session.get(PaymentRecord, payment_id)
    if payment is None:
        raise DirectSaleError("PAYMENT_NEVER_RECORDED")
    return order, lock_payment_for_update(payment)


def _licence_for(order: SalesOrder) -> License | None:
    subscription = db_session.execute(select(Subscription).where(Subscription.sales_order_id == order.id)).scalars().first()
    if subscription is None:
        return None
    return db_session.execute(select(License).where(License.subscription_id == subscription.id)).scalars().first()


def confirm_direct_sale(order_id, *, actor_staff_user_id: uuid.UUID, license_pepper: str) -> dict:
    """The manager's one action: the money is real -> the licence exists.
    Returns {order_id, license_id, full_key, whatsapp_message, replayed}.
    replayed=True: the sale was already finished; its key was shown once,
    then, and is never re-derivable (ADR-9) -- full_key is None."""
    with _hold_advisory_lock(_CONFIRM_LOCK_NAMESPACE, str(order_id), "CONFIRM_IN_PROGRESS"):
        try:
            return _confirm_locked(order_id, actor_staff_user_id=actor_staff_user_id, license_pepper=license_pepper)
        except BaseException:
            # Releases the payment row lock at once on any refusal or crash.
            db_session.rollback()
            raise


def _confirm_locked(order_id, *, actor_staff_user_id, license_pepper) -> dict:
    order, payment = _load_sale(order_id)

    if order.status == "FULFILLED":
        licence = _licence_for(order)
        replay = {"order_id": order.id, "license_id": licence.id if licence else None,
                  "full_key": None, "whatsapp_message": None, "replayed": True}
        db_session.rollback()  # nothing to write; release the payment row lock
        return replay
    if order.status not in ("DRAFT", "CONFIRMED"):
        raise DirectSaleError("ORDER_NOT_CONFIRMABLE", order_status=order.status)
    if payment.status not in ("PENDING", "CONFIRMED"):
        raise DirectSaleError("PAYMENT_NOT_CONFIRMABLE", payment_status=payment.status)
    _guard_confirmer(order, payment, actor_staff_user_id)
    if Decimal(payment.amount) != order.total or payment.currency != order.currency:
        # Unreachable through record_direct_sale (it refuses a mismatch);
        # refused rather than allocated if the rows were ever edited.
        raise DirectSaleError("PAYMENT_DOES_NOT_MATCH_TOTAL")
    # What fulfillment would refuse, refused BEFORE the money is
    # confirmed -- so a package retired since the sale stops here, not
    # after the payment is confirmed and the commission posted.
    plan = db_session.get(Plan, _plan_id_on(order))
    plan_item = describe_plan_for_sale(plan.id) if plan is not None else None
    if plan_item is None or not plan_item["supported_platform_codes"]:
        raise DirectSaleError("PACKAGE_NO_LONGER_SELLABLE")

    # -- writes: each step reuses what an interrupted earlier attempt did --
    if payment.status == "PENDING":
        # Decided under the row lock _load_sale took (re-taking it inside
        # is a no-op); its commit releases it.
        confirm_payment(payment, actor_staff_user_id=actor_staff_user_id, note="Confirmed with the one-screen sale.")
    else:
        # Already CONFIRMED (from the payments screen, or by an interrupted
        # earlier attempt): nothing is decided about the payment here, so
        # its row lock is released now, before the later steps take their
        # own locks -- never held into fulfill_order's advisory lock (lock
        # order: subscriptions/services.py::lock_payment_for_update).
        # allocate_payment re-checks the status under its own lock.
        db_session.commit()
    if order.status == "DRAFT":
        confirm_order(order, actor_staff_user_id=actor_staff_user_id)

    invoice = db_session.execute(
        select(CommercialInvoice).where(CommercialInvoice.sales_order_id == order.id, CommercialInvoice.status != "VOID")
    ).scalars().first()
    if invoice is None:
        invoice = create_invoice_from_order(
            order,
            # The SELLER, not the confirming manager: this column is what
            # the commission ledger credits.
            actor_employee_profile_id=order.created_by_employee_profile_id,
            actor_staff_user_id=actor_staff_user_id,
            idempotency_key=f"direct-sale-invoice-{order.id}",
        )
    if invoice.status == "DRAFT":
        issue_invoice(invoice, actor_staff_user_id=actor_staff_user_id)

    allocated = db_session.execute(
        select(PaymentAllocation).where(
            PaymentAllocation.payment_record_id == payment.id,
            PaymentAllocation.commercial_invoice_id == invoice.id,
            PaymentAllocation.reversed_at.is_(None),
        )
    ).scalars().first()
    if allocated is None:
        # The commission trigger (allocation.py -> ledger.py), dated today:
        # the payment-confirmed date the owner counts commission by.
        # "An allocation exists" really does mean "its commission was
        # posted" (or the seller has no commission plan): allocate_payment
        # writes the two in ONE transaction since fix round 3. Before,
        # a crash between its two commits left an allocation with no
        # commission and this resume skipped it for good (580cd65a, P1).
        allocate_payment(payment=payment, invoice=invoice, amount=Decimal(payment.amount), actor_staff_user_id=actor_staff_user_id)

    fulfilled = fulfill_order(
        order, actor_staff_user_id=actor_staff_user_id, license_pepper=license_pepper,
        idempotency_key=f"direct-sale-fulfill-{order.id}",
    )
    full_key = fulfilled.get("license_key")
    licence = _licence_for(order)

    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="DIRECT_SALE_CONFIRMED",
        entity_type="sales_order", entity_public_id=str(order.id),
        after_state={"payment_record_id": str(payment.id), "invoice_id": str(invoice.id),
                     "license_id": str(licence.id) if licence else None},
    )

    whatsapp_message = None
    if full_key is not None:
        customer = db_session.get(Customer, order.customer_id)
        whatsapp_message = compose_activation_whatsapp_message(
            customer_name=customer.legal_name, product_name=plan_item["name"], full_key=full_key
        )
    return {"order_id": order.id, "license_id": licence.id if licence else None, "full_key": full_key,
            "whatsapp_message": whatsapp_message, "replayed": full_key is None}


def reject_direct_sale(order_id, *, reason: str, actor_staff_user_id: uuid.UUID) -> SalesOrder:
    """The money did not arrive (or was wrong): payment FAILED, order
    CANCELLED, no licence. Allowed to anyone holding the manager permissions,
    including the recorder -- turning a claim into "not paid" never counts
    money (correct_payment's own reasoning).

    Allowed while nothing downstream exists -- also when the order itself
    was already confirmed on the order page (fix round 3: on 580cd65a such
    a sale could never be rejected, "This sale is CONFIRMED; it cannot be
    rejected", and sat on the waiting list for good while its payment
    stayed PENDING). Refused once money moved or a document was made from
    it: a confirmed payment (refund instead), a live allocation of this
    payment, or an invoice for this order -- each would be left pointing at
    a cancelled sale."""
    reason = (reason or "").strip()
    if not reason:
        raise CommercialSalesError("REASON_REQUIRED")
    with _hold_advisory_lock(_CONFIRM_LOCK_NAMESPACE, str(order_id), "REJECT_IN_PROGRESS"):
        try:
            order, payment = _load_sale(order_id)
            if payment.status == "CONFIRMED":
                raise DirectSaleError("REJECT_PAYMENT_CONFIRMED")
            if order.status not in ("DRAFT", "CONFIRMED"):
                raise DirectSaleError("ORDER_NOT_REJECTABLE", order_status=order.status)
            live_allocation = db_session.execute(
                select(PaymentAllocation.id).where(
                    PaymentAllocation.payment_record_id == payment.id, PaymentAllocation.reversed_at.is_(None)
                )
            ).first()
            if live_allocation is not None:
                raise DirectSaleError("REJECT_MONEY_ALLOCATED")
            invoice = db_session.execute(
                select(CommercialInvoice.id).where(
                    CommercialInvoice.sales_order_id == order.id, CommercialInvoice.status != "VOID"
                )
            ).first()
            if invoice is not None:
                raise DirectSaleError("REJECT_INVOICE_EXISTS")
            if payment.status == "PENDING":
                reject_payment(payment, reason=reason, actor_staff_user_id=actor_staff_user_id)
            cancel_order(order, reason=reason, actor_staff_user_id=actor_staff_user_id)
        except BaseException:
            db_session.rollback()  # releases the payment row lock at once
            raise
    return order
