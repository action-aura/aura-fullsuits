"""Part F/Y CONCURRENCY: two simultaneous activation requests for the final
device slot -- only one may be accepted, ever."""
from __future__ import annotations

import json
import threading

from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff


def test_simultaneous_final_slot_activations_only_one_accepted(app, seeded, signing_key):
    """Two threads, two different devices, one license with device_limit=1,
    hitting the real Flask test client concurrently. The row lock in
    activation.py (SELECT ... FOR UPDATE) must serialize them so the
    installation count never exceeds 1."""
    actor_id = make_staff(app, "conc1@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=1)

    key_a = make_device_keypair()
    key_b = make_device_keypair()
    body_a = build_activation_body(key_a, full_key=full_key, installation_id="conc-dev-a")
    body_b = build_activation_body(key_b, full_key=full_key, installation_id="conc-dev-b")

    results = {}

    def worker(name, body):
        with app.test_client() as c:
            resp = c.post("/api/licensing/v1/activations", data=json.dumps(body), content_type="application/json")
            results[name] = (resp.status_code, resp.get_json())

    t1 = threading.Thread(target=worker, args=("a", body_a))
    t2 = threading.Thread(target=worker, args=("b", body_b))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    statuses = sorted(results[name][0] for name in ("a", "b"))
    assert statuses == [200, 400]  # exactly one accepted, one rejected
    rejected = results["a"] if results["a"][0] == 400 else results["b"]
    assert rejected[1]["reason_code"] == "DEVICE_LIMIT_REACHED"

    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation
        from sqlalchemy import select

        count = len(db_session.execute(select(Installation).where(Installation.license_id == license_id)).scalars().all())
        assert count == 1  # never over-allocated


def test_retry_after_transaction_rollback_does_not_leak_a_slot(app, client, seeded, signing_key):
    """A rejected activation attempt (e.g. invalid platform) must not have
    consumed any device-limit capacity -- the transaction rolls back cleanly."""
    actor_id = make_staff(app, "conc2@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=1)
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        lic.allowed_platforms = "WINDOWS"
        db_session.commit()

    bad_key = make_device_keypair()
    bad_body = build_activation_body(bad_key, full_key=full_key, installation_id="conc-dev-bad", platform="ANDROID")
    resp = client.post("/api/licensing/v1/activations", data=json.dumps(bad_body), content_type="application/json")
    assert resp.status_code == 400

    good_key = make_device_keypair()
    good_body = build_activation_body(good_key, full_key=full_key, installation_id="conc-dev-good", platform="WINDOWS")
    resp2 = client.post("/api/licensing/v1/activations", data=json.dumps(good_body), content_type="application/json")
    assert resp2.status_code == 200  # the failed attempt did not consume the only slot
