"""Phase 9.5B-R2 -- regression guard for a real, pre-existing (not
introduced this wave), systemic bug found via required bilingual
functional-parity browser testing (Milestone 10): four "create new record"
forms (customers, installations, licensing, subscriptions) had no explicit
`action`, so they posted back to their own GET-only `/new` URL and always
returned 405 Method Not Allowed -- creating any of these four entity types
via the web UI never worked, in either language. Fixed by adding the
correct `action="{{ url_for(...) }}"` to each form, pointing at the real
POST endpoint. This is unrelated to translation/locale and would have been
broken identically before this wave; found only because this wave performed
real end-to-end browser submission testing rather than GET-only checks."""
from __future__ import annotations

from tests.conftest import force_login, get_csrf, make_staff


def test_new_customer_form_actually_creates_a_customer(app, client, seeded):
    admin_id = make_staff(app, "r2form1@example.com", super_admin=True)
    force_login(client, app, admin_id)
    page = client.get("/customers/new")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post("/customers", data={"csrf_token": csrf, "legal_name": "R2 Form Regression Co"})
    assert resp.status_code in (302, 303), f"expected redirect after creation, got {resp.status_code}"
    assert "/customers/" in resp.headers["Location"]


def test_new_subscription_form_action_points_at_the_real_post_route(app, client, seeded):
    admin_id = make_staff(app, "r2form2@example.com", super_admin=True)
    force_login(client, app, admin_id)
    resp = client.get("/subscriptions/new")
    assert resp.status_code == 200
    data = resp.get_data(as_text=True)
    assert 'action="/subscriptions"' in data


def test_new_installation_form_action_points_at_the_real_post_route(app, client, seeded):
    admin_id = make_staff(app, "r2form3@example.com", super_admin=True)
    force_login(client, app, admin_id)
    resp = client.get("/installations/new")
    assert resp.status_code == 200
    data = resp.get_data(as_text=True)
    assert 'action="/installations"' in data


def test_new_license_form_action_points_at_the_real_post_route(app, client, seeded):
    admin_id = make_staff(app, "r2form4@example.com", super_admin=True)
    force_login(client, app, admin_id)
    resp = client.get("/licenses/new")
    assert resp.status_code == 200
    data = resp.get_data(as_text=True)
    assert 'action="/licenses"' in data
