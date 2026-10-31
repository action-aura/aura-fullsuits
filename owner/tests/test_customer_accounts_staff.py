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


# ================================================================ fix round

def _licence_for(app, admin, assigned_to):
    lic_id, _key = make_license(app, admin)
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer
        from app.models.licensing import License

        lic = db_session.get(License, lic_id)
        db_session.get(Customer, lic.customer_id).assigned_sales_staff_id = assigned_to
        db_session.commit()
    return lic_id


def _branch_label(app, lic_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License

        return db_session.get(License, lic_id).subscription.branch_label


def _staff_with_only(app, email, permission_codes):
    """A staff user on a one-off role holding exactly `permission_codes`. Used because
    SALES lost subscriptions.update on release/seller-launch-owner (5302752f: the
    free buttons moved to SALES_MANAGER), so no stock seller role reaches the
    customer-scope check any more. The scope rule itself still binds anyone who
    holds subscriptions.update without customers.view_all, and this proves it."""
    staff_id = make_staff(app, email)
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import Permission, Role, RolePermission, StaffRoleAssignment

        role = Role(code="TEST_SUBS_EDITOR_" + email.split("@")[0].upper(), name="Test editor", description="test", is_system_role=False)
        db_session.add(role)
        db_session.flush()
        for code in permission_codes:
            perm = db_session.execute(select(Permission).where(Permission.code == code)).scalars().one()
            db_session.add(RolePermission(role_id=role.id, permission_id=perm.id))
        db_session.add(StaffRoleAssignment(staff_user_id=staff_id, role_id=role.id))
        db_session.commit()
    return staff_id


def test_a_staff_member_names_branches_only_for_their_own_customers(app, client, seeded):
    """M2: the branch route had permission checks but no customer scope, so anyone
    holding subscriptions.update could rename another seller's branches."""
    editor = _staff_with_only(app, "editor@example.com", ["subscriptions.update", "customers.view"])
    other = make_staff(app, "other@example.com", role_codes=["SALES"])
    admin = make_staff(app, "admin@example.com", super_admin=True)
    theirs = _licence_for(app, admin, other)
    mine = _licence_for(app, admin, editor)
    _enable(app)
    force_login(client, app, editor)
    token = get_csrf(client.get("/auth/reauth").get_data(as_text=True))
    resp = client.post(f"/licenses/{theirs}/branch-label", data={"csrf_token": token, "branch_label": "Hijacked"})
    assert resp.status_code == 404 and _branch_label(app, theirs) is None  # deny: answers like a missing licence
    resp = client.post(f"/licenses/{mine}/branch-label", data={"csrf_token": token, "branch_label": "Dabouq"})
    assert resp.status_code == 302 and _branch_label(app, mine) == "Dabouq"  # allow: their own


def test_a_stock_seller_cannot_name_any_branch_since_the_free_buttons_moved(app, client, seeded):
    """The seller-launch owner decision (5302752f): SALES no longer holds
    subscriptions.update, so the branch route refuses them outright (403), their
    own customer included. Pins that this branch's role split and the CA1 route agree."""
    seller = make_staff(app, "seller@example.com", role_codes=["SALES"])
    admin = make_staff(app, "admin@example.com", super_admin=True)
    mine = _licence_for(app, admin, seller)
    _enable(app)
    force_login(client, app, seller)
    token = get_csrf(client.get("/auth/reauth").get_data(as_text=True))
    assert client.post(f"/licenses/{mine}/branch-label", data={"csrf_token": token, "branch_label": "Dabouq"}).status_code == 403
    assert _branch_label(app, mine) is None


def test_a_viewer_without_subscription_update_cannot_name_a_branch(app, client, seeded):
    """The permission half only (the customer-scope half is the test above)."""
    viewer = make_staff(app, "viewer@example.com", role_codes=["VIEWER"])
    admin = make_staff(app, "admin@example.com", super_admin=True)
    lic_id, _key = make_license(app, admin)
    _enable(app)
    force_login(client, app, viewer)
    token = get_csrf(client.get("/auth/reauth").get_data(as_text=True))
    assert client.post(f"/licenses/{lic_id}/branch-label", data={"csrf_token": token, "branch_label": "Hack"}).status_code == 403
    assert _branch_label(app, lic_id) is None


def test_staff_account_posts_need_a_csrf_token_and_only_four_blueprints_are_csrf_exempt(app, client, seeded):
    """E12: the website API is exempt (its HMAC is its CSRF), the staff routes must not be."""
    make_staff(app, "admin@example.com", super_admin=True, mfa=True)
    _enable(app)
    cid = new_customer(app)
    login_and_verify_mfa(client, "admin@example.com")
    resp = client.post(f"/customers/{cid}/website-account/create", data={"email": "a@b.example", "language": "en"})
    assert resp.status_code == 400 and not _accounts(app)  # deny: refused, nothing written
    allowed = client.post(f"/customers/{cid}/website-account/create",
                          data={"csrf_token": _csrf(client, cid), "email": "a@b.example", "language": "en"})
    assert allowed.status_code == 200 and len(_accounts(app)) == 1  # allow: same request with a token
    exempt = {getattr(b, "name", b) for b in app.extensions["csrf"]._exempt_blueprints}
    assert exempt == {"api_external_licensing", "sync", "website_account_api", "website_intake"}


def test_the_arabic_panel_and_link_page_are_translated_and_right_to_left(app, client, seeded):
    make_staff(app, "admin@example.com", super_admin=True, mfa=True)
    _enable(app)
    cid = new_customer(app)
    login_and_verify_mfa(client, "admin@example.com")
    client.get("/locale/ar?next=/")
    page = client.get(f"/customers/{cid}").get_data(as_text=True)
    assert 'dir="rtl"' in page
    panel = page[page.index('id="website-account"'):]
    panel = panel[:panel.index("</div>")]
    for english in ("Website account", "This customer has no website account", "Create website account",
                    "Email address of the person", "Language of the emails"):
        assert english not in panel, english
    token = get_csrf(page)
    resp = client.post(f"/customers/{cid}/website-account/create",
                       data={"csrf_token": token, "email": "ar@shop.example", "language": "ar"})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200 and "/ar/account/set-password/#t=" in html
    assert "Copy this one-time link" not in html and "Back to the customer" not in html
    detail = client.get(f"/customers/{cid}").get_data(as_text=True)
    panel = detail[detail.index('id="website-account"'):]
    for english in ("Invited -- password not set yet", "Send the invitation again", "Sign out everywhere", "Last sign-in"):
        assert english not in panel, english
    assert '<bdi dir="ltr">ar@shop.example</bdi>' in panel


def _active_for(app, seller, email):
    cid = _assigned_customer(app, seller)
    with app.app_context():
        from app.customer_accounts import service
        from tests.test_customer_accounts_service import invite

        service.set_password_with_token(token_of(invite(app, cid, email)), GOOD, IP)
    return cid


def _tokens(app, kind):
    with app.app_context():
        from app.extensions import db_session
        from app.models.customer_accounts import CustomerAccountToken

        return db_session.execute(select(func.count()).select_from(CustomerAccountToken)
                                  .where(CustomerAccountToken.kind == kind)).scalar_one()


def test_with_email_off_only_support_and_super_admin_are_shown_a_reset_link(app, client, seeded):
    """A3: with email off a reset link for an ACTIVE account is shown to the staff
    member -- a key to the customer's login. A seller may not be handed one."""
    seller = make_staff(app, "seller@example.com", role_codes=["SALES"])
    support = make_staff(app, "support@example.com", role_codes=["SUPPORT"])
    _enable(app)
    cid = _active_for(app, seller, "active@shop.example")
    force_login(client, app, seller)
    token = _csrf_any(client)
    resp = client.post(BASE.format(cid=cid) + "/reset-link", data={"csrf_token": token})
    assert resp.status_code == 403 and _tokens(app, "RESET") == 0  # deny: refused, nothing minted
    # allow: the seller can still be shown an INVITE link for their own not-yet-active customer
    cid2 = _assigned_customer(app, seller)
    with app.app_context():
        from tests.test_customer_accounts_service import invite

        invite(app, cid2, "invited@shop.example")
    shown = client.post(BASE.format(cid=cid2) + "/resend", data={"csrf_token": token})
    assert shown.status_code == 200 and 'data-link-shown-once="1"' in shown.get_data(as_text=True)
    # allow: Support is handed the reset link, and the audit row says it was shown to staff
    support_client = app.test_client()
    force_login(support_client, app, support)
    shown = support_client.post(BASE.format(cid=cid) + "/reset-link", data={"csrf_token": _csrf_any(support_client)})
    assert shown.status_code == 200 and 'data-link-shown-once="1"' in shown.get_data(as_text=True)
    assert _tokens(app, "RESET") == 1
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog

        row = db_session.execute(select(AuditLog).where(AuditLog.action_code == "CUSTOMER_ACCOUNT_RESET_LINK_ISSUED")).scalars().one()
        assert row.after_state_redacted["link_shown_to_staff"] is True and row.actor_staff_user_id is not None


def test_with_email_on_a_seller_may_trigger_a_reset_because_it_goes_to_the_customers_mailbox(app, client, seeded):
    seller = make_staff(app, "seller@example.com", role_codes=["SALES"])
    _enable(app, mail=True)
    cid = _active_for(app, seller, "active@shop.example")
    force_login(client, app, seller)
    resp = client.post(BASE.format(cid=cid) + "/reset-link", data={"csrf_token": _csrf_any(client)})
    assert resp.status_code == 200 and 'data-link-shown-once="1"' not in resp.get_data(as_text=True)
    assert _tokens(app, "RESET") == 1
    with app.app_context():
        from app.extensions import db_session
        from app.models.email_outbox import EmailOutbox

        assert [r.kind for r in db_session.execute(select(EmailOutbox)).scalars().all()].count("customer_password_reset") == 1
