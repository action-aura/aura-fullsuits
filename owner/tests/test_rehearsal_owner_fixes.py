"""2026-10-31 dress rehearsal: the Owner-side fixes (owner_fixes.md items 1-10).

Each class of test pairs a DENY half with its ALLOW twin, and was mutation-proved
(see the commit messages for what was broken and what went red).
"""
from __future__ import annotations

from tests.conftest import force_login, make_staff
from tests.test_commission_statements import clock, w  # noqa: F401 -- fixtures: the statement world and its pinned clock
from tests.test_customer_onboarding import kyc  # noqa: F401 -- a fixture


# ------------------------------------------------- 10. "your session ended"

def test_a_brand_new_visitor_is_not_told_their_session_ended(app, client, seeded):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]
    assert "reason=session_expired" not in resp.headers["Location"]
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Your session ended" not in page


def test_a_visitor_whose_session_really_ended_is_told_so(app, client, seeded):
    from app.auth.session import COOKIE_NAME

    # allow half: a cookie that no longer matches a live session (expired / revoked)
    client.set_cookie(key=COOKIE_NAME, value="a-token-that-no-longer-exists", domain="localhost")
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302 and "reason=session_expired" in resp.headers["Location"]
    assert "Your session ended" in client.get(resp.headers["Location"]).get_data(as_text=True)


# ------------------------------------------------------ 9. seller dashboard

def _summary(app, who, w):
    from app.dashboard.services import get_dashboard_summary
    from app.extensions import db_session
    from app.models.staff import StaffUser
    from tests.test_customer_onboarding import _codes

    with app.app_context():
        return get_dashboard_summary(db_session.get(StaffUser, w[who]), _codes(app, w[who]))


def test_a_sellers_dashboard_counts_only_their_own_customers_a_managers_counts_all(app, client, seeded, kyc):
    from tests.test_customer_onboarding import _world, onboard

    w = _world(app)
    onboard(app, w, who="seller")
    onboard(app, w, who="other_seller")
    onboard(app, w, who="other_seller")

    mine = _summary(app, "seller", w)
    theirs = _summary(app, "other_seller", w)
    manager = _summary(app, "manager", w)
    # deny half: the seller does not see the company
    assert (mine["total_customers"], theirs["total_customers"]) == (1, 2)
    assert sum(mine["subs_by_product"].values()) == 1 and sum(theirs["subs_by_product"].values()) == 2
    assert sum(mine["licenses_by_status"].values()) == 1 and sum(theirs["licenses_by_status"].values()) == 2
    assert mine["pilots_needing_action"] == 0
    assert mine["scoped"] is True
    # allow half: a manager keeps the company view
    assert manager["scoped"] is False and manager["total_customers"] == 3
    assert sum(manager["subs_by_product"].values()) == 3 and sum(manager["licenses_by_status"].values()) == 3

    # the page says so to the seller, and not to the manager
    force_login(client, app, w["seller"])
    seller_page = client.get("/").get_data(as_text=True)
    assert 'data-dashboard-scoped="1"' in seller_page
    client.get("/auth/logout")
    force_login(client, app, w["manager"])
    assert 'data-dashboard-scoped="1"' not in client.get("/").get_data(as_text=True)


def _scoped_answerer(app):
    """A staff user the objections card IS drawn for AND whose dashboard is scoped.

    No seeded role is both: a seller (customers.view_own) cannot answer objections, and
    everyone who can has customers.view_all. Without such a user the template's own
    permission check would hide the card from a seller whether or not the numbers were
    scoped, so a page test on a plain seller could not tell the two apart (review r33)."""
    from tests.conftest import make_staff as _make

    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.staff import Permission, Role, RolePermission

        sales = db_session.execute(select(Role).where(Role.code == "SALES")).scalar_one()
        answer = db_session.execute(select(Permission).where(Permission.code == "commission_objections.answer")).scalar_one()
        role = Role(code="SCOPED_ANSWERER", name="Scoped answerer (test)", description="test only", is_system_role=False)
        db_session.add(role)
        db_session.flush()
        for perm_id in {rp.permission_id for rp in db_session.execute(
                select(RolePermission).where(RolePermission.role_id == sales.id)).scalars()} | {answer.id}:
            db_session.add(RolePermission(role_id=role.id, permission_id=perm_id))
        db_session.commit()
    return _make(app, f"scoped-{uuid.uuid4().hex[:6]}@example.com", role_codes=["SCOPED_ANSWERER"], mfa=True)


def _objection_tiles(html):
    """(open, overdue) as drawn in the dashboard's objections card."""
    values = re.findall(r'aura-stat-tile__value">\s*(\d+)\s*</span>\s*<span class="aura-stat-tile__label">(?:Open|Overdue) objections', html)
    assert len(values) == 2, html[:300]
    return int(values[0]), int(values[1])


def test_a_sellers_dashboard_does_not_carry_other_sellers_statement_objections(app, client, seeded, w, clock):
    from app.commissions import statements
    from tests.test_commission_statements import JUNE, UTC, _client, _object, amman

    answerer = _scoped_answerer(app)
    # two objections that belong to OTHER sellers: one past its deadline, one still inside it
    clock.set(amman(2026, 7, 2, 10))
    _object(app, w, "seller", JUNE, amman(2026, 7, 2, 10).astimezone(UTC))   # due end of Mon 6 July
    _object(app, w, "other", JUNE, amman(2026, 7, 6, 10).astimezone(UTC))    # due end of Wed 8 July
    clock.set(amman(2026, 7, 7, 0, 0, 1))
    with app.app_context():
        assert statements.open_and_overdue_counts() == (2, 1)  # the setup really has something to leak

    # allow half: a manager still sees the company's real counts, in the data and on the page
    manager = _summary(app, "manager", w)
    assert manager["scoped"] is False
    assert (manager["open_statement_objections"], manager["overdue_statement_objections"]) == (2, 1)
    assert _objection_tiles(_client(app, w["manager"]).get("/").get_data(as_text=True)) == (2, 1)

    # deny half: someone whose dashboard is scoped sees 0 / 0, in the data and on the page
    scoped = _summary(app, "answerer", {"answerer": answerer})
    assert scoped["scoped"] is True
    assert (scoped["open_statement_objections"], scoped["overdue_statement_objections"]) == (0, 0)
    page = _client(app, answerer).get("/").get_data(as_text=True)
    assert 'data-objections-card="1"' in page  # the card is drawn, so the zeros are what the page really says
    assert _objection_tiles(page) == (0, 0)
    assert "aura-stat-tile--danger" not in page.split('data-objections-card="1"')[1].split("</div>")[0]


# ------------------------------------- 2. the generic sale form + legacy form

import io
import re
import uuid

from tests.conftest import get_csrf, jordan_today
from tests.test_trial_conversion import SLIP, confirm, money_rows, sell


def _sale_post(client, app, who_id, w, *, method, slip=None, customer_id=None, amount="100.00", plan=None, converts=None):
    force_login(client, app, who_id)
    page = client.get("/licenses/issuance/new").get_data(as_text=True)
    data = {
        "csrf_token": get_csrf(page), "idempotency_key": re.search(r'name="idempotency_key" value="([^"]+)"', page).group(1),
        "plan_id": str(plan or w["plan"]), "extra_phones": "0", "extra_desktops": "0", "amount": amount, "method": method,
        "reference": "REF-" + uuid.uuid4().hex[:6], "payment_date": jordan_today().isoformat(),
    }
    if converts:
        data["converts_subscription_id"] = str(converts)
    if customer_id:
        data["customer_id"] = str(customer_id)
    else:
        data["new_customer_legal_name"] = "Corner Shop " + uuid.uuid4().hex[:4]
    if slip:
        data["slip_photo"] = (io.BytesIO(slip), "slip.jpg")
    return client.post("/licenses/issuance/sale", data=data, content_type="multipart/form-data")


def test_the_sale_form_needs_a_slip_for_a_transfer_but_not_for_cash(app, client, seeded, kyc):
    from tests.test_customer_onboarding import _world

    w = _world(app)
    base = money_rows(app)
    # deny: a bank transfer with no slip is refused and writes nothing
    resp = _sale_post(client, app, w["seller"], w, method="BANK_TRANSFER")
    assert resp.status_code == 400 and "Only a cash payment can be recorded without one" in resp.get_data(as_text=True)
    assert money_rows(app) == base
    # allow: the same sale as cash needs no slip ...
    resp = _sale_post(client, app, w["seller"], w, method="CASH")
    assert resp.status_code == 200 and "Sale recorded" in resp.get_data(as_text=True)
    assert money_rows(app)["payments"] == base["payments"] + 1
    # ... and a transfer WITH its slip is recorded together with the slip photo
    resp = _sale_post(client, app, w["seller"], w, method="BANK_TRANSFER", slip=SLIP)
    assert resp.status_code == 200 and "Sale recorded" in resp.get_data(as_text=True)
    after = money_rows(app)
    assert (after["payments"], after["slips"]) == (base["payments"] + 2, base["slips"] + 1)


def test_the_sale_form_sends_a_customer_in_a_trial_to_the_trial_payment_page(app, client, seeded, kyc):
    from tests.test_customer_onboarding import _world, onboard

    w = _world(app)
    trial = onboard(app, w)
    base = money_rows(app)
    # deny: a generic sale for this customer would confirm into a SECOND licence -- nothing is written
    resp = _sale_post(client, app, w["seller"], w, method="CASH", customer_id=trial.customer_id)
    assert resp.status_code == 302
    where = resp.headers["Location"]
    assert f"convert={trial.subscription_id}" in where and "from_sale=1" in where
    assert money_rows(app) == base
    assert 'data-sent-from-sale="1"' in client.get(where).get_data(as_text=True)
    # allow: once the trial has been paid for (CONVERTED), the customer is sold to the ordinary way
    confirm(app, w, sell(app, w, trial, amount="100.00"))
    resp = _sale_post(client, app, w["seller"], w, method="CASH", customer_id=trial.customer_id)
    assert resp.status_code == 200 and "Sale recorded" in resp.get_data(as_text=True)


def test_the_old_record_payment_form_is_for_finance_and_managers_not_sellers(app, client, seeded):
    from tests.test_customer_onboarding import _world

    w = _world(app)
    with app.test_request_context():
        from flask import url_for

        legacy = url_for("commercial_sales_web.new_payment_form")
    force_login(client, app, w["seller"])
    resp = client.get(legacy, follow_redirects=False)  # deny: a seller is sent to "Sell a licence"
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/licenses/issuance/new")
    client.get("/auth/logout")
    force_login(client, app, w["admin"])
    assert client.get(legacy, follow_redirects=False).status_code == 200  # allow: finance / admins keep it


def _legacy_payment_post(client, app, who_id, customer_id):
    force_login(client, app, who_id)
    page = client.get("/profile").get_data(as_text=True)  # any page of the signed-in user carries the CSRF token
    return client.post("/payments", data={
        "csrf_token": get_csrf(page), "customer_id": str(customer_id), "amount": "10.00", "currency": "JOD",
        "method": "CASH", "payment_date": jordan_today().isoformat(), "reference": "LEGACY-" + uuid.uuid4().hex[:6],
    }, follow_redirects=False)


def test_a_seller_cannot_record_a_bare_payment_by_posting_to_the_old_form_directly(app, client, seeded, kyc):
    from tests.test_customer_onboarding import _codes, _world, onboard

    w = _world(app)
    customer_id = onboard(app, w).customer_id
    assert "payments.create" in _codes(app, w["seller"]) and "payments.confirm" not in _codes(app, w["seller"])
    base = money_rows(app)
    # deny: the seller holds payments.create but not payments.confirm -- sent to "Sell a licence", nothing recorded
    resp = _legacy_payment_post(client, app, w["seller"], customer_id)
    assert money_rows(app)["payments"] == base["payments"]  # the count first: this is what must not move
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/licenses/issuance/new")
    client.get("/auth/logout")
    # allow: someone who holds payments.confirm still records the payment through the same route
    resp = _legacy_payment_post(client, app, w["admin"], customer_id)
    assert resp.status_code == 302 and "/payments/" in resp.headers["Location"]
    assert money_rows(app)["payments"] == base["payments"] + 1


# ------------------------------------ 1. the trial's payment page pre-fills

def _first_year_costs_250(app, w):
    """The launch Package 1 shape: 100 a year + 150 first time = 250 JOD to pay first."""
    from datetime import date

    with app.app_context():
        from app.catalog.services import add_plan_price
        from app.extensions import db_session
        from app.models.catalog import Plan

        add_plan_price(db_session.get(Plan, w["plan"]), 100, "JOD", date.today(), None, setup_fee=150)


def test_the_trial_payment_page_prefills_the_first_payment_and_says_what_it_is_made_of(app, client, seeded, kyc):
    from tests.test_customer_onboarding import _world, onboard

    w = _world(app)
    _first_year_costs_250(app, w)
    trial = onboard(app, w)
    force_login(client, app, w["seller"])
    html = client.get(f"/licenses/issuance/new?convert={trial.subscription_id}").get_data(as_text=True)
    assert re.search(r'id="sale-amount"[^>]*value="250\.00"', html)  # the amount is already typed
    assert re.search(r'data-amount-breakdown="1">\s*100\.00 JOD first year \+ 150\.00 JOD first-time fee', html)
    assert f'<option value="{w["plan"]}" data-first-payment="250.00"' in html and "selected" in html.split(f'value="{w["plan"]}"')[1][:200]
    assert re.search(r'data-cash-rule="1"\s+hidden', html)  # the cash rule is only shown once Cash is picked
    # the ordinary sale form (no trial) is NOT pre-filled: a new sale's amount is the seller's to enter
    plain = client.get("/licenses/issuance/new").get_data(as_text=True)
    assert re.search(r'id="sale-amount"[^>]*value=""', plain) and 'data-amount-breakdown' not in plain


def test_the_success_page_for_a_trial_payment_does_not_promise_a_new_key_and_repeats_the_cash_rule(app, client, seeded, kyc):
    from tests.test_customer_onboarding import _world, onboard

    w = _world(app)
    _first_year_costs_250(app, w)
    trial = onboard(app, w)
    cash = _sale_post(client, app, w["seller"], w, method="CASH", amount="250.00", converts=trial.subscription_id,
                      customer_id=trial.customer_id).get_data(as_text=True)
    assert "Sale recorded" in cash and 'data-recorded-trial="1"' in cash and 'data-cash-reminder="1"' in cash
    assert "licence key is made when the payment is confirmed" not in cash
    assert "within 24 hours" in cash
    # a second trial customer paying by transfer: same page, but no cash reminder
    trial2 = onboard(app, w)
    bank = _sale_post(client, app, w["seller"], w, method="BANK_TRANSFER", slip=SLIP, amount="250.00",
                      converts=trial2.subscription_id, customer_id=trial2.customer_id).get_data(as_text=True)
    assert 'data-recorded-trial="1"' in bank and 'data-cash-reminder' not in bank
    # and an ordinary (non-trial) sale keeps its own wording
    plain = _sale_post(client, app, w["seller"], w, method="CASH", amount="250.00").get_data(as_text=True)
    assert "licence key is made when the payment is confirmed" in plain and 'data-recorded-trial' not in plain


# -------------------------------- 3. the manager's list: method + reject message

def test_the_managers_list_names_the_payment_method_and_a_reject_says_so(app, client, seeded, kyc):
    from tests.test_customer_onboarding import _world, onboard

    w = _world(app)
    cash = _sale_post(client, app, w["seller"], w, method="CASH")
    assert "Sale recorded" in cash.get_data(as_text=True)
    bank = _sale_post(client, app, w["seller"], w, method="BANK_TRANSFER", slip=SLIP)
    assert "Sale recorded" in bank.get_data(as_text=True)

    force_login(client, app, w["manager"])
    page = client.get("/licenses/issuance/sales").get_data(as_text=True)
    assert 'data-payment-method="CASH"' in page and "Cash" in page
    assert 'data-payment-method="BANK_TRANSFER"' in page and "CliQ / bank transfer" in page
    # "No slip photo" is explained for cash, and kept for a transfer that somehow has none
    assert "No slip (cash the seller collected)" in page
    assert "data-slip-link" in page  # the transfer's slip is linked

    order_id = re.search(r'action="/licenses/issuance/sales/([0-9a-f-]{36})/reject"', page).group(1)
    resp = client.post(f"/licenses/issuance/sales/{order_id}/reject",
                       data={"csrf_token": get_csrf(page), "reason": "The money never arrived"}, follow_redirects=True)
    after = resp.get_data(as_text=True)
    assert resp.status_code == 200 and "was rejected. No licence was made." in after  # deny: the click is acknowledged
    assert order_id not in after  # and the sale left the list


# ------------------------------------------ 4. after payment: customer + paid until

def _lifecycle(app, customer_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer

        db_session.expire_all()
        return db_session.get(Customer, customer_id).lifecycle_status


def test_a_paid_trial_makes_the_customer_active_and_the_page_shows_paid_until(app, client, seeded, kyc):
    from tests.test_customer_onboarding import _world, onboard

    w = _world(app)
    trial = onboard(app, w)
    before = _lifecycle(app, trial.customer_id)
    assert before in ("LEAD", "PROSPECT", "PILOT")  # the starting point the rehearsal saw ("prospect")
    sale = sell(app, w, trial, amount="100.00")
    assert _lifecycle(app, trial.customer_id) == before  # nothing changes before the money is confirmed
    confirm(app, w, sale)
    assert _lifecycle(app, trial.customer_id) == "ACTIVE"
    force_login(client, app, w["manager"])
    page = client.get(f"/customers/{trial.customer_id}").get_data(as_text=True)
    assert 'data-paid-until="1"' in page and "Paid until" in page
    assert 'data-trial-state="CONVERTED"' in page

    # deny twin: a payment never reopens a customer who was suspended in the meantime
    other = onboard(app, w)
    sale2 = sell(app, w, other, amount="100.00")
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer

        db_session.get(Customer, other.customer_id).lifecycle_status = "SUSPENDED"
        db_session.commit()
    confirm(app, w, sale2)
    assert _lifecycle(app, other.customer_id) == "SUSPENDED"


# ------------------------------------------ 6. the sign-in names the package

from tests.test_account_activation import world  # noqa: E402,F401 -- a fixture: a trial customer with an account


def test_account_sign_in_carries_the_package_name_in_both_languages_next_to_the_code(app, client, world):
    from tests.conftest import make_device_keypair
    from tests.test_account_activation import sign_in_ok

    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.catalog import Plan

        plan = db_session.execute(select(Plan).where(Plan.id == world_plan_id(world))).scalar_one()
        plan.name = "Aura Retail - Package 1 (yearly)"  # the launch package's stored name
        db_session.commit()
    body = sign_in_ok(client, make_device_keypair(), world)
    (licence,) = body["licences"]
    assert licence["plan_name"] == "Aura Retail - Package 1 (yearly)"
    assert licence["plan_name_ar"] == "الباقة 1 (سنوية)"  # real Arabic, not the English again
    assert licence["plan_code"].startswith("NF_")  # allow twin: the code is still there for code that keys on it
    # a hand-made plan with no Arabic entry: the app still gets a name (the stored one), never nothing
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.catalog import Plan

        db_session.execute(select(Plan).where(Plan.id == world_plan_id(world))).scalar_one().name = "Corner Custom Plan"
        db_session.commit()
    (licence,) = sign_in_ok(client, make_device_keypair(), world)["licences"]
    assert licence["plan_name"] == "Corner Custom Plan" and licence["plan_name_ar"] == "Corner Custom Plan"


def world_plan_id(world):
    from app.extensions import db_session
    from app.models.licensing import License
    import uuid

    return db_session.get(License, uuid.UUID(str(world["licence_id"]))).plan_id


# --------------------------------- 7. the new-customer form: password kept, shown once

def test_a_refused_new_customer_form_keeps_the_password_and_asks_for_the_photos_again(app, client, seeded, kyc):
    from tests.test_customer_onboarding import GOOD, _post_form, _world

    w = _world(app)
    force_login(client, app, w["seller"])
    typed = "Typed-By-Seller-77!"
    resp = _post_form(client, w, temp_password=typed, consent="")  # no consent -> refused
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400 and 'data-onboarding-error="1"' in html
    assert re.search(r'name="temp_password"[^>]*value="Typed-By-Seller-77!"', html)  # deny: it is not blanked
    assert 'data-reattach-photos="1"' in html  # the photos cannot be kept, and the form says so
    assert "no-store" in resp.headers.get("Cache-Control", "")
    # allow twin: a fresh form (nothing refused) shows neither the note nor a password
    fresh = client.get("/customers/new-trial").get_data(as_text=True)
    assert 'data-reattach-photos' not in fresh and 'value="Typed-By-Seller-77!"' not in fresh


def test_the_new_customer_result_shows_the_temporary_password_once_with_a_copy_button(app, client, seeded, kyc):
    from tests.test_customer_onboarding import _post_form, _world

    w = _world(app)
    force_login(client, app, w["seller"])
    typed = "Shown-Once-Pass-91!"
    key = str(uuid.uuid4())
    same = dict(idempotency_key=key, business_name="Replay Shop", phone="079 555 1212", cr_number="RCR-REPLAY-1",
                email="replay@route.example")
    resp = _post_form(client, w, temp_password=typed, **same)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200 and 'data-temp-password-card="1"' in html
    assert re.search(r'id="onboarding-temp-password"[^>]*>Shown-Once-Pass-91!</code>', html)
    assert 'data-copy-target="onboarding-temp-password"' in html and "only time it is shown" in html
    # deny twin: pressing the same form again (same key) is a replay and shows no password
    again = _post_form(client, w, temp_password="Another-Pass-55!!", **same).get_data(as_text=True)
    assert "Already created" in again and "data-temp-password" not in again and "Another-Pass-55" not in again


# ----------------------------------------------------------- 8. Arabic wording

def _ar_catalog():
    from babel.messages.pofile import read_po
    import os

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "translations", "ar", "LC_MESSAGES", "messages.po")
    with open(path, "rb") as handle:
        return [m for m in read_po(handle) if m.id and isinstance(m.string, str) and m.string]


def test_arabic_strings_use_the_em_dash_and_spell_the_ladder_noun_with_its_vowels():
    catalog = _ar_catalog()
    # deny: no lone "--" (two hyphens standing in for a dash) is left in any Arabic translation
    assert [m.id for m in catalog if re.search(r"(?:(?<=\s)|^)--(?=\s|$)", m.string)] == []
    # deny: the ladder noun is never the misreadable "سلّم" glued to its noun ...
    assert [m.id for m in catalog if re.search(r"سلّم العمولة|السلّم|سلّمك", m.string)] == []
    # ... while the verb "hand over" (cash rule) is untouched, and the noun reads right where it is meant
    by_id = {m.id: m.string for m in catalog}
    assert "سُلَّم العمولة" in by_id["Monthly commission ladder"]
    assert "سلّم النقد كله" in by_id["Hand over ALL of the cash to the company within 24 hours, by bank transfer or at the office, with no deduction. Record the sale now, before you hand it over."]
    assert by_id["The customer is paying -- record the payment"] == "العميل يدفع — سجّل الدفعة"


def test_the_arabic_plan_cards_show_the_dinar_sign_and_an_arabic_comma_not_a_latin_jod(app, client, seeded):
    from tests.test_customer_onboarding import _world

    w = _world(app)
    _first_year_costs_250(app, w)
    force_login(client, app, w["seller"])
    client.get("/locale/ar?next=/")
    html = client.get("/licenses/issuance/new").get_data(as_text=True)
    option = re.search(r'<option value="%s"[^>]*>(.*?)</option>' % w["plan"], html, re.S).group(1)
    assert "د.أ" in option and "،" in option and "JOD" not in option and "," not in option.split("(")[0]
    assert "⁦250.00⁩ د.أ" in option  # the number is isolated so it cannot jumble
    client.get("/locale/en?next=/")  # allow twin: English keeps "250.00 JOD,"
    option_en = re.search(r'<option value="%s"[^>]*>(.*?)</option>' % w["plan"], client.get("/licenses/issuance/new").get_data(as_text=True), re.S).group(1)
    assert "250.00 JOD," in option_en
