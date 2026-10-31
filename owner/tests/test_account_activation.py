"""New customer flow, slice S5: "sign in with your Aura account" on a device.

Everything goes through the REAL endpoints over HTTP with real Ed25519 device
signatures, exactly as the shop app signs them (commercial_runtime/
licensing_contracts/client.py), because the guards live in the order of checks
inside the request. Each guard is proven by what it did or did not WRITE or by
the next request's outcome, and each deny test has its allow twin next to it
(the right device CAN use its ticket, the second use is the one that fails).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text

from app.models.installations import Installation
from tests.conftest import jordan_today, make_device_keypair, public_key_b64, sign_body
from tests.test_customer_accounts_service import GOOD
from tests.test_customer_onboarding import _world, kyc, onboard  # noqa: F401 -- kyc is a fixture

NEW_ENOUGH = "1.0.0-rc.17"
TOO_OLD = "1.0.0-rc.16"
CONTRACT_BODY = "عقد استخدام تطبيق أورا\n\n1. فترة التجربة سبعة أيام.\n2. البيانات تبقى للعميل."
NEW_PASSWORD = "A-Better-Passw0rd-2026"
SENTINEL = "Zq9-sentinel-PW-never-stored-7731"  # pragma: allowlist secret -- a test value


def sha(text_: str) -> str:
    return hashlib.sha256(text_.encode("utf-8")).hexdigest()


def fingerprint_of(key) -> str:
    return hashlib.sha256(base64.b64decode(public_key_b64(key))).hexdigest()


# ------------------------------------------------------------------ envelope

def envelope(key, **fields) -> dict:
    body = {
        "contract_version": "v1", "request_id": str(uuid.uuid4()), "correlation_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(), "nonce": uuid.uuid4().hex,
        "product_code": "AURA_RETAIL", "platform": "WINDOWS", "app_version": NEW_ENOUGH,
        "device_public_key": public_key_b64(key), "device_public_key_algorithm": "ed25519",
    }
    body.update(fields)
    return sign_body(key, body)


def post(client, path, body):
    return client.post(f"/api/licensing/v1{path}", data=json.dumps(body), content_type="application/json")


def sign_in(client, key, email, password, **extra):
    return post(client, "/account-sign-ins", envelope(key, email=email, password=password, **extra))


def change(client, key, ticket, new_password):
    return post(client, "/account-password-changes", envelope(key, ticket=ticket, new_password=new_password))


def accept(client, key, ticket, version_id, digest, **extra):
    return post(client, "/contract-acceptances", envelope(
        key, ticket=ticket, contract_version_id=version_id, text_sha256=digest, language="ar", **extra))


def activate(client, key, *, ticket=None, license_public_id=None, code=None, app_version=NEW_ENOUGH, **extra):
    fields = {"installation_id": f"inst-{uuid.uuid4().hex[:6]}", "idempotency_key": str(uuid.uuid4()),
              "app_version": app_version}
    if ticket is not None:
        fields["account_ticket"] = ticket
        fields["license_public_id"] = license_public_id
    if code is not None:
        fields["activation_code"] = code
    fields.update(extra)
    return post(client, "/activations", envelope(key, **fields))


def reason(resp):
    return resp.get_json().get("reason_code")


# ------------------------------------------------------------------- the world

@pytest.fixture()
def world(app, seeded, kyc, signing_key):
    """A trial customer made by a seller (account ACTIVE with a temporary password,
    password_must_change=True), customer accounts switched on, and one live
    lawyer-approved Arabic customer contract."""
    w = _world(app)
    result = onboard(app, w, language="ar")
    app.config.update(CUSTOMER_ACCOUNTS_ENABLED=True)
    with app.app_context():
        from app.contracts import service as contracts
        from app.extensions import db_session
        from app.models.customer_accounts import CustomerAccount
        from app.models.licensing import License

        account = db_session.execute(select(CustomerAccount)).scalars().one()
        licence = db_session.execute(select(License)).scalars().one()
        version = contracts.create_version(
            audience="CUSTOMER", language="ar", version_label="v1", title="عقد الاستخدام", body=CONTRACT_BODY,
            lawyer_approved=True, approval_note="Read by counsel 2026-10-31", actor_staff_user_id=w["admin"])
        contracts.publish(version, actor_staff_user_id=w["admin"])
        w.update(email=account.email, customer_id=result.customer_id, licence_id=str(licence.id),
                 account_id=account.id, version_id=str(version.id), key=result.license_key)
    return w


def sign_in_ok(client, key, w, password=GOOD):
    resp = sign_in(client, key, w["email"], password)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def ready_ticket(client, key, w):
    """A ticket that may activate: password already changed, contract accepted."""
    first = sign_in_ok(client, key, w)
    changed = change(client, key, first["ticket"], NEW_PASSWORD)
    assert changed.status_code == 200, changed.get_json()
    ticket = changed.get_json()["ticket"]
    acc = accept(client, key, ticket, w["version_id"], sha(CONTRACT_BODY))
    assert acc.status_code == 201, acc.get_json()
    return ticket


def rows(app, model):
    with app.app_context():
        from app.extensions import db_session

        db_session.expire_all()
        return db_session.execute(select(model)).scalars().all()


def count(app, model) -> int:
    with app.app_context():
        from app.extensions import db_session

        return db_session.execute(select(func.count()).select_from(model)).scalar_one()


# ------------------------------------------------------------------- sign-in

def test_sign_in_answers_exactly_what_the_shop_app_reads(app, client, world):
    key = make_device_keypair()
    body = sign_in_ok(client, key, world)
    assert set(body) == {"ticket", "expires_at", "password_change_required", "licences", "contract", "seller"}
    assert body["password_change_required"] is True  # the seller typed this password
    assert len(body["ticket"]) >= 32
    (licence,) = body["licences"]
    assert licence["license_public_id"] == world["licence_id"]
    assert licence["product_code"] == "AURA_RETAIL" and licence["plan_code"].startswith("NF_")
    assert licence["branch_label"] is None
    assert licence["trial"] == {"state": "NOT_STARTED", "ends_on": None}  # clock starts at first activation
    contract = body["contract"]
    assert contract == {"required": True, "version_id": world["version_id"], "language": "ar", "title": "عقد الاستخدام",
                        "body": CONTRACT_BODY, "sha256": sha(CONTRACT_BODY), "lawyer_approved": True}
    assert set(body["seller"]) == {"name", "phone"} and body["seller"]["name"]
    datetime.fromisoformat(body["expires_at"])


def test_every_failure_is_the_same_single_public_401(app, client, world):
    key = make_device_keypair()
    wrong_password = sign_in(client, key, world["email"], "not-the-password-1")
    unknown_email = sign_in(client, key, "nobody@nowhere.example", GOOD)
    # right password, but no licence for this product: must not be tellable apart either
    no_licence = sign_in(client, key, world["email"], GOOD, product_code="AURA_CLINIC")
    other_platform = sign_in(client, key, world["email"], GOOD, platform="ANDROID")
    for resp in (wrong_password, unknown_email):
        assert resp.status_code == 401 and reason(resp) == "ACCOUNT_SIGN_IN_FAILED"
    assert no_licence.status_code == 401 and reason(no_licence) == "ACCOUNT_SIGN_IN_FAILED"
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License

        allowed = db_session.execute(select(License)).scalars().one().allowed_platforms.split(",")
    # the licence is listed for a platform it allows and for no other (same single failure)
    assert other_platform.status_code == (200 if "ANDROID" in allowed else 401)
    shapes = {frozenset(r.get_json()) for r in (wrong_password, unknown_email)}
    assert len(shapes) == 1  # same fields, nothing extra for "right email"
    # the allow twin: the right credentials on the right product work
    assert sign_in(client, key, world["email"], GOOD).status_code == 200


def test_the_apps_door_shares_the_websites_lockout_both_ways(app, client, world):
    from app.customer_accounts import service

    key = make_device_keypair()
    with app.app_context():
        for _ in range(5):  # the website's own budget for (email, address)
            with pytest.raises(service.SignInFailed):
                service.sign_in(world["email"], "wrong-wrong-1", "127.0.0.1", None)
    locked = sign_in(client, key, world["email"], GOOD)  # the RIGHT password, from the same address
    assert locked.status_code == 429 and reason(locked) == "RATE_LIMITED"
    assert int(locked.headers["Retry-After"]) >= 1


def test_failures_in_the_app_count_against_the_website_too(app, client, world):
    from app.customer_accounts import service

    for _ in range(5):
        key = make_device_keypair()  # a fresh device each time: the per-device budget cannot be what stops it
        assert sign_in(client, key, world["email"], "wrong-wrong-1").status_code == 401
    with app.app_context():
        with pytest.raises(service.SignInLocked):
            service.sign_in(world["email"], GOOD, "127.0.0.1", None)


def test_a_forged_or_replayed_request_is_refused_before_the_password_is_looked_at(app, client, world):
    key = make_device_keypair()
    body = envelope(key, email=world["email"], password=GOOD)
    forged = {**body, "password": "something-else-entirely"}  # signed over the other password
    refused = post(client, "/account-sign-ins", forged)
    assert refused.status_code == 400 and reason(refused) == "INVALID_SIGNATURE"  # same status activation gives
    assert post(client, "/account-sign-ins", body).status_code == 200
    replay = post(client, "/account-sign-ins", body)  # the same signed bytes again
    assert replay.status_code == 400 and reason(replay) == "NONCE_REUSED"
    # the forged attempt did not burn the genuine nonce and was never checked as a password guess
    from app.models.customer_accounts import CustomerLoginAttempt

    assert count(app, CustomerLoginAttempt) == 1


def test_the_endpoints_do_not_exist_while_customer_accounts_are_off(app, client, world):
    app.config.update(CUSTOMER_ACCOUNTS_ENABLED=False)
    key = make_device_keypair()
    assert sign_in(client, key, world["email"], GOOD).status_code == 404
    assert change(client, key, "x" * 40, NEW_PASSWORD).status_code == 404
    assert accept(client, key, "x" * 40, world["version_id"], sha(CONTRACT_BODY)).status_code == 404
    assert "capabilities" not in client.get("/api/licensing/v1/service-info").get_json()
    # a ticket cannot be used either (nothing to prove here: no ticket could have been made)
    assert reason(activate(client, key, ticket="t" * 43, license_public_id=world["licence_id"])) == "ACTIVATION_GRANT_INVALID"
    app.config.update(CUSTOMER_ACCOUNTS_ENABLED=True)
    assert set(client.get("/api/licensing/v1/service-info").get_json()["capabilities"]) == {
        "account_activation", "activation_code", "contract_v1", "trial_v1"}


# ----------------------------------------------------------- password change

def test_password_change_policy_and_the_temporary_password_cannot_be_reused(app, client, world):
    key = make_device_keypair()
    ticket = sign_in_ok(client, key, world)["ticket"]
    short = change(client, key, ticket, "short")
    assert short.status_code == 400 and reason(short) == "PASSWORD_POLICY_FAILED"
    assert short.get_json()["policy_code"] == "too_short"
    again = change(client, key, ticket, GOOD)  # the seller knows this one
    assert again.status_code == 400 and again.get_json()["policy_code"] == "same_as_old"
    assert reason(change(client, key, ticket, world["email"].split("@")[0] + "-123456")) == "PASSWORD_POLICY_FAILED"
    with app.app_context():
        from app.extensions import db_session
        from app.models.customer_accounts import CustomerAccount

        assert db_session.execute(select(CustomerAccount)).scalars().one().password_must_change is True  # nothing changed
    ok = change(client, key, ticket, NEW_PASSWORD)  # allow twin: a proper new password works with the same ticket
    assert ok.status_code == 200 and ok.get_json()["ticket"] != ticket
    with app.app_context():
        from app.extensions import db_session
        from app.models.customer_accounts import CustomerAccount

        assert db_session.execute(select(CustomerAccount)).scalars().one().password_must_change is False
    # the new password signs in; the temporary one no longer does
    assert sign_in(client, make_device_keypair(), world["email"], NEW_PASSWORD).status_code == 200
    assert sign_in(client, make_device_keypair(), world["email"], GOOD).status_code == 401


def test_changing_the_password_kills_every_other_ticket_and_website_session(app, client, world):
    laptop, phone = make_device_keypair(), make_device_keypair()
    laptop_ticket = sign_in_ok(client, laptop, world)["ticket"]
    phone_ticket = sign_in_ok(client, phone, world)["ticket"]
    assert change(client, laptop, laptop_ticket, NEW_PASSWORD).status_code == 200
    assert change(client, phone, phone_ticket, "Another-Strong-Pass-99").status_code == 401  # killed by the first change
    assert reason(change(client, phone, phone_ticket, "Another-Strong-Pass-99")) == "ACCOUNT_SIGN_IN_FAILED"


# --------------------------------------------------------------- the ticket

def test_a_password_reset_kills_a_ticket_that_was_already_made(app, client, world):
    key = make_device_keypair()
    ticket = ready_ticket(client, key, world)
    with app.app_context():  # a staff reset link, used by the customer on the website
        from app.customer_accounts import service
        from app.extensions import db_session
        from app.models.customer_accounts import CustomerAccount

        account = db_session.get(CustomerAccount, world["account_id"])
        issued = service.issue_reset_link(account, None)
        raw = issued.link.split("#t=")[1]
        service.set_password_with_token(raw, "Reset-Then-Choose-77!", "203.0.113.9")
    dead = activate(client, key, ticket=ticket, license_public_id=world["licence_id"])
    assert dead.status_code == 400 and reason(dead) == "ACTIVATION_GRANT_INVALID"
    assert count(app, Installation) == 0
    # allow twin: signing in again with the NEW password gives a ticket that works (contract already accepted)
    again = sign_in(client, key, world["email"], "Reset-Then-Choose-77!")
    assert again.status_code == 200 and again.get_json()["contract"]["required"] is False
    ok = activate(client, key, ticket=again.get_json()["ticket"], license_public_id=world["licence_id"])
    assert ok.status_code == 200, ok.get_json()


def test_a_ticket_only_works_on_the_device_that_signed_in(app, client, world):
    key, thief = make_device_keypair(), make_device_keypair()
    ticket = ready_ticket(client, key, world)
    stolen = activate(client, thief, ticket=ticket, license_public_id=world["licence_id"])
    assert stolen.status_code == 400 and reason(stolen) == "ACTIVATION_GRANT_INVALID"
    assert change(client, thief, ticket, "Thief-Chooses-Pass-1").status_code == 401
    assert accept(client, thief, ticket, world["version_id"], sha(CONTRACT_BODY)).status_code == 401
    assert count(app, Installation) == 0
    # the failed theft did not spend it: the real device still can
    ok = activate(client, key, ticket=ticket, license_public_id=world["licence_id"])
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()["result"] == "SUCCESS" and ok.get_json()["signed_assertion"]


def test_a_ticket_works_once(app, client, world):
    key = make_device_keypair()
    ticket = ready_ticket(client, key, world)
    first = activate(client, key, ticket=ticket, license_public_id=world["licence_id"])
    assert first.status_code == 200
    second = activate(client, key, ticket=ticket, license_public_id=world["licence_id"])
    assert second.status_code == 400 and reason(second) == "ACTIVATION_GRANT_INVALID"
    # a sibling ticket for the same account still works for the second computer (two devices per shop)
    other = make_device_keypair()
    next_ticket = sign_in_ok(client, other, world, password=NEW_PASSWORD)["ticket"]
    assert activate(client, other, ticket=next_ticket, license_public_id=world["licence_id"]).status_code == 200


def test_two_racing_activations_with_one_ticket_cannot_both_win(app, client, world, monkeypatch):
    """Both requests pass the ticket check before either has spent it (a delay is forced
    between the check and the spend); the conditional UPDATE is what lets only one through."""
    import threading
    import time

    from app.customer_accounts import device_flow

    key = make_device_keypair()
    ticket = ready_ticket(client, key, world)
    real = device_flow.resolve_ticket_for_activation

    def slow(*args, **kwargs):
        found = real(*args, **kwargs)
        time.sleep(0.8)  # the other request is certainly past its own check by now
        return found

    monkeypatch.setattr(device_flow, "resolve_ticket_for_activation", slow)
    barrier, results = threading.Barrier(2), []

    def go(n):
        local = app.test_client()
        barrier.wait()
        resp = activate(local, key, ticket=ticket, license_public_id=world["licence_id"], installation_id=f"race-{n}")
        results.append((resp.status_code, reason(resp)))

    threads = [threading.Thread(target=go, args=(n,)) for n in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(code for code, _ in results) == [200, 400], results
    assert [why for code, why in results if code == 400] == ["ACTIVATION_GRANT_INVALID"]
    assert count(app, Installation) == 1  # the loser's installation was rolled back with its refusal


def test_a_refused_activation_does_not_spend_the_ticket(app, client, world):
    key = make_device_keypair()
    ticket = ready_ticket(client, key, world)
    old_build = activate(client, key, ticket=ticket, license_public_id=world["licence_id"], app_version=TOO_OLD)
    assert old_build.status_code == 400 and reason(old_build) == "TRIAL_REQUIRES_UPDATE"
    assert count(app, Installation) == 0
    ok = activate(client, key, ticket=ticket, license_public_id=world["licence_id"])  # updated app, same ticket
    assert ok.status_code == 200, ok.get_json()


def test_an_expired_ticket_is_refused(app, client, world):
    key = make_device_keypair()
    ticket = ready_ticket(client, key, world)
    with app.app_context():
        from app.extensions import db_session
        from app.models.account_activation import CustomerActivationGrant

        for grant in db_session.execute(select(CustomerActivationGrant)).scalars():
            grant.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()
    assert reason(activate(client, key, ticket=ticket, license_public_id=world["licence_id"])) == "ACTIVATION_GRANT_INVALID"


def test_the_ticket_cannot_activate_another_customers_licence(app, client, world):
    key = make_device_keypair()
    ticket = ready_ticket(client, key, world)
    other = onboard(app, _world(app), language="ar")  # a second shop
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License

        other_id = str(db_session.execute(select(License).where(License.id != uuid.UUID(world["licence_id"]))).scalars().one().id)
    bad = activate(client, key, ticket=ticket, license_public_id=other_id)
    assert bad.status_code == 400 and reason(bad) == "ACTIVATION_GRANT_INVALID"
    assert activate(client, key, ticket=ticket, license_public_id=world["licence_id"]).status_code == 200  # own licence: allow twin
    assert other.license_key  # (the other shop exists and its key is untouched)


def test_the_temporary_password_must_be_replaced_before_a_device_can_activate(app, client, world):
    key = make_device_keypair()
    first = sign_in_ok(client, key, world)
    assert accept(client, key, first["ticket"], world["version_id"], sha(CONTRACT_BODY)).status_code == 201
    early = activate(client, key, ticket=first["ticket"], license_public_id=world["licence_id"])
    assert early.status_code == 400 and reason(early) == "ACTIVATION_GRANT_INVALID"
    assert count(app, Installation) == 0


# ------------------------------------------------------------------ contract

def test_the_contract_gate_blocks_activation_until_the_exact_text_is_accepted(app, client, world):
    from app.models.contracts import ContractAcceptance

    key = make_device_keypair()
    first = sign_in_ok(client, key, world)
    ticket = change(client, key, first["ticket"], NEW_PASSWORD).get_json()["ticket"]
    blocked = activate(client, key, ticket=ticket, license_public_id=world["licence_id"])
    assert blocked.status_code == 400 and reason(blocked) == "CONTRACT_ACCEPTANCE_REQUIRED"
    # a hash of some other text is not an acceptance
    wrong = accept(client, key, ticket, world["version_id"], sha("something else"))
    assert wrong.status_code == 409 and reason(wrong) == "CONTRACT_ACCEPTANCE_REQUIRED"
    assert count(app, ContractAcceptance) == 0
    assert reason(activate(client, key, ticket=ticket, license_public_id=world["licence_id"])) == "CONTRACT_ACCEPTANCE_REQUIRED"
    good = accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY), installation_id="local-inst-1")
    assert good.status_code == 201 and good.get_json()["accepted"] is True
    again = accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY))  # pressing Accept twice
    assert again.status_code == 201 and count(app, ContractAcceptance) == 1
    (evidence,) = rows(app, ContractAcceptance)
    assert evidence.audience == "CUSTOMER" and str(evidence.customer_id) == str(world["customer_id"])
    assert evidence.email_snapshot == world["email"] and evidence.text_sha256 == sha(CONTRACT_BODY)
    assert evidence.device_key_fingerprint == fingerprint_of(key) and evidence.device_signature
    assert evidence.installation_id == "local-inst-1" and evidence.ip == "127.0.0.1"
    assert activate(client, key, ticket=ticket, license_public_id=world["licence_id"]).status_code == 200


def test_acceptance_is_per_version_and_a_new_version_asks_again(app, client, world):
    key = make_device_keypair()
    ticket = ready_ticket(client, key, world)
    with app.app_context():
        from app.contracts import service as contracts

        v2 = contracts.create_version(
            audience="CUSTOMER", language="ar", version_label="v2", title="عقد الاستخدام - الإصدار الثاني",
            body=CONTRACT_BODY + "\n3. بند جديد.", lawyer_approved=True, approval_note="Counsel re-read",
            actor_staff_user_id=world["admin"])
        contracts.publish(v2, actor_staff_user_id=world["admin"])
        new_id = str(v2.id)
    blocked = activate(client, key, ticket=ticket, license_public_id=world["licence_id"])
    assert reason(blocked) == "CONTRACT_ACCEPTANCE_REQUIRED"  # accepted v1, but v1 is retired now
    stale = accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY))
    assert stale.status_code == 409  # a retired version cannot be accepted any more
    body = sign_in_ok(client, key, world, password=NEW_PASSWORD)
    assert body["contract"]["required"] is True and body["contract"]["version_id"] == new_id
    assert accept(client, key, body["ticket"], new_id, body["contract"]["sha256"]).status_code == 201
    assert activate(client, key, ticket=body["ticket"], license_public_id=world["licence_id"]).status_code == 200


def test_a_seller_contract_cannot_stand_in_for_the_customers(app, client, world):
    key = make_device_keypair()
    with app.app_context():
        from app.contracts import service as contracts

        seller_v = contracts.create_version(
            audience="SELLER", language="ar", version_label="s1", title="عقد البائع", body="شروط البائع",
            lawyer_approved=True, approval_note="ok", actor_staff_user_id=world["admin"])
        contracts.publish(seller_v, actor_staff_user_id=world["admin"])
        seller_id = str(seller_v.id)
    first = sign_in_ok(client, key, world)
    ticket = change(client, key, first["ticket"], NEW_PASSWORD).get_json()["ticket"]
    wrong_audience = accept(client, key, ticket, seller_id, sha("شروط البائع"))
    assert wrong_audience.status_code == 409 and reason(wrong_audience) == "CONTRACT_ACCEPTANCE_REQUIRED"
    from app.models.contracts import ContractAcceptance

    assert count(app, ContractAcceptance) == 0
    assert reason(activate(client, key, ticket=ticket, license_public_id=world["licence_id"])) == "CONTRACT_ACCEPTANCE_REQUIRED"
    # the customer contract (allow twin) still works
    assert accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY)).status_code == 201


def test_with_no_live_customer_contract_activation_is_not_gated(app, client, world):
    with app.app_context():
        from app.contracts import service as contracts

        contracts.retire(contracts.get_version(world["version_id"]), actor_staff_user_id=world["admin"])
    key = make_device_keypair()
    first = sign_in_ok(client, key, world)
    assert first["contract"]["required"] is False and first["contract"]["version_id"] is None
    ticket = change(client, key, first["ticket"], NEW_PASSWORD).get_json()["ticket"]
    assert activate(client, key, ticket=ticket, license_public_id=world["licence_id"]).status_code == 200


# ---------------------------------------------------------------- activation

def test_account_activation_runs_the_trial_hook_and_signs_the_same_lease(app, client, world):
    key = make_device_keypair()
    ticket = ready_ticket(client, key, world)
    ok = activate(client, key, ticket=ticket, license_public_id=world["licence_id"])
    assert ok.status_code == 200, ok.get_json()
    payload = ok.get_json()["signed_assertion"]["payload"]
    today = jordan_today()
    assert payload["subscription_status"] == "PILOT" and payload["pilot_status"] == "ACTIVE"
    assert payload["term_start"] == today.isoformat() and payload["term_end"] == (today + timedelta(days=7)).isoformat()
    assert payload["device_key_fingerprint"] == fingerprint_of(key)
    assert payload["license_public_id"] == world["licence_id"]
    assert set(ok.get_json()) >= {"result", "installation_id", "signed_assertion"}


def test_exactly_one_credential_is_accepted_and_the_licence_key_still_works(app, client, world):
    key = make_device_keypair()
    ticket = ready_ticket(client, key, world)
    both = activate(client, key, ticket=ticket, license_public_id=world["licence_id"], license_key=world["key"])
    assert both.status_code == 400 and reason(both) == "INVALID_REQUEST"
    none_given = post(client, "/activations", envelope(key, installation_id="i-1", idempotency_key=str(uuid.uuid4())))
    assert none_given.status_code == 400 and reason(none_given) == "INVALID_REQUEST"
    no_licence_id = post(client, "/activations", envelope(
        key, installation_id="i-2", idempotency_key=str(uuid.uuid4()), account_ticket=ticket))
    assert no_licence_id.status_code == 400 and reason(no_licence_id) == "INVALID_REQUEST"
    assert count(app, Installation) == 0
    legacy = post(client, "/activations", envelope(
        key, installation_id="i-3", idempotency_key=str(uuid.uuid4()), license_key=world["key"]))
    assert legacy.status_code == 200, legacy.get_json()  # the original path is untouched


def test_an_activation_code_activates_one_licence_once(app, client, world):
    from app.models.account_activation import CustomerActivationGrant

    key = make_device_keypair()
    with app.app_context():
        from app.customer_accounts import device_flow
        from app.extensions import db_session
        from app.models.licensing import License

        licence = db_session.get(License, uuid.UUID(world["licence_id"]))
        code, expires = device_flow.make_activation_code(licence, actor_staff_user_id=world["admin"], pepper=app.config["LICENSE_PEPPER"])
    assert len(code.replace("-", "")) == 12 and code.count("-") == 2
    (stored,) = rows(app, CustomerActivationGrant)
    assert code.replace("-", "") not in json.dumps([stored.token_hash, str(stored.id)])  # only a hash is kept
    wrong = activate(client, key, code="ABCD-EFGH-JKMN")
    assert wrong.status_code == 400 and reason(wrong) == "ACTIVATION_GRANT_INVALID"
    ok = activate(client, key, code=code.lower().replace("-", " "))  # typed in lower case with spaces
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()["signed_assertion"]["payload"]["license_public_id"] == world["licence_id"]
    assert ok.get_json()["signed_assertion"]["payload"]["term_end"] == (jordan_today() + timedelta(days=7)).isoformat()
    again = activate(client, make_device_keypair(), code=code)
    assert again.status_code == 400 and reason(again) == "ACTIVATION_GRANT_INVALID"


def test_an_activation_code_expires_and_a_newer_code_replaces_it(app, client, world):
    with app.app_context():
        from app.customer_accounts import device_flow
        from app.extensions import db_session
        from app.models.account_activation import CustomerActivationGrant
        from app.models.licensing import License

        licence = db_session.get(License, uuid.UUID(world["licence_id"]))
        old, _ = device_flow.make_activation_code(licence, actor_staff_user_id=world["admin"], pepper=app.config["LICENSE_PEPPER"])
        newer, _ = device_flow.make_activation_code(licence, actor_staff_user_id=world["admin"], pepper=app.config["LICENSE_PEPPER"])
        assert old != newer
    assert reason(activate(client, make_device_keypair(), code=old)) == "ACTIVATION_GRANT_INVALID"  # one live code per licence
    with app.app_context():
        from app.extensions import db_session
        from app.models.account_activation import CustomerActivationGrant

        for grant in db_session.execute(select(CustomerActivationGrant).where(CustomerActivationGrant.revoked_at.is_(None))).scalars():
            grant.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()
    assert reason(activate(client, make_device_keypair(), code=newer)) == "ACTIVATION_GRANT_INVALID"


# ------------------------------------------------ the password never persists

def _everything_in_the_database(app) -> str:
    """Every column of every row of every table, as one string."""
    with app.app_context():
        from app.extensions import Base, db_session

        parts = []
        for table in Base.metadata.sorted_tables:
            for row in db_session.execute(text(f'SELECT * FROM "{table.name}"')).all():
                parts.append(repr(tuple(row)))
        return "\n".join(parts)


def test_the_password_and_the_ticket_never_reach_a_table_or_a_log(app, client, world, caplog):
    caplog.set_level(logging.DEBUG)
    key = make_device_keypair()
    assert sign_in(client, key, world["email"], SENTINEL + "-wrong").status_code == 401  # a failure path
    ticket = sign_in_ok(client, key, world)["ticket"]
    short_sentinel = SENTINEL[:8]
    assert change(client, key, ticket, short_sentinel).status_code == 400  # a refusal path with a sentinel
    ok = change(client, key, ticket, SENTINEL + "-Okay1")
    assert ok.status_code == 200, ok.get_json()
    fresh = ok.get_json()["ticket"]
    assert accept(client, key, fresh, world["version_id"], sha(CONTRACT_BODY)).status_code == 201
    assert activate(client, key, ticket=fresh, license_public_id=world["licence_id"]).status_code == 200
    dump = _everything_in_the_database(app)
    logs = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (SENTINEL, short_sentinel, GOOD):
        assert secret not in dump, f"{secret!r} reached a table"
        assert secret not in logs, f"{secret!r} reached a log"
    for token in (ticket, fresh):
        assert token not in dump and token not in logs  # only the hash is stored
    from app.models.licensing_service import ActivationRequest, ExternalIdempotencyRecord

    events = {r.event_type for r in rows(app, ActivationRequest)}
    assert "ACCOUNT_SIGN_IN" in events  # the rejection log DID run (the grep is not vacuous)
    idem = [r.operation_type for r in rows(app, ExternalIdempotencyRecord)]
    assert idem == ["ACTIVATION"]  # no account endpoint writes an idempotency record
    cached = rows(app, ExternalIdempotencyRecord)[0].cached_response_json
    assert SENTINEL not in cached and fresh not in cached


def test_the_activation_rejection_log_and_fingerprint_never_hold_a_ticket_or_code(app, client, world):
    key = make_device_keypair()
    secret_ticket = "T" * 43
    secret_code = "ZZZZ-ZZZZ-ZZZZ"
    activate(client, key, ticket=secret_ticket, license_public_id=world["licence_id"])
    activate(client, key, code=secret_code)
    dump = _everything_in_the_database(app)
    assert secret_ticket not in dump and secret_code not in dump and "ZZZZZZZZZZZZ" not in dump
    from app.models.licensing_service import ActivationRequest

    assert sum(r.reason_code == "ACTIVATION_GRANT_INVALID" for r in rows(app, ActivationRequest)) == 2
