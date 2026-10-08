"""/api/crash-reports/*: who may report, how much, and what lands on disk.

Both directions of each guard are proved, and where a guard's job is to keep
something OUT of the store the test counts the store's rows, because a status
code alone cannot tell "the guard refused" from "something else refused".
"""
import json

import pytest
from flask import Flask

from commercial_runtime.crash_reports.routes import (
    ClientReportLimiter,
    make_crash_reports_blueprint,
)
from commercial_runtime.crash_reports.store import CrashStore

URL = "/api/crash-reports/client"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def build(tmp_path, *, sending=False, limiter=None, with_store=True):
    store = CrashStore(tmp_path / "crash.db") if with_store else None
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(make_crash_reports_blueprint(
        store, app_version="1.2.3", platform="WINDOWS", sending_enabled=sending, limiter=limiter))
    return app, store


def login(client, role="cashier"):
    with client.session_transaction() as s:
        s["mt_user_id"] = "u1"
        s["mt_role"] = role


def post(client, **fields):
    body = {"message": "m", "stack": "", "screen": "sales"}
    body.update(fields)
    return client.post(URL, data=json.dumps(body), content_type="application/json")


def test_signed_out_is_401_and_nothing_is_recorded(tmp_path):
    app, store = build(tmp_path)
    r = post(app.test_client())
    assert r.status_code == 401
    assert store.all_rows() == []


def test_signed_in_gets_an_empty_204_and_the_row_is_scrubbed(tmp_path):
    app, store = build(tmp_path)
    c = app.test_client()
    login(c)
    stack = ("TypeError: x\n    at renderRow (http://127.0.0.1:5000/static/subsystem-retail.js?customer=Ahmad:812:19)")
    r = post(c, message=r"bad 'Ahmad Al-Khalidi' ahmad@shop.example 0791234567 C:\Users\bob\AppData\x", stack=stack,
             screen="sales")
    assert r.status_code == 204 and r.get_data() == b""
    rows = store.all_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "client" and row["route"] == "sales" and row["exception_type"] == "TypeError"
    assert row["app_version"] == "1.2.3" and row["platform"] == "WINDOWS"
    assert row["frames"] == [{"file": "subsystem-retail.js", "function": "renderRow", "line": 812}]
    for leaked in ("Ahmad", "shop.example", "0791234567", "bob", "AppData", "127.0.0.1"):
        assert leaked not in json.dumps(row), leaked


def test_a_location_href_is_never_stored_as_the_route(tmp_path):
    app, store = build(tmp_path)
    c = app.test_client()
    login(c)
    post(c, screen="http://127.0.0.1:5000/#/sales?customer=Ahmad")
    assert store.all_rows()[0]["route"] is None


def test_an_oversize_body_is_413_and_nothing_is_recorded(tmp_path):
    app, store = build(tmp_path)
    c = app.test_client()
    login(c)
    # The literal 16 KB, NOT the module's MAX_BODY_BYTES: comparing the guard
    # against its own constant would pass for any value the constant takes.
    r = post(c, message="x" * (16 * 1024 + 10))
    assert r.status_code == 413
    assert store.all_rows() == []
    # the allow-half: a body just under the cap is accepted
    ok = post(c, message="y" * 2000)
    assert ok.status_code == 204 and len(store.all_rows()) == 1


def test_the_twenty_first_report_in_ten_minutes_is_dropped_with_a_204(tmp_path):
    clock = Clock()
    limiter = ClientReportLimiter(clock=clock)
    app, store = build(tmp_path, limiter=limiter)
    c = app.test_client()
    login(c)
    for i in range(20):
        assert post(c, message="distinct error %d" % i, stack="Error\n    at f%d (http://h/static/a.js:1:1)" % i).status_code == 204
    assert len(store.all_rows()) == 20
    over = post(c, message="the 21st", stack="Error\n    at last (http://h/static/a.js:1:1)")
    assert over.status_code == 204 and over.get_data() == b""
    assert len(store.all_rows()) == 20, "an over-limit report must be DROPPED, not recorded"
    # the allow-half: once the window has passed, reporting works again
    clock.now += 601
    assert post(c, message="after the window", stack="Error\n    at later (http://h/static/a.js:1:1)").status_code == 204
    assert len(store.all_rows()) == 21


def test_malformed_bodies_are_400_and_record_nothing(tmp_path):
    app, store = build(tmp_path)
    c = app.test_client()
    login(c)
    assert c.post(URL, data="not json", content_type="application/json").status_code == 400
    assert c.post(URL, data="[1,2]", content_type="application/json").status_code == 400
    assert store.all_rows() == []


def test_a_store_that_raises_still_answers_204(tmp_path, monkeypatch):
    app, store = build(tmp_path)
    attempts = []
    monkeypatch.setattr(store, "record", lambda report: attempts.append(1) or (_ for _ in ()).throw(RuntimeError("x")))
    c = app.test_client()
    login(c)
    assert post(c).status_code == 204
    assert attempts == [1]


def test_with_no_store_reporting_is_a_quiet_204(tmp_path):
    app, _ = build(tmp_path, with_store=False)
    c = app.test_client()
    login(c)
    assert post(c).status_code == 204


# -- /summary ----------------------------------------------------------------
def test_summary_requires_an_administrator(tmp_path):
    app, store = build(tmp_path)
    c = app.test_client()
    assert c.get("/api/crash-reports/summary").status_code == 401
    login(c, "cashier")
    assert c.get("/api/crash-reports/summary").status_code == 403
    login(c, "admin")
    r = c.get("/api/crash-reports/summary")
    assert r.status_code == 200
    assert r.get_json() == {"pending": 0, "sent": 0, "last_sent_at": None, "enabled": False}


@pytest.mark.parametrize("sending", [True, False])
def test_summary_reports_whether_sending_is_enabled_and_the_counts(tmp_path, sending):
    app, store = build(tmp_path, sending=sending)
    c = app.test_client()
    login(c, "admin")
    post(c)
    body = c.get("/api/crash-reports/summary").get_json()
    assert body["pending"] == 1 and body["enabled"] is sending
