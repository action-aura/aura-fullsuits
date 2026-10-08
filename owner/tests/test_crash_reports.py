"""Crash reports -- Owner side: intake API, upsert, rejections, staff page, migration.

Real HTTP-level tests in the style of test_release_discovery.py: real
activation, real device signature, real rows. Why the assertions look the way
they do (ENGINEERING 1): a 400 cannot tell "validation refused it" from "the
route never ran", so every rejection test pairs with a positive control (the
same route ACCEPTED a valid batch in the same test) and asserts the table is
still empty of the rejected data -- and the retry test asserts on the stored
COUNT, because "200 both times" would pass whether or not it double-counted.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, select, text

from app.crash_reports import intake
from app.extensions import db_session
from app.models.crash_reports import CrashReport
from app.models.licensing import License
from tests.conftest import (
    TEST_DB_URL,
    build_activation_body,
    force_login,
    make_device_keypair,
    make_license,
    make_staff,
    sign_body,
)

URL = "/api/licensing/v1/crash-reports"
FP_A = "a" * 32
FP_B = "b" * 32


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _activate(app, client, email="crash@example.com"):
    actor_id = make_staff(app, email)
    license_id, full_key = make_license(app, actor_id)
    private_key = make_device_keypair()
    resp = client.post(
        "/api/licensing/v1/activations",
        data=json.dumps(build_activation_body(private_key, full_key=full_key, installation_id="crash-dev")),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return license_id, resp.get_json()["installation_id"], private_key


def _report(**over):
    now = datetime.now(timezone.utc)
    base = {
        "fingerprint": FP_A, "kind": "server", "exception_type": "ValueError", "message": "boom <n> '…'",
        "frames": [{"file": "products/retail/backend/api/retail_api.py", "function": "close_sale", "line": 812},
                   {"file": "app.py", "function": "wsgi_app", "line": 1}],
        "route": "/api/sub/retail/sales/<int:sale_id>", "app_version": "1.0.0-rc.8", "platform": "WINDOWS",
        "first_seen_at": (now - timedelta(hours=2)).isoformat(), "last_seen_at": now.isoformat(), "count": 3,
    }
    base.update(over)
    return base


def _body(private_key, installation_id, reports, **overrides):
    body = {
        "contract_version": "v1", "request_id": str(uuid_mod.uuid4()), "correlation_id": str(uuid_mod.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(), "nonce": uuid_mod.uuid4().hex,
        "installation_id": installation_id, "app_version": "1.0.0-rc.8", "platform": "WINDOWS",
        "reports": reports,
    }
    body.update(overrides)
    return sign_body(private_key, body)


def _send(client, body):
    return client.post(URL, data=json.dumps(body), content_type="application/json")


def _rows(app):
    with app.app_context():
        return db_session.execute(select(CrashReport).order_by(CrashReport.fingerprint)).scalars().all()


def _row_dicts(app):
    with app.app_context():
        return [
            {"fingerprint": r.fingerprint, "count": r.count, "first": r.first_seen_at, "last": r.last_seen_at,
             "version": r.app_version, "message": r.message, "frames": r.frames, "route": r.route,
             "product_code": r.product_code, "installation_id": str(r.installation_id), "license_id": str(r.license_id)}
            for r in db_session.execute(select(CrashReport).order_by(CrashReport.fingerprint)).scalars().all()
        ]


# --------------------------------------------------------------------------
# accept / upsert
# --------------------------------------------------------------------------
def test_a_valid_batch_is_accepted_and_stored_exactly_as_scrubbed(app, client, seeded, signing_key):
    license_id, installation_id, key = _activate(app, client)
    resp = _send(client, _body(key, installation_id, [_report(), _report(fingerprint=FP_B, kind="client", route="sales", frames=[])]))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["result"] == "ACCEPTED" and resp.get_json()["accepted"] == 2

    rows = _row_dicts(app)
    assert [r["fingerprint"] for r in rows] == [FP_A, FP_B]
    first = rows[0]
    assert first["count"] == 3 and first["message"] == "boom <n> '…'"
    assert first["frames"][0] == {"file": "products/retail/backend/api/retail_api.py", "function": "close_sale", "line": 812}
    assert first["route"] == "/api/sub/retail/sales/<int:sale_id>"
    assert first["product_code"] == "AURA_CLINIC" and first["installation_id"] == installation_id
    assert first["license_id"] == str(license_id)


def test_unknown_keys_in_a_report_or_frame_are_never_stored(app, client, seeded, signing_key):
    _license, installation_id, key = _activate(app, client)
    sneaky = _report(frames=[{"file": "a.py", "function": "f", "line": 1, "locals": {"customer": "Ahmad"}}],
                     customer_name="Ahmad Al-Khalidi")
    assert _send(client, _body(key, installation_id, [sneaky])).status_code == 200
    stored = _row_dicts(app)[0]
    assert stored["frames"] == [{"file": "a.py", "function": "f", "line": 1}]
    assert "Ahmad" not in json.dumps({k: str(v) for k, v in stored.items()})


def test_the_same_bug_upserts_and_a_retried_identical_batch_does_not_double_count(app, client, seeded, signing_key):
    _license, installation_id, key = _activate(app, client)
    now = datetime.now(timezone.utc)
    early, late = (now - timedelta(days=2)).isoformat(), now.isoformat()

    assert _send(client, _body(key, installation_id, [_report(count=3, first_seen_at=early, last_seen_at=late)])).status_code == 200
    # The retry: the response was lost, so the product sends the SAME running total again.
    retry = [_report(count=3, first_seen_at=early, last_seen_at=late)]
    assert _send(client, _body(key, installation_id, retry)).status_code == 200
    rows = _row_dicts(app)
    assert len(rows) == 1, "one row per (installation, fingerprint, version)"
    assert rows[0]["count"] == 3, "a retried batch must NOT add its count to the stored one"

    # A genuine increase moves the count, last_seen only forward, first_seen only back.
    later = (now + timedelta(hours=1)).isoformat()
    earlier = (now - timedelta(days=5)).isoformat()
    assert _send(client, _body(key, installation_id, [_report(count=9, first_seen_at=earlier, last_seen_at=later)])).status_code == 200
    rows = _row_dicts(app)
    assert rows[0]["count"] == 9
    assert rows[0]["last"] == datetime.fromisoformat(later) and rows[0]["first"] == datetime.fromisoformat(earlier)

    # A stale (smaller / older) report never moves anything backwards.
    assert _send(client, _body(key, installation_id, [_report(count=4, first_seen_at=late, last_seen_at=early)])).status_code == 200
    rows = _row_dicts(app)
    assert rows[0]["count"] == 9
    assert rows[0]["last"] == datetime.fromisoformat(later) and rows[0]["first"] == datetime.fromisoformat(earlier)


def test_a_new_app_version_of_the_same_bug_is_its_own_row(app, client, seeded, signing_key):
    _license, installation_id, key = _activate(app, client)
    assert _send(client, _body(key, installation_id, [_report(), _report(app_version="1.0.1")])).status_code == 200
    assert sorted(r["version"] for r in _row_dicts(app)) == ["1.0.0-rc.8", "1.0.1"]


# --------------------------------------------------------------------------
# rejections -- each one paired with a positive control and an empty table
# --------------------------------------------------------------------------
def _control_accepts(app, client, installation_id, key):
    """The route works: a valid batch is ACCEPTED (so a later 4xx is the guard talking)."""
    assert _send(client, _body(key, installation_id, [_report(fingerprint="c" * 32)])).status_code == 200
    assert len(_rows(app)) == 1


@pytest.mark.parametrize("label,mutate", [
    ("21 reports", lambda: [_report(fingerprint="%032x" % i) for i in range(21)]),
    ("0 reports", lambda: []),
    ("message of 301 characters", lambda: [_report(message="x" * 301)]),
    ("bad kind", lambda: [_report(kind="desktop")]),
    ("fingerprint not hex", lambda: [_report(fingerprint="z" * 32)]),
    ("fingerprint wrong length", lambda: [_report(fingerprint="a" * 31)]),
    ("fingerprint upper-case", lambda: [_report(fingerprint="A" * 32)]),
    ("count 0", lambda: [_report(count=0)]),
    ("count 100001", lambda: [_report(count=100001)]),
    ("count is a bool", lambda: [_report(count=True)]),
    ("31 frames", lambda: [_report(frames=[{"file": "a", "function": "f", "line": 1}] * 31)]),
    ("frame file of 201 characters", lambda: [_report(frames=[{"file": "f" * 201, "function": "f", "line": 1}])]),
    ("frame missing function", lambda: [_report(frames=[{"file": "a.py", "line": 1}])]),
    ("not a list", lambda: "nope"),
    ("one bad report among good ones", lambda: [_report(), _report(fingerprint="d" * 32, kind="bad")]),
    ("timestamp unparseable", lambda: [_report(first_seen_at="yesterday")]),
])
def test_invalid_batches_are_refused_whole_and_store_nothing(app, client, seeded, signing_key, label, mutate):
    _license, installation_id, key = _activate(app, client)
    _control_accepts(app, client, installation_id, key)
    before = _row_dicts(app)
    resp = _send(client, _body(key, installation_id, mutate()))
    assert resp.status_code == 400, (label, resp.get_data(as_text=True))
    assert resp.get_json()["reason_code"] == "INVALID_REQUEST"
    assert _row_dicts(app) == before, "%s: a refused request must not change the table" % label


def test_a_bad_signature_is_refused(app, client, seeded, signing_key):
    _license, installation_id, key = _activate(app, client)
    _control_accepts(app, client, installation_id, key)
    body = _body(key, installation_id, [_report(fingerprint="e" * 32)])
    body["reports"][0]["count"] = 99999        # altered after signing
    resp = _send(client, body)
    assert resp.status_code == 400 and resp.get_json()["reason_code"] == "INVALID_SIGNATURE"
    assert [r["fingerprint"] for r in _row_dicts(app)] == ["c" * 32]


def test_a_signature_from_another_device_is_refused(app, client, seeded, signing_key):
    _license, installation_id, key = _activate(app, client)
    _control_accepts(app, client, installation_id, key)
    stranger = make_device_keypair()
    resp = _send(client, _body(stranger, installation_id, [_report(fingerprint="e" * 32)]))
    assert resp.status_code == 400 and resp.get_json()["reason_code"] == "INVALID_SIGNATURE"
    assert len(_rows(app)) == 1


def test_a_replayed_request_is_refused_and_counts_once(app, client, seeded, signing_key):
    _license, installation_id, key = _activate(app, client)
    body = _body(key, installation_id, [_report(count=5)])
    first = _send(client, body)
    assert first.status_code == 200
    replay = _send(client, body)                 # byte-identical: same nonce
    assert replay.status_code == 400
    assert replay.get_json()["reason_code"] != "ACCEPTED" and replay.get_json()["result"] == "FAILURE"
    assert _row_dicts(app)[0]["count"] == 5


def test_a_suspended_licence_is_refused(app, client, seeded, signing_key):
    license_id, installation_id, key = _activate(app, client)
    _control_accepts(app, client, installation_id, key)
    with app.app_context():
        lic = db_session.get(License, license_id)
        lic.status = "SUSPENDED"
        db_session.commit()
    resp = _send(client, _body(key, installation_id, [_report(fingerprint="e" * 32)]))
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] != "ACCEPTED" and resp.get_json()["result"] == "FAILURE"
    assert [r["fingerprint"] for r in _row_dicts(app)] == ["c" * 32]


def test_an_unknown_installation_is_refused(app, client, seeded, signing_key):
    _license, _installation_id, key = _activate(app, client)
    resp = _send(client, _body(key, str(uuid_mod.uuid4()), [_report()]))
    assert resp.status_code == 400 and _rows(app) == []


def test_the_route_is_rate_limited(app, client, seeded, signing_key):
    # Invalid JSON is cheap and still counts against the bucket: 30 pass
    # through to the 400 (proving the handler really ran each time), the 31st
    # is 429 and never reaches the handler.
    codes = []
    for _ in range(31):
        r = client.post(URL, data="{not json", content_type="application/json")
        codes.append(r.status_code)
    assert codes[:30] == [400] * 30 and codes[30] == 429


# --------------------------------------------------------------------------
# staff page
# --------------------------------------------------------------------------
def _seed_rows(app, specs):
    """Inserts CrashReport rows directly: specs = [(fingerprint, installation_index, version, count, exc, frames)]."""
    from app.models.catalog import Platform, Product
    from app.models.customers import Customer
    from app.models.installations import Installation

    with app.app_context():
        product = db_session.execute(select(Product).where(Product.product_code == "AURA_CLINIC")).scalars().first()
        platform = db_session.execute(select(Platform).where(Platform.platform_code == "WINDOWS")).scalars().first()
        customer = Customer(legal_name="Page Test Co")
        db_session.add(customer)
        db_session.flush()
        installations = []
        for i in range(2):
            inst = Installation(customer_id=customer.id, product_id=product.id, platform_id=platform.id,
                                first_registered_at=datetime.now(timezone.utc), status="ACTIVE")
            db_session.add(inst)
            installations.append(inst)
        db_session.flush()
        now = datetime.now(timezone.utc)
        for n, (fp, inst_idx, version, count, exc, frames) in enumerate(specs):
            db_session.add(CrashReport(
                installation_id=installations[inst_idx].id, product_code="AURA_CLINIC", platform="WINDOWS",
                app_version=version, fingerprint=fp, kind="server", exception_type=exc, message="msg %d" % n,
                frames=frames, route="/r", first_seen_at=now - timedelta(days=1), last_seen_at=now - timedelta(minutes=n),
                count=count))
        db_session.commit()


FRAMES = [{"file": "retail_api.py", "function": "close_sale", "line": 9}]


def test_staff_page_requires_the_permission_403_without_200_with(app, client, seeded):
    _seed_rows(app, [(FP_A, 0, "1.0.0", 7, "ValueError", FRAMES)])
    sales_id = make_staff(app, "sales@example.com", role_codes=["SALES"])      # has catalog.view, NOT system.view
    admin_id = make_staff(app, "admin@example.com", super_admin=True)

    anonymous = client.get("/crash-reports")
    assert anonymous.status_code in (302, 401)

    force_login(client, app, sales_id)
    assert client.get("/crash-reports").status_code == 403
    assert client.get("/crash-reports/" + FP_A).status_code == 403

    admin_client = app.test_client()
    force_login(admin_client, app, admin_id)
    listing = admin_client.get("/crash-reports")
    assert listing.status_code == 200
    assert "ValueError" in listing.get_data(as_text=True)
    assert admin_client.get("/crash-reports/" + FP_A).status_code == 200


def test_staff_page_groups_by_fingerprint_and_sums_counts_and_installs(app, client, seeded):
    _seed_rows(app, [
        (FP_A, 0, "1.0.0", 7, "ValueError", FRAMES),
        (FP_A, 1, "1.0.1", 5, "ValueError", FRAMES),
        (FP_B, 0, "1.0.0", 3, "KeyError", [{"file": "x.py", "function": "other_fn", "line": 2}]),
    ])
    admin_id = make_staff(app, "admin@example.com", super_admin=True)
    force_login(client, app, admin_id)
    html = client.get("/crash-reports").get_data(as_text=True)
    assert html.count("ValueError") == 1 and html.count("KeyError") == 1, "one row per bug, not per install"
    assert "retail_api.py:close_sale" in html and "x.py:other_fn" in html
    assert "<td>12</td>" in html, "7 + 5 occurrences summed across the two installs"
    assert "<td>2</td>" in html, "two installations hit it"
    assert "1.0.0, 1.0.1" in html
    assert html.index("ValueError") < html.index("KeyError"), "newest first"

    detail = client.get("/crash-reports/" + FP_A).get_data(as_text=True)
    assert "close_sale" in detail and detail.count("<bdi dir=\"ltr\">1.0.1</bdi>") == 1 and "1.0.0" in detail
    assert client.get("/crash-reports/" + "f" * 32).status_code == 404
    assert client.get("/crash-reports/not-a-fingerprint").status_code == 404


def test_staff_page_paginates_by_50(app, client, seeded):
    _seed_rows(app, [("%032x" % i, 0, "1.0.0", 1, "Err%02d" % i, FRAMES) for i in range(51)])
    admin_id = make_staff(app, "admin@example.com", super_admin=True)
    force_login(client, app, admin_id)
    page1 = client.get("/crash-reports").get_data(as_text=True)
    page2 = client.get("/crash-reports?page=2").get_data(as_text=True)
    assert page1.count("<bdi dir=\"ltr\">Err") == 50
    assert page2.count("<bdi dir=\"ltr\">Err") == 1
    assert "Err50" in page2 and "Err50" not in page1, "the oldest row is the one on page 2"


def test_the_nav_links_crash_reports_only_for_those_who_may_open_it(app, client, seeded):
    sales_id = make_staff(app, "sales@example.com", role_codes=["SALES"])
    admin_id = make_staff(app, "admin@example.com", super_admin=True)
    force_login(client, app, sales_id)
    sales_html = client.get("/").get_data(as_text=True)
    admin_client = app.test_client()
    force_login(admin_client, app, admin_id)
    admin_html = admin_client.get("/").get_data(as_text=True)
    assert 'href="/crash-reports"' in admin_html
    assert 'href="/crash-reports"' not in sales_html
    assert 'href="/catalog"' in sales_html or "Catalog" in sales_html, "the sales page really rendered a nav"


# --------------------------------------------------------------------------
# retention
# --------------------------------------------------------------------------
def test_prune_deletes_only_reports_not_seen_for_90_days(app, client, seeded):
    edge = "c" * 32
    _seed_rows(app, [(FP_A, 0, "1.0.0", 1, "Old", FRAMES), (FP_B, 0, "1.0.0", 1, "Fresh", FRAMES),
                     (edge, 0, "1.0.0", 1, "JustInsideTheWindow", FRAMES)])
    with app.app_context():
        for fingerprint, age_days in ((FP_A, 91), (edge, 89)):
            row = db_session.execute(select(CrashReport).where(CrashReport.fingerprint == fingerprint)).scalar_one()
            row.last_seen_at = datetime.now(timezone.utc) - timedelta(days=age_days)
        db_session.commit()
        assert intake.prune_old_reports(dry_run=True) == 1
        assert len(db_session.execute(select(CrashReport)).scalars().all()) == 3, "dry run deletes nothing"
        assert intake.prune_old_reports(dry_run=False) == 1
        remaining = sorted(r.fingerprint for r in db_session.execute(select(CrashReport)).scalars().all())
    assert remaining == sorted([FP_B, edge]), "the 89-day-old report is inside the 90-day window and must survive"


# --------------------------------------------------------------------------
# migration
# --------------------------------------------------------------------------
def test_the_crash_reports_migration_upgrades_and_downgrades_cleanly():
    owner_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    scratch_db = "aura_owner_crash_reports_migration_scratch"
    scratch_url = TEST_DB_URL.rsplit("/", 1)[0] + "/" + scratch_db
    admin_url = TEST_DB_URL.rsplit("/", 1)[0] + "/postgres"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS %s" % scratch_db))
        conn.execute(text("CREATE DATABASE %s OWNER aura_owner" % scratch_db))
    admin_engine.dispose()
    env = {**os.environ, "OWNER_DATABASE_URL": scratch_url}

    def alembic(*args):
        return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=owner_dir, env=env, capture_output=True, text=True)

    try:
        before = alembic("upgrade", "7c41d2e9a0b5")
        assert before.returncode == 0, before.stderr
        engine = create_engine(scratch_url)
        assert "owner_crash_reports" not in inspect(engine).get_table_names(), "the table must not exist before the migration"
        engine.dispose()

        up = alembic("upgrade", "head")
        assert up.returncode == 0, up.stderr
        engine = create_engine(scratch_url)
        insp = inspect(engine)
        assert "owner_crash_reports" in insp.get_table_names()
        uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("owner_crash_reports")}
        assert ("installation_id", "fingerprint", "app_version") in uniques
        indexes = {i["name"] for i in insp.get_indexes("owner_crash_reports")}
        assert {"ix_owner_crash_reports_fingerprint", "ix_owner_crash_reports_last_seen_at"} <= indexes
        engine.dispose()

        down = alembic("downgrade", "7c41d2e9a0b5")
        assert down.returncode == 0, down.stderr
        engine = create_engine(scratch_url)
        assert "owner_crash_reports" not in inspect(engine).get_table_names()
        assert "owner_installations" in inspect(engine).get_table_names(), "downgrade must touch only this table"
        engine.dispose()

        again = alembic("upgrade", "head")
        assert again.returncode == 0, again.stderr
        heads = alembic("heads")
        assert heads.returncode == 0 and heads.stdout.count("(head)") == 1, "exactly one migration head: " + heads.stdout
    finally:
        admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS %s" % scratch_db))
        admin_engine.dispose()
