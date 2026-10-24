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

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.audit.services import record as audit_record
from app.commercial_sales.allocation import allocate_payment
from app.commercial_sales.calculator import LineInput, calculate_document, calculate_line
from app.commercial_sales.catalog_for_sales import (
    EXTRA_DEVICE_ADDON_CODE,
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
from app.subscriptions.services import record_payment

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

_RECORD_LOCK_NAMESPACE = "commercial_sales.record_direct_sale"
_CONFIRM_LOCK_NAMESPACE = "commercial_sales.confirm_direct_sale"

# Fixed forever: a retry after a deploy that changed it would derive
# different ids and stop finding the rows its failed attempt left behind.
_ROW_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "urn:aura-owner:commercial_sales:direct_sale")

_MAX_REFERENCE_LENGTH = 128  # PaymentRecord.reference is String(128)


class DirectSaleError(ValueError):
    """Plain operator-facing message, the same shape as
    licensing.issuance.LicenseIssuanceError -- this flow lives on that
    screen. Errors raised by the reused commercial_sales services keep their
    own CommercialSalesError stable codes."""


def _advisory_key(namespace: str, value: str) -> int:
    digest = hashlib.sha256(f"{namespace}|{value}".encode("utf-8")).hexdigest()
    return int(digest[:15], 16)  # fits in a Postgres bigint


@contextmanager
def _hold_advisory_lock(namespace: str, value: str, busy_message: str):
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
                raise DirectSaleError(busy_message) from exc
            raise
        try:
            yield
        finally:
            txn.rollback()
    finally:
        conn.close()


def _row_id_for(idempotency_key: str, kind: str) -> uuid.UUID:
    return uuid.uuid5(_ROW_ID_NAMESPACE, f"{kind}|{idempotency_key}")


def extra_device_offer(product_id) -> dict | None:
    """The sellable EXTRA_DEVICE add-on for this product, or None. Its price
    is the only price an extra device ever gets on this screen -- never a
    typed one. addon_code is unique catalog-wide, so only the one product
    that add-on belongs to can sell extra devices (named limitation)."""
    addon = db_session.execute(
        select(Addon).where(Addon.addon_code == EXTRA_DEVICE_ADDON_CODE, Addon.product_id == product_id)
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
        raise DirectSaleError("The amount paid must be a number, like 250.00.") from exc
    if not amount.is_finite() or amount <= 0:
        raise DirectSaleError("The amount paid must be a number greater than zero.")
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
            raise DirectSaleError("Recording a sale needs your own employee profile. Ask a manager to add one.")
        if sales_owner_employee_profile_id and str(sales_owner_employee_profile_id) != str(own.id):
            raise DirectSaleError("You can only record a sale as its seller yourself.")
        seller = own
    else:
        if not sales_owner_employee_profile_id:
            raise DirectSaleError("Pick the seller (sales owner) of this sale.")
        try:
            seller = db_session.get(EmployeeProfile, uuid.UUID(str(sales_owner_employee_profile_id)))
        except ValueError:
            seller = None
        if seller is None:
            raise DirectSaleError("The selected seller was not found.")
    if seller.employment_status != "ACTIVE" or seller.archived_at is not None:
        raise DirectSaleError("The seller must be an active employee.")
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
            raise DirectSaleError("Selected customer was not found.")
        if customer.archived_at is not None:
            raise DirectSaleError("Selected customer is archived.")
        return customer, ""
    legal_name = (new_customer_legal_name or "").strip()
    if not legal_name:
        raise DirectSaleError("Pick an existing customer or type the legal name of a new one.")
    return None, legal_name


def _price_sale(plan: Plan, extra_devices: int) -> tuple[dict, dict | None, list[tuple[dict, int, object]], Decimal, str]:
    """Catalog price of the sale, through the one shared calculator
    (calculator.py) -- the plan's current base price plus extra_devices x the
    EXTRA_DEVICE add-on's price. Returns (plan item, add-on item, lines,
    total, currency). setup_fee is NOT added: the quote pipeline never adds
    it either (quotes.py prices base_price only), and the two paths must not
    price the same package differently; how a first-time fee is charged is
    the packages item's decision."""
    plan_item = describe_plan_for_sale(plan.id)
    if plan_item is None:
        raise DirectSaleError("Selected package is not currently sellable (check its effective date and price).")
    if not plan_item["supported_platform_codes"]:
        raise DirectSaleError("Selected package has no supported platforms configured.")
    if extra_devices < 0:
        raise DirectSaleError("Extra devices must not be negative.")
    device_limit = plan.included_device_count + extra_devices
    if plan.max_device_count is not None and device_limit > plan.max_device_count:
        raise DirectSaleError(
            f"Requested device count ({device_limit}) exceeds this package's maximum ({plan.max_device_count})."
        )

    lines = [(plan_item, 1, None)]
    addon_item = None
    if extra_devices > 0:
        addon_item = extra_device_offer(plan.product_id)
        if addon_item is None:
            raise DirectSaleError(
                "Extra devices are not on sale for this package yet: the Extra Device add-on needs a price "
                "and must be Available in the catalog."
            )
        if addon_item["currency"] != plan_item["currency"]:
            raise DirectSaleError("The Extra Device add-on is priced in a different currency than this package.")
        lines.append((addon_item, extra_devices, uuid.UUID(addon_item["addon_id"])))

    document = calculate_document(
        [LineInput(quantity=qty, unit_price=item["unit_price"], sort_order=i) for i, (item, qty, _addon) in enumerate(lines)]
    )
    if document.total <= 0:
        raise DirectSaleError(
            "This package costs nothing, so there is no payment to record. Use a no-charge licence instead."
        )
    return plan_item, addon_item, lines, document.total, plan_item["currency"]


def _validate_payment(*, amount: Decimal, currency: str, total: Decimal, method: str, reference: str, payment_date, as_of: date):
    if method not in DIRECT_SALE_PAYMENT_METHODS:
        raise DirectSaleError("The customer pays the company by CliQ or bank transfer; sellers never take cash.")
    if not reference:
        raise DirectSaleError("Type the CliQ or transfer reference number.")
    if len(reference) > _MAX_REFERENCE_LENGTH:
        raise DirectSaleError(f"The reference is too long (at most {_MAX_REFERENCE_LENGTH} characters).")
    if payment_date is None:
        raise DirectSaleError("Type the date the money arrived.")
    if payment_date > as_of:
        raise DirectSaleError("The payment date cannot be in the future.")
    if amount != total:
        # A one-screen sale is paid in full. A part payment would leave the
        # invoice PARTIALLY_PAID, which fulfillment rightly refuses (full
        # payment required) -- the operator would be told "recorded" and the
        # licence would never come. Refused here instead, with the real total.
        raise DirectSaleError(f"The amount paid must be the full price of this sale: {total} {currency}.")


def _extra_devices_on(order: SalesOrder) -> int:
    rows = db_session.execute(
        select(SalesOrderLine.quantity)
        .join(Addon, Addon.id == SalesOrderLine.addon_id)
        .where(SalesOrderLine.sales_order_id == order.id, Addon.addon_code == EXTRA_DEVICE_ADDON_CODE)
    ).scalars().all()
    return sum(rows)


def _plan_id_on(order: SalesOrder):
    return db_session.execute(
        select(SalesOrderLine.plan_id).where(SalesOrderLine.sales_order_id == order.id, SalesOrderLine.plan_id.is_not(None))
    ).scalars().first()


_KEY_USED_FOR_DIFFERENT_SALE = (
    "This sale form was already used to record a different sale. Open a fresh form to record another one."
)


def _check_same_sale(order: SalesOrder, *, idempotency_key, customer, legal_name, plan_id, extra_devices, seller) -> None:
    """A key names one sale: the same customer choice, package, devices and
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
        and _extra_devices_on(order) == extra_devices
        and order.created_by_employee_profile_id == seller.id
    )
    if not same:
        raise DirectSaleError(_KEY_USED_FOR_DIFFERENT_SALE)


def _check_same_payment(payment: PaymentRecord, *, amount, method, reference, payment_date) -> None:
    if (Decimal(payment.amount), payment.method, payment.reference, payment.payment_date) != (amount, method, reference, payment_date):
        raise DirectSaleError(_KEY_USED_FOR_DIFFERENT_SALE)


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
    extra_devices: int = 0,
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
        raise DirectSaleError("An idempotency key is required to record a sale.")
    as_of = as_of or utcnow().date()
    reference = (reference or "").strip()
    method = (method or "").strip()

    with _hold_advisory_lock(
        _RECORD_LOCK_NAMESPACE, idempotency_key,
        "This sale is still being recorded from an earlier click. Wait a moment, then check the sales list.",
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
                plan_id=plan_id, extra_devices=extra_devices, seller=seller,
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
            raise DirectSaleError("Selected package was not found.")
        plan_item, _addon_item, lines, total, currency = _price_sale(plan, extra_devices)
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
                raise DirectSaleError(_KEY_USED_FOR_DIFFERENT_SALE)
            if customer is not None and customer.id != derived_customer.id:
                raise DirectSaleError(_KEY_USED_FOR_DIFFERENT_SALE)

        # -- writes start here --
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
        for sort_order, (item, quantity, addon_id) in enumerate(lines):
            line = calculate_line(LineInput(quantity=quantity, unit_price=item["unit_price"], sort_order=sort_order))
            db_session.add(
                SalesOrderLine(
                    sales_order_id=order.id,
                    plan_id=plan.id if addon_id is None else None,
                    addon_id=addon_id,
                    price_version_id=uuid.UUID(item["price_version_id"]),
                    description=item["name"],
                    quantity=quantity,
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
                "extra_devices": extra_devices, "total": str(total), "currency": currency,
            },
        )
        _record_sale_payment(order, payment_id, amount=amount, method=method, reference=reference,
                             payment_date=payment_date, seller=seller, actor_staff_user_id=actor_staff_user_id)
        return _result(order, payment_id, replayed=False)


def _record_sale_payment(order, payment_id, *, amount, method, reference, payment_date, seller, actor_staff_user_id):
    # The existing storage writer, PENDING -- record_payment itself refuses
    # CONFIRMED (two-person rule, 16cbc323).
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
            "extra_devices": _extra_devices_on(order),
        })
    return sales


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
    order = db_session.get(SalesOrder, order_id)
    if order is None:
        raise DirectSaleError("Sale not found.")
    db_session.refresh(order)
    payment_id = direct_sale_payment_id_for_order(order.id)
    if payment_id is None:
        raise DirectSaleError("This order was not recorded from the one-screen sale form.")
    payment = db_session.get(PaymentRecord, payment_id)
    if payment is None:
        raise DirectSaleError(
            "This sale's payment was never recorded (the form was interrupted). The seller should submit the same form again."
        )
    db_session.refresh(payment)
    return order, payment


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
    with _hold_advisory_lock(
        _CONFIRM_LOCK_NAMESPACE, str(order_id),
        "This sale is already being confirmed by someone else. Wait a moment, then reload the list.",
    ):
        order, payment = _load_sale(order_id)

        if order.status == "FULFILLED":
            licence = _licence_for(order)
            return {"order_id": order.id, "license_id": licence.id if licence else None,
                    "full_key": None, "whatsapp_message": None, "replayed": True}
        if order.status not in ("DRAFT", "CONFIRMED"):
            raise DirectSaleError(f"This sale is {order.status}; it cannot be confirmed.")
        if payment.status not in ("PENDING", "CONFIRMED"):
            raise DirectSaleError(f"This sale's payment is {payment.status}; it cannot be confirmed.")
        _guard_confirmer(order, payment, actor_staff_user_id)
        if Decimal(payment.amount) != order.total or payment.currency != order.currency:
            # Unreachable through record_direct_sale (it refuses a mismatch);
            # refused rather than allocated if the rows were ever edited.
            raise DirectSaleError("This sale's payment does not match its total; it cannot be confirmed.")
        # What fulfillment would refuse, refused BEFORE the money is
        # confirmed -- so a package retired since the sale stops here, not
        # after the payment is confirmed and the commission posted.
        plan = db_session.get(Plan, _plan_id_on(order))
        plan_item = describe_plan_for_sale(plan.id) if plan is not None else None
        if plan_item is None or not plan_item["supported_platform_codes"]:
            raise DirectSaleError("This sale's package is no longer sellable; it cannot be confirmed as it stands.")

        # -- writes: each step reuses what an interrupted earlier attempt did --
        if payment.status == "PENDING":
            confirm_payment(payment, actor_staff_user_id=actor_staff_user_id, note="Confirmed with the one-screen sale.")
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
    money (correct_payment's own reasoning)."""
    reason = (reason or "").strip()
    if not reason:
        raise CommercialSalesError("REASON_REQUIRED")
    with _hold_advisory_lock(
        _CONFIRM_LOCK_NAMESPACE, str(order_id),
        "This sale is being confirmed by someone else right now. Reload the list.",
    ):
        order, payment = _load_sale(order_id)
        if payment.status == "CONFIRMED":
            raise DirectSaleError(
                "This sale's payment is already confirmed; it can no longer be rejected (use a refund instead)."
            )
        if order.status != "DRAFT":
            raise DirectSaleError(f"This sale is {order.status}; it cannot be rejected.")
        if payment.status == "PENDING":
            reject_payment(payment, reason=reason, actor_staff_user_id=actor_staff_user_id)
        cancel_order(order, reason=reason, actor_staff_user_id=actor_staff_user_id)
    return order
