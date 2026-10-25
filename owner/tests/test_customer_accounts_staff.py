"""The staff side of customer website accounts: the panel, who may do what (the
Sales-sees-only-own-customers scope is the important one), show-once links, recent
auth on the sensitive actions, the sale-confirmed reminder and the branch name."""
from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import func, select

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_license, make_staff
from tests.test_customer_accounts_service import GOOD, IP, PORTAL, new_customer, setup_accounts, token_of

BASE = "/customers/{cid}/website-account"


def _enable(app, *, mail=False):
    setup_accounts(app, mail=mail)


def _csrf(client, cid):
    page = client.get(f"/customers/{cid}")
    assert page.status_code == 200, page.status_code
    return get_csrf(page.get_data(as_text=True))


def _post(client, cid, action, **data):
    return client.post(BASE.format(cid=cid) + f"/{action}", data={"csrf_token": _csrf_any(client), **data})


def _csrf_any(client):
    # any rendered form carries a token; the reauth page needs no customer
    return get_csrf(client.get("/auth/reauth").get_data(as_text=True))


def _accounts(app):
    with app.app_context():
        from app.extensions import db_session
        from app.models.customer_accounts import CustomerAccount

        return db_session.execute(select(CustomerAccount)).scalars().all()


def _assigned_customer(app, staff_id):
    cid = new_customer(app)
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer

        db_session.get(Customer, cid).assigned_sales_staff_id = staff_id
        db_session.commit()
    return cid


# ------------------------------------------------------------- off by default

def test_everything_is_invisible_and_404_while_the_feature_is_off(app, client, seeded):
    admin = make_staff(app, "admin@example.com", super_admin=True, mfa=True)
    cid = new_customer(app)
    login_and_verify_mfa(client, "admin@example.com")
    page = client.get(f"/customers/{cid}").get_data(as_text=True)
    assert "website-account" not in page and "Website account" not in page
    resp = client.post(BASE.format(cid=cid) + "/create", data={"csrf_token": _csrf_any(client), "email": "a@b.example"})
    assert resp.status_code == 404
    assert _accounts(app) == []
    # allow: switched on, the same admin sees the panel
    _enable(app)
    assert "id=\"website-account\"" in client.get(f"/customers/{cid}").get_data(as_text=True)


# ---------------------------------------------------------------- show once

def test_with_email_off_the_link_is_shown_once_and_never_again(app, client, seeded):
    make_staff(app, "admin@example.com", super_admin=True, mfa=True)
    _enable(app)
    cid = new_customer(app)
    login_and_verify_mfa(client, "admin@example.com")
    resp = _post(client, cid, "create", email="Owner@Shop.Example", language="ar")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"
    assert 'data-link-shown-once="1"' in html and f"{PORTAL}/ar/account/set-password/#t=" in html
    token = re.search(r"#t=([A-Za-z0-9_\-%]+)", html).group(1)
    accounts = _accounts(app)
    assert [(a.email, a.status, a.language) for a in accounts] == [("owner@shop.example", "INVITED", "ar")]
    # gone from every later page
    assert token not in client.get(f"/customers/{cid}").get_data(as_text=True)
    # allow: the shown link really works
    with app.app_context():
        from urllib.parse import unquote

        from app.customer_accounts import service

        assert service.set_password_with_token(unquote(token), GOOD, IP).status == "ACTIVE"


def test_with_email_on_the_link_is_mailed_and_not_shown(app, client, seeded):
    make_staff(app, "admin@example.com", super_admin=True, mfa=True)
    _enable(app, mail=True)
    cid = new_customer(app)
    login_and_verify_mfa(client, "admin@example.com")
    resp = _post(client, cid, "create", email="owner@shop.example", language="en")
    html = resp.get_data(as_text=True)
    assert 'data-link-emailed="1"' in html and "#t=" not in html and 'data-link-shown-once' not in html
    with app.app_context():
        from app.extensions import db_session
        from app.models.email_outbox import EmailOutbox

        row = db_session.execute(select(EmailOutbox)).scalars().one()
        assert row.kind == "customer_invitation" and row.to_address == "owner@shop.example" and "#t=" in row.body_text


def test_a_duplicate_email_is_refused_with_a_message_and_writes_nothing(app, client, seeded):
    make_staff(app, "admin@example.com", super_admin=True, mfa=True)
    _enable(app)
    first, second = new_customer(app), new_customer(app)
    login_and_verify_mfa(client, "admin@example.com")
    _post(client, first, "create", email="one@shop.example", language="en")
    resp = _post(client, second, "create", email="ONE@shop.example", language="en")
    assert resp.status_code == 302
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "already used by another customer" in page
    assert len(_accounts(app)) == 1


# --------------------------------------------------- who may do what (RBAC)

def test_sales_creates_an_account_only_for_their_own_customer(app, client, seeded):
    seller = make_staff(app, "seller@example.com", role_codes=["SALES"])
    other_seller = make_staff(app, "other@example.com", role_codes=["SALES"])
    _enable(app)
    mine, theirs = _assigned_customer(app, seller), _assigned_customer(app, other_seller)
    force_login(client, app, seller)
    # deny: another seller's customer -- indistinguishable from a missing one, nothing written
    token = get_csrf(client.get(f"/customers/{mine}").get_data(as_text=True))
    denied = client.post(BASE.format(cid=theirs) + "/create", data={"csrf_token": token, "email": "x@shop.example", "language": "en"})
    assert denied.status_code == 404
    assert _accounts(app) == []
    # allow: their own customer
    allowed = client.post(BASE.format(cid=mine) + "/create", data={"csrf_token": token, "email": "x@shop.example", "language": "en"})
    assert allowed.status_code == 200
    assert [a.customer_id for a in _accounts(app)] == [mine]
    # the panel shows on their own customer's page
    assert 'id="website-account"' in client.get(f"/customers/{mine}").get_data(as_text=True)
    assert client.get(f"/customers/{theirs}").status_code == 404


def test_sales_cannot_see_or_touch_another_sellers_existing_account(app, client, seeded):
    seller = make_staff(app, "seller@example.com", role_codes=["SALES"])
    other_seller = make_staff(app, "other@example.com", role_codes=["SALES"])
    _enable(app)
    theirs = _assigned_customer(app, other_seller)
    with app.app_context():
        from tests.test_customer_accounts_service import invite

        invite(app, theirs, "theirs@shop.example")
    force_login(client, app, seller)
    token = get_csrf(client.get("/auth/reauth").get_data(as_text=True))
    for action in ("resend", "reset-link", "sign-out"):
        assert client.post(BASE.format(cid=theirs) + f"/{action}", data={"csrf_token": token}).status_code == 404, action
    assert len(_accounts(app)) == 1


def test_sales_cannot_disable_or_change_the_email_even_on_their_own_customer(app, client, seeded):
    seller = make_staff(app, "seller@example.com", role_codes=["SALES"])
    _enable(app)
    mine = _assigned_customer(app, seller)
    with app.app_context():
        from tests.test_customer_accounts_service import invite

        invite(app, mine, "mine@shop.example")
    force_login(client, app, seller)
    token = get_csrf(client.get(f"/customers/{mine}").get_data(as_text=True))
    assert client.post(BASE.format(cid=mine) + "/disable", data={"csrf_token": token, "reason": "x"}).status_code == 403
    assert client.post(BASE.format(cid=mine) + "/enable", data={"csrf_token": token}).status_code == 403
    assert client.post(BASE.format(cid=mine) + "/change-email", data={"csrf_token": token, "new_email": "n@shop.example", "reason": "x"}).status_code == 403
    assert _accounts(app)[0].status == "INVITED" and _accounts(app)[0].email == "mine@shop.example"


def test_finance_may_look_but_not_change_and_viewer_not_even_look(app, client, seeded):
    finance = make_staff(app, "fin@example.com", role_codes=["FINANCE"])
    viewer = make_staff(app, "view@example.com", role_codes=["VIEWER"])
    _enable(app)
    cid = new_customer(app)
    with app.app_context():
        from tests.test_customer_accounts_service import invite

        invite(app, cid, "a@shop.example")
    force_login(client, app, finance)
    page = client.get(f"/customers/{cid}").get_data(as_text=True)
    assert 'id="website-account"' in page and "a@shop.example" in page
    assert "Send the invitation again" not in page and "Disable this account" not in page  # view only
    token = get_csrf(page)
    assert client.post(BASE.format(cid=cid) + "/resend", data={"csrf_token": token}).status_code == 403
    c2 = app.test_client()
    force_login(c2, app, viewer)
    assert 'id="website-account"' not in c2.get(f"/customers/{cid}").get_data(as_text=True)


def test_support_sees_every_customers_account_and_disable_needs_recent_auth(app, client, seeded):
    seller = make_staff(app, "seller@example.com", role_codes=["SALES"])
    support = make_staff(app, "support@example.com", role_codes=["SUPPORT"], mfa=True)
    _enable(app)
    cid = _assigned_customer(app, seller)  # not Support's customer
    with app.app_context():
        from tests.test_customer_accounts_service import invite

        issued = invite(app, cid, "a@shop.example")
        from app.customer_accounts import service

        service.set_password_with_token(token_of(issued), GOOD, IP)

    # deny: a session WITHOUT a recent confirmation is sent to re-confirm; nothing changes
    force_login(client, app, support)
    token = get_csrf(client.get("/auth/reauth").get_data(as_text=True))
    bounced = client.post(BASE.format(cid=cid) + "/disable", data={"csrf_token": token, "reason": "chargeback"})
    assert bounced.status_code == 302 and "/auth/reauth" in bounced.headers["Location"]
    assert _accounts(app)[0].status == "ACTIVE"

    # allow: a freshly MFA-confirmed session disables it, with the reason kept
    fresh = app.test_client()
    login_and_verify_mfa(fresh, "support@example.com")
    token = get_csrf(fresh.get("/auth/reauth").get_data(as_text=True))
    done = fresh.post(BASE.format(cid=cid) + "/disable", data={"csrf_token": token, "reason": "chargeback"})
    assert done.status_code == 302
    account = _accounts(app)[0]
    assert (account.status, account.disabled_reason) == ("DISABLED", "chargeback")
    # a missing reason is refused
    again = fresh.post(BASE.format(cid=cid) + "/enable", data={"csrf_token": token})
    assert again.status_code == 302 and _accounts(app)[0].status == "ACTIVE"
    refused = fresh.post(BASE.format(cid=cid) + "/disable", data={"csrf_token": token, "reason": "  "})
    assert refused.status_code == 302 and _accounts(app)[0].status == "ACTIVE"


def test_changing_the_email_needs_recent_auth_and_a_reason_and_the_old_address_stays_until_confirmed(app, client, seeded):
    make_staff(app, "admin@example.com", super_admin=True, mfa=True)
    _enable(app)
    cid = new_customer(app)
    with app.app_context():
        from tests.test_customer_accounts_service import invite

        invite(app, cid, "old@shop.example")
    stale = app.test_client()
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        admin_id = db_session.execute(select(StaffUser.id).where(StaffUser.email == "admin@example.com")).scalar_one()
    force_login(stale, app, admin_id)
    token = get_csrf(stale.get("/auth/reauth").get_data(as_text=True))
    bounced = stale.post(BASE.format(cid=cid) + "/change-email", data={"csrf_token": token, "new_email": "new@shop.example", "reason": "moved"})
    assert bounced.status_code == 302 and "/auth/reauth" in bounced.headers["Location"]
    login_and_verify_mfa(client, "admin@example.com")
    token = _csrf_any(client)
    no_reason = client.post(BASE.format(cid=cid) + "/change-email", data={"csrf_token": token, "new_email": "new@shop.example", "reason": ""})
    assert no_reason.status_code == 302
    ok = client.post(BASE.format(cid=cid) + "/change-email", data={"csrf_token": token, "new_email": "new@shop.example", "reason": "moved"})
    assert ok.status_code == 200 and "#t=" in ok.get_data(as_text=True) and "/account/confirm-email/" in ok.get_data(as_text=True)
    assert _accounts(app)[0].email == "old@shop.example"


def test_a_customer_account_page_never_leaks_other_customers(app, client, seeded):
    """The seller's panel shows only that customer's own email."""
    seller = make_staff(app, "seller@example.com", role_codes=["SALES"])
    _enable(app)
    mine = _assigned_customer(app, seller)
    other = new_customer(app)
    with app.app_context():
        from tests.test_customer_accounts_service import invite

        invite(app, mine, "mine@shop.example")
        invite(app, other, "secret-other@shop.example")
    force_login(client, app, seller)
    assert "secret-other@shop.example" not in client.get(f"/customers/{mine}").get_data(as_text=True)


# ------------------------------------------------------- sale-confirmed prompt

def test_confirming_a_sale_reminds_the_confirmer_when_the_customer_has_no_account(app, client, seeded):
    from tests.test_step1_sales_manager_and_leaderboard import _confirm_over_http, _email, _record_only, _team

    _enable(app)
    w = _team(app)
    sale = _record_only(app, w, recorder=w["seller"])
    login_and_verify_mfa(client, _email(app, w["sm"]))
    resp = _confirm_over_http(client, sale["order_id"])
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200 and 'data-account-prompt="1"' in html and "no website account" in html
    assert _accounts(app) == []  # a reminder, never an automatic creation
    link = re.search(r'href="(/customers/[0-9a-f-]+#website-account)"', html)
    assert link, html[:2000]
    # allow: once the account exists the reminder is gone (replaying the confirm shows the page again)
    cid = uuid.UUID(link.group(1).split("/")[2].split("#")[0])
    with app.app_context():
        from tests.test_customer_accounts_service import invite

        invite(app, cid, "shop@shop.example")
    replay = _confirm_over_http(client, sale["order_id"])
    assert replay.status_code == 200 and 'data-account-prompt="1"' not in replay.get_data(as_text=True)


def test_no_reminder_while_the_feature_is_off(app, client, seeded):
    from tests.test_step1_sales_manager_and_leaderboard import _confirm_over_http, _email, _record_only, _team

    w = _team(app)
    sale = _record_only(app, w, recorder=w["seller"])
    login_and_verify_mfa(client, _email(app, w["sm"]))
    assert 'data-account-prompt' not in _confirm_over_http(client, sale["order_id"]).get_data(as_text=True)


# --------------------------------------------------------------- branch name

def test_the_branch_name_is_edited_on_the_licence_page_and_hidden_while_off(app, client, seeded):
    admin = make_staff(app, "admin@example.com", super_admin=True, mfa=True)
    lic_id, _key = make_license(app, admin, device_limit=2)
    login_and_verify_mfa(client, "admin@example.com")
    assert "licence-branch-label" not in client.get(f"/licenses/{lic_id}").get_data(as_text=True)
    token = _csrf_any(client)
    assert client.post(f"/licenses/{lic_id}/branch-label", data={"csrf_token": token, "branch_label": "Dabouq"}).status_code == 404
    _enable(app)
    page = client.get(f"/licenses/{lic_id}").get_data(as_text=True)
    assert 'id="licence-branch-label"' in page
    saved = client.post(f"/licenses/{lic_id}/branch-label", data={"csrf_token": token, "branch_label": "  Dabouq   Branch "})
    assert saved.status_code == 302
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        sub = db_session.get(Subscription, db_session.get(License, lic_id).subscription_id)
        assert sub.branch_label == "Dabouq Branch"
    too_long = client.post(f"/licenses/{lic_id}/branch-label", data={"csrf_token": token, "branch_label": "x" * 121})
    assert too_long.status_code == 302
    with app.app_context():
        db_session.expire_all()
        assert db_session.get(Subscription, sub.id).branch_label == "Dabouq Branch"  # unchanged
    client.post(f"/licenses/{lic_id}/branch-label", data={"csrf_token": token, "branch_label": ""})
    with app.app_context():
        db_session.expire_all()
        assert db_session.get(Subscription, sub.id).branch_label is None


def test_a_seller_without_subscription_update_cannot_name_a_branch(app, client, seeded):
    viewer = make_staff(app, "viewer@example.com", role_codes=["VIEWER"])
    admin = make_staff(app, "admin@example.com", super_admin=True)
    lic_id, _key = make_license(app, admin)
    _enable(app)
    force_login(client, app, viewer)
    token = get_csrf(client.get("/auth/reauth").get_data(as_text=True))
    assert client.post(f"/licenses/{lic_id}/branch-label", data={"csrf_token": token, "branch_label": "Hack"}).status_code == 403


# ------------------------------------------------------------------ permissions

def test_the_four_account_permissions_are_seeded_with_the_owners_role_split(app, seeded):
    from app.staff.seed_data import PERMISSIONS, ROLES

    codes = {c for c, _cat, _d in PERMISSIONS}
    assert {"customer_accounts.view", "customer_accounts.manage", "customer_accounts.disable",
            "customer_accounts.change_email"} <= codes
    held = {role: {p for p in perms["permissions"] if p.startswith("customer_accounts.")} if perms["permissions"] != "*" else "*"
            for role, perms in ROLES.items()}
    assert held["SALES"] == {"customer_accounts.view", "customer_accounts.manage"}
    assert held["SALES_MANAGER"] == {"customer_accounts.view", "customer_accounts.manage"}
    assert held["SUPPORT"] == {"customer_accounts.view", "customer_accounts.manage", "customer_accounts.disable",
                               "customer_accounts.change_email"}
    assert held["FINANCE"] == {"customer_accounts.view"}
    assert held["VIEWER"] == set() and held["SUPER_ADMIN"] == "*"
    # lost-phone reset stays exactly where it was: nobody gained it here
    assert "installations.replace_device" not in ROLES["SALES_MANAGER"]["permissions"]
