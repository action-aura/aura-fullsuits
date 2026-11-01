"""The contract screens, made readable for a non-technical owner (feat/contracts-page).

Part 1 -- sign-in returns to the page that was asked for (and ONLY to a same-site path).
Part 2 -- the list, the detail page (3-step strip, inline PDF, approve / publish logic)
          and the seller's reading page.

Every guard here is proven by what the response / database actually shows, and each deny
has its allow twin: a hostile ``next`` is refused AND a safe one is followed; publish is
offered only with recent authentication AND the re-auth button only without it.
"""
from __future__ import annotations

import html as html_lib
import re
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_staff, totp_code
from tests.test_contracts import PDF, _admin, _create, _version
from tests.test_customer_onboarding import kyc  # noqa: F401 -- a fixture

BS = chr(92)
HOSTILE = ["//evil.com", "https://evil.com", "/" + BS + "evil.com", "/%5Cevil.com", BS + BS + "evil.com"]


def _staff(staff_id):
    from app.extensions import db_session
    from app.models.staff import StaffUser

    return db_session.get(StaffUser, staff_id)  # call inside an app context


def _location_path(response) -> str:
    parts = urlsplit(response.headers["Location"])
    assert parts.netloc == "", f"redirected off-site: {response.headers['Location']}"
    return parts.path + (("?" + parts.query) if parts.query else "")


def _sign_in(client, email, next_url=None, password="Sup3r-Str0ng-Pass!"):
    """The password step exactly as the browser does it: the form posts to the URL the
    sign-in page itself was opened at (so ?next= rides along)."""
    url = "/auth/login" + (f"?next={next_url}" if next_url is not None else "")
    csrf = get_csrf(client.get(url).get_data(as_text=True))
    return client.post(url, data={"csrf_token": csrf, "email": email, "password": password})


def _finish_mfa(client):
    csrf = get_csrf(client.get("/auth/mfa-verify").get_data(as_text=True))
    return client.post("/auth/mfa-verify", data={"csrf_token": csrf, "code": totp_code()})


# ===================================================================== sign-in returns to the page

def test_the_bounce_to_sign_in_keeps_the_page_and_its_query(app, client, seeded):
    resp = client.get("/contracts/seller?required=1")
    assert resp.status_code == 302
    query = parse_qs(urlsplit(resp.headers["Location"]).query)
    assert query["next"] == ["/contracts/seller?required=1"]


def test_a_post_only_action_does_not_become_a_dead_next(app, client, seeded):
    """A POST route cannot be "come back to" (the browser would GET it -> 405), so no next."""
    admin = make_staff(app, "ctp-post@example.com", super_admin=True, mfa=True)
    version_id = _create(app, admin, lawyer_approved=True)
    force_login(client, app, admin)  # signed in, but never confirmed with the authenticator
    csrf = get_csrf(client.get("/contracts").get_data(as_text=True))
    resp = client.post(f"/contracts/{version_id}/publish", data={"csrf_token": csrf})
    assert resp.status_code == 302 and "/auth/reauth" in resp.headers["Location"]
    assert "publish" not in resp.headers["Location"]
    assert _version(app, version_id).published_at is None  # and the bounce published nothing


def test_signing_in_without_a_code_lands_on_the_page_that_was_asked_for(app, client, seeded):
    make_staff(app, "ctp-seller@example.com")  # no role: the code step is not mandatory for them
    bounce = client.get("/contracts/seller?required=1")
    resp = _sign_in(client, "ctp-seller@example.com", next_url=parse_qs(urlsplit(bounce.headers["Location"]).query)["next"][0]
                    .replace("?", "%3F").replace("=", "%3D"))
    assert resp.status_code == 302
    assert _location_path(resp) == "/contracts/seller?required=1"


def test_signing_in_WITH_an_authenticator_code_still_lands_on_the_asked_for_page(app, client, seeded):
    """The real bug: the code page is a different URL, so ?next= was lost and the person
    always ended on the dashboard. Allow-half: it is carried through the server-side session."""
    admin = make_staff(app, "ctp-mfa@example.com", super_admin=True, mfa=True)
    version_id = _create(app, admin, lawyer_approved=True)
    bounce = client.get(f"/contracts/{version_id}")
    assert bounce.status_code == 302
    after_password = _sign_in(client, "ctp-mfa@example.com", next_url=f"/contracts/{version_id}")
    assert "/auth/mfa-verify" in after_password.headers["Location"]
    done = _finish_mfa(client)
    assert done.status_code == 302
    assert _location_path(done) == f"/contracts/{version_id}"
    assert client.get(f"/contracts/{version_id}").status_code == 200  # and the session is real


@pytest.mark.parametrize("hostile", HOSTILE)
def test_a_hostile_next_is_refused_with_and_without_a_code(app, client, seeded, hostile):
    make_staff(app, "ctp-h-mfa@example.com", super_admin=True, mfa=True)
    make_staff(app, "ctp-h-plain@example.com")
    plain = _sign_in(client, "ctp-h-plain@example.com", next_url=hostile)
    assert plain.status_code == 302 and _location_path(plain) == "/"
    other = app.test_client()
    first = _sign_in(other, "ctp-h-mfa@example.com", next_url=hostile)
    assert "/auth/mfa-verify" in first.headers["Location"]
    done = _finish_mfa(other)
    assert done.status_code == 302 and _location_path(done) == "/"
    assert "evil" not in done.headers["Location"]


def test_a_next_left_by_an_abandoned_sign_in_is_not_reused_by_the_next_one(app, client, seeded):
    admin = make_staff(app, "ctp-stale@example.com", super_admin=True, mfa=True)
    version_id = _create(app, admin, lawyer_approved=True)
    _sign_in(client, "ctp-stale@example.com", next_url=f"/contracts/{version_id}")  # abandoned at the code page
    _sign_in(client, "ctp-stale@example.com")  # a fresh sign-in that asked for nothing
    done = _finish_mfa(client)
    assert _location_path(done) == "/"


def test_opening_the_sign_in_page_while_signed_in_follows_a_safe_next_only(app, client, seeded):
    admin = make_staff(app, "ctp-in@example.com", super_admin=True, mfa=True)
    force_login(client, app, admin)
    assert _location_path(client.get("/auth/login?next=/contracts")) == "/contracts"
    for hostile in HOSTILE:
        assert _location_path(client.get(f"/auth/login?next={hostile}")) == "/"


def test_the_re_auth_link_on_the_detail_page_returns_to_that_page(app, client, seeded):
    admin = make_staff(app, "ctp-reauth@example.com", super_admin=True, mfa=True)
    version_id = _create(app, admin, lawyer_approved=True)
    force_login(client, app, admin)  # no recent authenticator check
    page = client.get(f"/contracts/{version_id}").get_data(as_text=True)
    href = html_lib.unescape(re.search(r'data-contract-reauth="1" href="([^"]+)"', page).group(1))
    reauth = client.get(href)
    assert reauth.status_code == 200
    body = reauth.get_data(as_text=True)
    next_value = html_lib.unescape(re.search(r'name="next" value="([^"]*)"', body).group(1))
    assert next_value == f"/contracts/{version_id}"
    done = client.post("/auth/reauth", data={"csrf_token": get_csrf(body), "code": totp_code(), "next": next_value})
    assert done.status_code == 302 and _location_path(done) == f"/contracts/{version_id}"
    again = client.get(f"/contracts/{version_id}").get_data(as_text=True)
    assert "Publish now" in again and 'data-contract-reauth="1"' not in again  # the check took


# ===================================================================== the detail page

def test_a_draft_shows_the_three_steps_with_the_approve_step_current(app, client, seeded, kyc):
    admin = make_staff(app, "ctp-d1@example.com", super_admin=True, mfa=True)
    version_id = _create(app, admin, lawyer_approved=False, approval_note="", pdf_bytes=PDF)
    force_login(client, app, admin)
    html = client.get(f"/contracts/{version_id}").get_data(as_text=True)
    assert 'data-contract-steps="1"' in html and 'data-current-step="2"' in html
    assert re.search(r'data-step="2"[^>]*aria-current="step"', html)
    for label in ("Review the text", "Approve for publication", "Publish"):
        assert label in html
    assert "2. Approve this text for publication" in html
    assert "Who reviewed this text and how (for example: in-house review, approved by the general manager, date)" in html
    assert 'action="/contracts/%s/approve"' % version_id in html
    assert "lawyer" not in html.lower()  # honest wording: nobody is called a lawyer on this page
    # the approve step is the only action offered; publishing waits for it
    assert 'data-contract-publish-blocked="1"' in html
    assert f'action="/contracts/{version_id}/publish"' not in html and 'data-contract-reauth="1"' not in html


def test_publishing_stays_locked_until_the_text_is_approved_even_with_a_fresh_check(app, client, seeded):
    admin = _admin(app, client, "ctp-d2@example.com")  # signed in WITH the code: recent auth is satisfied
    version_id = _create(app, admin, lawyer_approved=False, approval_note="")
    html = client.get(f"/contracts/{version_id}").get_data(as_text=True)
    assert 'data-contract-publish-blocked="1"' in html
    assert f'action="/contracts/{version_id}/publish"' not in html and "Publish now" not in html


def test_approving_records_the_note_and_moves_to_the_publish_step(app, client, seeded):
    admin = _admin(app, client, "ctp-d3@example.com")
    version_id = _create(app, admin, lawyer_approved=False, approval_note="")
    page = client.get(f"/contracts/{version_id}")
    resp = client.post(f"/contracts/{version_id}/approve", data={
        "csrf_token": get_csrf(page.get_data(as_text=True)), "approval_note": "In-house review by the GM, 1 Nov"})
    assert resp.status_code == 302
    version = _version(app, version_id)
    assert version.lawyer_approved is True and version.lawyer_approval_note == "In-house review by the GM, 1 Nov"
    html = client.get(f"/contracts/{version_id}").get_data(as_text=True)
    assert 'data-current-step="3"' in html
    assert "Approved for publication" in html and "In-house review by the GM, 1 Nov" in html
    assert f'action="/contracts/{version_id}/approve"' not in html  # nothing left to approve


def test_an_approved_text_offers_publish_only_with_a_fresh_check_and_the_button_only_without(app, client, seeded):
    admin = make_staff(app, "ctp-d4@example.com", super_admin=True, mfa=True)
    version_id = _create(app, admin, lawyer_approved=True)
    publish_action = f'action="/contracts/{version_id}/publish"'
    warning = "After publishing, this text can never be changed; sellers/shops must accept it."

    # DENY half: signed in, no recent authenticator check -> the re-auth button, no publish form.
    force_login(client, app, admin)
    html = client.get(f"/contracts/{version_id}").get_data(as_text=True)
    assert 'data-current-step="3"' in html
    assert "Confirm with your authenticator code to publish" in html
    href = html_lib.unescape(re.search(r'data-contract-reauth="1" href="([^"]+)"', html).group(1))
    assert urlsplit(href).path == "/auth/reauth" and parse_qs(urlsplit(href).query)["next"] == [f"/contracts/{version_id}"]
    assert publish_action not in html and "Publish now" not in html
    assert warning in html  # the consequence is stated before the click either way

    # ALLOW half: a real authenticator-confirmed session -> "Publish now", no re-auth button.
    fresh = app.test_client()
    login_and_verify_mfa(fresh, "ctp-d4@example.com")
    html = fresh.get(f"/contracts/{version_id}").get_data(as_text=True)
    assert publish_action in html and "Publish now" in html and warning in html
    assert "Confirm with your authenticator code to publish" not in html and 'data-contract-reauth="1"' not in html


def test_the_pdf_is_shown_inline_with_a_download_button_and_the_text_reads_in_its_own_direction(app, client, seeded, kyc):
    admin = _admin(app, client, "ctp-d5@example.com")
    ar_id = _create(app, admin, language="ar", body="نص العقد\nالبند الثاني", pdf_bytes=PDF)
    en_id = _create(app, admin, language="en", body="Plain English text")
    html = client.get(f"/contracts/{ar_id}").get_data(as_text=True)
    assert f'<iframe class="cs-pdf" data-contract-pdf-viewer="1" src="/contracts/{ar_id}/pdf?inline=1"' in html
    assert f'data-contract-pdf-download="1" href="/contracts/{ar_id}/pdf"' in html
    assert re.search(r'data-contract-text="1" dir="rtl" lang="ar">نص العقد', html)
    plain = client.get(f"/contracts/{en_id}").get_data(as_text=True)
    assert re.search(r'data-contract-text="1" dir="ltr" lang="en">Plain English text', plain)
    assert "<iframe" not in plain and 'data-contract-no-pdf="1"' in plain  # no PDF -> no empty viewer


def test_the_inline_pdf_may_be_framed_by_this_site_only_and_nothing_else_may(app, client, seeded, kyc):
    admin = _admin(app, client, "ctp-d6@example.com")
    version_id = _create(app, admin, pdf_bytes=PDF)
    inline = client.get(f"/contracts/{version_id}/pdf?inline=1")
    assert inline.status_code == 200 and inline.data == PDF
    assert inline.headers["Content-Disposition"].startswith("inline;")
    assert inline.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in inline.headers["Content-Security-Policy"]
    assert inline.headers["Cache-Control"] == "no-store"
    download = client.get(f"/contracts/{version_id}/pdf")
    assert download.headers["Content-Disposition"].startswith("attachment;")
    assert download.headers["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in download.headers["Content-Security-Policy"]
    page = client.get(f"/contracts/{version_id}")
    assert page.headers["X-Frame-Options"] == "DENY"  # the pages themselves are still unframeable


def test_a_live_version_shows_since_when_who_accepted_and_the_retire_action(app, client, seeded):
    from app.contracts import service

    admin = _admin(app, client, "ctp-d7@example.com")
    seller = make_staff(app, "ctp-d7-seller@example.com", role_codes=["SALES"])
    version_id = _create(app, admin, audience="SELLER", language="en", body="Sell honestly.")
    with app.app_context():
        service.publish(service.get_version(version_id), actor_staff_user_id=admin)
        service.record_seller_acceptance(staff=_staff(seller), version=service.get_version(version_id), ip="1.2.3.4")
    html = client.get(f"/contracts/{version_id}").get_data(as_text=True)
    assert 'data-current-step="4"' in html and 'data-contract-live="1"' in html and "Live since" in html
    assert "ctp-d7-seller@example.com" in html and "Who accepted this version (1)" in html
    assert f'action="/contracts/{version_id}/retire"' in html  # fresh check -> the retire form
    # no recent check -> the same action becomes the re-auth button
    stale = app.test_client()
    force_login(stale, app, admin)
    html = stale.get(f"/contracts/{version_id}").get_data(as_text=True)
    assert f'action="/contracts/{version_id}/retire"' not in html and "Confirm with your authenticator code to retire" in html


# ===================================================================== the list

def test_the_list_groups_by_audience_and_names_each_state_and_the_next_action(app, client, seeded):
    from app.contracts import service

    admin = _admin(app, client, "ctp-l1@example.com")
    seller = make_staff(app, "ctp-l1-seller@example.com", role_codes=["SALES"])
    draft = _create(app, admin, audience="CUSTOMER", language="en", version_label="shop-draft", lawyer_approved=False, approval_note="")
    approved = _create(app, admin, audience="CUSTOMER", language="ar", version_label="shop-approved", lawyer_approved=True)
    live = _create(app, admin, audience="SELLER", language="en", version_label="sell-live", lawyer_approved=True)
    old = _create(app, admin, audience="SELLER", language="ar", version_label="sell-old", lawyer_approved=True)
    with app.app_context():
        service.publish(service.get_version(old), actor_staff_user_id=admin)
        service.retire(service.get_version(old), actor_staff_user_id=admin)
        service.publish(service.get_version(live), actor_staff_user_id=admin)
        service.record_seller_acceptance(staff=_staff(seller), version=service.get_version(live), ip="1.2.3.4")
    html = client.get("/contracts").get_data(as_text=True)
    assert 'data-contracts-intro="1"' in html  # one plain line at the top saying what the page is
    shop_block, seller_block = (re.search(r'data-contract-group="%s">(.*?)</table>' % aud, html, re.S).group(1)
                                for aud in ("CUSTOMER", "SELLER"))
    assert "Shop contracts" in html and "Seller agreements" in html
    row = lambda block, label: re.search(r'<tr data-contract-row="(\w+)">(?:(?!</tr>).)*%s(?:(?!</tr>).)*</tr>' % label, block, re.S)
    assert row(shop_block, "shop-draft").group(1) == "DRAFT" and "shop-draft" not in seller_block
    assert row(shop_block, "shop-approved").group(1) == "APPROVED"
    assert row(seller_block, "sell-live").group(1) == "LIVE" and row(seller_block, "sell-old").group(1) == "RETIRED"
    assert "sell-live" not in shop_block
    # the primary action, per state
    assert "Review and publish" in row(shop_block, "shop-draft").group(0)
    assert "Publish this text" in row(shop_block, "shop-approved").group(0)
    assert "Review and publish" not in row(seller_block, "sell-live").group(0)
    # dates and the acceptance count
    assert "Live since" in row(seller_block, "sell-live").group(0) and "Retired on" in row(seller_block, "sell-old").group(0)
    assert re.search(r"<td>1</td>", row(seller_block, "sell-live").group(0))
    assert re.search(r"<td>0</td>", row(seller_block, "sell-old").group(0))
    assert approved and draft


def test_the_screens_say_nothing_about_a_lawyer_to_the_person_reading_them(app, client, seeded):
    admin = _admin(app, client, "ctp-l2@example.com")
    html = client.get("/contracts").get_data(as_text=True)
    visible = re.sub(r'name="lawyer_approved"', "", html)  # the stored field name is allowed; the words are not
    assert "lawyer" not in visible.lower()


# ===================================================================== the seller's reading page

def test_the_seller_page_has_the_pdf_the_text_the_tick_box_and_then_the_copy(app, client, seeded, kyc):
    from app.contracts import service

    admin = make_staff(app, "ctp-s1@example.com", super_admin=True, mfa=True)
    seller = make_staff(app, "ctp-s1-seller@example.com", role_codes=["SALES"])
    version_id = _create(app, admin, audience="SELLER", language="ar", body="نص اتفاقية البائع", pdf_bytes=PDF)
    with app.app_context():
        service.publish(service.get_version(version_id), actor_staff_user_id=admin)
    force_login(client, app, seller)
    html = client.get("/contracts/seller").get_data(as_text=True)
    assert f'<iframe class="cs-pdf" data-contract-pdf-viewer="1" src="/contracts/seller/pdf/{version_id}?inline=1"' in html
    assert f'data-contract-pdf="1" href="/contracts/seller/pdf/{version_id}"' in html
    assert re.search(r'data-contract-text="1" dir="rtl" lang="ar">نص اتفاقية البائع', html)
    assert 'name="agree"' in html and "Accept" in html
    assert "Download my copy" not in html  # nothing to copy before accepting
    inline = client.get(f"/contracts/seller/pdf/{version_id}?inline=1")
    assert inline.status_code == 200 and inline.data == PDF and inline.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert client.get(f"/contracts/seller/pdf/{version_id}").headers["X-Frame-Options"] == "DENY"
    # accept, and the page flips to the copy
    digest = re.search(r'name="text_sha256" value="([0-9a-f]{64})"', html).group(1)
    client.post("/contracts/seller/accept", data={"csrf_token": get_csrf(html), "version_id": version_id, "agree": "on",
                                                  "text_sha256": digest})
    after = client.get("/contracts/seller").get_data(as_text=True)
    assert 'data-contract-accepted="1"' in after and "Download my copy" in after
    assert 'name="agree"' not in after
    assert client.get(f"/contracts/seller/copy/{version_id}").status_code == 200


def test_the_staff_pages_stay_super_admin_only(app, client, seeded):
    admin = make_staff(app, "ctp-p1@example.com", super_admin=True, mfa=True)
    seller = make_staff(app, "ctp-p1-seller@example.com", role_codes=["SALES"])
    version_id = _create(app, admin, lawyer_approved=False, approval_note="")
    force_login(client, app, seller)
    token = get_csrf(client.get("/contracts/seller").get_data(as_text=True)) or get_csrf(client.get("/").get_data(as_text=True))
    assert client.get("/contracts").status_code == 403
    assert client.get(f"/contracts/{version_id}").status_code == 403
    assert client.get(f"/contracts/{version_id}/pdf?inline=1").status_code == 403
    denied = client.post(f"/contracts/{version_id}/approve", data={"csrf_token": token, "approval_note": "x"})
    assert denied.status_code in (400, 403)  # CSRF or permission: either way the write is refused
    assert _version(app, version_id).lawyer_approved is False  # and nothing was written
    assert client.get(f"/contracts/{uuid.uuid4()}").status_code == 403  # a stranger learns nothing, not even "no such id"
