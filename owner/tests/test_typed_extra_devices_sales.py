"""Typed extra devices, the SALES side (owner decision 2026-10-31): extra phones and extra desktops.

Companion of test_typed_extra_devices.py (the licensing rule itself). Here: what the one-screen
sale prices and records (EXTRA_PHONE / EXTRA_DESKTOP lines, tax-inclusive 50 JOD each), what
fulfilment writes onto the licence (device_limit = included + extras, plus the typed columns), the
old pair offer (retired but still honoured for sales already waiting), the seed command, refunds
that take back exactly the refunded kind, and a trial paid with a phone.

Mutation proofs are listed in the commit message; every guard has its deny half and allow half.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from tests.test_customer_onboarding import kyc  # noqa: F401 -- a fixture (slip folder + onboarding switch)


# =============================================================== sales side ==

def _sales_world(app, **kwargs):
    from tests.test_step0_packages import _world

    return _world(app, **kwargs)


def _licence_of(app, order_id):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        db_session.expire_all()
        sub = db_session.execute(select(Subscription).where(Subscription.sales_order_id == order_id)).scalars().one()
        lic = db_session.execute(select(License).where(License.subscription_id == sub.id)).scalars().one()
        return {"id": lic.id, "limit": lic.device_limit, "desktops": lic.extra_desktop_slots,
                "phones": lic.extra_phone_slots, "allowance": sub.device_allowance}


def test_t10_one_desktop_and_two_phones_are_priced_lined_and_typed_on_the_licence(app, client, seeded):
    from tests.test_issuance_sale import NOTHING, _counts
    from tests.test_step0_packages import _confirm, _finished, _only_order, _order_lines, _post_sale

    w = _sales_world(app)
    # deny half: 250 + 50 + 100 = 400 is the price; anything else is refused before any write
    for wrong in ("350.00", "300.00", "450.00"):
        assert _post_sale(client, app, w, "p1", amount=wrong, desktops=1, phones=2).status_code == 400
    assert _counts(app) == NOTHING
    resp = _post_sale(client, app, w, "p1", amount="400.00", desktops=1, phones=2)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:800]
    order_id = _only_order(app)
    assert _order_lines(app, order_id) == [
        ("PLAN", 1, Decimal("250.00"), Decimal("250.00")),
        ("EXTRA_DESKTOP", 1, Decimal("50.00"), Decimal("50.00")),
        ("EXTRA_PHONE", 2, Decimal("50.00"), Decimal("100.00")),
    ]
    _confirm(app, order_id, w["manager"])
    done = _finished(app, order_id)
    assert done["invoice_total"] == Decimal("400.00")
    assert (done["device_limit"], done["device_allowance"], done["extra_desktops"], done["extra_phones"]) == (5, 5, 1, 2)
    assert done["desktop_invoice_items"] == [(1, Decimal("50.00"))] and done["phone_invoice_items"] == [(2, Decimal("100.00"))]


def test_t11_a_form_key_names_one_sale_each_kind_counted_on_its_own(app, seeded, monkeypatch):
    """The order (1 desktop + 2 phones, 400) committed, then the process died before the payment.
    Resubmitting the same form key with ONLY the desktops changed, or ONLY the phones changed -- each
    time with the amount that matches the NEW total, so the amount check cannot be what refuses it --
    must be refused as a different sale. Only the per-kind comparison can catch these (the payment-amount
    replay check is never reached: there is no payment yet), so dropping either comparison turns one case red.
    Allow half: the same form with its own counts finishes its own sale."""
    from tests.test_issuance_sale import _codes

    w = _sales_world(app)
    import app.commercial_sales.direct_sale as direct_sale_module

    real = direct_sale_module.record_payment
    state = {"crash": True}

    def _crash_once(*args, **kwargs):
        if state["crash"]:
            state["crash"] = False
            raise RuntimeError("simulated crash before the payment was written")
        return real(*args, **kwargs)

    monkeypatch.setattr(direct_sale_module, "record_payment", _crash_once)
    with app.app_context():
        from app.commercial_sales.direct_sale import DirectSaleError, record_direct_sale

        fields = dict(new_customer_legal_name="Corner Shop", plan_id=w["p1"], method="BANK_TRANSFER",
                      reference="CLIQ-1", payment_date=date.today(), actor_staff_user_id=w["seller"],
                      actor_permission_codes=_codes(w["seller"]))
        for changed in (dict(extra_desktops=2, extra_phones=2), dict(extra_desktops=1, extra_phones=3)):
            key = str(uuid.uuid4())
            state["crash"] = True
            with pytest.raises(RuntimeError):
                record_direct_sale(idempotency_key=key, extra_desktops=1, extra_phones=2, amount=Decimal("400.00"), **fields)
            with pytest.raises(DirectSaleError) as exc:
                record_direct_sale(idempotency_key=key, amount=Decimal("450.00"), **changed, **fields)
            assert exc.value.code == "KEY_USED_FOR_DIFFERENT_SALE", changed
            done = record_direct_sale(idempotency_key=key, extra_desktops=1, extra_phones=2,
                                      amount=Decimal("400.00"), **fields)
            assert done["replayed"] is False and done["total"] == Decimal("400.00")


def test_t12_a_waiting_pair_sale_still_becomes_one_desktop_and_one_phone(app, client, seeded):
    """Sales recorded BEFORE this change carry an EXTRA_DEVICE_PAIR line. Confirming one must
    keep giving the shop its pair, now typed: 1 desktop + 1 phone, limit 2 + 2."""
    from tests.test_step0_packages import _confirm, _finished, _only_order, _post_sale

    w = _sales_world(app)
    assert _post_sale(client, app, w, "p1", amount="300.00", phones=1).status_code == 200
    order_id = _only_order(app)
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.catalog import Addon
        from app.models.commercial_sales import SalesOrderLine

        pair = db_session.execute(select(Addon).where(Addon.addon_code == "EXTRA_DEVICE_PAIR")).scalars().one()
        pair.price, pair.currency, pair.availability_status = Decimal("50.00"), "JOD", "RETIRED"  # off sale, as seed-packages leaves it
        line = db_session.execute(select(SalesOrderLine).where(
            SalesOrderLine.sales_order_id == order_id, SalesOrderLine.plan_id.is_(None))).scalars().one()
        line.addon_id = pair.id  # the order as the pair-selling code wrote it
        db_session.commit()
    _confirm(app, order_id, w["manager"])
    lic = _licence_of(app, order_id)
    assert (lic["limit"], lic["desktops"], lic["phones"], lic["allowance"]) == (4, 1, 1, 4)
    assert _finished(app, order_id)["invoice_total"] == Decimal("300.00")


def test_t13_the_retired_pair_field_is_refused_not_ignored(app, client, seeded):
    """A sale page opened before this change still posts extra_device_pairs. Ignoring it would
    sell nothing and charge a confusing total; it is refused with its own message and no write.
    Allow half: the old form's untouched default ("0") still passes."""
    from tests.test_issuance_sale import NOTHING, _counts
    from tests.test_step0_packages import _post_sale

    w = _sales_world(app)
    refused = _post_sale(client, app, w, "p1", amount="300.00", pairs=1)
    assert refused.status_code == 400
    assert "This sale form is out of date" in refused.get_data(as_text=True)
    assert _counts(app) == NOTHING
    assert _post_sale(client, app, w, "p1", amount="250.00", pairs=0).status_code == 200


def test_t14_the_sale_form_sells_phones_and_desktops_in_both_languages(app, client, seeded):
    from tests.conftest import force_login

    w = _sales_world(app)
    force_login(client, app, w["seller"])
    body = client.get("/licenses/issuance/new").get_data(as_text=True)
    assert 'id="sale-extra-phones" name="extra_phones"' in body and 'id="sale-extra-desktops" name="extra_desktops"' in body
    assert "Extra phones (Android only; each paid once, not on the renewal)" in body
    assert "Extra desktops (Windows only; each paid once, not on the renewal)" in body
    assert 'data-phone-price="50.00"' in body and 'data-desktop-price="50.00"' in body
    assert "Amount paid (the full first payment, extra phones and desktops included)" in body
    assert "extra_device_pairs" not in body
    client.get("/locale/ar?next=/")
    ar = client.get("/licenses/issuance/new").get_data(as_text=True)
    assert "هواتف إضافية (أندرويد فقط؛ تُدفع مرة واحدة ولا تدخل في التجديد)" in ar
    assert "أجهزة مكتبية إضافية (ويندوز فقط؛ تُدفع مرة واحدة ولا تدخل في التجديد)" in ar
    assert "المبلغ المدفوع (الدفعة الأولى كاملة، شاملة الهواتف والأجهزة المكتبية الإضافية)" in ar
    assert "Extra phones (Android only" not in ar


def test_t14b_the_script_adds_both_extras_to_the_amount():
    """The seller's amount box is pre-filled by sale-form.js on the trial-payment page; it must add
    phones and desktops at their own prices (a copy of the old pair maths would price them wrong)."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "sale-form.js").read_text(encoding="utf-8")
    assert "first + phoneCount * phonePrice + desktopCount * desktopPrice" in js
    assert "sale-extra-phones" in js and "sale-extra-desktops" in js and "data-pair-price" not in js


def test_t15_seed_packages_prices_the_typed_extras_and_retires_the_old_offers(app, seeded):
    import json as _json

    def _run():
        result = app.test_cli_runner().invoke(args=["seed-packages"])
        assert result.exit_code == 0, result.output
        report, _end = _json.JSONDecoder().raw_decode(result.output)
        return report, result.output

    def _addon(code):
        with app.app_context():
            from sqlalchemy import select

            from app.extensions import db_session
            from app.models.catalog import Addon

            row = db_session.execute(select(Addon).where(Addon.addon_code == code)).scalars().one()
            return row.availability_status, (Decimal(row.price) if row.price is not None else None), row.currency

    first, _ = _run()
    assert first["extras"] == {"EXTRA_DESKTOP": "put on sale", "EXTRA_PHONE": "put on sale"}
    assert sorted(first["retired_addons"]) == ["EXTRA_BRANCH", "EXTRA_DEVICE_PAIR"]
    assert _addon("EXTRA_PHONE") == ("AVAILABLE", Decimal("50.00"), "JOD")
    assert _addon("EXTRA_DESKTOP") == ("AVAILABLE", Decimal("50.00"), "JOD")
    assert _addon("EXTRA_DEVICE_PAIR")[0] == "RETIRED" and _addon("EXTRA_BRANCH")[0] == "RETIRED"
    again, _ = _run()  # idempotent: nothing more to do
    assert again["extras"] == {} and again["retired_addons"] == [] and again["differs"] == []
    # a price someone set by hand is reported and KEPT, never overwritten
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.catalog import Addon

        db_session.execute(select(Addon).where(Addon.addon_code == "EXTRA_PHONE")).scalars().one().price = Decimal("45.00")
        db_session.commit()
    third, output = _run()
    assert _addon("EXTRA_PHONE")[1] == Decimal("45.00")
    assert [d["addon_code"] for d in third["differs"]] == ["EXTRA_PHONE"] and "45.00" in output
    assert "taken off sale" not in output  # nothing left to retire this time


def test_t16_commission_ignores_both_typed_kinds(app, seeded):
    from tests.test_step0_ladder import _entries, _sell, _world

    w = _world(app, rule="PERCENT")
    _sell(app, w, "p1", phones=1, desktops=1)  # pays 350, of which 250 is the package
    rows = _entries(app, w["seller_profile"], kind="PAYMENT_RULE")
    assert [r[1] for r in rows] == [Decimal("25.00")]  # 10% of 250, not of 350


def test_t17_a_refund_takes_back_exactly_the_refunded_kind_and_never_twice(app, seeded):
    """Typed refund accounting, with the line order pinned: the desktop line comes before the
    phone line on the invoice, so the FIRST 50 given back is the desktop and the SECOND 50 the phone.
    The second refund must remove only the phone: what the first already scheduled is not taken twice."""
    from datetime import timedelta, timezone

    from tests.test_step0_ladder import _refund, _rows, _sell, _world

    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1", phones=1, desktops=1)["order_id"]
    rows = _rows(app, sale)

    def _stops():
        with app.app_context():
            from sqlalchemy import select

            from app.extensions import db_session
            from app.models.licensing import LicenseScheduledStop

            return sorted((s.devices_removed, s.desktops_removed, s.phones_removed)
                          for s in db_session.execute(select(LicenseScheduledStop)).scalars().all())

    def _apply(days):
        with app.app_context():
            from sqlalchemy import select

            from app.extensions import db_session
            from app.licensing.scheduled_stops import apply_due_licence_stops
            from app.models.commercial_sales import CommercialRefund
            from app.models.licensing import License

            paid = max(r.paid_at for r in db_session.execute(select(CommercialRefund)).scalars().all()).astimezone(timezone.utc)
            apply_due_licence_stops(now=paid + timedelta(days=days))
            db_session.expire_all()
            lic = db_session.get(License, rows["license_id"])
            return lic.device_limit, lic.extra_desktop_slots, lic.extra_phone_slots

    _refund(app, w, sale, "50.00")
    assert _stops() == [(1, 1, 0)]
    assert _apply(2) == (3, 0, 1)       # the DESKTOP went, the phone stayed
    _refund(app, w, sale, "50.00")
    assert _stops() == [(1, 0, 1), (1, 1, 0)]  # the second stop is the phone only, not 2 devices
    assert _apply(2) == (2, 0, 0)       # the phone went; back to the 2 included devices


def test_t20_paying_for_a_trial_with_a_phone_keeps_the_same_licence_and_types_the_phone(app, client, seeded, kyc, signing_key):
    from tests.test_customer_onboarding import _world as onboarding_world
    from tests.test_customer_onboarding import onboard
    from tests.test_trial_conversion import confirm, sell, snapshot

    w = onboarding_world(app)
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.catalog import Addon

        addon = db_session.execute(select(Addon).where(Addon.addon_code == "EXTRA_PHONE")).scalars().one()
        addon.price, addon.currency, addon.availability_status = Decimal("50.00"), "JOD", "AVAILABLE"
        db_session.commit()
    trial = onboard(app, w)
    before = snapshot(app, trial)
    done = confirm(app, w, sell(app, w, trial, amount="150.00", extra_phones=1))
    after = snapshot(app, trial)
    assert done["converted"] is True
    assert (after["lic"]["id"], after["lic"]["hmac"]) == (before["lic"]["id"], before["lic"]["hmac"])  # same key
    assert after["lic"]["limit"] == 3 and after["sub"]["devices"] == 3
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License

        lic = db_session.get(License, trial.license_id)
        assert (lic.extra_desktop_slots, lic.extra_phone_slots) == (0, 1)


def test_t20b_the_trial_payment_page_offers_the_typed_fields(app, client, seeded, kyc):
    from tests.conftest import force_login
    from tests.test_customer_onboarding import _world as onboarding_world
    from tests.test_customer_onboarding import onboard

    w = onboarding_world(app)
    trial = onboard(app, w)
    force_login(client, app, w["seller"])
    html = client.get(f"/licenses/issuance/new?convert={trial.subscription_id}").get_data(as_text=True)
    assert 'data-converting-trial="1"' in html
    assert 'name="extra_phones"' in html and 'name="extra_desktops"' in html and "extra_device_pairs" not in html


def test_t21_the_invoice_is_tax_inclusive_and_the_renewal_stays_100(app, client, seeded):
    """Owner: 50 JOD per device is tax-INCLUSIVE. The invoice shows no separate tax (0.00), total =
    subtotal = 300.00 for Package 1 + one phone, and the renewal is the package's 100 whatever was added."""
    from tests.test_step0_packages import _confirm, _finished, _only_order, _post_sale

    w = _sales_world(app)
    assert _post_sale(client, app, w, "p1", amount="300.00", phones=1).status_code == 200
    order_id = _only_order(app)
    _confirm(app, order_id, w["manager"])
    done = _finished(app, order_id)
    assert (done["invoice_subtotal"], done["invoice_tax"], done["invoice_total"]) == (
        Decimal("300.00"), Decimal("0.00"), Decimal("300.00"))
    with app.app_context():
        from sqlalchemy import select

        from app.commercial_sales.packages import renewal_price
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        sub = db_session.execute(select(Subscription).where(Subscription.sales_order_id == order_id)).scalars().one()
        assert renewal_price(sub) == (Decimal("100.00"), "JOD")
