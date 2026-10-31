"""Owner <-> the shop app's REAL licensing client, over real HTTP.

The shop app's own tests run against a FAKE Owner (commercial_runtime/
licensing_contracts/tests/fake_owner.py in the feat/first-run worktree); this
Owner's tests run against hand-written requests. Each side can be green while
they disagree about a field name or a status code. This file removes the middle
man: the product's actual `LicensingClient` + `account_flow` run in a
SUBPROCESS (so nothing of the product is imported here) against THIS Owner
served on a real socket, doing the whole new-customer sequence:

    sign in (wrong password x5 -> locked, Retry-After read) ->
    sign in -> choose a password (policy refusal read, then accepted, fresh ticket) ->
    accept the contract (hash verified by the client) -> activate with the ticket ->
    verify the signed lease with the product's own verifier.

Skipped (loudly) when the product worktree is not on this machine
(set AURA_FIRST_RUN_WORKTREE).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading

import pytest
from sqlalchemy import select
from werkzeug.serving import make_server

from tests.test_account_activation import CONTRACT_BODY, NEW_PASSWORD, world  # noqa: F401 -- world is a fixture
from tests.test_customer_accounts_service import GOOD
from tests.test_customer_onboarding import kyc  # noqa: F401 -- a fixture the world needs

PRODUCT_ROOT = os.environ.get(
    "AURA_FIRST_RUN_WORKTREE",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "first-run")),
)

pytestmark = pytest.mark.skipif(
    not os.path.isfile(os.path.join(PRODUCT_ROOT, "commercial_runtime", "licensing_contracts", "account_flow.py")),
    reason=f"product worktree with the account flow not found at {PRODUCT_ROOT} (set AURA_FIRST_RUN_WORKTREE)",
)

SCRIPT = textwrap.dedent('''
    import base64, json, sys, tempfile
    from datetime import datetime, timezone
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from commercial_runtime.licensing_contracts import account_flow
    from commercial_runtime.licensing_contracts.account_flow import AccountFlowError
    from commercial_runtime.licensing_contracts.assertion_verifier import verify_assertion
    from commercial_runtime.licensing_contracts.client import AccountTicket, LicensingClient, LicensingClientConfig
    from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore

    case = json.load(sys.stdin)
    key = Ed25519PrivateKey.generate()

    class Signer:
        def sign(self, canonical_bytes):
            return base64.b64encode(key.sign(canonical_bytes)).decode("ascii")
        def get_public_key_b64(self):
            raw = key.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
            return base64.b64encode(raw).decode("ascii")

    signer = Signer()
    client = LicensingClient(LicensingClientConfig(base_url=case["base_url"], max_retries=0, verify_tls=False))
    common = dict(client=client, signer=signer, product_code="AURA_RETAIL", platform="WINDOWS", app_version="1.0.0-rc.17")
    out = {}

    def attempt(name, fn):
        try:
            out[name] = {"ok": True, "value": fn()}
        except AccountFlowError as exc:
            out[name] = {"ok": False, "reason": exc.reason_code, "retry_after": exc.retry_after_seconds, "policy": exc.policy_code}

    # 1. wrong passwords: one public failure, then the lockout with its Retry-After
    for n in range(5):
        attempt(f"bad{n}", lambda: account_flow.sign_in(email=case["email"], password="not-the-password-1", **common))
    attempt("locked", lambda: account_flow.sign_in(email=case["email"], password=case["password"], **common))
    sys.stdout.write(json.dumps(out)); sys.stdout.flush()
''')

SCRIPT_HAPPY = textwrap.dedent('''
    import base64, json, sys, tempfile
    from datetime import datetime, timezone
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from commercial_runtime.licensing_contracts import account_flow
    from commercial_runtime.licensing_contracts.account_flow import AccountFlowError
    from commercial_runtime.licensing_contracts.assertion_verifier import verify_assertion
    from commercial_runtime.licensing_contracts.client import AccountTicket, LicensingClient, LicensingClientConfig
    from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore

    case = json.load(sys.stdin)
    key = Ed25519PrivateKey.generate()

    class Signer:
        def sign(self, canonical_bytes):
            return base64.b64encode(key.sign(canonical_bytes)).decode("ascii")
        def get_public_key_b64(self):
            raw = key.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
            return base64.b64encode(raw).decode("ascii")

    signer = Signer()
    client = LicensingClient(LicensingClientConfig(base_url=case["base_url"], max_retries=0, verify_tls=False))
    common = dict(client=client, signer=signer, product_code="AURA_RETAIL", platform="WINDOWS", app_version="1.0.0-rc.17")
    out = {}

    signed_in = account_flow.sign_in(email=case["email"], password=case["password"], **common)
    out["sign_in"] = {
        "password_change_required": signed_in.password_change_required,
        "licences": [c.public_view() for c in signed_in.licences],
        "contract": {"required": signed_in.contract.required, "version_id": signed_in.contract.version_id,
                     "lawyer_approved": signed_in.contract.lawyer_approved, "sha256": signed_in.contract.sha256},
        "seller_name": signed_in.seller_name, "expires_at": signed_in.expires_at,
    }
    ticket = signed_in.ticket
    try:
        account_flow.change_password(ticket=ticket, new_password=case["weak_password"], **common)
        out["policy"] = None
    except AccountFlowError as exc:
        out["policy"] = {"reason": exc.reason_code, "policy_code": exc.policy_code}
    fresh = account_flow.change_password(ticket=ticket, new_password=case["new_password"], **common)
    out["fresh_ticket"] = bool(fresh) and fresh != ticket
    ticket = fresh or ticket
    account_flow.accept_contract(ticket=ticket, contract=signed_in.contract, **common)   # raises on any refusal
    out["accepted"] = True
    choice = signed_in.licences[0]
    response = client.activate(
        product_code="AURA_RETAIL", platform="WINDOWS", app_version="1.0.0-rc.17", release_channel=None,
        installation_id="client-inst-1", device_public_key_b64=signer.get_public_key_b64(), idempotency_key="idem-1",
        signer=signer, credential=AccountTicket(ticket, choice.license_public_id))
    out["activation"] = {"result": response.get("result"), "reason_code": response.get("reason_code"),
                         "has_assertion": bool(response.get("signed_assertion")), "installation_id": response.get("installation_id")}
    store = OwnerTrustStore(Path(tempfile.mkdtemp()) / "trust.json")
    store.bootstrap_from_anchor({"keys": [{"key_id": case["key_id"], "public_key": case["public_key"], "algorithm": "ed25519"}]})
    import hashlib
    verified = verify_assertion(
        response["signed_assertion"], trust_store=store, expected_product_code="AURA_RETAIL", expected_platform="WINDOWS",
        expected_installation_id=response["installation_id"],
        expected_device_key_fingerprint=hashlib.sha256(base64.b64decode(signer.get_public_key_b64())).hexdigest(),
        trusted_now=datetime.now(timezone.utc))
    out["lease"] = {"subscription_status": verified.evidence.subscription_status, "term_end": str(verified.evidence.term_end)}
    # a second use of the same ticket must be refused by Owner and read as ACTIVATION_GRANT_INVALID
    again = client.activate(
        product_code="AURA_RETAIL", platform="WINDOWS", app_version="1.0.0-rc.17", release_channel=None,
        installation_id="client-inst-2", device_public_key_b64=signer.get_public_key_b64(), idempotency_key="idem-2",
        signer=signer, credential=AccountTicket(ticket, choice.license_public_id))
    out["reuse"] = {"result": again.get("result"), "reason_code": again.get("reason_code")}
    sys.stdout.write(json.dumps(out)); sys.stdout.flush()
''')


class _LiveOwner:
    def __init__(self, app):
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}/api/licensing/v1"

    def __exit__(self, *exc):
        self.server.shutdown()
        self.thread.join(timeout=10)


def _run(script, case):
    done = subprocess.run([sys.executable, "-I", "-c", script, PRODUCT_ROOT], input=json.dumps(case), capture_output=True,
                          text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout)


def _case(app, world, base_url, **extra):
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing_service import SigningKey

        key = db_session.execute(select(SigningKey).where(SigningKey.status == "ACTIVE")).scalars().one()
        return {"base_url": base_url, "email": world["email"], "password": GOOD, "key_id": key.key_id,
                "public_key": key.public_key, **extra}


def test_the_real_client_runs_the_whole_sequence_against_this_owner(app, world):
    with _LiveOwner(app) as base_url:
        out = _run(SCRIPT_HAPPY, _case(app, world, base_url, new_password=NEW_PASSWORD,
                                       weak_password=world["email"].split("@")[0] + "-12345"))
    assert out["sign_in"]["password_change_required"] is True
    (licence,) = out["sign_in"]["licences"]
    assert licence["license_public_id"] == world["licence_id"] and licence["trial"]["state"] == "NOT_STARTED"
    assert out["sign_in"]["contract"] == {"required": True, "version_id": world["version_id"], "lawyer_approved": True,
                                          "sha256": __import__("hashlib").sha256(CONTRACT_BODY.encode()).hexdigest()}
    assert out["sign_in"]["seller_name"]
    assert out["policy"] == {"reason": "PASSWORD_POLICY_FAILED", "policy_code": "contains_personal"}  # Owner's code, read by the client
    assert out["fresh_ticket"] is True and out["accepted"] is True
    assert out["activation"]["result"] == "SUCCESS" and out["activation"]["has_assertion"]
    assert out["lease"]["subscription_status"] == "PILOT"  # the product's own verifier accepted Owner's signature
    assert out["reuse"]["result"] != "SUCCESS" and out["reuse"]["reason_code"] == "ACTIVATION_GRANT_INVALID"


def test_the_real_client_reads_a_failed_sign_in_and_the_lockout(app, world):
    with _LiveOwner(app) as base_url:
        out = _run(SCRIPT, _case(app, world, base_url))
    for n in range(5):
        assert out[f"bad{n}"] == {"ok": False, "reason": "ACCOUNT_SIGN_IN_FAILED", "retry_after": None, "policy": None}
    assert out["locked"]["ok"] is False and out["locked"]["reason"] == "RATE_LIMITED"
    assert isinstance(out["locked"]["retry_after"], int) and out["locked"]["retry_after"] >= 1  # Retry-After header read
