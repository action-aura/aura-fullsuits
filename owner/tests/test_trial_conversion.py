"""New customer flow, slice S3: a customer PAYS for the trial they already have.

The sale (one-screen sale form) names the trial; the seller must attach a photo of
the bank slip; a manager confirms the money; fulfillment then converts the trial's
OWN subscription and licence in place (no second subscription, no second key).
Every guard is proven by what it wrote or did not write, and each has its allow
twin (the conversion that must work, the second attempt that is allowed once the
first was rejected, the slip a Finance confirmer may open but an ID they may not).
"""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from tests.conftest import build_activation_body, force_login, get_csrf, jordan_today, make_device_keypair, make_staff
from tests.test_customer_onboarding import _codes, _jpeg, _world, kyc, onboard  # noqa: F401 -- kyc is a fixture

PEPPER = "test-license-pepper"
SLIP = _jpeg(b"bank-slip-gps")


def _activate(client, full_key, *, installation="inst-1", key_pair=None):
    key_pair = key_pair or make_device_keypair()
    body = build_activation_body(key_pair, full_key=full_key, installation_id=installation, product_code="AURA_RETAIL",
                                 app_version="1.0.0-rc.17")
    resp = client.post("/api/licensing/v1/activations", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json(), key_pair


def second_plan(app, w, *, devices=3, price=200, product_code="AURA_RETAIL", months=12):
    with app.app_context():
        from app.catalog.services import add_plan_price, create_plan
        from app.extensions import db_session
        from app.models.catalog import Product

        product = db_session.execute(select(Product).where(Product.product_code == product_code)).scalars().one()
        product.is_sellable = True
        product.is_active = True
        db_session.commit()
        plan = create_plan(
            {"plan_code": f"NF2_{uuid.uuid4().hex[:6]}", "product_id": product.id, "name": "Aura Retail Bigger",
             "billing_model": "ANNUAL", "billing_interval_months": months, "effective_date": date.today() - timedelta(days=1),
             "included_device_count": devices, "currency": "JOD"},
            actor_staff_user_id=None,
        )
        add_plan_price(plan, price, "JOD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        return plan.id


def sell(app, w, trial, *, who="seller", plan=None, amount="100.00", slip=SLIP, key=None, **over):
    """record_direct_sale for the trial. Raises DirectSaleError."""
    from app.commercial_sales.direct_sale import record_direct_sale

    args = dict(
        converts_subscription_id=trial.subscription_id, plan_id=plan or w["plan"], amount=Decimal(amount),
        method="BANK_TRANSFER", reference="CLIQ-" + uuid.uuid4().hex[:8], payment_date=jordan_today(),
        idempotency_key=key or str(uuid.uuid4()), actor_staff_user_id=w[who], actor_permission_codes=_codes(app, w[who]),
        slip_photo=slip,
    )
    args.update(over)
    with app.app_context():
        return record_direct_sale(**args)


def confirm(app, w, sale, *, who="manager"):
    from app.commercial_sales.direct_sale import confirm_direct_sale

    with app.app_context():
        return confirm_direct_sale(sale["order_id"], actor_staff_user_id=w[who], license_pepper=PEPPER)


def snapshot(app, trial):
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_ops import PilotRecord
        from app.models.commercial_sales import SalesOrder
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        db_session.expire_all()
        sub = db_session.get(Subscription, trial.subscription_id)
        lic = db_session.get(License, trial.license_id)
        pilot = db_session.execute(select(PilotRecord).where(PilotRecord.subscription_id == sub.id)).scalars().one()
        return {
            "sub": {"status": sub.status, "plan": sub.plan_id, "start": sub.start_date, "end": sub.end_date,
                    "devices": sub.device_allowance, "order": sub.sales_order_id, "contract": sub.contract_term_months},
            "lic": {"status": lic.status, "plan": lic.plan_id, "limit": lic.device_limit, "from": lic.valid_from,
                    "until": lic.valid_until, "hmac": lic.key_secret_hmac, "id": lic.id},
            "pilot": {"status": pilot.status, "decision": pilot.conversion_decision},
            "subs": db_session.execute(select(func.count()).select_from(Subscription)).scalar_one(),
            "lics": db_session.execute(select(func.count()).select_from(License)).scalar_one(),
            "orders": db_session.execute(select(func.count()).select_from(SalesOrder)).scalar_one(),
        }


def money_rows(app):
    with app.app_context():
        from app.extensions import db_session
        from app.models.customer_onboarding import CustomerDocument
        from app.models.subscriptions import PaymentRecord
        from app.models.commercial_sales import SalesOrder

        count = lambda m, *w: db_session.execute(select(func.count()).select_from(m).where(*w)).scalar_one()  # noqa: E731
        return {"orders": count(SalesOrder), "payments": count(PaymentRecord),
                "slips": count(CustomerDocument, CustomerDocument.kind == "PAYMENT_SLIP")}


# ------------------------------------------------------------ the happy path

def test_paying_converts_the_trial_in_place_with_the_same_licence_and_key(app, client, seeded, kyc, signing_key):
    w = _world(app)
    bigger = second_plan(app, w, devices=3, price=200)
    trial = onboard(app, w)
    _activate(client, trial.license_key)  # the clock is running
    before = snapshot(app, trial)
    assert before["sub"]["status"] == "PILOT" and before["lic"]["plan"] == w["plan"]
    sale = sell(app, w, trial, plan=bigger, amount="200.00")  # the package may differ from the trial's
    assert sale["replayed"] is False and snapshot(app, trial)["sub"]["status"] == "PILOT"  # nothing converts before the money is confirmed
    done = confirm(app, w, sale)
    after = snapshot(app, trial)
    assert done["converted"] is True and done["full_key"] is None and done["whatsapp_message"] is None  # no new key
    assert after["sub"]["status"] == "ACTIVE" and after["sub"]["plan"] == bigger and after["sub"]["devices"] == 3
    assert after["sub"]["order"] == sale["order_id"]
    assert after["sub"]["start"] == jordan_today() and after["sub"]["end"] > jordan_today() + timedelta(days=300)
    assert (after["lic"]["id"], after["lic"]["hmac"]) == (before["lic"]["id"], before["lic"]["hmac"])  # the SAME licence and key
    assert after["lic"]["plan"] == bigger and after["lic"]["limit"] == 3 and after["lic"]["status"] == before["lic"]["status"]
    assert after["lic"]["from"] == after["sub"]["start"] and after["lic"]["until"] == after["sub"]["end"]
    assert after["pilot"] == {"status": "CONVERTED", "decision": "CONVERT"}
    assert (after["subs"], after["lics"]) == (before["subs"], before["lics"])  # no second subscription or licence
    with app.app_context():
        # entitlements follow the licence's plan (that IS the re-sync) and the documents are kept for good
        from app.customer_onboarding import documents
        from app.extensions import db_session
        from app.licensing_service.entitlements import resolve_entitlements
        from app.models.licensing import License
        from datetime import datetime, timezone

        resolve_entitlements(db_session.get(License, trial.license_id), datetime.now(timezone.utc))
        assert all(d.purge_after is None for d in documents.documents_of(trial.customer_id, kinds=("NATIONAL_ID", "COMMERCIAL_REGISTRATION")))
        assert any(d.kind == "PAYMENT_SLIP" and d.purge_after is not None for d in documents.documents_of(trial.customer_id))


def test_the_shops_next_lease_says_active_with_the_paid_term(app, client, seeded, kyc, signing_key):
    from tests.conftest import build_checkin_body

    w = _world(app)
    trial = onboard(app, w)
    activated, key_pair = _activate(client, trial.license_key)
    assert activated["signed_assertion"]["payload"]["subscription_status"] == "PILOT"
    confirm(app, w, sell(app, w, trial))
    body = build_checkin_body(key_pair, installation_id=activated["installation_id"])
    resp = client.post("/api/licensing/v1/check-ins", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 200, resp.get_json()
    payload = resp.get_json()["signed_assertion"]["payload"]
    assert payload["subscription_status"] == "ACTIVE" and payload["pilot_status"] == "CONVERTED"
    assert payload["term_end"] > (jordan_today() + timedelta(days=300)).isoformat()


def test_an_unstarted_trial_can_be_paid_for_too(app, seeded, kyc):
    w = _world(app)
    trial = onboard(app, w)
    confirm(app, w, sell(app, w, trial))
    after = snapshot(app, trial)
    assert after["sub"]["status"] == "ACTIVE" and after["sub"]["start"] == jordan_today() and after["pilot"]["status"] == "CONVERTED"


# --------------------------------------------------------------- the slip

def test_a_conversion_sale_without_a_valid_slip_writes_nothing(app, seeded, kyc):
    from app.commercial_sales.direct_sale import DirectSaleError

    w = _world(app)
    trial = onboard(app, w)
    base = money_rows(app)
    for slip, code in ((None, "SLIP_REQUIRED"), (b"", "SLIP_REQUIRED"), (b"MZ not a picture", "SLIP_TYPE_NOT_ALLOWED"),
                       (b"\xff\xd8\xff" + b"0" * (10 * 1024 * 1024 + 1), "SLIP_TOO_LARGE"), (b"\xff\xd8\xff\xe1\x00", "SLIP_INVALID")):
        with pytest.raises(DirectSaleError) as caught:
            sell(app, w, trial, slip=slip)
        assert caught.value.code == code, code
        assert money_rows(app) == base
    app.config["KYC_KEYS"] = ""
    with pytest.raises(DirectSaleError) as caught:
        sell(app, w, trial)
    assert caught.value.code == "SLIP_STORAGE_NOT_READY" and money_rows(app) == base


def test_the_slip_is_stored_encrypted_against_the_payment_and_is_optional_for_an_ordinary_sale(app, seeded, kyc):
    w = _world(app)
    trial = onboard(app, w)
    sale = sell(app, w, trial)
    with app.app_context():
        from app.customer_onboarding import documents
        from app.extensions import db_session
        from app.models.customer_onboarding import CustomerDocument

        slip = db_session.execute(select(CustomerDocument).where(CustomerDocument.kind == "PAYMENT_SLIP")).scalars().one()
        assert slip.payment_record_id == sale["payment_id"] and slip.customer_id == trial.customer_id
        plain, content_type = documents.read_document(slip)
        assert plain.startswith(b"\xff\xd8\xff") and b"bank-slip-gps" not in plain  # metadata stripped
        assert slip.purge_after.date() >= jordan_today() + timedelta(days=365 * 7 - 2)  # slips are kept 7 years
    # an ordinary new-customer sale (no trial) needs no slip -- and keeps one if sent
    from app.commercial_sales.direct_sale import record_direct_sale

    with app.app_context():
        plain_sale = record_direct_sale(
            new_customer_legal_name="Walk-in Shop", plan_id=w["plan"], amount=Decimal("100.00"), method="BANK_TRANSFER",
            reference="R-1", payment_date=jordan_today(), idempotency_key=str(uuid.uuid4()), actor_staff_user_id=w["seller"],
            actor_permission_codes=_codes(app, w["seller"]),
        )
    assert plain_sale["replayed"] is False
    assert money_rows(app)["slips"] == 1


# ---------------------------------------------------- who and what may be paid

def test_only_the_trials_own_customer_and_package_family_can_be_paid(app, seeded, kyc):
    from app.commercial_sales.direct_sale import DirectSaleError

    w = _world(app)
    clinic_plan = second_plan(app, w, product_code="AURA_CLINIC")
    trial = onboard(app, w)
    other = onboard(app, w, who="other_seller")
    from tests.conftest import make_license

    paid_license, _ = make_license(app, w["admin"], device_limit=1, product_code="AURA_RETAIL")
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License

        paid_sub = db_session.get(License, paid_license).subscription_id
    base = money_rows(app)
    cases = [
        ({"customer_id": other.customer_id}, "CONVERSION_CUSTOMER_MISMATCH"),
        ({"new_customer_legal_name": "Someone Else"}, "CONVERSION_CUSTOMER_MISMATCH"),
        ({"converts_subscription_id": str(uuid.uuid4())}, "CONVERSION_TRIAL_NOT_FOUND"),
        ({"converts_subscription_id": "not-a-uuid"}, "CONVERSION_TRIAL_NOT_FOUND"),
        ({"converts_subscription_id": paid_sub}, "CONVERSION_TRIAL_NOT_FOUND"),   # not the actor's customer
        ({"plan": clinic_plan}, "CONVERSION_WRONG_PRODUCT"),
    ]
    for over, code in cases:
        with pytest.raises(DirectSaleError) as caught:
            sell(app, w, trial, **over)
        assert caught.value.code == code, (over, caught.value.code)
        assert money_rows(app) == base
    # deny: another seller cannot pay for MY customer's trial -- it answers like a trial that does not exist
    with pytest.raises(DirectSaleError) as caught:
        sell(app, w, trial, who="other_seller")
    assert caught.value.code == "CONVERSION_TRIAL_NOT_FOUND" and money_rows(app) == base
    # allow: the right seller, the right trial
    sell(app, w, trial)
    assert money_rows(app)["orders"] == 1


def test_a_subscription_that_is_not_a_running_trial_cannot_be_converted(app, seeded, kyc):
    from app.commercial_sales.direct_sale import DirectSaleError

    w = _world(app)
    trial = onboard(app, w)
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_ops import PilotRecord
        from app.models.subscriptions import Subscription

        pilot = db_session.execute(select(PilotRecord)).scalars().one()
        pilot.status = "CANCELLED"
        db_session.commit()
    base = money_rows(app)
    with pytest.raises(DirectSaleError) as caught:
        sell(app, w, trial)
    assert caught.value.code == "CONVERSION_NOT_A_TRIAL" and money_rows(app) == base


# --------------------------------------------- one live payment per trial

def test_only_one_live_payment_per_trial_and_a_rejected_one_frees_it(app, seeded, kyc):
    from app.commercial_sales.direct_sale import DirectSaleError, reject_direct_sale

    w = _world(app)
    trial = onboard(app, w)
    first = sell(app, w, trial)
    with pytest.raises(DirectSaleError) as caught:
        sell(app, w, trial)  # a second form for the same trial
    assert caught.value.code == "CONVERSION_ALREADY_PENDING" and money_rows(app)["orders"] == 1
    # the database says the same with every application check bypassed
    from sqlalchemy.exc import IntegrityError

    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_sales import SalesOrder

        src = db_session.get(SalesOrder, first["order_id"])
        clone = SalesOrder(customer_id=src.customer_id, created_by_employee_profile_id=src.created_by_employee_profile_id,
                           status="DRAFT", order_number="X-" + uuid.uuid4().hex[:8], currency="JOD", total=1,
                           converts_subscription_id=src.converts_subscription_id)
        db_session.add(clone)
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()
        # reject the first (the money did not arrive): the trial is untouched and free again
        before = snapshot(app, trial)
        reject_direct_sale(first["order_id"], reason="The transfer never arrived", actor_staff_user_id=w["manager"])
        db_session.expire_all()
    after = snapshot(app, trial)
    assert after["sub"] == before["sub"] and after["pilot"] == {"status": "ACTIVE", "decision": "PENDING"} and after["lic"] == before["lic"]
    sell(app, w, trial)  # allow: a fresh attempt, now that the first is cancelled
    assert money_rows(app)["orders"] == 2


# --------------------------------------------------- exactly once / recovery

def test_pressing_the_sale_form_twice_records_one_order_and_one_slip(app, seeded, kyc):
    w = _world(app)
    trial = onboard(app, w)
    key = str(uuid.uuid4())
    first = sell(app, w, trial, key=key, reference="CLIQ-SAME")
    again = sell(app, w, trial, key=key, reference="CLIQ-SAME")
    assert first["replayed"] is False and again["replayed"] is True and again["order_id"] == first["order_id"]
    assert money_rows(app) == {"orders": 1, "payments": 1, "slips": 1}


def test_a_crash_while_converting_is_finished_by_confirming_again(app, seeded, kyc, monkeypatch):
    from app.customer_onboarding import trial as trial_module

    w = _world(app)
    trial = onboard(app, w)
    sale = sell(app, w, trial)
    real = trial_module.convert_trial_pilot
    monkeypatch.setattr(trial_module, "convert_trial_pilot", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        confirm(app, w, sale)
    half = snapshot(app, trial)
    assert half["sub"]["status"] == "ACTIVE" and half["pilot"]["status"] == "ACTIVE"  # the half-converted state a crash leaves
    monkeypatch.setattr(trial_module, "convert_trial_pilot", real)
    done = confirm(app, w, sale)
    after = snapshot(app, trial)
    assert done["converted"] is True and after["pilot"]["status"] == "CONVERTED" and after["sub"]["status"] == "ACTIVE"
    assert (after["subs"], after["lics"]) == (half["subs"], half["lics"])
    again = confirm(app, w, sale)  # a third press changes nothing
    assert again["replayed"] is True and snapshot(app, trial) == after


def test_the_seller_cannot_confirm_their_own_trial_payment(app, seeded, kyc):
    from app.commercial_sales.errors import CommercialSalesError

    w = _world(app)
    trial = onboard(app, w)
    sale = sell(app, w, trial)
    before = snapshot(app, trial)
    with pytest.raises(CommercialSalesError):
        confirm(app, w, sale, who="seller")
    assert snapshot(app, trial) == before


# --------------------------------------------------------- the screens

def test_the_sale_form_opened_for_a_trial_fixes_the_customer_and_asks_for_the_slip(app, client, seeded, kyc):
    w = _world(app)
    trial = onboard(app, w)
    force_login(client, app, w["seller"])
    html = client.get(f"/licenses/issuance/new?convert={trial.subscription_id}").get_data(as_text=True)
    assert 'data-converting-trial="1"' in html and f'value="{trial.subscription_id}"' in html
    assert 'name="slip_photo"' in html and "required" in html.split('name="slip_photo"')[1].split(">")[0]
    assert 'enctype="multipart/form-data"' in html and 'name="new_customer_legal_name"' not in html
    # another seller does not even see that this trial exists
    other = app.test_client()
    force_login(other, app, w["other_seller"])
    assert 'data-converting-trial' not in other.get(f"/licenses/issuance/new?convert={trial.subscription_id}").get_data(as_text=True)


def test_posting_the_form_with_a_slip_records_it_and_without_one_is_refused(app, client, seeded, kyc):
    import io

    w = _world(app)
    trial = onboard(app, w)
    force_login(client, app, w["seller"])
    token = get_csrf(client.get(f"/licenses/issuance/new?convert={trial.subscription_id}").get_data(as_text=True))
    data = {"csrf_token": token, "idempotency_key": str(uuid.uuid4()), "converts_subscription_id": str(trial.subscription_id),
            "customer_id": str(trial.customer_id), "plan_id": str(w["plan"]), "extra_device_pairs": "0", "amount": "100.00",
            "method": "BANK_TRANSFER", "reference": "CLIQ-FORM", "payment_date": jordan_today().isoformat()}
    refused = client.post("/licenses/issuance/sale", data=dict(data), content_type="multipart/form-data")
    assert refused.status_code == 400 and "bank slip" in refused.get_data(as_text=True)
    assert money_rows(app) == {"orders": 0, "payments": 0, "slips": 0}
    ok = client.post("/licenses/issuance/sale", data={**data, "slip_photo": (io.BytesIO(SLIP), "slip.jpg", "image/jpeg")},
                     content_type="multipart/form-data")
    assert ok.status_code == 200 and "Sale recorded" in ok.get_data(as_text=True)
    assert money_rows(app) == {"orders": 1, "payments": 1, "slips": 1}


def test_the_confirm_screen_shows_the_slip_and_who_may_open_it(app, client, seeded, kyc):
    w = _world(app)
    finance = make_staff(app, "fin@example.com", role_codes=["FINANCE"], mfa=True)
    trial = onboard(app, w)
    sell(app, w, trial)
    with app.app_context():
        from app.customer_onboarding import documents
        from app.extensions import db_session

        slip = documents.documents_of(trial.customer_id, kinds=("PAYMENT_SLIP",))[0]
        national_id = documents.documents_of(trial.customer_id, kinds=("NATIONAL_ID",))[0]
        slip_url = f"/customers/{trial.customer_id}/documents/{slip.id}"
        id_url = f"/customers/{trial.customer_id}/documents/{national_id.id}"
    manager = app.test_client()
    force_login(manager, app, w["manager"])
    page = manager.get("/licenses/issuance/sales").get_data(as_text=True)
    assert 'data-slip-link="1"' in page and "Trial payment" in page
    # the confirm BUTTON needs recent MFA; signed in that way it says what it does for a trial
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        manager_email = db_session.get(StaffUser, w["manager"]).email
    from tests.conftest import login_and_verify_mfa

    mfa_manager = app.test_client()
    login_and_verify_mfa(mfa_manager, manager_email)
    assert "confirm and unlock the shop" in mfa_manager.get("/licenses/issuance/sales").get_data(as_text=True)
    assert manager.get(slip_url).status_code == 200 and manager.get(id_url).status_code == 200
    # a Finance confirmer may open the SLIP (to confirm against it) but not the owner's ID
    fin = app.test_client()
    force_login(fin, app, finance)
    assert fin.get(slip_url).status_code == 200
    assert fin.get(id_url).status_code == 403
    # the seller who took the photo opens neither
    seller = app.test_client()
    force_login(seller, app, w["seller"])
    assert seller.get(slip_url).status_code == 403 and seller.get(id_url).status_code == 403


def test_the_customer_page_offers_the_payment_link_only_while_the_trial_runs(app, client, seeded, kyc):
    w = _world(app)
    trial = onboard(app, w)
    force_login(client, app, w["seller"])
    html = client.get(f"/customers/{trial.customer_id}").get_data(as_text=True)
    assert 'data-convert-link="1"' in html and f"convert={trial.subscription_id}" in html
    confirm(app, w, sell(app, w, trial))
    html = client.get(f"/customers/{trial.customer_id}").get_data(as_text=True)
    assert 'data-convert-link' not in html and 'data-trial-state="CONVERTED"' in html
