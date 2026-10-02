"""AUDIT-033 fix round (verifier V finding #1) across REAL processes: an insider
cannot speak as the main computer by winning the first signer binding.

Ported from the verifier's `test_v_multiprocess_binding.py` (RED on fafaf7cc: the
insider's password signed the OWNER in on the joining computer). Same scenario,
unmodified product code, harness from the repo:

  Computers: A (founder, main computer), B (a licensed INSIDER), C (joins later).
  1. A builds the shop's history unsigned (old build).
  2. BEFORE A upgrades, insider B -- with ONLY its own key and its own Owner lease
     (`_insider_forge.py`, which runs against B's own app data) -- re-signs A's own
     admin_device claim and the owner's row (new password hash) naming A's
     device id, and pushes them.
  3. A upgrades for real (app._announce_device -> signed re-announce) and pushes.
  4. C joins and pulls everything from cursor 0 through the real SyncService.

The harness models Owner's pin (`_accept_device._provision_signed_identity`
attests each app-data's OWN local_device_uuid), so B's lease attests B and only
B: it cannot vouch for A's id. SECURE outcome, asserted below:

  * the insider's password does NOT sign the owner in on C, the real one does;
  * C's registry quarantine holds BOTH forged events with the reason
    `device_id_not_attested` (the check ran and refused them -- not merely
    "something refused them");
  * C's `sync_signers` binds A's device id to A's OWN installation, and the
    insider's installation is bound to nothing it did not legitimately speak as.

Run (ONE FILE PER PYTEST PROCESS):
    pytest products/retail/tests/retail_s_insider_binding_multiprocess_test.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SUITE_ROOT = TESTS.parent.parent.parent
sys.path.insert(0, str(TESTS))

from retail_s_fresh_join_after_upgrade_test import Fixture, LICENCE  # noqa: E402

import pytest  # noqa: E402

PY = sys.executable
ATTACKER_PASSWORD = "attacker-pw-123"
NOT_ATTESTED = "device_id_not_attested"


@pytest.fixture
def fx(tmp_path, monkeypatch):
    monkeypatch.delenv("AURA_ACCEPT_SIGNED", raising=False)
    monkeypatch.setenv("AURA_ACCEPT_SIGNED_LICENCE", LICENCE)
    f = Fixture(tmp_path)
    f.build_unsigned_history()
    return f


def login(fx, which, password):
    [r] = fx.shop.run(which, [{"op": "http", "method": "POST", "path": "/api/auth/login",
                               "json": {"email": fx.shop.email, "password": password}}])
    return r["status_code"]


def test_an_insider_cannot_speak_as_the_main_on_a_joining_computer(fx, monkeypatch, tmp_path):
    monkeypatch.setenv("AURA_ACCEPT_SIGNED", "1")
    # B is a real licensed computer: provision its own identity (one process run).
    fx.plant_company_on("b")
    out = tmp_path / "forge.json"
    env = dict(os.environ, PYTHONPATH=str(SUITE_ROOT))
    res = subprocess.run([PY, str(TESTS / "_insider_forge.py"), str(fx.shop.dirs["b"]),
                          str(fx.shop.reg_relays["b"]), fx.shop.ids["a"], str(TESTS), str(out)],
                         env=env, capture_output=True, text=True, timeout=240)
    assert res.returncode == 0, res.stderr[-3000:]
    forge = json.loads(out.read_text())
    assert forge["own_dev"] != fx.shop.ids["a"]
    assert [f[1] for f in forge["forged"]] == [fx.shop.ids["a"]] * 2, \
        f"the insider's forged events do not name the main computer's id: {forge['forged']}"

    fx.upgrade_a(monkeypatch)                  # the real main upgrades and re-announces, signed
    fx.plant_company_on("c")
    fx.pull_both("c")

    # 1. the answer a person would see: only the real password opens the owner's account
    evil = login(fx, "c", ATTACKER_PASSWORD)
    real = login(fx, "c", fx.shop.password)
    assert evil != 200, "the insider's password signs the OWNER in on the joining computer"
    assert real == 200, "the owner's real password no longer works on the joining computer"

    # 2. the check RAN and refused the forgeries (a login refusal could have come from anywhere)
    reg_q, _ = fx.parked("c")
    refused = {(r["entity_type"], r["reason"]) for r in reg_q}
    assert ("admin_device", NOT_ATTESTED) in refused and ("user", NOT_ATTESTED) in refused, sorted(refused)

    # 3. the binding table says A's id belongs to A's OWN installation
    signers = {r["device_id"]: r["installation_id"]
               for r in fx.shop.reg_sel("c", "SELECT device_id, installation_id FROM sync_signers")}
    a_dev = fx.shop.ids["a"]
    assert signers.get(a_dev) == f"inst-{a_dev}", signers
    assert f"inst-{forge['own_dev']}" not in {inst for dev, inst in signers.items() if dev == a_dev}
