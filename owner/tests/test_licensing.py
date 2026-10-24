from __future__ import annotations

import uuid

import pytest

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_staff


def _make_license(app, staff_id):
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.licensing.services import create_license
    from app.subscriptions.services import create_subscription

    product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
    plan = Plan(plan_code="LIC1", product_id=product.id, name="LIC1", billing_model="MONTHLY", currency="USD")
    customer = Customer(legal_name="License Test Co")
    db_session.add_all([plan, customer])
    db_session.commit()
    sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id}, staff_id)
    return create_license(
        {"customer_id": customer.id, "subscription_id": sub.id, "product_id": product.id, "plan_id": plan.id,
         "allowed_platforms": "WINDOWS,ANDROID", "device_limit": 1},
        staff_id,
    )


def test_generated_key_has_expected_format_and_entropy(app):
    from app.security.license_keys import generate_license_key

    full_key, prefix, masked = generate_license_key("AURA_CLINIC")
    assert full_key.startswith("AURA-CLN-1-")
    parts = full_key.split("-")
    assert len(parts) == 8  # AURA, CLN, 1, then 5 groups of 4
    # >=100 bits from a 32-symbol alphabet across 20 symbols: log2(32**20) = 100 bits.
    assert len("".join(parts[3:])) == 20


def test_generated_keys_are_not_sequential_or_deterministic(app):
    from app.security.license_keys import generate_license_key

    keys = {generate_license_key("AURA_CLINIC")[0] for _ in range(50)}
    assert len(keys) == 50  # no collisions across 50 generations


def test_full_key_never_persisted_in_plaintext(app, seeded):
    staff_id = make_staff(app, "lic1@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.services import issue_license_key

        license_row = _make_license(app, staff_id)
        license_row, full_key = issue_license_key(license_row, "test-pepper", str(uuid.uuid4()), staff_id)
        assert full_key is not None

        db_session.expire_all()
        from app.models.licensing import License

        stored = db_session.get(License, license_row.id)
        assert stored.key_secret_hmac != full_key
        assert full_key not in (stored.key_secret_hmac or "")
        assert stored.key_prefix in full_key  # only the non-secret prefix matches
        assert stored.key_suffix_masked.replace("*", "") in full_key  # only masked suffix matches


def test_reveal_only_happens_once_idempotent_replay_returns_no_key(app, seeded):
    staff_id = make_staff(app, "lic2@example.com")
    with app.app_context():
        from app.licensing.services import issue_license_key

        license_row = _make_license(app, staff_id)
        idem_key = str(uuid.uuid4())
        _, full_key_1 = issue_license_key(license_row, "test-pepper", idem_key, staff_id)
        _, full_key_2 = issue_license_key(license_row, "test-pepper", idem_key, staff_id)
        assert full_key_1 is not None
        assert full_key_2 is None  # replay of the same idempotency key never re-reveals the secret


def test_issuance_event_never_contains_the_secret(app, seeded):
    staff_id = make_staff(app, "lic3@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.services import issue_license_key
        from app.models.audit import AuditLog

        license_row = _make_license(app, staff_id)
        _, full_key = issue_license_key(license_row, "test-pepper", str(uuid.uuid4()), staff_id)

        events = db_session.query(AuditLog).filter_by(action_code="LICENSE_KEY_ISSUED").all()
        assert len(events) == 1
        payload = str(events[0].after_state_redacted)
        assert full_key not in payload
        assert full_key.split("-", 3)[-1] not in payload  # none of the 5 secret groups leak


def test_verify_license_key_hmac(app):
    from app.security.license_keys import generate_license_key, hash_license_secret, verify_license_key

    full_key, _, _ = generate_license_key("AURA_RETAIL")
    stored = hash_license_secret(full_key, "pepper-a")
    assert verify_license_key(full_key, "pepper-a", stored) is True
    assert verify_license_key(full_key, "pepper-b", stored) is False  # wrong pepper fails
    assert verify_license_key("wrong-key", "pepper-a", stored) is False


def test_invalid_status_transition_rejected(app, seeded):
    from app.licensing.services import InvalidLicenseTransitionError, transition_license

    staff_id = make_staff(app, "lic4@example.com")
    with app.app_context():
        license_row = _make_license(app, staff_id)
        with pytest.raises(InvalidLicenseTransitionError):
            transition_license(license_row, "REVOKED", staff_id)  # DRAFT -> REVOKED is not a valid path


def test_issuance_route_requires_permission_and_recent_auth(app, client, seeded):
    staff_id = make_staff(app, "lic5@example.com", role_codes=["SUPPORT"])  # SUPPORT has no licenses.issue
    force_login(client, app, staff_id)
    with app.app_context():
        license_row = _make_license(app, staff_id)
        license_id = license_row.id
    resp = client.post(f"/licenses/{license_id}/issue", data={"csrf_token": "x", "idempotency_key": "abc"})
    assert resp.status_code in (400, 403)


def test_issuance_route_redirects_to_reauth_without_recent_mfa(app, client, seeded):
    staff_id = make_staff(app, "lic6@example.com", super_admin=True, mfa=True)
    with app.app_context():
        license_row = _make_license(app, staff_id)
        license_id = license_row.id
    login_and_verify_mfa(client, "lic6@example.com")
    # Force the recent-auth window to have expired.
    with app.app_context():
        from datetime import timedelta

        from app.extensions import db_session
        from app.models.base import utcnow
        from app.models.staff import StaffSession

        session_row = db_session.query(StaffSession).order_by(StaffSession.created_at.desc()).first()
        session_row.mfa_verified_at = utcnow() - timedelta(seconds=app.config["RECENT_AUTH_WINDOW_SECONDS"] + 5)
        db_session.commit()

    page = client.get(f"/licenses/{license_id}")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(f"/licenses/{license_id}/issue", data={"csrf_token": csrf, "idempotency_key": "xyz"})
    assert resp.status_code == 302
    assert "reauth" in resp.headers["Location"]


def test_issue_key_reveal_carries_copy_control_and_reload_never_reveals_again(app, client, seeded):
    """owner AI-context DEFECT 1: the one-time reveal on this detail page
    must carry a copy-to-clipboard control bound to the key via the
    data-attribute mechanism (static/js/copy-to-clipboard.js) -- and (5)
    the security property that reload never re-shows the key must survive
    adding it."""
    staff_id = make_staff(app, "lic7@example.com", super_admin=True, mfa=True)
    with app.app_context():
        license_row = _make_license(app, staff_id)
        license_id = license_row.id
    login_and_verify_mfa(client, "lic7@example.com")
    page = client.get(f"/licenses/{license_id}")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(
        f"/licenses/{license_id}/issue",
        data={"csrf_token": csrf, "idempotency_key": str(uuid.uuid4())},
    )
    body = resp.get_data(as_text=True)
    assert "AURA-" in body
    assert 'id="licensing-detail-revealed-key"' in body
    assert 'data-copy-target="licensing-detail-revealed-key"' in body

    import re

    # Extract the FULL revealed key -- the always-visible masked key
    # elsewhere on this same page (e.g. "AURA-CLN-1-...****A7W5") also
    # contains "AURA-", so the reload assertion below must check for the
    # exact secret, not that generic substring.
    match = re.search(r'id="licensing-detail-revealed-key">([^<]+)<', body)
    assert match is not None
    full_key = match.group(1)
    assert full_key.count("-") >= 7  # a real full key, not just the masked prefix

    reload_page = client.get(f"/licenses/{license_id}")
    reload_body = reload_page.get_data(as_text=True)
    assert full_key not in reload_body


# -- Step 0 fix round 2: the detail-page issue path (issue_license_key) ------
#
# POST /licenses/<id>/issue calls issue_license_key directly. An adversarial
# pass over 16cbc323 (probe A7) measured two gaps in it:
#   - two issues of the SAME DRAFT licence with DIFFERENT idempotency keys
#     (two open tabs of the detail page, each with its own form key) both
#     passed the DRAFT check -- nothing locked the row -- and both revealed a
#     key. Only the last one written is the key the licence verifies against;
#     the other was shown to the operator once and is dead on arrival.
#   - the replay lookup matched the idempotency key alone, never the licence
#     it was used on: a key spent issuing licence 1, sent against licence 2,
#     answered "already issued" for licence 2, which was still DRAFT.

def _make_draft_licence(app, staff_id, tag):
    from app.extensions import db_session
    from app.licensing.services import create_license
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription

    with app.app_context():
        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        plan = Plan(plan_code=f"LICR2_{tag}", product_id=product.id, name=f"LICR2_{tag}", billing_model="MONTHLY", currency="USD")
        customer = Customer(legal_name=f"Licence Round Two {tag} Co")
        db_session.add_all([plan, customer])
        db_session.commit()
        sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id}, staff_id)
        return create_license(
            {"customer_id": customer.id, "subscription_id": sub.id, "product_id": product.id, "plan_id": plan.id,
             "allowed_platforms": "WINDOWS,ANDROID", "device_limit": 1},
            staff_id,
        ).id


def _licence_state(app, license_id):
    from app.extensions import db_session
    from app.models.licensing import License, LicenseKeyIssuanceEvent

    with app.app_context():
        db_session.expire_all()
        row = db_session.get(License, license_id)
        events = db_session.query(LicenseKeyIssuanceEvent).filter_by(license_id=license_id).count()
        return row.status, row.key_secret_hmac, events


def test_two_tabs_issuing_one_draft_licence_reveal_exactly_one_key(app, seeded, monkeypatch):
    """Probe A7. Both callers are pushed into the window between "status is
    DRAFT" and the commit by a 0.75s sleep in key generation (the same
    technique test_license_issuance.py's concurrency test uses). Exactly one
    key revealed and recorded; the other caller is refused cleanly."""
    import threading
    import time

    staff_id = make_staff(app, "licr2-1@example.com")
    license_id = _make_draft_licence(app, staff_id, "TABS")

    import app.licensing.services as licensing_services

    real_generate = licensing_services.generate_license_key_for_product

    def _slow_generate(row):
        time.sleep(0.75)
        return real_generate(row)

    monkeypatch.setattr(licensing_services, "generate_license_key_for_product", _slow_generate)

    revealed, refused, crashed = [], [], []
    lock = threading.Lock()

    def worker():
        try:
            with app.app_context():
                from app.extensions import db_session
                from app.models.licensing import License

                row = db_session.get(License, license_id)
                try:
                    _, full_key = licensing_services.issue_license_key(row, "test-pepper", str(uuid.uuid4()), staff_id)
                except licensing_services.InvalidLicenseTransitionError as exc:
                    with lock:
                        refused.append(str(exc))
                else:
                    with lock:
                        revealed.append(full_key)
        except Exception as exc:  # noqa: BLE001
            with lock:
                crashed.append(f"{type(exc).__name__}: {exc}")
        finally:
            from app.extensions import db_session as scoped_db_session

            scoped_db_session.remove()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert crashed == []
    assert len([k for k in revealed if k]) == 1, f"keys revealed: {len(revealed)}"
    assert len(refused) == 1
    status, stored_hmac, events = _licence_state(app, license_id)
    assert (status, events) == ("ISSUED", 1)
    # The one key shown is the one the licence actually verifies against.
    from app.security.license_keys import verify_license_key

    assert verify_license_key(revealed[0], "test-pepper", stored_hmac) is True


def test_a_key_used_on_one_licence_is_refused_on_another_not_reported_issued(app, client, seeded):
    """The second gap, end to end through the detail-page route. Licence 2
    must stay DRAFT and the answer must be a refusal (400), not a 200 page
    rendered as if licence 2 had been issued already."""
    staff_id = make_staff(app, "licr2-2@example.com", super_admin=True, mfa=True)
    first = _make_draft_licence(app, staff_id, "KEY_ONE")
    second = _make_draft_licence(app, staff_id, "KEY_TWO")
    login_and_verify_mfa(client, "licr2-2@example.com")
    key = str(uuid.uuid4())

    csrf = get_csrf(client.get(f"/licenses/{first}").get_data(as_text=True))
    issued = client.post(f"/licenses/{first}/issue", data={"csrf_token": csrf, "idempotency_key": key})
    assert issued.status_code == 200 and "AURA-" in issued.get_data(as_text=True)

    csrf = get_csrf(client.get(f"/licenses/{second}").get_data(as_text=True))
    reused = client.post(f"/licenses/{second}/issue", data={"csrf_token": csrf, "idempotency_key": key})
    assert reused.status_code == 400
    assert _licence_state(app, second)[0] == "DRAFT"
    assert _licence_state(app, second)[2] == 0
    assert _licence_state(app, first)[0] == "ISSUED"


def test_two_different_licences_each_still_get_their_own_key(app, seeded):
    """Allow half of the row lock: it serializes issues of ONE licence, not
    issuance in general."""
    staff_id = make_staff(app, "licr2-3@example.com")
    first = _make_draft_licence(app, staff_id, "EACH_ONE")
    second = _make_draft_licence(app, staff_id, "EACH_TWO")
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.services import issue_license_key
        from app.models.licensing import License

        _, key_one = issue_license_key(db_session.get(License, first), "test-pepper", str(uuid.uuid4()), staff_id)
        _, key_two = issue_license_key(db_session.get(License, second), "test-pepper", str(uuid.uuid4()), staff_id)
    assert key_one and key_two and key_one != key_two
    assert _licence_state(app, first)[0] == _licence_state(app, second)[0] == "ISSUED"
