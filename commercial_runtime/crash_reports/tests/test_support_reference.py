"""SUPPORT-1: the reference code, the diagnostics snapshot, and how the sender ships it.

Every guard here has a mutation proof run against it (quoted in the commit).
"""
import json
import re
from types import SimpleNamespace

import pytest
from flask import Flask

from commercial_runtime.crash_reports import capture
from commercial_runtime.crash_reports.reference_code import ALPHABET, reference_code
from commercial_runtime.crash_reports.sender import CrashReportSender
from commercial_runtime.crash_reports.store import CrashStore

FP = "0123456789abcdef0123456789abcdef"


# -- the code ---------------------------------------------------------------
def test_code_is_six_chars_from_the_unambiguous_alphabet():
    code = reference_code(FP)
    assert len(code) == 6 and set(code) <= set(ALPHABET)
    assert not set("01OI") & set(ALPHABET), "0, O, 1 and I are the characters people misread"
    assert len(set(ALPHABET)) == 32


def test_code_is_stable_and_pinned():
    # A pinned value, not a re-derivation: Owner's copy of the function must
    # produce exactly this too (owner test_crash_reports_reference_code.py).
    assert reference_code(FP) == reference_code(FP) == "26KNCT"  # derived by hand: 0x01234567>>2 = 00000 00100 10001 10100 01010 11001


def test_code_follows_the_fingerprint_and_ignores_the_unused_tail():
    assert reference_code("f" * 32) == "ZZZZZZ"
    assert reference_code("0" * 32) == "222222"
    assert reference_code(FP[:8] + "0" * 24) == reference_code(FP)
    assert reference_code("1" + FP[1:]) != reference_code(FP)


@pytest.mark.parametrize("bad", [None, "", "xyz", "G" * 32, FP.upper(), FP[:-1], 12345])
def test_malformed_fingerprint_never_becomes_a_plausible_code(bad):
    assert reference_code(bad) is None


# -- the snapshot -----------------------------------------------------------
EXPORT = {
    "retail_schema_version": 52, "licence": {"state": "ACTIVE_ONLINE"},
    "row_counts": {"sales": 12345, "products": 87, "customers": 3, "users": 2},
    "sync_health": {"state": "OK"},
    "log_tail": ["GET /x for Ahmad Al-Khalidi ahmad@shop.example"],
    "platform": "Windows-11 C:\\Users\\bob\\x",
}


def test_snapshot_is_kind_diagnostics_and_carries_only_the_whitelisted_facts():
    report = capture.build_diagnostics_report(EXPORT, app_version="1.2.3", platform="WINDOWS")
    assert report["kind"] == "diagnostics" and report["exception_type"] == "DiagnosticsSnapshot"
    assert report["frames"] == [] and report["route"] is None
    wire = json.dumps(report)
    assert "licence ACTIVE_ONLINE" in report["message"] and "schema 52" in report["message"]
    assert "sales 12k" in report["message"], "4+ digit counts are shortened, not blanked to <n>"
    for leaked in ("Ahmad", "shop.example", "bob", "log_tail", "GET /x"):
        assert leaked not in wire
    assert re.fullmatch(r"[0-9a-f]{32}", report["fingerprint"])
    assert len(report["message"]) <= 300


def test_free_text_in_a_state_field_is_refused_not_forwarded():
    hostile = dict(EXPORT, licence={"state": "ok Ahmad@x.com 0791234567 and more text"})
    report = capture.build_diagnostics_report(hostile, app_version="1", platform="WINDOWS")
    assert "Ahmad" not in report["message"] and "0791234567" not in report["message"]
    assert "licence ?" in report["message"]


def test_garbage_facts_still_make_a_valid_snapshot():
    for junk in (None, [], "x", {"row_counts": "no", "licence": 5}):
        report = capture.build_diagnostics_report(junk, app_version="1", platform="WINDOWS")
        assert report["kind"] == "diagnostics" and report["message"]


def test_each_press_gets_its_own_fingerprint_and_so_its_own_code(tmp_path):
    a = capture.build_diagnostics_report(EXPORT, app_version="1", platform="W")
    b = capture.build_diagnostics_report(EXPORT, app_version="1", platform="W")
    assert a["fingerprint"] != b["fingerprint"] and reference_code(a["fingerprint"]) != reference_code(b["fingerprint"])
    store = CrashStore(tmp_path / "c.db")
    store.record(a)
    store.record(b)
    assert len(store.all_rows()) == 2, "two presses must be two rows, not one counted twice"
    pinned = capture.build_diagnostics_report(EXPORT, app_version="1", platform="W", nonce="n")
    assert pinned["fingerprint"] == capture.build_diagnostics_report(EXPORT, app_version="1", platform="W", nonce="n")["fingerprint"]


# -- the sender ships snapshots in their own request -------------------------
class FakeClient:
    def __init__(self, refuse_diagnostics=False):
        self.calls = []
        self.refuse = refuse_diagnostics

    def send_crash_reports(self, **kw):
        self.calls.append(kw)
        if self.refuse and any(r["kind"] == "diagnostics" for r in kw["reports"]):
            return {"result": "REJECTED", "reason_code": "INVALID_REQUEST"}
        return {"result": "ACCEPTED", "accepted": len(kw["reports"])}


def _sender(store, client):
    return CrashReportSender(
        store, lambda: client, lambda: object(),
        lambda: SimpleNamespace(load=lambda: SimpleNamespace(owner_installation_id="inst")),
        "AURA_RETAIL", "1.2.3", "WINDOWS")


def _crash(store, i=1):
    store.record({"fingerprint": "%032x" % i, "kind": "server", "exception_type": "ValueError", "message": "m",
                  "frames": [{"file": "x.py", "function": "f", "line": 1}], "route": "/r",
                  "app_version": "1.2.3", "platform": "WINDOWS"})


def test_snapshot_goes_in_its_own_request_after_the_crashes(tmp_path):
    store = CrashStore(tmp_path / "c.db")
    store.record(capture.build_diagnostics_report(EXPORT, app_version="1.2.3", platform="WINDOWS"))
    _crash(store)
    client = FakeClient()
    assert _sender(store, client).run_once() == "SENT"
    kinds = [[r["kind"] for r in call["reports"]] for call in client.calls]
    assert kinds == [["server"], ["diagnostics"]]
    assert store.summary()["pending"] == 0


def test_an_owner_that_refuses_snapshots_does_not_hold_back_real_crashes(tmp_path):
    store = CrashStore(tmp_path / "c.db")
    store.record(capture.build_diagnostics_report(EXPORT, app_version="1.2.3", platform="WINDOWS"))
    _crash(store)
    client = FakeClient(refuse_diagnostics=True)
    assert _sender(store, client).run_once() == "REFUSED"
    pending = store.pending(10)
    assert [r["kind"] for r in pending] == ["diagnostics"], "the crash was delivered; only the snapshot waits"


# -- the code header on a 500 ------------------------------------------------
def _app(store):
    app = Flask(__name__)

    @app.route("/boom")
    def boom():
        raise ValueError("x")

    @app.route("/fine")
    def fine():
        return "ok"

    handle = capture.install(app, store, app_version="1.2.3", platform="WINDOWS")
    return app, handle


def test_a_500_carries_the_code_of_the_report_that_was_saved(tmp_path):
    store = CrashStore(tmp_path / "c.db")
    app, handle = _app(store)
    try:
        resp = app.test_client().get("/boom")
        assert resp.status_code == 500
        row = store.all_rows()[-1]
        assert resp.headers[capture.REPORT_CODE_HEADER] == reference_code(row["fingerprint"])
        assert capture.REPORT_CODE_HEADER not in app.test_client().get("/fine").headers
    finally:
        handle.remove()


def test_no_code_header_when_the_report_could_not_be_saved(tmp_path):
    class Broken(CrashStore):
        def record(self, report):
            raise RuntimeError("disk full")

    app, handle = _app(Broken(tmp_path / "c.db"))
    try:
        resp = app.test_client().get("/boom")
        assert resp.status_code == 500 and capture.REPORT_CODE_HEADER not in resp.headers
    finally:
        handle.remove()
