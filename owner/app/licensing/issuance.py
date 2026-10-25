"""Direct license-issuance orchestration
(docs/owner/packages-and-issuance-design.md section B.4 -- "Issue licence:
three acts").

Step 0 (2026-10-24): a licence that is SOLD no longer comes from here. The
one screen now records a sale -- payment and seller -- and the licence is
issued by fulfillment once a manager confirms the money
(app/commercial_sales/direct_sale.py). This immediate path is reachable
only through issue_no_charge_license below: Super Admin, a written reason,
an audit row -- a demo, a replacement key, an internal install.

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

One idempotency key = one issuance (Step 0 fix, 2026-10-24). This module
used to name, as a "known gap", that it had no whole-action idempotency of
its own -- only issue_license_key's LicenseKeyIssuanceEvent lookup, consulted
LAST, after four other service calls had each already committed new rows.
Measured before the fix (tests/test_license_issuance.py, "Step 0" section):

  - the same key submitted again after success (double click, network
    retry, browser resubmit) created a second Subscription -- ACTIVE, not
    DRAFT as this docstring used to claim, because transition_subscription
    had already run -- a second DRAFT License, and on the inline path a
    second Customer; the replay then returned the ORPHAN licence's id;
  - the same key submitted concurrently (two tabs, a racing double click):
    5 callers -> 5 Subscriptions and 5 Licenses, every caller but one then
    crashing on the event table's unique idempotency_key;
  - an inline new customer was created BEFORE the package/device checks,
    so a rejected form left a Customer behind, and the corrected resubmit
    (same key: _render_issuance_form deliberately keeps it) made a second;
  - a failure part-way left committed rows that no retry ever found again.

The root cause is that the action is five separately-committed steps with
no key tying them together. The services commit internally (their own
established behaviour, relied on by every other caller), so this cannot be
one outer transaction. Instead, the same three pieces fulfill_order uses
for the same reason (commercial_sales/fulfillment.py, and
docs/corrections/owner/fulfillment-lock-released-by-nested-commits-root-cause-analysis.md):

  1. every validation runs before the first write, so a refused request
     writes nothing;
  2. a per-key advisory lock on a DEDICATED connection serializes callers
     for the whole sequence -- a lock on db_session's own transaction would
     be released by the first nested commit;
  3. under that lock the existing LicenseKeyIssuanceEvent ledger is checked
     FIRST, so a replay returns the original rows instead of making new
     ones, and a replay with a different payload is refused.

And one piece fulfill_order gets from its SalesOrder row and this path has
no row for: the Customer (inline only), Subscription and License ids are
DERIVED from the key (uuid5), so a retry after a partial failure finds and
reuses the rows the failed attempt committed. No schema change: the ids go
through the canonical services' own `fields` dicts, and primary-key
uniqueness backs the lock up at the database level.
"""
from __future__ import annotations

import hashlib
import uuid
from contextlib import contextmanager
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.audit.services import record as audit_record
from app.commercial_ops.renewal_dates import add_interval
from app.commercial_sales.catalog_for_sales import describe_plan_for_sale
from app.customers.services import create_customer
from app.extensions import db_session, get_engine
from app.licensing.services import create_license, issue_license_key
from app.models.audit import AuditLog
from app.operational_reports.periods import jordan_today
from app.models.catalog import Plan
from app.models.customers import Customer
from app.models.licensing import License, LicenseKeyIssuanceEvent
from app.models.staff import StaffUser
from app.models.subscriptions import Subscription
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
    as_of = as_of or jordan_today()  # Jordan's day; activation.py judges validity by the same day
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


_ISSUANCE_LOCK_NAMESPACE = "licensing.issue_license_direct"

# Fixed namespace for the row ids derived from an idempotency key (see
# _row_id_for). It must never change: a retry after a deploy that changed it
# would derive different ids and no longer find the rows its failed attempt
# left behind.
_ISSUANCE_ROW_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "urn:aura-owner:licensing:issue_license_direct")

# Subscription states a retry may resume from. Anything else means something
# else already happened to that row outside this issuance (same rule as
# fulfillment.py's _RESUMABLE_SUBSCRIPTION_STATUSES).
_RESUMABLE_SUBSCRIPTION_STATUSES = ("DRAFT", "ACTIVE")

_KEY_ALREADY_USED_FOR_DIFFERENT_ISSUANCE = (
    "This issuance form was already used to issue a different licence. "
    "Open a fresh issuance form to issue another one."
)


def _issuance_lock_key(idempotency_key: str) -> int:
    # Same derivation shape as fulfillment.py::_fulfillment_lock_key.
    raw = f"{_ISSUANCE_LOCK_NAMESPACE}|{idempotency_key}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(digest[:15], 16)  # fits in a Postgres bigint


def _row_id_for(idempotency_key: str, kind: str) -> uuid.UUID:
    """The id the row of `kind` ("customer"/"subscription"/"license") gets
    when created for this key. Deterministic on purpose: it is how a retry
    after a partial failure finds the rows the failed attempt already
    committed -- this path has no SalesOrder row to search by the way
    fulfill_order does. uuid5 over a random uuid4 form token cannot collide
    with an ordinary uuid4 primary key in practice."""
    return uuid.uuid5(_ISSUANCE_ROW_ID_NAMESPACE, f"{kind}|{idempotency_key}")


@contextmanager
def _hold_issuance_lock(idempotency_key: str):
    """Per-key mutual exclusion that SURVIVES the commits inside
    issue_license_direct. A copy of fulfillment.py::_hold_fulfillment_lock's
    mechanism (read its docstring for the full reasoning), not a new one:
    every service called below commits, so a lock held on db_session's own
    transaction would be released by the first of those commits, before a
    concurrent second caller even reached its replay check. This lock lives
    on a dedicated connection whose otherwise-empty transaction is rolled
    back on exit, which is what releases a transaction-scoped advisory lock
    (and Postgres releases it by itself if this process dies)."""
    conn = get_engine().connect()
    try:
        txn = conn.begin()
        try:
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _issuance_lock_key(idempotency_key)})
        except OperationalError as exc:
            txn.rollback()
            # 55P03 = lock_not_available (lock_timeout expired) -- the first
            # submit is still running. A plain-message error, so the route
            # re-renders the form with it, never a 500.
            if getattr(getattr(exc, "orig", None), "sqlstate", None) == "55P03":
                raise LicenseIssuanceError(
                    "This licence is still being issued from an earlier click. Wait a moment, "
                    "then check the licence list before trying again."
                ) from exc
            raise
        try:
            yield
        finally:
            txn.rollback()
    finally:
        conn.close()


def _replay_if_already_issued(
    idempotency_key: str, *, customer: Customer | None, legal_name: str, plan: Plan | None, extra_devices: int
) -> dict | None:
    """The replay check -- the existing LicenseKeyIssuanceEvent ledger, now
    consulted FIRST instead of last. The event is committed in the same
    transaction that moves the licence to ISSUED, so "an event exists for
    this key" means exactly "this issuance finished". Returns the ORIGINAL
    rows (never new ones), or raises if the key was used for a different
    payload: that is not a retry, and resolving it to either issuance would
    be silently wrong (fulfill_order's IDEMPOTENCY_CONFLICT rule)."""
    event = db_session.execute(
        select(LicenseKeyIssuanceEvent).where(LicenseKeyIssuanceEvent.idempotency_key == idempotency_key)
    ).scalars().first()
    if event is None:
        return None
    license_row = db_session.get(License, event.license_id)
    if customer is not None:
        same_customer = license_row.customer_id == customer.id
    else:
        same_customer = license_row.customer.legal_name == legal_name
    same_payload = (
        same_customer
        and plan is not None
        and license_row.plan_id == plan.id
        and license_row.device_limit == plan.included_device_count + extra_devices
    )
    if not same_payload:
        raise LicenseIssuanceError(_KEY_ALREADY_USED_FOR_DIFFERENT_ISSUANCE)
    return {
        "customer_id": license_row.customer_id,
        "subscription_id": license_row.subscription_id,
        "license_id": license_row.id,
        "full_key": None,
        "whatsapp_message": None,
        "replayed": True,
    }


def _interrupted_issuance_conflict(customer_name: str, plan: Plan | None, devices: int) -> LicenseIssuanceError:
    plan_name = plan.name if plan is not None else "an unknown package"
    return LicenseIssuanceError(
        "An earlier submit of this form was interrupted part-way with different details: "
        f"customer {customer_name}, package {plan_name}, {devices} device(s). "
        "Submit it again with exactly those details to finish that licence."
    )


def _resumable_rows(
    idempotency_key: str, *, customer: Customer | None, legal_name: str, plan: Plan, device_limit: int
) -> tuple[Customer | None, Subscription | None, License | None]:
    """The rows an earlier, interrupted attempt with this key committed, IF
    this request is the same issuance; otherwise refuses -- before any write.

    Step 0 fix round 2 (2026-10-24). The resume path used to compare only
    customer and package, and only one row at a time, AS it wrote:
      - a crash inside issue_license_key (licence DRAFT, 2 devices), then a
        resubmit with the same key and 5 extra devices, issued the 2-device
        licence -- the operator was shown a key for 7 devices that activates
        on 2 (adversarial probe A1);
      - an inline customer committed by the crashed attempt, then a resubmit
        with an EXISTING customer picked, issued to that customer and left
        the inline one orphaned (probe A6);
      - the reverse switch created a NEW inline Customer and only then
        noticed the subscription belonged to someone else -- an orphan made
        by the refusal itself.
    A key names one issuance: the same customer choice, package and device
    count every time, exactly the replay check's rule. Everything the
    request determines is compared here against every row that exists, and
    a mismatch is refused with what the interrupted attempt was for, so the
    operator can finish THAT licence instead of stranding its rows."""
    derived_customer = db_session.get(Customer, _row_id_for(idempotency_key, "customer"))
    subscription = db_session.get(Subscription, _row_id_for(idempotency_key, "subscription"))
    license_row = db_session.get(License, _row_id_for(idempotency_key, "license"))

    # Who the interrupted attempt was for, for the refusal message.
    started_for = license_row or subscription
    if started_for is not None:
        original = (started_for.customer.legal_name, started_for.plan,
                    license_row.device_limit if license_row is not None else subscription.device_allowance)
    elif derived_customer is not None:
        original = (derived_customer.legal_name, plan, device_limit)
    else:
        return None, None, None  # nothing was started under this key

    if customer is None:
        if derived_customer is not None and derived_customer.legal_name != legal_name:
            raise _interrupted_issuance_conflict(*original)
        expected_customer_id = _row_id_for(idempotency_key, "customer")
    else:
        # An inline customer the crashed attempt created is still the same
        # issuance if the operator now picks THAT customer from the list;
        # any other existing customer is a different issuance.
        if derived_customer is not None and derived_customer.id != customer.id:
            raise _interrupted_issuance_conflict(*original)
        expected_customer_id = customer.id

    requested = (expected_customer_id, plan.id, device_limit)
    if subscription is not None and (subscription.customer_id, subscription.plan_id, subscription.device_allowance) != requested:
        raise _interrupted_issuance_conflict(*original)
    if license_row is not None and (license_row.customer_id, license_row.plan_id, license_row.device_limit) != requested:
        raise _interrupted_issuance_conflict(*original)

    if subscription is not None and subscription.status not in _RESUMABLE_SUBSCRIPTION_STATUSES:
        raise LicenseIssuanceError(
            f"The subscription started by an earlier attempt is now {subscription.status}; "
            "it cannot be resumed. Open a fresh issuance form."
        )
    if license_row is not None and license_row.status != "DRAFT":
        # Unreachable in a consistent database: ISSUED and its event commit
        # together, and an event sends the caller down the replay path.
        # Refused rather than guessed at if it ever happens.
        raise LicenseIssuanceError(
            f"The licence started by an earlier attempt is already {license_row.status}; check the licence list."
        )
    return derived_customer, subscription, license_row


NO_CHARGE_AUDIT_ACTION = "LICENSE_ISSUED_NO_CHARGE"
NO_CHARGE_MIN_REASON_LENGTH = 10


def _record_no_charge_reason(license_id, reason: str, actor_staff_user_id, *, plan_id, device_limit: int) -> None:
    """Once per licence: the audit row saying who issued it without a
    payment, and why. Called under the per-key issuance lock, so the
    exists-check cannot race a second caller with the same key."""
    already = db_session.execute(
        select(AuditLog.id).where(
            AuditLog.action_code == NO_CHARGE_AUDIT_ACTION, AuditLog.entity_public_id == str(license_id)
        )
    ).first()
    if already is not None:
        return
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code=NO_CHARGE_AUDIT_ACTION,
        entity_type="license", entity_public_id=str(license_id), reason=reason,
        after_state={"plan_id": str(plan_id), "device_limit": device_limit},
    )


def issue_no_charge_license(*, reason: str | None, actor_staff_user_id, **issuance_kwargs) -> dict:
    """The ONLY way the immediate, no-payment issuance is reachable now
    (Step 0, 2026-10-24). Every licence that is sold goes through the
    one-screen sale instead (commercial_sales/direct_sale.py): payment
    recorded, seller credited, licence issued only after a manager confirms
    the money. What stays here is a licence nobody pays for -- a demo, a
    replacement for a lost key, an internal install -- and it is:
      - SUPER_ADMIN only, checked on the account itself: licenses.issue is
        a grantable permission, and a custom role holding it must not
        become a way to hand out paid licences for free;
      - never silent: a written reason is required and lands in the audit
        log (LICENSE_ISSUED_NO_CHARGE) together with the package and devices;
      - never credited: it has no seller and no commission.
    All of issue_license_direct's double-submit guarantees are unchanged;
    this only adds the checks in front and the reason behind."""
    staff = db_session.get(StaffUser, actor_staff_user_id)
    if staff is None or not staff.is_super_admin:
        raise LicenseIssuanceError("Only a Super Admin can issue a licence without a payment.")
    reason = (reason or "").strip()
    if len(reason) < NO_CHARGE_MIN_REASON_LENGTH:
        raise LicenseIssuanceError(
            "Write why this licence is issued without a payment (a demo, a replacement key, ...) "
            f"-- at least {NO_CHARGE_MIN_REASON_LENGTH} characters. A licence that is sold must be "
            "recorded as a sale instead."
        )
    return issue_license_direct(actor_staff_user_id=actor_staff_user_id, no_charge_reason=reason, **issuance_kwargs)


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
    no_charge_reason: str | None = None,
) -> dict:
    """The three-act path's single guarded action. Returns a dict:
    {customer_id, subscription_id, license_id, full_key, whatsapp_message,
    replayed}. On a replay of an already-finished issuance (same key, same
    payload) `replayed` is True, the ids are the ORIGINAL rows' and
    `full_key`/`whatsapp_message` are None -- same "never re-derivable"
    contract as issue_license_key itself (ADR-9); the caller must treat None
    as "already issued, nothing new to show", never as a blank/error state."""
    if not idempotency_key:
        raise LicenseIssuanceError("An idempotency key is required to issue a license.")
    if extra_devices < 0:
        raise LicenseIssuanceError("extra_devices must not be negative.")

    with _hold_issuance_lock(idempotency_key):
        # Read-only lookups only, until every check below has passed. The
        # inline new customer used to be CREATED here, before the package
        # and device checks -- a refused request then left that Customer
        # behind for the corrected resubmit to duplicate.
        customer = None
        legal_name = ""
        if customer_id:
            customer = db_session.get(Customer, customer_id)
            if customer is None:
                raise LicenseIssuanceError("Selected customer was not found.")
        else:
            legal_name = (new_customer_legal_name or "").strip()
            if not legal_name:
                raise LicenseIssuanceError("Pick an existing customer or provide a legal name for a new one.")

        plan = db_session.get(Plan, plan_id)

        # Before the sellability checks on purpose: a finished issuance
        # stays replayable even if its package has since stopped being
        # sellable -- the retry must see "already issued", not a new error.
        replay = _replay_if_already_issued(
            idempotency_key, customer=customer, legal_name=legal_name, plan=plan, extra_devices=extra_devices
        )
        if replay is not None:
            if no_charge_reason is not None:
                # A crash between the issuance commit and the reason's audit
                # row would otherwise lose the reason for good: the retry
                # lands here. _record_no_charge_reason writes it only once.
                _record_no_charge_reason(replay["license_id"], no_charge_reason, actor_staff_user_id,
                                         plan_id=plan.id, device_limit=plan.included_device_count + extra_devices)
            return replay

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

        # The last check before any write: rows an interrupted attempt with
        # this key committed are reused only if this request is the same
        # issuance (see _resumable_rows).
        derived_customer, subscription, license_row = _resumable_rows(
            idempotency_key, customer=customer, legal_name=legal_name, plan=plan, device_limit=device_limit
        )

        # -- writes start here; each step reuses what an earlier, failed
        # attempt with this same key already committed (see _row_id_for).
        if customer is None:
            customer = derived_customer or create_customer(
                {"id": _row_id_for(idempotency_key, "customer"), "legal_name": legal_name}, actor_staff_user_id
            )

        if subscription is None:
            # Canonical service call -- never a direct model-row insert. Ceremony cut
            # per spec B.2: billing_cycle is derived from plan.billing_model (never a
            # separately-typed field), device_allowance mirrors the license's own
            # device_limit (never typed twice), and -- the one place spec B.4 is now
            # wrong, corrected by resolve_license_term above -- start/end are finally
            # set here instead of being silently left blank as the pre-existing
            # direct path does.
            subscription = create_subscription(
                {
                    "id": _row_id_for(idempotency_key, "subscription"),
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
        if subscription.status == "DRAFT":
            transition_subscription(subscription, "ACTIVE", actor_staff_user_id, reason="Issued via the direct issuance screen.")

        if license_row is None:
            # Canonical service call -- never a direct model-row insert.
            license_row = create_license(
                {
                    "id": _row_id_for(idempotency_key, "license"),
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
        # writes key fields directly. Its own LicenseKeyIssuanceEvent row,
        # keyed on this same idempotency_key, is what the replay check above
        # reads on every later call.
        license_row, full_key = issue_license_key(license_row, license_pepper, idempotency_key, actor_staff_user_id)
        if no_charge_reason is not None:
            _record_no_charge_reason(license_row.id, no_charge_reason, actor_staff_user_id,
                                     plan_id=plan.id, device_limit=device_limit)

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
        "replayed": False,
    }
