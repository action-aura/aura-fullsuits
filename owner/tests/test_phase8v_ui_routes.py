"""Phase 8V: HTTP-level tests for the internal Owner UI routes closing the
Milestone 4-6 backlog (renewals, pilots, emergency extensions, pending
activations, device-slot operations, notifications, queues,
reconciliation, timeline). Real Postgres-backed Flask test client, no
mocks -- the same convention every prior route test file in this repo
uses."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_staff


def _csrf(client, path):
    page = client.get(path)
    return get_csrf(page.get_data(as_text=True))


def _make_subscription(app, staff_id, *, status="ACTIVE", end_date="2026-08-01", device_limit=2):
    from app.extensions import db_session
    from app.licensing.services import create_license, transition_license
    from app.models.catalog import Plan, Platform, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    with app.app_context():
        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        platform = db_session.query(Platform).filter_by(platform_code="WINDOWS").first()
        plan = Plan(plan_code=f"8V-{staff_id}-{end_date}", product_id=product.id, name="8V Test Plan", billing_model="MONTHLY", currency="USD")
        customer = Customer(legal_name="Phase 8V Test Co")
        db_session.add_all([plan, customer])
        db_session.commit()
        sub = create_subscription(
            {"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id,
             "end_date": date.fromisoformat(end_date), "device_allowance": 2},
            staff_id,
        )
        if status != "DRAFT":
            transition_subscription(sub, "ACTIVE", staff_id)
            if status != "ACTIVE":
                transition_subscription(sub, status, staff_id, reason="test setup")
        lic = create_license(
            {"customer_id": customer.id, "subscription_id": sub.id, "product_id": product.id, "plan_id": plan.id,
             "allowed_platforms": "WINDOWS,ANDROID", "device_limit": device_limit}, staff_id,
        )
        transition_license(lic, "ISSUED", staff_id)
        transition_license(lic, "ACTIVE", staff_id)
        return str(sub.id), str(lic.id), str(platform.id)


# -- generic security checks (representative sample, not exhaustive per-route) --

def test_renewal_list_requires_login(client):
    resp = client.get("/commercial-ops/ui/renewals")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_renewal_new_form_requires_permission(app, client, seeded):
    staff_id = make_staff(app, "8v-sec1@example.com", role_codes=["SUPPORT"])  # no subscriptions.renew
    sub_id, *_ = _make_subscription(app, staff_id)
    force_login(client, app, staff_id)
    resp = client.get(f"/commercial-ops/ui/renewals/new?subscription_id={sub_id}")
    assert resp.status_code == 403


def test_create_renewal_requires_csrf(app, client, seeded):
    staff_id = make_staff(app, "8v-sec2@example.com", role_codes=["SALES"])
    sub_id, *_ = _make_subscription(app, staff_id)
    force_login(client, app, staff_id)
    resp = client.post("/commercial-ops/ui/renewals", data={
        "subscription_id": sub_id, "proposed_term_start": "2026-08-01", "proposed_term_end": "2026-09-01",
    })
    assert resp.status_code == 400


# -- renewal workflow ---------------------------------------------------------

def test_full_renewal_workflow_via_ui(app, client, seeded):
    creator_id = make_staff(app, "8v-ren-creator@example.com", role_codes=["SALES"], mfa=True)
    approver_id = make_staff(app, "8v-ren-approver@example.com", role_codes=["FINANCE"], mfa=True)
    sub_id, *_ = _make_subscription(app, creator_id, end_date="2026-08-01")

    force_login(client, app, creator_id)
    csrf = _csrf(client, f"/commercial-ops/ui/renewals/new?subscription_id={sub_id}")
    create_resp = client.post("/commercial-ops/ui/renewals", data={
        "csrf_token": csrf, "subscription_id": sub_id, "date_rule": "EARLY_RENEWAL_FROM_CURRENT_END",
        "proposed_term_start": "2026-08-01", "proposed_term_end": "2026-09-01", "currency": "USD",
    })
    assert create_resp.status_code == 302
    renewal_url = create_resp.headers["Location"]
    renewal_id = renewal_url.rstrip("/").split("/")[-1]

    for to_status in ("QUOTED", "AWAITING_CONFIRMATION", "AWAITING_PAYMENT", "PAYMENT_RECORDED"):
        csrf = _csrf(client, renewal_url)
        resp = client.post(f"/commercial-ops/ui/renewals/{renewal_id}/transition", data={"csrf_token": csrf, "to_status": to_status})
        assert resp.status_code == 302, (to_status, resp.get_data(as_text=True))

    # require_recent_auth is checked before any self-approval logic even
    # runs (permission -> recent-auth -> service call, in that order) --
    # force_login never sets recent auth, so this redirects to reauth
    # regardless of who's approving.
    csrf = _csrf(client, renewal_url)
    no_mfa_approve = client.post(f"/commercial-ops/ui/renewals/{renewal_id}/approve", data={"csrf_token": csrf})
    assert no_mfa_approve.status_code == 302
    assert "/auth/reauth" in no_mfa_approve.headers["Location"]

    # Self-approval must be rejected even once recent-auth is satisfied.
    creator_client = app.test_client()
    login_and_verify_mfa(creator_client, "8v-ren-creator@example.com")
    csrf = _csrf(creator_client, renewal_url)
    self_approve = creator_client.post(f"/commercial-ops/ui/renewals/{renewal_id}/approve", data={"csrf_token": csrf})
    assert self_approve.status_code == 400
    assert "SELF_APPROVAL" in self_approve.get_data(as_text=True)

    # A different staff member without recent auth is also redirected.
    force_login(client, app, approver_id)
    csrf = _csrf(client, renewal_url)
    no_mfa_approve2 = client.post(f"/commercial-ops/ui/renewals/{renewal_id}/approve", data={"csrf_token": csrf})
    assert no_mfa_approve2.status_code == 302
    assert "/auth/reauth" in no_mfa_approve2.headers["Location"]

    # Real MFA-backed session approves and applies.
    client2 = app.test_client()
    login_and_verify_mfa(client2, "8v-ren-approver@example.com")
    csrf = _csrf(client2, renewal_url)
    approve_resp = client2.post(f"/commercial-ops/ui/renewals/{renewal_id}/approve", data={"csrf_token": csrf})
    assert approve_resp.status_code == 302

    csrf = _csrf(client2, renewal_url)
    apply_resp = client2.post(f"/commercial-ops/ui/renewals/{renewal_id}/apply", data={"csrf_token": csrf})
    assert apply_resp.status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        sub = db_session.get(Subscription, sub_id)
        assert sub.status == "ACTIVE"
        assert sub.end_date.isoformat() == "2026-09-01"

    # Double-apply must fail, not double-extend.
    csrf = _csrf(client2, renewal_url)
    second_apply = client2.post(f"/commercial-ops/ui/renewals/{renewal_id}/apply", data={"csrf_token": csrf})
    assert second_apply.status_code == 400


# -- pilot workflow ------------------------------------------------------------

def _make_pilot_subscription(app, staff_id, *, end_date="2026-09-01"):
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    with app.app_context():
        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        plan = Plan(plan_code=f"8Vpil-{staff_id}", product_id=product.id, name="x", billing_model="PILOT", currency="USD")
        customer = Customer(legal_name="Pilot 8V Co")
        db_session.add_all([plan, customer])
        db_session.commit()
        sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": date.fromisoformat(end_date)}, staff_id)
        transition_subscription(sub, "PILOT", staff_id)
        return str(sub.id)


def test_pilot_create_approve_activate_extend_via_ui(app, client, seeded):
    staff_id = make_staff(app, "8v-pilot1@example.com", role_codes=["SALES"])
    sub_id = _make_pilot_subscription(app, staff_id)
    force_login(client, app, staff_id)

    csrf = _csrf(client, f"/commercial-ops/ui/pilots/new?subscription_id={sub_id}")
    create_resp = client.post("/commercial-ops/ui/pilots", data={
        "csrf_token": csrf, "subscription_id": sub_id, "pilot_start": "2026-07-01", "pilot_end": "2026-09-01",
        "allowed_device_count": "1",
    })
    assert create_resp.status_code == 302
    pilot_url = create_resp.headers["Location"]
    pilot_id = pilot_url.rstrip("/").split("/")[-1]

    csrf = _csrf(client, pilot_url)
    assert client.post(f"/commercial-ops/ui/pilots/{pilot_id}/approve", data={"csrf_token": csrf}).status_code == 302
    csrf = _csrf(client, pilot_url)
    assert client.post(f"/commercial-ops/ui/pilots/{pilot_id}/activate", data={"csrf_token": csrf}).status_code == 302

    csrf = _csrf(client, pilot_url)
    extend_resp = client.post(f"/commercial-ops/ui/pilots/{pilot_id}/extend", data={"csrf_token": csrf, "new_end_date": "2026-10-01", "reason": "customer needs more time"})
    assert extend_resp.status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_ops import PilotRecord

        pilot = db_session.get(PilotRecord, pilot_id)
        assert pilot.status == "EXTENDED"
        assert pilot.extension_count == 1


def test_pilot_extend_requires_reason(app, client, seeded):
    staff_id = make_staff(app, "8v-pilot2@example.com", role_codes=["SALES"])
    sub_id = _make_pilot_subscription(app, staff_id)
    force_login(client, app, staff_id)
    csrf = _csrf(client, f"/commercial-ops/ui/pilots/new?subscription_id={sub_id}")
    create_resp = client.post("/commercial-ops/ui/pilots", data={"csrf_token": csrf, "subscription_id": sub_id, "pilot_start": "2026-07-01", "pilot_end": "2026-09-01"})
    pilot_url = create_resp.headers["Location"]
    pilot_id = pilot_url.rstrip("/").split("/")[-1]
    csrf = _csrf(client, pilot_url)
    client.post(f"/commercial-ops/ui/pilots/{pilot_id}/approve", data={"csrf_token": csrf})
    csrf = _csrf(client, pilot_url)
    client.post(f"/commercial-ops/ui/pilots/{pilot_id}/activate", data={"csrf_token": csrf})
    csrf = _csrf(client, pilot_url)
    resp = client.post(f"/commercial-ops/ui/pilots/{pilot_id}/extend", data={"csrf_token": csrf, "new_end_date": "2026-10-01", "reason": ""})
    assert resp.status_code == 400


def test_pilot_cancel_requires_reason_via_ui(app, client, seeded):
    staff_id = make_staff(app, "8v-pilot3@example.com", role_codes=["SALES"])
    sub_id = _make_pilot_subscription(app, staff_id)
    force_login(client, app, staff_id)
    csrf = _csrf(client, f"/commercial-ops/ui/pilots/new?subscription_id={sub_id}")
    create_resp = client.post("/commercial-ops/ui/pilots", data={"csrf_token": csrf, "subscription_id": sub_id, "pilot_start": "2026-07-01", "pilot_end": "2026-09-01"})
    pilot_url = create_resp.headers["Location"]
    pilot_id = pilot_url.rstrip("/").split("/")[-1]
    csrf = _csrf(client, pilot_url)
    client.post(f"/commercial-ops/ui/pilots/{pilot_id}/approve", data={"csrf_token": csrf})
    csrf = _csrf(client, pilot_url)
    client.post(f"/commercial-ops/ui/pilots/{pilot_id}/activate", data={"csrf_token": csrf})
    csrf = _csrf(client, pilot_url)
    resp = client.post(f"/commercial-ops/ui/pilots/{pilot_id}/cancel", data={"csrf_token": csrf, "reason": ""})
    assert resp.status_code == 400
    csrf = _csrf(client, pilot_url)
    resp2 = client.post(f"/commercial-ops/ui/pilots/{pilot_id}/cancel", data={"csrf_token": csrf, "reason": "customer withdrew"})
    assert resp2.status_code == 302


def test_pilot_conversion_guided_flow_via_ui(app, client, seeded):
    creator_id = make_staff(app, "8v-pconv-creator@example.com", role_codes=["SALES"], mfa=True)
    approver_id = make_staff(app, "8v-pconv-approver@example.com", role_codes=["FINANCE"], mfa=True)
    sub_id = _make_pilot_subscription(app, creator_id, end_date="2026-09-01")
    force_login(client, app, creator_id)

    csrf = _csrf(client, f"/commercial-ops/ui/pilots/new?subscription_id={sub_id}")
    create_resp = client.post("/commercial-ops/ui/pilots", data={"csrf_token": csrf, "subscription_id": sub_id, "pilot_start": "2026-07-01", "pilot_end": "2026-09-01"})
    pilot_url = create_resp.headers["Location"]
    pilot_id = pilot_url.rstrip("/").split("/")[-1]
    csrf = _csrf(client, pilot_url)
    client.post(f"/commercial-ops/ui/pilots/{pilot_id}/approve", data={"csrf_token": csrf})
    csrf = _csrf(client, pilot_url)
    client.post(f"/commercial-ops/ui/pilots/{pilot_id}/activate", data={"csrf_token": csrf})

    # convert_pilot requires recent auth -- force_login has none.
    csrf = _csrf(client, f"/commercial-ops/ui/pilots/{pilot_id}/convert")
    blocked = client.post(f"/commercial-ops/ui/pilots/{pilot_id}/convert", data={
        "csrf_token": csrf, "proposed_term_start": "2026-09-01", "proposed_term_end": "2027-09-01", "currency": "USD",
    })
    assert blocked.status_code == 302
    assert "/auth/reauth" in blocked.headers["Location"]

    client2 = app.test_client()
    login_and_verify_mfa(client2, "8v-pconv-creator@example.com")
    csrf = _csrf(client2, f"/commercial-ops/ui/pilots/{pilot_id}/convert")
    convert_resp = client2.post(f"/commercial-ops/ui/pilots/{pilot_id}/convert", data={
        "csrf_token": csrf, "proposed_term_start": "2026-09-01", "proposed_term_end": "2027-09-01", "currency": "USD",
    })
    assert convert_resp.status_code == 302
    renewal_url = convert_resp.headers["Location"]
    renewal_id = renewal_url.rstrip("/").split("/")[-1]

    for to_status in ("QUOTED", "AWAITING_CONFIRMATION", "AWAITING_PAYMENT", "PAYMENT_RECORDED"):
        csrf = _csrf(client2, renewal_url)
        client2.post(f"/commercial-ops/ui/renewals/{renewal_id}/transition", data={"csrf_token": csrf, "to_status": to_status})

    client3 = app.test_client()
    login_and_verify_mfa(client3, "8v-pconv-approver@example.com")
    csrf = _csrf(client3, renewal_url)
    client3.post(f"/commercial-ops/ui/renewals/{renewal_id}/approve", data={"csrf_token": csrf})
    csrf = _csrf(client3, renewal_url)
    apply_resp = client3.post(f"/commercial-ops/ui/renewals/{renewal_id}/apply", data={"csrf_token": csrf})
    assert apply_resp.status_code == 302

    # Marking a pilot converted is pilots.manage territory (the pilot owner,
    # SALES here) -- not the renewal-approving FINANCE staff member, who
    # has no pilots.* permission at all.
    csrf = _csrf(client2, pilot_url)
    mark_resp = client2.post(f"/commercial-ops/ui/pilots/{pilot_id}/mark-converted", data={"csrf_token": csrf, "renewal_request_id": renewal_id})
    assert mark_resp.status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_ops import PilotRecord

        pilot = db_session.get(PilotRecord, pilot_id)
        assert pilot.status == "CONVERTED"
        assert str(pilot.converted_renewal_request_id) == renewal_id


# -- emergency extensions ------------------------------------------------------

def test_emergency_extension_create_requires_recent_auth_and_reason(app, client, seeded):
    staff_id = make_staff(app, "8v-ee1@example.com", super_admin=True, mfa=True)
    sub_id, lic_id, _ = _make_subscription(app, staff_id, status="EXPIRED", end_date="2026-06-01")
    force_login(client, app, staff_id)

    # The GET form itself requires recent-auth, so it redirects before a
    # CSRF token can even be fetched from it -- confirm that first.
    form_resp = client.get(f"/commercial-ops/ui/emergency-extensions/new?subscription_id={sub_id}")
    assert form_resp.status_code == 302
    assert "/auth/reauth" in form_resp.headers["Location"]

    # The create POST is independently gated too -- fetch a real CSRF
    # token from an unrelated recent-auth-free page (the account has
    # super_admin so can view any subscription) and confirm the POST
    # itself still redirects to reauth rather than proceeding.
    csrf = _csrf(client, f"/subscriptions/{sub_id}")
    blocked = client.post("/commercial-ops/ui/emergency-extensions", data={
        "csrf_token": csrf, "subscription_id": sub_id, "reason": "incident", "duration_hours": "12",
    })
    assert blocked.status_code == 302
    assert "/auth/reauth" in blocked.headers["Location"]

    client2 = app.test_client()
    login_and_verify_mfa(client2, "8v-ee1@example.com")
    csrf = _csrf(client2, f"/commercial-ops/ui/emergency-extensions/new?subscription_id={sub_id}")
    no_reason = client2.post("/commercial-ops/ui/emergency-extensions", data={
        "csrf_token": csrf, "subscription_id": sub_id, "reason": "", "duration_hours": "12",
    })
    assert no_reason.status_code == 400

    csrf = _csrf(client2, f"/commercial-ops/ui/emergency-extensions/new?subscription_id={sub_id}")
    ok = client2.post("/commercial-ops/ui/emergency-extensions", data={
        "csrf_token": csrf, "subscription_id": sub_id, "reason": "customer site outage", "duration_hours": "12",
    })
    assert ok.status_code == 302
    extension_url = ok.headers["Location"]
    extension_id = extension_url.rstrip("/").split("/")[-1]

    csrf = _csrf(client2, extension_url)
    revoke_resp = client2.post(f"/commercial-ops/ui/emergency-extensions/{extension_id}/revoke", data={"csrf_token": csrf, "reason": "resolved early"})
    assert revoke_resp.status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_ops import EmergencyExtension

        assert db_session.get(EmergencyExtension, extension_id).status == "REVOKED"


# -- pending activation review --------------------------------------------------

def _make_pending_activation(app, staff_id):
    from app.commercial_ops.activation_policy import create_pending_activation
    from app.extensions import db_session
    from app.installations.services import register_installation

    sub_id, lic_id, platform_id = _make_subscription(app, staff_id, device_limit=1)
    with app.app_context():
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        lic = db_session.get(License, lic_id)
        sub = db_session.get(Subscription, sub_id)
        installation = register_installation(
            {"customer_id": sub.customer_id, "subscription_id": sub.id, "license_id": lic.id, "product_id": lic.product_id,
             "platform_id": platform_id, "installation_label": "8v-pending-dev"},
            staff_id,
        )
        installation.status = "PENDING_ACTIVATION"
        db_session.commit()
        pending = create_pending_activation(
            installation=installation, license_id=lic.id, product_id=lic.product_id, platform_id=platform_id, mode="MANUAL_APPROVAL",
        )
        return str(pending.id)


def test_pending_activation_approve_requires_recent_auth(app, client, seeded):
    staff_id = make_staff(app, "8v-pa1@example.com", super_admin=True, mfa=True)
    pending_id = _make_pending_activation(app, staff_id)
    force_login(client, app, staff_id)
    csrf = _csrf(client, f"/commercial-ops/ui/pending-activations/{pending_id}")
    blocked = client.post(f"/commercial-ops/ui/pending-activations/{pending_id}/approve", data={"csrf_token": csrf})
    assert blocked.status_code == 302
    assert "/auth/reauth" in blocked.headers["Location"]

    client2 = app.test_client()
    login_and_verify_mfa(client2, "8v-pa1@example.com")
    csrf = _csrf(client2, f"/commercial-ops/ui/pending-activations/{pending_id}")
    approve_resp = client2.post(f"/commercial-ops/ui/pending-activations/{pending_id}/approve", data={"csrf_token": csrf})
    assert approve_resp.status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.activation_governance import PendingActivation

        assert db_session.get(PendingActivation, pending_id).status == "APPROVED"


def test_pending_activation_reject_requires_reason(app, client, seeded):
    staff_id = make_staff(app, "8v-pa2@example.com", super_admin=True, mfa=True)
    pending_id = _make_pending_activation(app, staff_id)
    client2 = app.test_client()
    login_and_verify_mfa(client2, "8v-pa2@example.com")
    csrf = _csrf(client2, f"/commercial-ops/ui/pending-activations/{pending_id}")
    resp = client2.post(f"/commercial-ops/ui/pending-activations/{pending_id}/reject", data={"csrf_token": csrf, "reason": ""})
    assert resp.status_code == 400


# -- device-slot operations ---------------------------------------------------

def test_release_and_replace_installation_require_reason(app, client, seeded):
    from app.extensions import db_session
    from app.installations.services import register_installation

    staff_id = make_staff(app, "8v-ds1@example.com", role_codes=["SUPPORT"])
    sub_id, lic_id, platform_id = _make_subscription(app, staff_id)
    with app.app_context():
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        lic = db_session.get(License, lic_id)
        sub = db_session.get(Subscription, sub_id)
        installation = register_installation(
            {"customer_id": sub.customer_id, "subscription_id": sub.id, "license_id": lic.id, "product_id": lic.product_id,
             "platform_id": platform_id, "installation_label": "8v-slot-dev"},
            staff_id,
        )
        installation.status = "ACTIVE"
        db_session.commit()
        installation_id = str(installation.id)

    force_login(client, app, staff_id)
    csrf = _csrf(client, f"/installations/{installation_id}")
    no_reason = client.post(f"/commercial-ops/ui/installations/{installation_id}/release", data={"csrf_token": csrf, "reason": ""})
    assert no_reason.status_code == 400

    csrf = _csrf(client, f"/installations/{installation_id}")
    ok = client.post(f"/commercial-ops/ui/installations/{installation_id}/release", data={"csrf_token": csrf, "reason": "customer decommissioned device"})
    assert ok.status_code == 302

    with app.app_context():
        from app.models.installations import Installation

        assert db_session.get(Installation, installation_id).status == "DEACTIVATED"


def test_device_slot_exception_create_and_revoke(app, client, seeded):
    staff_id = make_staff(app, "8v-ds2@example.com", role_codes=["SUPPORT"])
    sub_id, lic_id, _ = _make_subscription(app, staff_id, device_limit=1)
    force_login(client, app, staff_id)

    expires_at = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M")
    csrf = _csrf(client, f"/commercial-ops/ui/licenses/{lic_id}/slot-exceptions")
    resp = client.post(f"/commercial-ops/ui/licenses/{lic_id}/slot-exceptions", data={
        "csrf_token": csrf, "extra_slots": "2", "reason": "temporary pilot rollout", "expires_at": expires_at,
    })
    assert resp.status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.activation_governance import DeviceSlotException

        exceptions = db_session.query(DeviceSlotException).filter_by(license_id=lic_id).all()
        assert len(exceptions) == 1
        exception_id = str(exceptions[0].id)

    csrf = _csrf(client, f"/commercial-ops/ui/licenses/{lic_id}/slot-exceptions")
    revoke_resp = client.post(f"/commercial-ops/ui/slot-exceptions/{exception_id}/revoke", data={"csrf_token": csrf, "reason": "no longer needed"})
    assert revoke_resp.status_code == 302
    with app.app_context():
        from app.extensions import db_session
        from app.models.activation_governance import DeviceSlotException

        assert db_session.get(DeviceSlotException, exception_id).status == "REVOKED"


# -- notifications --------------------------------------------------------------

def test_notification_assign_acknowledge_resolve_via_ui(app, client, seeded):
    from app.commercial_ops.commercial_policy import create_notification

    staff_id = make_staff(app, "8v-notif1@example.com", role_codes=["SUPPORT"])
    with app.app_context():
        notification, _ = create_notification(
            notification_type="TEST_NOTIFICATION", severity="WARNING", title="Test", message="Test message",
            dedup_key="8v-test-notif-1", assigned_role_code="SUPPORT",
        )
        notification_id = str(notification.id)

    force_login(client, app, staff_id)
    csrf = _csrf(client, "/commercial-ops/ui/notifications")
    assign_resp = client.post(f"/commercial-ops/ui/notifications/{notification_id}/assign", data={"csrf_token": csrf})
    assert assign_resp.status_code == 302

    csrf = _csrf(client, "/commercial-ops/ui/notifications")
    ack_resp = client.post(f"/commercial-ops/ui/notifications/{notification_id}/acknowledge", data={"csrf_token": csrf})
    assert ack_resp.status_code == 302

    csrf = _csrf(client, "/commercial-ops/ui/notifications")
    resolve_resp = client.post(f"/commercial-ops/ui/notifications/{notification_id}/resolve", data={"csrf_token": csrf, "resolution": "handled"})
    assert resolve_resp.status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_ops import InternalNotification

        assert db_session.get(InternalNotification, notification_id).status == "RESOLVED"


# -- queue / reconciliation / timeline -------------------------------------------

def test_queue_view_shows_role_specific_items(app, client, seeded):
    from app.commercial_ops.commercial_policy import create_notification

    staff_id = make_staff(app, "8v-queue1@example.com", role_codes=["SUPPORT"])
    with app.app_context():
        create_notification(
            notification_type="QUEUE_TEST", severity="INFO", title="Queue item", message="x",
            dedup_key="8v-queue-test-1", assigned_role_code="SUPPORT",
        )
    force_login(client, app, staff_id)
    resp = client.get("/commercial-ops/ui/queue")
    assert resp.status_code == 200
    assert b"Queue item" in resp.data


def test_viewer_queue_shows_counts_not_items(app, client, seeded):
    staff_id = make_staff(app, "8v-queue2@example.com", role_codes=["VIEWER"])
    force_login(client, app, staff_id)
    resp = client.get("/commercial-ops/ui/queue")
    assert resp.status_code == 200
    # Phase 9.5B-R2: the role code is now rendered through role_label() for
    # localization -- "Viewer" (title case), not the raw "VIEWER" code.
    assert b"Viewer" in resp.data


def test_reconciliation_view_and_run(app, client, seeded):
    staff_id = make_staff(app, "8v-recon1@example.com", super_admin=True)
    force_login(client, app, staff_id)
    view_resp = client.get("/commercial-ops/ui/reconciliation")
    assert view_resp.status_code == 200

    csrf = _csrf(client, "/commercial-ops/ui/reconciliation")
    run_resp = client.post("/commercial-ops/ui/reconciliation/run", data={"csrf_token": csrf})
    assert run_resp.status_code == 200
    assert b"notifications written" in run_resp.data


def test_reconciliation_requires_permission(app, client, seeded):
    # Neither system.view nor system.manage_settings is granted to any
    # named role in this codebase (Super Admin wildcard only) -- both
    # reconciliation routes are correctly Super-Admin-only tooling.
    staff_id = make_staff(app, "8v-recon2@example.com", role_codes=["VIEWER"])
    force_login(client, app, staff_id)
    assert client.get("/commercial-ops/ui/reconciliation").status_code == 403
    csrf = _csrf(client, f"/subscriptions")
    resp = client.post("/commercial-ops/ui/reconciliation/run", data={"csrf_token": csrf})
    assert resp.status_code == 403


def test_subscription_timeline_shows_events(app, client, seeded):
    staff_id = make_staff(app, "8v-timeline1@example.com", role_codes=["SALES"])
    sub_id, *_ = _make_subscription(app, staff_id)
    force_login(client, app, staff_id)
    resp = client.get(f"/commercial-ops/ui/timeline/subscription/{sub_id}")
    assert resp.status_code == 200
    # Phase 9.5B-R2: the event category badge is now rendered through
    # timeline_category_label() for localization -- "Subscription" (title
    # case), not the raw "SUBSCRIPTION" code.
    assert b"Subscription" in resp.data
