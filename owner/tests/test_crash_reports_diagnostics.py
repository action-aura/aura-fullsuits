"""SUPPORT-1 (Owner side): diagnostics snapshots on the crash-report channel and
the 6-character reference code.

Why each test is shaped as it is (ENGINEERING 1): the list-filter tests pair
every "found" with a "not found" for a different code in the same page, so a
filter that is simply ignored cannot pass; the code-agreement test imports the
TILL's function and compares it with Owner's copy, because two copies that
merely look alike are exactly how a code would stop finding its report.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path
from types import SimpleNamespace

from tests.conftest import force_login, make_staff
from tests.test_crash_reports import _activate, _body, _report, _row_dicts, _send

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from commercial_runtime.crash_reports import capture  # noqa: E402
from commercial_runtime.crash_reports.reference_code import reference_code as till_code  # noqa: E402
from commercial_runtime.crash_reports.sender import CrashReportSender  # noqa: E402
from commercial_runtime.crash_reports.store import CrashStore  # noqa: E402

from app.crash_reports.reference_code import ALPHABET, fingerprint_range, reference_code  # noqa: E402

DIAG_FP = "0123456789abcdef0123456789abcdef"      # code 26KNCT (derived by hand on the till side)


def _diag(**over):
    return _report(fingerprint=DIAG_FP, kind="diagnostics", exception_type="DiagnosticsSnapshot",
                   message="snapshot v1 schema 52 licence ACTIVE_ONLINE", frames=[], route=None, count=1, **over)


def _admin(app, client):
    force_login(client, app, make_staff(app, "admin@example.com", super_admin=True))


# -- the code ----------------------------------------------------------------
def test_owner_and_till_compute_the_same_code_for_any_fingerprint():
    rng = random.Random(7)
    samples = ["0" * 32, "f" * 32, DIAG_FP] + ["%032x" % rng.getrandbits(128) for _ in range(500)]
    for fp in samples:
        assert reference_code(fp) == till_code(fp), fp
    assert reference_code(DIAG_FP) == "26KNCT"
    assert ALPHABET == "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def test_a_code_maps_back_to_a_range_that_contains_its_fingerprint_and_only_its_siblings():
    rng = random.Random(11)
    for _ in range(300):
        fp = "%032x" % rng.getrandbits(128)
        low, high = fingerprint_range(reference_code(fp))
        assert low <= fp <= high
        assert reference_code(low) == reference_code(high) == reference_code(fp)
    # the neighbours on either side belong to other codes
    low, high = fingerprint_range("26KNCT")
    assert reference_code("%032x" % (int(low, 16) - 1)) != "26KNCT"
    assert reference_code("%032x" % (int(high, 16) + 1)) != "26KNCT"


def test_a_typed_code_is_forgiving_about_case_and_spaces_but_never_guesses():
    assert fingerprint_range(" 26knct ") == fingerprint_range("26KNCT")
    for bad in ["", "26KNC", "26KNCTT", "26KNC0", "26KNCO", "26KNC1", "26KNCI", "26KN-T", None, 123]:
        assert fingerprint_range(bad) is None, bad


# -- intake ------------------------------------------------------------------
def test_a_signed_diagnostics_snapshot_is_accepted_and_stored_as_that_kind(app, client, seeded, signing_key):
    _lic, installation_id, key = _activate(app, client)
    resp = _send(client, _body(key, installation_id, [_diag()]))
    assert resp.status_code == 200 and resp.get_json()["accepted"] == 1
    rows = _row_dicts(app)
    assert [r["fingerprint"] for r in rows] == [DIAG_FP]
    from sqlalchemy import select
    from app.extensions import db_session
    from app.models.crash_reports import CrashReport
    with app.app_context():
        assert db_session.execute(select(CrashReport.kind)).scalar_one() == "diagnostics"


def test_an_unsigned_or_altered_diagnostics_submission_is_refused_and_stores_nothing(app, client, seeded, signing_key):
    _lic, installation_id, key = _activate(app, client)
    body = _body(key, installation_id, [_diag()])
    body["reports"][0]["message"] = "tampered after signing"
    resp = _send(client, body)
    assert resp.status_code == 400 and resp.get_json()["reason_code"] == "INVALID_SIGNATURE"
    unsigned = {k: v for k, v in _body(key, installation_id, [_diag()]).items() if k != "signature"}
    assert _send(client, unsigned).status_code == 400
    assert _row_dicts(app) == [], "a refused snapshot must leave no row"
    # positive control: the same route takes the genuine one
    assert _send(client, _body(key, installation_id, [_diag()])).status_code == 200


def test_what_the_till_really_produces_is_accepted_by_owner(app, client, seeded, signing_key, tmp_path):
    """Cross-stack: the till's own snapshot builder + the sender's own wire
    shaping, signed, into Owner's real intake."""
    _lic, installation_id, key = _activate(app, client)
    store = CrashStore(tmp_path / "c.db")
    report = capture.build_diagnostics_report(
        {"retail_schema_version": 52, "licence": {"state": "ACTIVE_ONLINE"}, "row_counts": {"sales": 9}},
        app_version="1.0.0-rc.8", platform="WINDOWS")
    store.record(report)
    sender = CrashReportSender(store, lambda: None, lambda: None,
                               lambda: SimpleNamespace(load=lambda: SimpleNamespace(owner_installation_id="x")),
                               "AURA_RETAIL", "1.0.0-rc.8", "WINDOWS")
    wire = sender._payload_for(store.pending(5))
    assert wire[0]["kind"] == "diagnostics"
    assert _send(client, _body(key, installation_id, wire)).status_code == 200
    assert [r["fingerprint"] for r in _row_dicts(app)] == [report["fingerprint"]]


# -- the staff page ----------------------------------------------------------
def _seed_mixed(app, client, signing_key):
    _lic, installation_id, key = _activate(app, client)
    other = "9" * 32
    assert _send(client, _body(key, installation_id, [_diag(), _report(fingerprint=other)])).status_code == 200
    return other


def test_the_list_labels_diagnostics_and_searches_by_code(app, client, seeded, signing_key):
    other = _seed_mixed(app, client, signing_key)
    _admin(app, client)
    html = client.get("/crash-reports").get_data(as_text=True)
    assert "26KNCT" in html and reference_code(other) in html, "every row shows its code"
    assert "crash-kind-diagnostics" in html, "diagnostics is labelled distinctly"

    hit = client.get("/crash-reports?q=26KNCT").get_data(as_text=True)
    assert "DiagnosticsSnapshot" in hit and "ValueError" not in hit
    assert "DiagnosticsSnapshot" in client.get("/crash-reports?q=26knct").get_data(as_text=True)
    miss = client.get("/crash-reports?q=" + ("ZZZZZZ" if reference_code(other) != "ZZZZZZ" else "222222")).get_data(as_text=True)
    assert "DiagnosticsSnapshot" not in miss and "ValueError" not in miss
    for not_a_code in ("26KNC", "26KNC0", "%25"):
        junk = client.get("/crash-reports?q=" + not_a_code).get_data(as_text=True)
        assert "DiagnosticsSnapshot" not in junk and "ValueError" not in junk, not_a_code
    by_other = client.get("/crash-reports?q=" + reference_code(other)).get_data(as_text=True)
    assert "ValueError" in by_other and "DiagnosticsSnapshot" not in by_other


def test_the_kind_filter_splits_snapshots_from_crashes(app, client, seeded, signing_key):
    _seed_mixed(app, client, signing_key)
    _admin(app, client)
    diag = client.get("/crash-reports?kind=diagnostics").get_data(as_text=True)
    crash = client.get("/crash-reports?kind=crashes").get_data(as_text=True)
    both = client.get("/crash-reports?kind=nonsense").get_data(as_text=True)
    assert "DiagnosticsSnapshot" in diag and "ValueError" not in diag
    assert "ValueError" in crash and "DiagnosticsSnapshot" not in crash
    assert "ValueError" in both and "DiagnosticsSnapshot" in both


def test_detail_page_shows_the_code_and_search_still_needs_the_permission(app, client, seeded, signing_key):
    _seed_mixed(app, client, signing_key)
    sales = app.test_client()
    force_login(sales, app, make_staff(app, "sales@example.com", role_codes=["SALES"]))
    assert sales.get("/crash-reports?q=26KNCT").status_code == 403
    _admin(app, client)
    detail = client.get("/crash-reports/" + DIAG_FP).get_data(as_text=True)
    assert "26KNCT" in detail
