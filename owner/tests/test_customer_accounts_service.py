"""Customer website accounts (Step 2 CA1): the rules in
app/customer_accounts/service.py.

Every guard is proven by what it did or did not write, and each has a deny test
AND the matching allow test (the invite must work, the lockout must not trip early,
disabling must block sign-in but leave the licence alone)."""
from __future__ import annotations

import uuid
from datetime import timedelta
from urllib.parse import unquote

import pytest
from sqlalchemy import func, select

from tests.conftest import make_license, make_staff

PORTAL = "https://actionaura.me"
IP = "203.0.113.5"
GOOD = "Tr0ub4dor-and-3-horses"


def setup_accounts(app, *, mail=False, portal=PORTAL):
    app.config.update(
        CUSTOMER_ACCOUNTS_ENABLED=True, CUSTOMER_PORTAL_BASE_URL=portal,
        SMTP_HOST="smtp.test" if mail else "", CUSTOMER_MAIL_FROM="", CUSTOMER_MAIL_REPLY_TO="",
    )


def new_customer(app, legal="Haddad Market", trade="Haddad", **kw):
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer

        customer = Customer(legal_name=f"{legal} {uuid.uuid4().hex[:4]}", trade_name=trade, **kw)
        db_session.add(customer)
        db_session.commit()
        return customer.id


def token_of(issued) -> str:
    return unquote(issued.link.split("#t=", 1)[1])


def invite(app, customer_id, email="owner@haddad.example", lang="en", actor=None):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customers import Customer

    return service.create_account(db_session.get(Customer, customer_id), email, lang, actor)


def active_account(app, email="owner@haddad.example", password=GOOD, customer_id=None):
    """A customer with an ACTIVE account. Returns (customer_id, email)."""
    from app.customer_accounts import service

    customer_id = customer_id or new_customer(app)
    issued = invite(app, customer_id, email)
    service.set_password_with_token(token_of(issued), password, IP)
    return customer_id, email


def count(app_model_stmt):
    from app.extensions import db_session

    return db_session.execute(app_model_stmt).scalar_one()


@pytest.fixture()
def acc(app):
    setup_accounts(app)
    with app.app_context():
        yield app


# ---------------------------------------------------------------- invitation

def test_create_account_invites_with_a_hashed_token_and_a_fragment_link(acc):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerAccount, CustomerAccountToken

    cid = new_customer(acc)
    issued = invite(acc, cid, "  Owner@Haddad.Example ")
    account = db_session.execute(select(CustomerAccount)).scalars().one()
    assert account.email == "owner@haddad.example" and account.status == "INVITED" and account.password_hash is None
    assert issued.link.startswith(f"{PORTAL}/en/account/set-password/#t=")
    raw = token_of(issued)
    row = db_session.execute(select(CustomerAccountToken)).scalars().one()
    assert row.kind == "INVITE" and raw not in (row.token_hash, row.kind) and len(raw) >= 40
    assert row.token_hash == service.hash_token(raw)
    assert 71 * 3600 < (row.expires_at - row.created_at).total_seconds() < 73 * 3600


def test_link_is_built_from_the_configured_portal_never_from_the_request_host(app):
    from app.customer_accounts import mail

    setup_accounts(app, portal="https://actionaura.me/")
    with app.test_request_context("/", headers={"Host": "evil.example"}):
        assert mail.build_link("INVITE", "tok", "ar") == "https://actionaura.me/ar/account/set-password/#t=tok"
        assert mail.build_link("EMAIL_CHANGE", "tok", "en") == "https://actionaura.me/en/account/confirm-email/#t=tok"
    app.config["CUSTOMER_PORTAL_BASE_URL"] = ""
    with app.test_request_context("/"):
        with pytest.raises(mail.PortalUrlNotSet):
            mail.build_link("INVITE", "tok", "en")


def test_one_account_per_customer_and_one_email_per_company(acc):
    from app.customer_accounts import service

    first = new_customer(acc)
    invite(acc, first, "a@shop.example")
    with pytest.raises(service.AccountError) as same_customer:
        invite(acc, first, "other@shop.example")
    assert same_customer.value.code == "account_exists"
    second = new_customer(acc)
    with pytest.raises(service.AccountError) as same_email:
        invite(acc, second, "A@SHOP.example")
    assert same_email.value.code == "email_in_use"
    invite(acc, second, "b@shop.example")  # allow: a different address works


@pytest.mark.parametrize("bad", ["", "no-at-sign", "a@b", "a b@c.example", "a@c.example\r\nBcc: x@y.example"])
def test_a_malformed_email_is_refused(acc, bad):
    from app.customer_accounts import service

    with pytest.raises(service.AccountError) as exc:
        invite(acc, new_customer(acc), bad)
    assert exc.value.code == "invalid_email"


def test_nothing_is_written_when_the_portal_url_is_not_set(app):
    from app.customer_accounts import mail
    from app.models.customer_accounts import CustomerAccount

    setup_accounts(app, portal="")
    cid = new_customer(app)
    with app.app_context():
        from app.extensions import db_session

        with pytest.raises(mail.PortalUrlNotSet):
            invite(app, cid)
        assert db_session.execute(select(func.count()).select_from(CustomerAccount)).scalar_one() == 0


# ---------------------------------------------------------------- set password

def test_the_invite_link_activates_the_account_once(acc):
    from app.customer_accounts import service
    from app.extensions import db_session

    cid = new_customer(acc)
    issued = invite(acc, cid)
    raw = token_of(issued)
    account = service.set_password_with_token(raw, GOOD, IP)
    assert account.status == "ACTIVE" and account.password_hash and account.activated_at is not None
    # deny: the same link a second time
    with pytest.raises(service.AccountError) as again:
        service.set_password_with_token(raw, "An0ther-Good-Pass-9", IP)
    assert again.value.code == "token_invalid"
    db_session.refresh(account)
    assert service._verify_password(GOOD, account.password_hash)  # the first password stands


def test_an_expired_invite_link_does_not_work_and_a_fresh_one_does(acc):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerAccountToken

    cid = new_customer(acc)
    raw = token_of(invite(acc, cid))
    row = db_session.execute(select(CustomerAccountToken)).scalars().one()
    row.expires_at = row.created_at - timedelta(seconds=1)
    db_session.commit()
    with pytest.raises(service.AccountError) as exc:
        service.set_password_with_token(raw, GOOD, IP)
    assert exc.value.code == "token_invalid"
    # allow: the same token, not expired, works (so the denial above was the expiry)
    row.expires_at = row.created_at + timedelta(hours=1)
    db_session.commit()
    assert service.set_password_with_token(raw, GOOD, IP).status == "ACTIVE"


def test_the_reset_link_lives_30_minutes_and_the_email_change_link_24_hours(acc):
    from app.customer_accounts import service

    assert service.TOKEN_TTL["INVITE"] == timedelta(hours=72)
    assert service.TOKEN_TTL["RESET"] == timedelta(minutes=30)
    assert service.TOKEN_TTL["EMAIL_CHANGE"] == timedelta(hours=24)
    assert service.SESSION_IDLE == timedelta(minutes=60) and service.SESSION_ABSOLUTE == timedelta(hours=12)


def test_resending_revokes_the_earlier_link(acc):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerAccount

    cid = new_customer(acc)
    first = token_of(invite(acc, cid))
    account = db_session.execute(select(CustomerAccount)).scalars().one()
    second = token_of(service.resend_invitation(account, None))
    with pytest.raises(service.AccountError):
        service.set_password_with_token(first, GOOD, IP)  # deny: the old link
    assert service.set_password_with_token(second, GOOD, IP).status == "ACTIVE"  # allow: the new one


def test_a_weak_password_is_refused_without_burning_the_link(acc):
    from app.customer_accounts import policy, service

    cid = new_customer(acc, legal="Haddad Market")
    raw = token_of(invite(acc, cid, "sami.haddad@shop.example"))
    for weak, code in (("short1!", "too_short"), ("password123", "too_common"), ("1234567890", "too_common"),
                       ("x" * 129, "too_long"), ("my-sami.haddad-pass", "contains_personal"),
                       ("Sami.Haddad@shop.example", "contains_personal"), ("The-HADDAD-Market-1", "contains_personal")):
        with pytest.raises(policy.PasswordPolicyError) as exc:
            service.set_password_with_token(raw, weak, IP)
        assert exc.value.code == code, (weak, exc.value.code)
    # the link still works afterwards
    assert service.set_password_with_token(raw, GOOD, IP).status == "ACTIVE"


def test_the_password_policy_allows_a_long_unrelated_phrase():
    from app.customer_accounts import policy

    policy.validate("correct horse battery staple", email="a@b.example", legal_name="Haddad", trade_name="")
    policy.validate("0123456789abc", email="", legal_name="", trade_name="")  # not in the list, long enough


# ------------------------------------------------------------------ sign in

def test_sign_in_allow_and_deny(acc):
    from app.customer_accounts import service

    _, email = active_account(acc)
    token, account = service.sign_in(email, GOOD, IP, "ua")
    assert account.last_sign_in_at is not None and len(token) >= 40
    assert service.get_session(token) is not None
    with pytest.raises(service.SignInFailed):
        service.sign_in(email, "Wrong-Password-1", IP, "ua")


def test_an_invited_account_cannot_sign_in(acc):
    from app.customer_accounts import service

    invite(acc, new_customer(acc), "new@shop.example")
    with pytest.raises(service.SignInFailed):
        service.sign_in("new@shop.example", GOOD, IP, None)


def test_no_enumeration_every_failure_runs_exactly_one_password_check(acc, monkeypatch):
    """An unknown email, a wrong password, an invited and a disabled account all
    raise the SAME error after exactly ONE Argon2 verification each -- the answer
    and the time say nothing about which it was."""
    from app.customer_accounts import service

    cid, email = active_account(acc)
    invite(acc, new_customer(acc), "invited@shop.example")
    cid2, disabled = active_account(acc, "off@shop.example", customer_id=new_customer(acc))
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerAccount

    service.disable_account(db_session.execute(select(CustomerAccount).where(CustomerAccount.email == disabled)).scalars().one(), "test", None)

    calls = []
    real = service._verify_password
    monkeypatch.setattr(service, "_verify_password", lambda pw, h: (calls.append(1), real(pw, h))[1])
    for who in ("nobody@shop.example", email, "invited@shop.example", disabled, "not-an-email"):
        calls.clear()
        with pytest.raises(service.SignInFailed) as exc:
            service.sign_in(who, "Wrong-Password-1" if who == email else GOOD, IP, None)
        assert exc.value.code == "invalid_credentials"
        assert len(calls) == 1, (who, len(calls))
    # allow: a right password is also exactly one check
    calls.clear()
    service.sign_in(email, GOOD, IP, None)
    assert len(calls) == 1


def test_lockout_five_tries_per_email_and_address_then_locked_even_for_the_right_password(acc):
    from app.customer_accounts import service

    _, email = active_account(acc)
    for _ in range(5):
        with pytest.raises(service.SignInFailed):
            service.sign_in(email, "Wrong-Password-1", IP, None)
    with pytest.raises(service.SignInLocked) as locked:  # deny: the 6th, with the RIGHT password
        service.sign_in(email, GOOD, IP, None)
    assert locked.value.retry_after_seconds >= 1
    # allow: the same person from another address is not caught by the pair limit
    token, _account = service.sign_in(email, GOOD, "198.51.100.7", None)
    assert token


def test_lockout_cannot_be_walked_around_with_a_list_of_emails_from_one_address(acc):
    from app.customer_accounts import service

    for i in range(30):
        with pytest.raises(service.SignInFailed):
            service.sign_in(f"user{i}@shop.example", GOOD, IP, None)
    with pytest.raises(service.SignInLocked):
        service.sign_in("user99@shop.example", GOOD, IP, None)


def test_customer_lockout_and_staff_lockout_are_separate_worlds(app, client, seeded):
    """A flood of failed customer sign-ins leaves the staff login-attempt history
    empty and cannot lock a staff member out; failed staff logins are invisible to
    the customer limits."""
    from tests.conftest import login

    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerLoginAttempt
    from app.models.staff import LoginAttempt

    setup_accounts(app)
    make_staff(app, "boss@example.com", super_admin=True)
    with app.app_context():
        _, email = active_account(app)
        for _ in range(7):
            with pytest.raises((service.SignInFailed, service.SignInLocked)):
                service.sign_in(email, "Wrong-Password-1", IP, None)
        assert db_session.execute(select(func.count()).select_from(LoginAttempt)).scalar_one() == 0
        assert db_session.execute(select(func.count()).select_from(CustomerLoginAttempt)).scalar_one() == 7
    assert login(client, "boss@example.com").status_code in (302, 303)  # staff still sign in
    with app.app_context():
        # staff failures do not touch the customer tables
        before = db_session.execute(select(func.count()).select_from(CustomerLoginAttempt)).scalar_one()
        login(client, "boss@example.com", "Wrong-Staff-Pass-1!")
        assert db_session.execute(select(func.count()).select_from(CustomerLoginAttempt)).scalar_one() == before


def test_attempts_store_hashes_not_addresses(acc):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerLoginAttempt

    with pytest.raises(service.SignInFailed):
        service.sign_in("secret.person@shop.example", GOOD, IP, None)
    row = db_session.execute(select(CustomerLoginAttempt)).scalars().one()
    assert "secret" not in row.email_hash and IP not in (row.ip_hash or "") and len(row.email_hash) == 64
    assert (row.success, row.reason) == (False, "UNKNOWN")


# ----------------------------------------------------------------- sessions

def test_session_idle_absolute_and_version_limits(acc):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerAccount, CustomerSession

    _, email = active_account(acc)

    def fresh():
        token, _a = service.sign_in(email, GOOD, "198.51.100.%d" % (uuid.uuid4().int % 200 + 1), None)
        return token, db_session.execute(select(CustomerSession).order_by(CustomerSession.created_at.desc())).scalars().first()

    token, row = fresh()
    assert service.get_session(token) is not None
    row.last_seen_at = row.last_seen_at - timedelta(minutes=61)  # idle
    db_session.commit()
    assert service.get_session(token) is None
    token, row = fresh()
    row.last_seen_at = row.last_seen_at - timedelta(minutes=59)  # allow: still inside the idle limit
    db_session.commit()
    assert service.get_session(token) is not None
    row.absolute_expires_at = row.created_at - timedelta(seconds=1)  # past 12 h
    db_session.commit()
    assert service.get_session(token) is None
    token, row = fresh()
    account = db_session.execute(select(CustomerAccount)).scalars().one()
    account.session_version += 1  # password changed elsewhere
    db_session.commit()
    assert service.get_session(token) is None


def test_sign_out_ends_only_that_session(acc):
    from app.customer_accounts import service

    _, email = active_account(acc)
    a, _ = service.sign_in(email, GOOD, "198.51.100.1", None)
    b, _ = service.sign_in(email, GOOD, "198.51.100.2", None)
    service.sign_out(a)
    assert service.get_session(a) is None and service.get_session(b) is not None


def test_sign_out_everywhere_ends_every_session(acc):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerAccount

    _, email = active_account(acc)
    tokens = [service.sign_in(email, GOOD, f"198.51.100.{i}", None)[0] for i in (1, 2)]
    account = db_session.execute(select(CustomerAccount)).scalars().one()
    assert service.sign_out_everywhere(account, None) == 2
    assert [service.get_session(t) for t in tokens] == [None, None]
    token, _a = service.sign_in(email, GOOD, "198.51.100.3", None)  # allow: signing in again works
    assert service.get_session(token) is not None


# --------------------------------------------------------- reset and change

def test_forgot_password_answers_the_same_for_known_and_unknown_and_mails_only_the_known(app):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.email_outbox import EmailOutbox

    setup_accounts(app, mail=True)
    with app.app_context():
        _, email = active_account(app)
        assert service.forgot_password("nobody@shop.example", IP) is None
        assert db_session.execute(select(func.count()).select_from(EmailOutbox)).scalar_one() == 1  # only the invite
        assert service.forgot_password(email, IP) is None
        rows = db_session.execute(select(EmailOutbox).order_by(EmailOutbox.created_at)).scalars().all()
        assert [r.kind for r in rows] == ["customer_invitation", "customer_password_reset"]
        assert rows[1].to_address == email and f"{PORTAL}/en/account/set-password/#t=" in rows[1].body_text


def test_forgot_password_is_three_per_address_per_hour_and_silent_after(app):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.email_outbox import EmailOutbox

    setup_accounts(app, mail=True)
    with app.app_context():
        _, email = active_account(app)
        for i in range(5):
            assert service.forgot_password(email, f"198.51.100.{i + 1}") is None  # same answer every time
        kinds = [r.kind for r in db_session.execute(select(EmailOutbox)).scalars().all()]
        assert kinds.count("customer_password_reset") == 3  # the 4th and 5th sent nothing


def test_forgot_password_sends_nothing_to_a_disabled_account(app):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerAccount
    from app.models.email_outbox import EmailOutbox

    setup_accounts(app, mail=True)
    with app.app_context():
        _, email = active_account(app)
        service.disable_account(db_session.execute(select(CustomerAccount)).scalars().one(), "fraud", None)
        before = db_session.execute(select(func.count()).select_from(EmailOutbox)).scalar_one()
        service.forgot_password(email, IP)
        assert db_session.execute(select(func.count()).select_from(EmailOutbox)).scalar_one() == before


def test_reset_sets_the_password_kills_every_session_and_mails_a_notice(app):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerAccount
    from app.models.email_outbox import EmailOutbox

    setup_accounts(app, mail=True)
    with app.app_context():
        _, email = active_account(app)
        old_session, _a = service.sign_in(email, GOOD, "198.51.100.9", None)
        service.forgot_password(email, IP)
        reset = db_session.execute(select(EmailOutbox).where(EmailOutbox.kind == "customer_password_reset")).scalars().one()
        raw = unquote(reset.body_text.split("#t=", 1)[1].split()[0])
        new_pw = "A-brand-new-Phrase-77"
        service.set_password_with_token(raw, new_pw, IP)
        assert service.get_session(old_session) is None  # every session died
        with pytest.raises(service.SignInFailed):
            service.sign_in(email, GOOD, "198.51.100.10", None)  # deny: the old password
        assert service.sign_in(email, new_pw, "198.51.100.11", None)[0]  # allow: the new one
        kinds = [r.kind for r in db_session.execute(select(EmailOutbox)).scalars().all()]
        assert "customer_password_changed" in kinds
        with pytest.raises(service.AccountError):
            service.set_password_with_token(raw, "Yet-Another-Phrase-11", IP)  # single use


def test_changing_the_password_needs_the_current_one_ends_other_sessions_and_keeps_this_one(acc):
    from app.customer_accounts import service

    _, email = active_account(acc)
    mine, _a = service.sign_in(email, GOOD, "198.51.100.1", None)
    other, _a = service.sign_in(email, GOOD, "198.51.100.2", None)
    with pytest.raises(service.SignInFailed):
        service.change_password(mine, "Wrong-Password-1", "Another-Good-Phrase-3", IP, None)
    assert service.get_session(mine) is not None  # nothing changed
    new_token, account = service.change_password(mine, GOOD, "Another-Good-Phrase-3", IP, None)
    assert service.get_session(other) is None and service.get_session(mine) is None
    assert service.get_session(new_token) is not None
    assert service.sign_in(email, "Another-Good-Phrase-3", "198.51.100.3", None)[0]


# ----------------------------------------------------- disable (never licences)

def test_disable_blocks_sign_in_and_sessions_but_enable_restores_it(acc):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerAccount

    _, email = active_account(acc)
    token, _a = service.sign_in(email, GOOD, "198.51.100.1", None)
    account = db_session.execute(select(CustomerAccount)).scalars().one()
    with pytest.raises(service.AccountError) as no_reason:
        service.disable_account(account, "  ", None)
    assert no_reason.value.code == "reason_required" and account.status == "ACTIVE"
    service.disable_account(account, "chargeback", None)
    assert account.status == "DISABLED" and account.disabled_reason == "chargeback"
    assert service.get_session(token) is None
    with pytest.raises(service.SignInFailed):
        service.sign_in(email, GOOD, "198.51.100.2", None)
    service.enable_account(account, None)
    assert account.status == "ACTIVE" and account.disabled_at is None
    assert service.sign_in(email, GOOD, "198.51.100.3", None)[0]


def test_enabling_an_account_that_never_set_a_password_returns_it_to_invited(acc):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerAccount

    old_link = token_of(invite(acc, new_customer(acc)))
    account = db_session.execute(select(CustomerAccount)).scalars().one()
    service.disable_account(account, "paused", None)
    with pytest.raises(service.AccountError):
        service.set_password_with_token(old_link, GOOD, IP)  # disabling killed the link
    service.enable_account(account, None)
    assert account.status == "INVITED"
    assert service.resend_invitation(account, None).account.status == "INVITED"


def test_account_actions_never_touch_licences(app, seeded):
    """Disabling, resetting, signing out everywhere and changing the email leave
    the licence, its subscription and its device exactly as they were."""
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.installations.services import register_installation
    from app.licensing.services import transition_license
    from app.models.catalog import Platform
    from app.models.customer_accounts import CustomerAccount
    from app.models.installations import Installation
    from app.models.licensing import License
    from app.models.subscriptions import Subscription

    setup_accounts(app, mail=True)
    staff_id = make_staff(app, "lic-admin@example.com", super_admin=True)
    with app.app_context():
        lic_id, _key = make_license(app, staff_id, device_limit=2)
        lic = db_session.get(License, lic_id)
        transition_license(lic, "ACTIVE", staff_id)
        platform = db_session.query(Platform).filter_by(platform_code="WINDOWS").first()
        inst = register_installation({"customer_id": lic.customer_id, "license_id": lic.id, "product_id": lic.product_id,
                                      "platform_id": platform.id, "installation_label": "till-1"}, staff_id)
        inst.status = "ACTIVE"
        db_session.commit()

        def snapshot():
            db_session.expire_all()
            lic_row = db_session.get(License, lic_id)
            sub = db_session.get(Subscription, lic_row.subscription_id)
            return (lic_row.status, lic_row.valid_until, lic_row.device_limit, sub.status, sub.end_date,
                    db_session.get(Installation, inst.id).status)

        before = snapshot()
        assert before[0] == "ACTIVE" and before[5] == "ACTIVE"
        issued = invite(app, lic.customer_id, "owner@shop.example")
        service.set_password_with_token(token_of(issued), GOOD, IP)
        account = db_session.execute(select(CustomerAccount)).scalars().one()
        service.sign_out_everywhere(account, staff_id)
        service.issue_reset_link(account, staff_id)
        service.request_email_change(account, "new@shop.example", "moved", staff_id)
        service.disable_account(account, "chargeback", staff_id)
        service.enable_account(account, staff_id)
        assert snapshot() == before


# --------------------------------------------------------------- email change

def test_email_change_keeps_the_old_address_until_the_new_one_confirms(app):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerAccount
    from app.models.email_outbox import EmailOutbox

    setup_accounts(app, mail=True)
    with app.app_context():
        _, old = active_account(app)
        session_token, _a = service.sign_in(old, GOOD, "198.51.100.1", None)
        account = db_session.execute(select(CustomerAccount)).scalars().one()
        issued = service.request_email_change(account, "New@Shop.example", "customer moved", None)
        assert account.email == old  # unchanged so far
        sent = {(r.kind, r.to_address) for r in db_session.execute(select(EmailOutbox)).scalars().all()}
        assert ("customer_email_change", "new@shop.example") in sent
        assert ("customer_email_changed_notice", old) in sent
        assert service.get_session(session_token) is not None
        service.confirm_email_change(token_of(issued), IP)
        assert account.email == "new@shop.example"
        assert service.get_session(session_token) is None  # the sign-in email changed: sign in again
        with pytest.raises(service.AccountError):
            service.confirm_email_change(token_of(issued), IP)  # single use
        assert service.sign_in("new@shop.example", GOOD, "198.51.100.2", None)[0]
        with pytest.raises(service.SignInFailed):
            service.sign_in(old, GOOD, "198.51.100.3", None)


def test_email_change_refuses_a_taken_address_a_missing_reason_and_a_disabled_account(acc):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerAccount

    active_account(acc, "a@shop.example")
    active_account(acc, "b@shop.example", customer_id=new_customer(acc))
    a = db_session.execute(select(CustomerAccount).where(CustomerAccount.email == "a@shop.example")).scalars().one()
    for new, reason, code in (("b@shop.example", "x", "email_in_use"), ("c@shop.example", " ", "reason_required"),
                              ("a@shop.example", "x", "same_email")):
        with pytest.raises(service.AccountError) as exc:
            service.request_email_change(a, new, reason, None)
        assert exc.value.code == code
    service.disable_account(a, "x", None)
    with pytest.raises(service.AccountError) as exc:
        service.request_email_change(a, "c@shop.example", "x", None)
    assert exc.value.code == "account_disabled"


def test_a_reset_token_cannot_confirm_an_email_change_and_the_reverse(acc):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerAccount

    active_account(acc)
    account = db_session.execute(select(CustomerAccount)).scalars().one()
    reset = token_of(service.issue_reset_link(account, None))
    change = token_of(service.request_email_change(account, "n@shop.example", "x", None))
    with pytest.raises(service.AccountError):
        service.confirm_email_change(reset, IP)
    with pytest.raises(service.AccountError):
        service.set_password_with_token(change, GOOD + "x", IP)


# ---------------------------------------------------------------- mail / links

def test_when_email_is_off_the_link_is_returned_to_staff_and_nothing_is_queued(acc):
    from app.extensions import db_session
    from app.models.email_outbox import EmailOutbox

    issued = invite(acc, new_customer(acc))
    assert issued.emailed is False and issued.link.startswith(PORTAL)
    assert db_session.execute(select(func.count()).select_from(EmailOutbox)).scalar_one() == 0


def test_when_email_is_on_it_is_queued_in_the_customers_language(app):
    from app.extensions import db_session
    from app.models.email_outbox import EmailOutbox

    setup_accounts(app, mail=True)
    with app.app_context():
        issued = invite(app, new_customer(app), "owner@shop.example", lang="ar")
        row = db_session.execute(select(EmailOutbox)).scalars().one()
        assert issued.emailed is True and row.kind == "customer_invitation" and row.to_address == "owner@shop.example"
        assert issued.link.startswith(f"{PORTAL}/ar/") and issued.link in row.body_text
        assert row.subject == "إعداد حسابك في Action Aura"  # Arabic first for an Arabic customer
        assert "support@" not in row.body_text  # that mailbox may not exist


def test_a_sent_link_mail_loses_its_body_but_other_mail_keeps_it(app):
    from app.extensions import db_session
    from app.mail import outbox

    setup_accounts(app, mail=True)
    with app.app_context():
        invite(app, new_customer(app))
        link_row = db_session.execute(select(outbox.EmailOutbox)).scalars().one()
        other = outbox.enqueue(kind="staff_invitation", to_address="s@example.com", subject="s", body_text="keep me")
        db_session.commit()
        for row in (link_row, other):
            row.status = "SENDING"
        db_session.commit()
        assert outbox.mark_sent(link_row.id) and outbox.mark_sent(other.id)
        db_session.commit()
        db_session.expire_all()
        link_row, other = db_session.get(outbox.EmailOutbox, link_row.id), db_session.get(outbox.EmailOutbox, other.id)
        assert link_row.status == "SENT" and link_row.body_text == "" and link_row.body_html is None
        assert other.status == "SENT" and other.body_text == "keep me"


def test_customer_mail_uses_its_own_sender_and_no_reply_to_unless_configured(app):
    from app.mail import smtp
    from app.mail.worker import REPLY_TO, _sender_for

    base = smtp.SmtpSettings(host="h", port=587, user="u", password="p", from_address="info@actionaura.me",
                             from_name="Action Aura", use_tls=True)
    app.config.update(CUSTOMER_MAIL_FROM="", CUSTOMER_MAIL_REPLY_TO="")
    assert _sender_for(app.config, base, "staff_invitation") == (base, REPLY_TO)  # unchanged for everything else
    settings, reply = _sender_for(app.config, base, "customer_invitation")
    assert settings.from_address == "info@actionaura.me" and reply is None  # NOT support@
    app.config.update(CUSTOMER_MAIL_FROM="noreply@actionaura.me", CUSTOMER_MAIL_REPLY_TO="help@actionaura.me")
    settings, reply = _sender_for(app.config, base, "customer_password_reset")
    assert settings.from_address == "noreply@actionaura.me" and reply == "help@actionaura.me"
    assert _sender_for(app.config, base, "staff_invitation")[0].from_address == "info@actionaura.me"


def test_the_product_mail_relay_cannot_be_asked_to_send_account_mail(app):
    from app.mail import relay

    for kind in ("customer_invitation", "customer_password_reset", "customer_password_changed",
                 "customer_email_change", "customer_email_changed_notice"):
        assert kind not in relay.KINDS


def test_account_actions_are_audited_without_secrets(acc):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.audit import AuditLog
    from app.models.customer_accounts import CustomerAccount

    issued = invite(acc, new_customer(acc))
    raw = token_of(issued)
    service.set_password_with_token(raw, GOOD, IP)
    account = db_session.execute(select(CustomerAccount)).scalars().one()
    service.disable_account(account, "chargeback", None)
    rows = db_session.execute(select(AuditLog).where(AuditLog.entity_type == "customer_account")).scalars().all()
    assert {r.action_code for r in rows} >= {"CUSTOMER_ACCOUNT_CREATED", "CUSTOMER_ACCOUNT_PASSWORD_SET", "CUSTOMER_ACCOUNT_DISABLED"}
    blob = " ".join(str(r.after_state_redacted) + str(r.reason) for r in rows)
    assert raw not in blob and GOOD not in blob and account.password_hash not in blob
