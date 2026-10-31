"""Seller agreement, clauses 17 + 20: a seller who has not accepted the CURRENT live
SELLER contract cannot use any selling route; they are sent to /contracts/seller.

Both directions of every guard are proven:
  deny   a bound seller with an unaccepted live agreement is turned away from every
         route that is not on the exempt list (walked over the REAL url map, so a route
         added tomorrow is covered without anyone remembering to decorate it)
  allow  the same seller after accepting reaches the same routes untouched; a manager
         without the SALES role, a Super Admin, and EVERYONE while no SELLER contract
         is live are never turned away; a newly published version asks again.
The counts that matter are "what was written" (acceptance rows) and "what was served".
"""
from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import func, select

from tests.conftest import force_login, get_csrf, make_staff
from tests.test_contracts import PDF
from tests.test_customer_onboarding import kyc  # noqa: F401 -- fixture

BODY = "You agree to sell honestly."
SELLER_PAGE = "/contracts/seller"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _publish_seller_version(app, admin, *, label=None, body=BODY, language="en", pdf=None, approved=True):
    from app.contracts import service

    with app.app_context():
        version = service.create_version(
            audience="SELLER", language=language, version_label=label or f"v-{uuid.uuid4().hex[:5]}", title="Seller agreement",
            body=body, lawyer_approved=approved, approval_note="Counsel, 2026-11-01" if approved else "",
            pdf_bytes=pdf, actor_staff_user_id=admin,
        )
        if not approved:
            app.config.update(CONTRACT_ALLOW_DRAFT=True)
        service.publish(version, actor_staff_user_id=admin)
        return str(version.id)


def _accept(client, version_id, body=BODY):
    page = client.get(SELLER_PAGE)
    token = get_csrf(page.get_data(as_text=True))
    return client.post("/contracts/seller/accept", data={
        "csrf_token": token, "version_id": version_id, "agree": "on", "text_sha256": _sha(body)})


def _gated(response) -> bool:
    """True when this response IS the gate talking (redirect to the agreement page, or the JSON refusal)."""
    if response.status_code == 303 and response.headers.get("Location", "").startswith(SELLER_PAGE):
        return True
    return response.status_code == 403 and (response.get_json(silent=True) or {}).get("error") == "seller_agreement_required"


def _count_acceptances(app, staff_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.contracts import ContractAcceptance

        return db_session.execute(
            select(func.count()).select_from(ContractAcceptance).where(ContractAcceptance.staff_user_id == staff_id)
        ).scalar_one()


@pytest.fixture()
def people(app, seeded):
    return {
        "admin": make_staff(app, "gate-admin@example.com", super_admin=True, mfa=True),
        "seller": make_staff(app, "gate-seller@example.com", role_codes=["SALES"]),
        "other": make_staff(app, "gate-other@example.com", role_codes=["SALES"]),
        "manager": make_staff(app, "gate-manager@example.com", role_codes=["SALES_MANAGER"], mfa=True),
        "both": make_staff(app, "gate-both@example.com", role_codes=["SALES", "SALES_MANAGER"], mfa=True),
        "finance": make_staff(app, "gate-finance@example.com", role_codes=["FINANCE"], mfa=True),
    }


def _client(app, staff_id):
    client = app.test_client()
    force_login(client, app, staff_id)
    return client


# The selling routes the owner named, one real URL each. Whatever happens AFTER the gate
# (404 for a feature flag, 403 for a missing permission) is not this test's business:
# the question is only whether the gate stopped the request first.
NAMED_ROUTES = [
    "/licenses/issuance/new",      # the one-screen sale
    "/customers/new-trial",        # create a new customer
    "/payments/new",               # record a payment
    "/leads",                      # leads
    "/commissions",                # commission pages beyond the agreement
    "/leaderboard",
    "/quotes",
    "/commissions/statements",     # the monthly statement
    "/",                           # the dashboard: a seller lands on the agreement first
]


# ------------------------------------------------------------------ deny

def test_a_seller_who_has_not_accepted_is_sent_to_the_agreement_from_every_named_route(app, people):
    _publish_seller_version(app, people["admin"])
    client = _client(app, people["seller"])
    for path in NAMED_ROUTES:
        response = client.get(path)
        assert _gated(response), (path, response.status_code, response.headers.get("Location"))
    # a refused POST is not executed and not replayed: 303 to the agreement page
    blocked = client.post("/payments", data={"x": "y"})
    assert blocked.status_code in (303, 400) and (blocked.status_code == 400 or _gated(blocked))
    # the JSON API behind the mobile app answers 403 JSON, not an HTML redirect
    api = client.get("/api/operations/v1/me", headers={"Accept": "application/json"})
    assert api.status_code == 403 and api.get_json()["error"] == "seller_agreement_required"
    assert api.get_json()["agreement_url"] == SELLER_PAGE


def test_the_gate_covers_the_whole_url_map_except_a_short_exempt_list(app, people):
    """Walks EVERY route of the real app. Each one the gate does not exempt must be turned
    away for an unaccepted seller. A route added tomorrow in any blueprint is therefore
    covered (or the exempt list is deliberately extended and reviewed here)."""
    from app.contracts import gate

    app.config.update(WTF_CSRF_ENABLED=False)  # the question is the gate, not the CSRF token
    _publish_seller_version(app, people["admin"])
    client = _client(app, people["seller"])
    checked, exempt_seen = 0, set()
    for rule in app.url_map.iter_rules():
        if gate.is_exempt(rule.endpoint, rule.endpoint.split(".")[0] if "." in rule.endpoint else None):
            exempt_seen.add(rule.endpoint)
            continue
        args = {name: (str(uuid.uuid4()) if "uuid" in type(rule._converters[name]).__name__.lower() else "1")
                for name in rule.arguments}
        for method in sorted(rule.methods & {"GET", "POST"}):
            url = rule.build(args, append_unknown=False)[1]
            response = client.open(url, method=method, headers={"Accept": "text/html"})
            assert _gated(response), (method, url, rule.endpoint, response.status_code)
            checked += 1
    assert checked > 200  # the walk really walked the app, not an empty map
    # the exempt list is exactly the reviewed one; a new exemption must be added here on purpose
    assert {e for e in exempt_seen if e.startswith("contracts.")} == {
        "contracts.seller_page", "contracts.seller_accept", "contracts.seller_pdf", "contracts.seller_copy"}
    assert "contracts.index" not in exempt_seen and "leads.index" not in exempt_seen


def test_the_agreement_page_and_sign_out_stay_reachable_while_gated(app, people):
    version_id = _publish_seller_version(app, people["admin"])
    client = _client(app, people["seller"])
    page = client.get(f"{SELLER_PAGE}?required=1")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and BODY in html and 'data-contract-required="1"' in html
    assert not _gated(client.get("/locale/en")) and not _gated(client.get("/profile"))
    assert not _gated(client.get("/auth/login"))
    assert _accept(client, version_id).status_code == 302  # and the acceptance POST itself is not gated


# ------------------------------------------------------------------ allow

def test_after_accepting_the_same_seller_is_no_longer_turned_away(app, people):
    version_id = _publish_seller_version(app, people["admin"])
    client = _client(app, people["seller"])
    assert _gated(client.get("/leads"))
    assert _count_acceptances(app, people["seller"]) == 0
    assert _accept(client, version_id).status_code == 302
    assert _count_acceptances(app, people["seller"]) == 1
    for path in NAMED_ROUTES:
        response = client.get(path)
        assert not _gated(response), (path, response.status_code)
    assert client.get("/leads").status_code == 200  # really served, not merely "not gated"
    # one seller's acceptance does not open the door for another
    assert _gated(_client(app, people["other"]).get("/leads"))


def test_acceptance_records_the_version_the_time_and_the_address(app, people):
    from app.contracts import service

    version_id = _publish_seller_version(app, people["admin"])
    client = _client(app, people["seller"])
    _accept(client, version_id)
    with app.app_context():
        (row,) = service.seller_acceptances(people["seller"])
        acceptance, version = row
        assert str(version.id) == version_id and acceptance.text_sha256 == _sha(BODY)
        assert acceptance.ip == "127.0.0.1" and acceptance.accepted_at is not None
        assert acceptance.email_snapshot == "gate-seller@example.com"


def test_who_is_bound_a_manager_alone_a_super_admin_and_finance_are_not(app, people):
    """Decision recorded in contracts/service.py: the agreement binds the SALES role. The
    SALES_MANAGER on their own is the company's checker and the one who answers objections,
    so they must not be locked out; a manager who ALSO sells (holds SALES) is bound."""
    _publish_seller_version(app, people["admin"])
    for who in ("manager", "admin", "finance"):
        client = _client(app, people[who])
        assert not _gated(client.get("/")), who
    assert _gated(_client(app, people["both"]).get("/leads"))  # SALES + SALES_MANAGER: bound as a seller


def test_the_gate_is_off_while_no_seller_contract_is_live(app, people):
    client = _client(app, people["seller"])
    for path in NAMED_ROUTES:
        assert not _gated(client.get(path)), path
    # a CUSTOMER contract being live does not switch the seller gate on (audiences never mix)
    from app.contracts import service

    with app.app_context():
        customer = service.create_version(
            audience="CUSTOMER", language="en", version_label="c1", title="Terms", body="Customer terms", lawyer_approved=True,
            approval_note="Counsel", actor_staff_user_id=people["admin"])
        service.publish(customer, actor_staff_user_id=people["admin"])
    assert not _gated(client.get("/leads"))


def test_a_retired_agreement_switches_the_gate_off_again(app, people):
    from app.contracts import service

    version_id = _publish_seller_version(app, people["admin"])
    client = _client(app, people["seller"])
    assert _gated(client.get("/leads"))
    with app.app_context():
        service.retire(service.get_version(version_id), actor_staff_user_id=people["admin"])
    assert not _gated(client.get("/leads"))


def test_a_newly_published_version_asks_every_seller_again(app, people):
    v1 = _publish_seller_version(app, people["admin"], label="v1")
    client = _client(app, people["seller"])
    _accept(client, v1)
    assert not _gated(client.get("/leads"))
    v2 = _publish_seller_version(app, people["admin"], label="v2", body="Changed words: commission policy v2.")
    assert _gated(client.get("/leads")), "acceptance of v1 must not carry over to v2"
    assert _accept(client, v2, body="Changed words: commission policy v2.").status_code == 302
    assert not _gated(client.get("/leads"))
    assert _count_acceptances(app, people["seller"]) == 2  # both are evidence, neither was overwritten


def test_the_gate_does_not_touch_the_session_clock(app, people):
    """The gate runs before every view. It must read the session without advancing
    last_seen_at (views that poll in the background read with touch=False on purpose)."""
    from app.extensions import db_session
    from app.models.staff import StaffSession

    _publish_seller_version(app, people["admin"])
    client = _client(app, people["seller"])
    with app.app_context():
        before = db_session.execute(select(StaffSession.last_seen_at).where(StaffSession.staff_user_id == people["seller"])).scalar_one()
    client.get("/static/does-not-matter.css")
    client.get("/leads")  # gated: the gate answered, no view ran
    with app.app_context():
        db_session.expire_all()
        after = db_session.execute(select(StaffSession.last_seen_at).where(StaffSession.staff_user_id == people["seller"])).scalar_one()
    assert after == before


# ------------------------------------------------------------------ copies

def test_the_seller_can_download_their_own_copy_and_only_their_own(app, people, kyc):
    version_id = _publish_seller_version(app, people["admin"], pdf=PDF)
    client = _client(app, people["seller"])
    page = client.get(SELLER_PAGE).get_data(as_text=True)
    assert 'data-contract-pdf="1"' in page  # the PDF can be read BEFORE accepting
    pdf_before = client.get(f"/contracts/seller/pdf/{version_id}")
    assert pdf_before.status_code == 200 and pdf_before.data == PDF and pdf_before.headers["Cache-Control"] == "no-store"
    assert client.get(f"/contracts/seller/copy/{version_id}").status_code == 404  # nothing accepted yet: no copy
    _accept(client, version_id)
    copy = client.get(f"/contracts/seller/copy/{version_id}")
    text = copy.get_data(as_text=True)
    assert copy.status_code == 200 and BODY in text and _sha(BODY) in text and "gate-seller@example.com" in text
    assert hashlib.sha256(PDF).hexdigest() in text  # the PDF's hash is named in the copy
    assert "attachment" in copy.headers["Content-Disposition"]
    assert 'data-contract-copy="1"' in client.get(SELLER_PAGE).get_data(as_text=True)
    # another seller cannot fetch this seller's copy (404, not 403: its existence is not confirmed)
    other = _client(app, people["other"])
    assert other.get(f"/contracts/seller/copy/{version_id}").status_code == 404


def test_the_pdf_route_refuses_unpublished_drafts_and_other_audiences(app, people, kyc):
    from app.contracts import service

    client = _client(app, people["seller"])
    with app.app_context():
        draft = service.create_version(
            audience="SELLER", language="en", version_label="draft1", title="t", body="b", lawyer_approved=False,
            pdf_bytes=PDF, actor_staff_user_id=people["admin"])
        customer = service.create_version(
            audience="CUSTOMER", language="en", version_label="cust1", title="t", body="b", lawyer_approved=True,
            approval_note="x", pdf_bytes=PDF, actor_staff_user_id=people["admin"])
        service.publish(customer, actor_staff_user_id=people["admin"])
        draft_id, customer_id = str(draft.id), str(customer.id)
    assert client.get(f"/contracts/seller/pdf/{draft_id}").status_code == 404
    assert client.get(f"/contracts/seller/pdf/{customer_id}").status_code == 404
    assert client.get(f"/contracts/seller/pdf/{uuid.uuid4()}").status_code == 404


# ------------------------------------------------------------------ preflight

def test_preflight_warns_when_no_seller_contract_is_live_and_is_quiet_when_one_is(app, people):
    from app.commercial_ops.preflight import run_preflight

    def check():
        with app.app_context():
            result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
            (found,) = [c for c in result.checks if c.name == "seller_agreement_live"]
            return found

    warn = check()
    assert warn.status == "WARNING" and "gate is OFF" in warn.detail
    _publish_seller_version(app, people["admin"])
    assert check().status == "OK"
    _publish_seller_version(app, people["admin"], label="d1", language="ar", approved=False)
    assert check().status == "WARNING" and "DRAFT" in check().detail
