"""Runs the FAKE Owner (commercial_runtime/licensing_contracts/tests/fake_owner.py) as a stand-alone
process for the real-browser E2E (retail_account_flow_e2e.py).

WHY A PROCESS. The E2E driver runs under the SYSTEM Python (Playwright lives there) while the fake
needs the repo venv (Flask + cryptography), the same split retail_smoke_e2e.py already lives with.

Usage: fake_owner_server.py <out_dir> <email> <temp_password> [--approved 0|1]
Writes <out_dir>/port.txt (when listening) and <out_dir>/anchor.json (the trust anchor the product
must be pointed at). Debug routes, test-only (they exist ONLY in this stand-alone fake):
  GET  /__state          -> {acceptances, activations, requests:[{path, keys}], bad_signatures, hits}
  POST /__set {"approved": bool}  -> flips whether the contract counts as lawyer-approved
  POST /__account {email, password, must_change} -> sets that account's state
`requests` carries the KEYS of every signed body, never its values, so the E2E can prove what the
product sent without this fake having to hold anything secret in a log.
"""
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from flask import jsonify, request  # noqa: E402

from commercial_runtime.licensing_contracts.tests.fake_owner import FakeOwner  # noqa: E402


def main():
    out = Path(sys.argv[1])
    email, temp_password = sys.argv[2], sys.argv[3]
    approved = "--approved" in sys.argv and sys.argv[sys.argv.index("--approved") + 1] == "1"
    out.mkdir(parents=True, exist_ok=True)

    fake = FakeOwner(lawyer_approved=approved)
    # Long enough that it cannot fit the modal without scrolling: the scroll-to-end gate is only
    # testable on text that really overflows.
    fake.contract["body"] = fake.contract["body"] + "".join(
        f"{n + 4}. بند تجريبي رقم {n} من عقد الاستخدام، ويجب قراءته حتى النهاية قبل الموافقة.\n"
        for n in range(70))
    # rc.19: the REAL launch plan code (the rehearsal screen printed it raw), no plan_name -- the
    # older-Owner case, so the screen must map the code itself.
    fake.add_account(email, temp_password, must_change=True, licences=[
        {"license_public_id": "lic-1", "product_code": "AURA_RETAIL", "plan_code": "AURA_RETAIL_P1_YEARLY",
         "branch_label": None, "trial": {"state": "TRIAL", "ends_on": "2026-11-09"}}])

    @fake.app.route("/__state")
    def state():
        return jsonify({
            "acceptances": fake.acceptances,
            "activations": fake.activations,
            "requests": [{"path": p, "keys": sorted(b.keys())} for p, b in fake.requests],
            "bad_signatures": fake.bad_signatures,
            "hits": fake.hits,
        })

    @fake.app.route("/__set", methods=["POST"])
    def set_state():
        body = request.get_json(silent=True) or {}
        if "approved" in body:
            fake.contract["lawyer_approved"] = bool(body["approved"])
        return jsonify({"ok": True})

    @fake.app.route("/__account", methods=["POST"])
    def set_account():
        body = request.get_json(silent=True) or {}
        acct = fake.accounts[body["email"].lower()]
        acct["password"] = body["password"]
        acct["must_change"] = bool(body.get("must_change"))
        return jsonify({"ok": True})

    fake.start()
    (out / "anchor.json").write_text(json.dumps(fake.trust_anchor_json()), encoding="utf-8")
    (out / "port.txt").write_text(str(fake.port), encoding="utf-8")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
