"""Phase 9R M6/M8 -- request-body limits. Real bug found and fixed this
milestone: app.config["MAX_CONTENT_LENGTH"] was set to the licensing API's
own MAX_REQUEST_BYTES (64KB) as a Flask-GLOBAL ceiling -- meaning any
expense attachment upload over 64KB (nearly every real PDF/photo receipt)
was silently rejected with a raw 413 before ever reaching the
attachment-specific 10MB size check. Undetected by existing tests because
they all call upload_attachment() directly as a Python function, bypassing
the WSGI layer entirely -- this file is the first to go through the real
HTTP route. Fixed by decoupling the global MAX_CONTENT_LENGTH (now 12MB,
covers the largest legitimate body) from the licensing API's own tighter
bound, which is now enforced independently and explicitly in
app/api_external/routes.py."""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

from tests.conftest import force_login, get_csrf, make_staff


def _csrf(client):
    return get_csrf(client.get("/profile").get_data(as_text=True))


def _seed_employee(app, email, role_codes):
    from app.employees.services import activate_employee, create_employee_profile

    staff_id = make_staff(app, email, role_codes=role_codes)
    with app.app_context():
        profile = create_employee_profile(
            {
                "staff_user_id": staff_id, "employee_number": f"EMP-{email[:5]}",
                "full_name": f"Employee {email}", "employment_start_date": date(2026, 1, 1),
            },
            actor_staff_user_id=staff_id,
        )
        activate_employee(profile, actor_staff_user_id=staff_id)
        return staff_id, profile.id


def _seed_category(app, code):
    from app.extensions import db_session
    from app.models.expenses import ExpenseCategory

    with app.app_context():
        cat = ExpenseCategory(category_code=code, name=code.title(), is_active=True)
        db_session.add(cat)
        db_session.commit()
        return cat.id


def _seed_payee(app, creator_staff_id):
    from app.expenses.payees import create_payee

    with app.app_context():
        payee = create_payee(
            payee_type="EXTERNAL", display_name="Vendor Co", employee_profile_id=None,
            external_contact_reference="v@example.com", created_by_staff_user_id=creator_staff_id,
        )
        return payee.id


def _make_expense(app, req_profile_id, category_id, payee_id):
    from app.expenses.lifecycle import create_expense

    with app.app_context():
        expense = create_expense(
            category_id=category_id, payee_id=payee_id, amount=Decimal("50.00"), currency="USD",
            expense_date=date(2026, 8, 1), description="Body-limit test receipt", external_reference=None,
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=req_profile_id,
        )
        return expense.id


def _authenticated_client_with_expense(app, client, seeded):
    staff_id, profile_id = _seed_employee(app, "bodylimit@example.com", ["SALES"])
    category_id = _seed_category(app, "BODYLIMIT")
    payee_id = _seed_payee(app, staff_id)
    expense_id = _make_expense(app, profile_id, category_id, payee_id)
    force_login(client, app, staff_id)
    return expense_id


class TestExpenseAttachmentUploadNoLongerBrokenByGlobalLimit:
    def test_200kb_attachment_upload_succeeds(self, app, client, seeded):
        # 200KB is comfortably over the OLD global 64KB ceiling and
        # comfortably under EXPENSE_ATTACHMENT_MAX_BYTES (10MB) -- exactly
        # the range that was silently broken before this fix. Goes through
        # the real HTTP route, not a direct Python call, so it actually
        # exercises the WSGI-level MAX_CONTENT_LENGTH this bug lived in.
        expense_id = _authenticated_client_with_expense(app, client, seeded)
        csrf = _csrf(client)
        big = b"%PDF-1.4" + b"A" * (200 * 1024)
        resp = client.post(
            f"/api/operations/v1/expenses/{expense_id}/attachments",
            data={"file": (io.BytesIO(big), "receipt.pdf", "application/pdf")},
            content_type="multipart/form-data",
            headers={"X-CSRFToken": csrf},
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)

    def test_upload_over_the_real_10mb_limit_is_still_rejected_by_the_app(self, app, client, seeded):
        # Must still be rejected -- just by the attachment-specific
        # ATTACHMENT_TOO_LARGE check (app-level, informative), not by a raw
        # Werkzeug 413 -- proving the *attachment* limit is still real and
        # enforced, only no longer masked by an unrelated global ceiling.
        expense_id = _authenticated_client_with_expense(app, client, seeded)
        csrf = _csrf(client)
        too_big = b"%PDF-1.4" + b"A" * (11 * 1024 * 1024)
        resp = client.post(
            f"/api/operations/v1/expenses/{expense_id}/attachments",
            data={"file": (io.BytesIO(too_big), "receipt.pdf", "application/pdf")},
            content_type="multipart/form-data",
            headers={"X-CSRFToken": csrf},
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "ATTACHMENT_TOO_LARGE"


class TestLicensingApiKeepsItsOwnTighterLimit:
    def test_oversized_licensing_payload_is_rejected_even_though_the_global_cap_is_larger(self, app, client):
        # The global MAX_CONTENT_LENGTH is now 12MB (to allow attachments),
        # but the licensing API must still reject anything over its own
        # much smaller MAX_REQUEST_BYTES (64KB by default) -- proving
        # _bounded_payload() actually enforces the tighter bound
        # independently, not just inheriting the raised global one.
        oversized_body = b'{"padding": "' + b"A" * (200 * 1024) + b'"}'
        resp = client.post(
            "/api/licensing/v1/activations",
            data=oversized_body,
            content_type="application/json",
        )
        assert resp.status_code == 413
        assert resp.get_json()["reason_code"] == "PAYLOAD_TOO_LARGE"

    def test_normal_sized_licensing_request_is_not_affected(self, app, client):
        small_body = b'{"license_key": "not-a-real-key"}'
        resp = client.post(
            "/api/licensing/v1/activations",
            data=small_body,
            content_type="application/json",
        )
        # Not a 413 -- whatever it is (likely a real validation rejection
        # given the fake key), the size check must not be what blocks it.
        assert resp.status_code != 413
