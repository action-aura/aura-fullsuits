"""Direct license-issuance orchestration
(docs/owner/packages-and-issuance-design.md section B.4 -- "Issue licence:
three acts").

Collapses the existing 6-step direct path (create customer -> create
subscription -> create license (DRAFT) -> issue -> copy key -> [approve
activation]) into a single guarded action: pick/create the customer, pick
the package + extra devices, press Issue. This module orchestrates ONLY
existing canonical services -- app.customers.services.create_customer,
app.subscriptions.services.create_subscription/transition_subscription,
app.licensing.services.create_license/issue_license_key -- never a raw
model-row insert for any of those. This is the same "orchestrate, never
insert" contract app.commercial_sales.fulfillment documents (Non-Negotiable
Principle 5 there) and follows it for the same reason: every audit row,
status-history row, and issuance-event row falls out of those service calls
unchanged, because the services write them -- this module writes none of
them directly.

Known, named gap (not built here, matching this task's explicit scope): unlike
commercial_sales.fulfillment.fulfill_order, this path has no SalesOrder-like
row to FOR-UPDATE-lock or replay against, so it has no whole-action
idempotency ledger of its own -- a double-submitted browser POST can create a
second, orphaned DRAFT Subscription/License pair (the second
issue_license_key call still safely no-ops via its own
LicenseKeyIssuanceEvent ledger and never reveals a second key -- see that
function's docstring -- but the duplicate DRAFT rows are not cleaned up).
Building a CommercialOperationsIdempotencyKey-style ledger for this path is a
real follow-on; it is deliberately not built here because the spec that
created this module scoped issuance idempotency to "as today" (the existing
issue_license_key ledger), not a new write ("no new writes invented").
"""
from __future__ import annotations

from datetime import date

from app.commercial_ops.renewal_dates import add_interval
from app.commercial_sales.catalog_for_sales import describe_plan_for_sale
from app.customers.services import create_customer
from app.extensions import db_session
from app.licensing.services import create_license, issue_license_key
from app.models.base import utcnow
from app.models.catalog import Plan
from app.models.customers import Customer
from app.subscriptions.services import create_subscription, transition_subscription

# Aura Pilot's own commercial term (docs/owner/packages-and-issuance-design.md
# A.4: "TRIAL -- 'Aura Pilot' ... 0 JOD, 30 days"). A PILOT plan has no
# billing_interval_months (that field is a MONTHLY/ANNUAL concept), so the
# trial length is not derivable from the plan row -- it is a fixed
# commercial policy, named here explicitly rather than silently inferred.
DEFAULT_PILOT_TRIAL_DAYS = 30

# billing_model values that carry a real recurring interval but might not
# have billing_interval_months populated on a hand-created plan -- ANNUAL
# defaults to a 12-month term, MONTHLY to 1, CUSTOM (negotiated terms) to a
# conservative 12-month default rather than silently perpetual.
_DEFAULT_INTERVAL_MONTHS_BY_BILLING_MODEL = {"ANNUAL": 12, "MONTHLY": 1, "CUSTOM": 12}


class LicenseIssuanceError(ValueError):
    """Plain-message error, matching licensing.services.InvalidLicenseTransitionError
    and subscriptions.services.InvalidTransitionError's convention -- this
    module sits beside licensing/routes.py and licensing/services.py, not
    commercial_sales, and matches THEIR error shape rather than
    commercial_sales.errors.CommercialSalesError's structured-code one."""


def resolve_license_term(plan: Plan, *, as_of: date | None = None) -> tuple[date, date | None]:
    """Returns (start, end) for a licence/subscription issued against `plan`,
    branching on Plan.billing_model (ONE_TIME/MONTHLY/ANNUAL/PILOT/CUSTOM).

    ONE_TIME -> (today, None). THIS IS NOT AN OVERSIGHT -- do not "fix" it by
    giving a ONE_TIME licence an end date. The owner's 2026-08 pricing
    decision prices every product as a single one-time device fee, never a
    subscription (docs/owner/packages-and-issuance-design.md, "the one place
    the spec is now wrong": spec B.4 originally said the term defaults "to 1
    year from today", written before that pricing decision existed). A NULL
    end_date/valid_until is the CORRECT, deliberate representation of "this
    licence never expires": commercial_ops/expiry_scan.py::run_expiry_scan
    only ever selects subscriptions where `Subscription.end_date.isnot(None)`
    -- a perpetual licence is invisible to the expiry scan BY CONSTRUCTION,
    exactly like every OTHER licence issued through the pre-existing direct
    path today (which never sets a term at all -- see B.1). Setting an end
    date here for ONE_TIME would silently start expiring a paying customer's
    licence on the anniversary of a purchase they were told was final -- the
    single most damaging mistake this function could make.
    """
    as_of = as_of or utcnow().date()
    if plan.billing_model == "ONE_TIME":
        return as_of, None
    if plan.billing_model == "PILOT":
        return as_of, add_interval(as_of, days=DEFAULT_PILOT_TRIAL_DAYS)
    months = plan.billing_interval_months or _DEFAULT_INTERVAL_MONTHS_BY_BILLING_MODEL.get(plan.billing_model, 12)
    return as_of, add_interval(as_of, months=months)


def compose_activation_whatsapp_message(*, customer_name: str, product_name: str, full_key: str) -> str:
    """Pre-composed bilingual (English + Arabic) WhatsApp message body (spec
    B.4: "a pre-composed bilingual WhatsApp message containing the key and
    activation instructions"). Deliberately plain text, no markup -- this is
    a message body meant to be copied into WhatsApp, not HTML."""
    return (
        f"Hello {customer_name}, this is your {product_name} licence key:\n"
        f"{full_key}\n\n"
        "To activate: open the app, go to Settings > Licensing, paste this key "
        "exactly as shown, and press Activate. Keep this message safe -- the "
        "key is shown to us only once and cannot be recovered if lost; "
        "contact support for a replacement licence if that happens.\n\n"
        "---\n\n"
        f"مرحبًا {customer_name}، هذا مفتاح ترخيص {product_name} الخاص بك:\n"
        f"{full_key}\n\n"
        "للتفعيل: افتح التطبيق، اذهب إلى الإعدادات > الترخيص، الصق هذا المفتاح "
        "كما هو تمامًا، ثم اضغط تفعيل. احتفظ بهذه الرسالة -- هذا المفتاح يُعرض "
        "مرة واحدة فقط ولا يمكن استرجاعه في حال فقدانه؛ تواصل مع الدعم للحصول "
        "على ترخيص بديل في هذه الحالة."
    )


def issue_license_direct(
    *,
    customer_id=None,
    new_customer_legal_name: str | None = None,
    plan_id,
    extra_devices: int = 0,
    idempotency_key: str,
    actor_staff_user_id,
    license_pepper: str,
    as_of: date | None = None,
) -> dict:
    """The three-act path's single guarded action. Returns a dict:
    {customer_id, subscription_id, license_id, full_key, whatsapp_message}.
    `full_key`/`whatsapp_message` are None on an idempotency-key replay of an
    already-issued license -- same "never re-derivable" contract as
    issue_license_key itself (ADR-9); the caller must treat None as "already
    issued, nothing new to show", never as a blank/error state."""
    if not idempotency_key:
        raise LicenseIssuanceError("An idempotency key is required to issue a license.")
    if extra_devices < 0:
        raise LicenseIssuanceError("extra_devices must not be negative.")

    if customer_id:
        customer = db_session.get(Customer, customer_id)
        if customer is None:
            raise LicenseIssuanceError("Selected customer was not found.")
    else:
        legal_name = (new_customer_legal_name or "").strip()
        if not legal_name:
            raise LicenseIssuanceError("Pick an existing customer or provide a legal name for a new one.")
        customer = create_customer({"legal_name": legal_name}, actor_staff_user_id)

    plan = db_session.get(Plan, plan_id)
    if plan is None:
        raise LicenseIssuanceError("Selected package was not found.")

    # Derive allowed_platforms from the plan's real supported platforms --
    # the exact fix commercial_sales.fulfillment.fulfill_order already uses
    # (fulfillment.py's own comment: a literal "ALL" free-text value would
    # never match activation.py's exact membership check, silently rejecting
    # every real activation). describe_plan_for_sale also doubles as this
    # screen's "is this package actually sellable" gate (current effective
    # date/price/product status) -- the same catalog eligibility real money
    # changes hands against, not a separate, looser check.
    #
    # Deliberately NOT passed `as_of` here -- "is this package purchasable"
    # is always evaluated as of the real current moment (describe_plan_for_sale's
    # own default), independent of the `as_of` override below, which exists
    # only to make the ISSUED TERM's start date deterministic for tests.
    # Conflating the two would make a package created "yesterday" look
    # unsellable to a caller testing an `as_of` from further in the past.
    catalog_item = describe_plan_for_sale(plan.id)
    if catalog_item is None:
        raise LicenseIssuanceError("Selected package is not currently sellable (check its effective date and price).")
    allowed_platforms = ",".join(catalog_item["supported_platform_codes"])
    if not allowed_platforms:
        raise LicenseIssuanceError("Selected package has no supported platforms configured.")

    device_limit = plan.included_device_count + extra_devices
    if plan.max_device_count is not None and device_limit > plan.max_device_count:
        raise LicenseIssuanceError(
            f"Requested device count ({device_limit}) exceeds this package's maximum ({plan.max_device_count})."
        )

    start, end = resolve_license_term(plan, as_of=as_of)

    # Canonical service call -- never a direct model-row insert. Ceremony cut
    # per spec B.2: billing_cycle is derived from plan.billing_model (never a
    # separately-typed field), device_allowance mirrors the license's own
    # device_limit (never typed twice), and -- the one place spec B.4 is now
    # wrong, corrected by resolve_license_term above -- start/end are finally
    # set here instead of being silently left blank as the pre-existing
    # direct path does.
    subscription = create_subscription(
        {
            "customer_id": customer.id,
            "product_id": plan.product_id,
            "plan_id": plan.id,
            "billing_cycle": plan.billing_model,
            "start_date": start,
            "end_date": end,
            "device_allowance": device_limit,
        },
        actor_staff_user_id,
    )
    # create_subscription always creates DRAFT (subscriptions/services.py) --
    # ceremony cut per spec B.2: auto-ACTIVE at issue, the same transition
    # fulfillment.py performs for the sales pipeline, so ops screens and Part
    # W term reads see the correct status without a separate manual step.
    transition_subscription(subscription, "ACTIVE", actor_staff_user_id, reason="Issued via the direct issuance screen.")

    # Canonical service call -- never a direct model-row insert.
    license_row = create_license(
        {
            "customer_id": customer.id,
            "subscription_id": subscription.id,
            "product_id": plan.product_id,
            "plan_id": plan.id,
            "allowed_platforms": allowed_platforms,
            "device_limit": device_limit,
            "valid_from": start,
            "valid_until": end,
        },
        actor_staff_user_id,
    )

    # The one real key-issuance authority -- never constructs the key or
    # writes key fields directly. Its own idempotency ledger
    # (LicenseKeyIssuanceEvent) makes this call itself safely retryable,
    # exactly as it does for the existing licensing.routes::issue path.
    license_row, full_key = issue_license_key(license_row, license_pepper, idempotency_key, actor_staff_user_id)

    whatsapp_message = None
    if full_key is not None:
        whatsapp_message = compose_activation_whatsapp_message(
            customer_name=customer.legal_name, product_name=catalog_item["name"], full_key=full_key
        )

    return {
        "customer_id": customer.id,
        "subscription_id": subscription.id,
        "license_id": license_row.id,
        "full_key": full_key,
        "whatsapp_message": whatsapp_message,
    }
