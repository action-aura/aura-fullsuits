from __future__ import annotations

from tests.conftest import force_login, make_staff


def test_create_and_view_customer(app, client, seeded):
    staff_id = make_staff(app, "s@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)
    resp = client.post("/customers", data={"csrf_token": _csrf(client), "legal_name": "Acme LLC"})
    assert resp.status_code == 302
    resp2 = client.get(resp.headers["Location"])
    assert b"Acme LLC" in resp2.data


def test_duplicate_customer_flagged_not_blocked(app, client, seeded):
    staff_id = make_staff(app, "s2@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)
    client.post("/customers", data={"csrf_token": _csrf(client), "legal_name": "Duplicate Corp"})
    resp = client.post("/customers", data={"csrf_token": _csrf(client), "legal_name": "Duplicate Corp"})
    assert resp.status_code == 200  # not redirected -- shown a duplicate warning, not silently blocked
    assert b"Possible duplicate" in resp.data

    # Confirming proceeds and creates the second record.
    resp2 = client.post(
        "/customers", data={"csrf_token": _csrf(client), "legal_name": "Duplicate Corp", "confirm_duplicate": "1"}
    )
    assert resp2.status_code == 302


def test_archive_does_not_hard_delete(app, client, seeded):
    staff_id = make_staff(app, "s3@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)
    resp = client.post("/customers", data={"csrf_token": _csrf(client), "legal_name": "Archive Me LLC"})
    detail_url = resp.headers["Location"]
    customer_id = detail_url.rsplit("/", 1)[-1]

    client.post(f"/customers/{customer_id}/archive", data={"csrf_token": _csrf(client)})

    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer

        row = db_session.get(Customer, customer_id)
        assert row is not None  # still exists in the database
        assert row.lifecycle_status == "ARCHIVED"
        assert row.archived_at is not None


def test_add_contact_and_note(app, client, seeded):
    staff_id = make_staff(app, "s4@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)
    resp = client.post("/customers", data={"csrf_token": _csrf(client), "legal_name": "Contactable Corp"})
    detail_url = resp.headers["Location"]
    customer_id = detail_url.rsplit("/", 1)[-1]

    contact_resp = client.post(
        f"/customers/{customer_id}/contacts",
        data={"csrf_token": _csrf(client), "name": "Jane Doe", "business_email": "jane@contactable.example"},
    )
    assert contact_resp.status_code == 302

    note_resp = client.post(f"/customers/{customer_id}/notes", data={"csrf_token": _csrf(client), "body": "Called, interested."})
    assert note_resp.status_code == 302

    detail = client.get(detail_url)
    assert b"Jane Doe" in detail.data
    assert b"Called, interested." in detail.data


def _csrf(client):
    from tests.conftest import get_csrf

    page = client.get("/customers/new")
    return get_csrf(page.get_data(as_text=True))
