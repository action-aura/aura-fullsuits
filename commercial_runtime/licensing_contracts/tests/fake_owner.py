"""A FAKE Owner for the new customer flow (account sign-in), as a tiny Flask app.

Implements ONLY the S5 contract the product client is written against (see
progress.md, 'the exact API contract'), plus the existing /activations and
/signing-keys that activation needs, so the REAL client code runs end to end
over real HTTP -- signing, JSON, status codes, no retry on 429 -- against
something that behaves the way the contract says Owner does:

  * every signed request's device signature is VERIFIED (a client that signed
    the wrong bytes, or left the password out of the signature, is refused);
  * sign-in: one public failure ACCOUNT_SIGN_IN_FAILED (401); 5 failures ->
    429 + Retry-After; the ticket is bound to the device fingerprint, single
    use for activation;
  * password change: policy (>=10 chars), clears must_change;
  * contract acceptance: must name the current version and its exact sha256;
  * activation by {account_ticket, license_public_id}: refused with
    ACTIVATION_GRANT_INVALID unless the ticket is valid, bound to THIS device,
    for a licence on the account and password change is done;
    CONTRACT_ACCEPTANCE_REQUIRED until the contract is accepted;
    TRIAL_REQUIRES_UPDATE for an app older than 1.0.0-rc.17 on a trial licence.

It is a test double, not Owner: it exists so tests can fail when the CLIENT is
wrong. It is deliberately strict about the things the client must get right.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
from datetime import datetime, timedelta, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes

DEFAULT_CONTRACT_BODY_AR = (
    "عقد استخدام تطبيق أورا\n\n"
    "1. يلتزم العميل بشروط الاستخدام.\n"
    "2. فترة التجربة سبعة أيام.\n"
    "3. تبقى البيانات للعميل دائماً.\n"
)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _b64_pub(private_key) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def _version_tuple(v: str):
    """'1.0.0-rc.16' -> (1,0,0,16); good enough for the rc.17 gate."""
    import re
    nums = [int(x) for x in re.findall(r"\d+", v or "")]
    return tuple(nums)


class FakeOwner:
    def __init__(self, *, lawyer_approved: bool = True):
        self.owner_key = Ed25519PrivateKey.generate()
        self.key_id = "owner-test-1"
        self.contract = {
            "version_id": "contract-v1",
            "language": "ar",
            "title": "عقد الاستخدام",
            "body": DEFAULT_CONTRACT_BODY_AR,
            "lawyer_approved": lawyer_approved,
        }
        # email -> account dict
        self.accounts: dict = {}
        self.tickets: dict = {}
        self.failures: dict = {}
        self.acceptances: list = []
        self.activations: list = []
        self.requests: list = []   # (path, body) every signed request received, signature already verified
        self.hits: list = []       # every path that reached the server at all (even refused/limited ones)
        self.bad_signatures = 0    # a CLIENT bug counter: signed over the wrong bytes
        self.tamper_sha = False    # serve a contract body whose sha256 does not match
        self.lock = threading.Lock()
        self.max_failures = 5
        self.app = Flask("fake-owner")
        self._routes()
        self._server = None
        self._thread = None
        self.port = None

    # -- set-up helpers -----------------------------------------------------
    def add_account(self, email, password, *, must_change=True, licences=None, seller=None):
        self.accounts[email.lower()] = {
            "password": password,
            "must_change": must_change,
            "licences": licences if licences is not None else [
                {"license_public_id": "lic-1", "product_code": "AURA_RETAIL", "plan_code": "P1",
                 "branch_label": "Main shop", "trial": {"state": "TRIAL", "ends_on": "2026-11-09"}}],
            "seller": seller or {"name": "Sami Seller", "phone": "+962780069485"},
        }

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/api/licensing/v1"

    def trust_anchor_json(self) -> dict:
        return {"keys": [{"key_id": self.key_id, "public_key": _b64_pub(self.owner_key), "algorithm": "ed25519"}]}

    def hit_count(self, name: str) -> int:
        return self.hits.count(name)

    def start(self):
        self._server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._thread.join(timeout=5)

    # -- internals ----------------------------------------------------------
    def _verified_body(self):
        """The JSON body, with the device signature checked over the canonical
        bytes of everything else. Returns (body, error_response)."""
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return None, (jsonify({"reason_code": "INVALID_REQUEST"}), 400)
        signature = body.get("signature")
        unsigned = {k: v for k, v in body.items() if k != "signature"}
        try:
            pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(unsigned.get("device_public_key", "")))
            pub.verify(base64.b64decode(signature or ""), canonicalize_bytes(unsigned))
        except (InvalidSignature, ValueError, TypeError):
            self.bad_signatures += 1
            return None, (jsonify({"reason_code": "INVALID_SIGNATURE"}), 401)
        self.requests.append((request.path, unsigned))
        return unsigned, None

    @staticmethod
    def _fingerprint(device_public_key_b64: str) -> str:
        return hashlib.sha256(base64.b64decode(device_public_key_b64)).hexdigest()

    def _new_ticket(self, email, fingerprint):
        ticket = "tk_" + secrets.token_urlsafe(24)
        self.tickets[ticket] = {
            "email": email, "fingerprint": fingerprint, "used": False, "contract_accepted": False,
            "expires": datetime.now(timezone.utc) + timedelta(minutes=10),
        }
        return ticket

    def _ticket_for(self, body):
        t = self.tickets.get(body.get("ticket") or body.get("account_ticket") or "")
        if not t or t["used"] or t["expires"] < datetime.now(timezone.utc):
            return None
        if t["fingerprint"] != self._fingerprint(body["device_public_key"]):
            return None
        return t

    def _routes(self):
        app = self.app
        base = "/api/licensing/v1"

        @app.before_request
        def _count():
            self.hits.append(request.path.rsplit("/", 1)[-1])

        @app.route(base + "/service-info")
        def service_info():
            return jsonify({"capabilities": ["account_activation", "activation_code", "contract_v1", "trial_v1"]})

        @app.route(base + "/signing-keys")
        def signing_keys():
            return jsonify({"schema_version": 1, "keys": []})

        @app.route(base + "/account-sign-ins", methods=["POST"])
        def sign_in():
            body, err = self._verified_body()
            if err:
                return err
            email = str(body.get("email", "")).lower()
            with self.lock:
                fails = self.failures.get(email, 0)
                if fails >= self.max_failures:
                    resp = jsonify({"reason_code": "RATE_LIMITED"})
                    resp.status_code = 429
                    resp.headers["Retry-After"] = "120"
                    return resp
                acct = self.accounts.get(email)
                if not acct or acct["password"] != body.get("password"):
                    self.failures[email] = fails + 1
                    return jsonify({"reason_code": "ACCOUNT_SIGN_IN_FAILED"}), 401
                self.failures[email] = 0
                ticket = self._new_ticket(email, self._fingerprint(body["device_public_key"]))
            c = self.contract
            return jsonify({
                "ticket": ticket,
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                "password_change_required": acct["must_change"],
                "licences": acct["licences"],
                "contract": {"required": True, "version_id": c["version_id"], "language": c["language"],
                             "title": c["title"], "body": c["body"], "sha256": sha256_hex(c["body"] + ("x" if self.tamper_sha else "")),
                             "lawyer_approved": c["lawyer_approved"]},
                "seller": acct["seller"],
            })

        @app.route(base + "/account-password-changes", methods=["POST"])
        def password_change():
            body, err = self._verified_body()
            if err:
                return err
            t = self._ticket_for(body)
            if not t:
                return jsonify({"reason_code": "ACCOUNT_SIGN_IN_FAILED"}), 401
            new = body.get("new_password") or ""
            if len(new) < 10:
                return jsonify({"reason_code": "PASSWORD_POLICY_FAILED", "policy_code": "too_short"}), 400
            acct = self.accounts[t["email"]]
            if new == acct["password"]:
                return jsonify({"reason_code": "PASSWORD_POLICY_FAILED", "policy_code": "same_as_old"}), 400
            acct["password"] = new
            acct["must_change"] = False
            return jsonify({}), 200

        @app.route(base + "/contract-acceptances", methods=["POST"])
        def contract_acceptance():
            body, err = self._verified_body()
            if err:
                return err
            t = self._ticket_for(body)
            if not t:
                return jsonify({"reason_code": "ACCOUNT_SIGN_IN_FAILED"}), 401
            c = self.contract
            if (body.get("contract_version_id") != c["version_id"]
                    or body.get("text_sha256") != sha256_hex(c["body"])):
                return jsonify({"reason_code": "CONTRACT_ACCEPTANCE_REQUIRED"}), 409
            t["contract_accepted"] = True
            self.acceptances.append({"email": t["email"], "version": body["contract_version_id"],
                                     "sha256": body["text_sha256"], "language": body.get("language"),
                                     "fingerprint": t["fingerprint"]})
            return jsonify({"accepted": True}), 201

        @app.route(base + "/activations", methods=["POST"])
        def activations():
            body, err = self._verified_body()
            if err:
                return err
            given = [k for k in ("license_key", "account_ticket", "activation_code") if body.get(k)]
            if len(given) != 1:
                return jsonify({"result": "REJECTED", "reason_code": "INVALID_REQUEST"}), 400
            fingerprint = self._fingerprint(body["device_public_key"])
            if given[0] != "account_ticket":
                # The legacy / code paths are out of scope for this fake beyond
                # recording that they arrived with the right shape.
                self.activations.append({"kind": given[0]})
                if given[0] == "license_key" and body["license_key"] == "AURA-RETAIL-GOOD":
                    return self._success(body, fingerprint, "lic-legacy")
                if given[0] == "activation_code" and body["activation_code"] == "CODE-GOOD":
                    return self._success(body, fingerprint, "lic-code")
                return jsonify({"result": "REJECTED", "reason_code": "ACTIVATION_GRANT_INVALID"
                                if given[0] == "activation_code" else "ACTIVATION_REJECTED"}), 400
            with self.lock:
                t = self._ticket_for(body)
                acct = self.accounts.get(t["email"]) if t else None
                lic_ids = {l["license_public_id"] for l in acct["licences"]} if acct else set()
                if (not t or body.get("license_public_id") not in lic_ids or acct["must_change"]):
                    return jsonify({"result": "REJECTED", "reason_code": "ACTIVATION_GRANT_INVALID"}), 400
                if not t["contract_accepted"]:
                    return jsonify({"result": "REJECTED", "reason_code": "CONTRACT_ACCEPTANCE_REQUIRED"}), 400
                licence = next(l for l in acct["licences"] if l["license_public_id"] == body["license_public_id"])
                if (licence.get("trial") and _version_tuple(body.get("app_version")) < _version_tuple("1.0.0-rc.17")):
                    return jsonify({"result": "REJECTED", "reason_code": "TRIAL_REQUIRES_UPDATE"}), 400
                t["used"] = True
            self.activations.append({"kind": "account_ticket", "license_public_id": body["license_public_id"],
                                     "email": t["email"]})
            return self._success(body, fingerprint, body["license_public_id"])

    def _success(self, body, fingerprint, license_public_id):
        now = datetime.now(timezone.utc)
        installation_id = "owner-inst-" + secrets.token_hex(4)
        payload = {
            "assertion_id": "a-" + secrets.token_hex(4), "issuer": "aura-owner",
            "product_code": body["product_code"], "license_public_id": license_public_id,
            "installation_public_id": installation_id, "platform": body["platform"],
            "app_version_policy": "1.0.0-rc.2", "release_channel": "rc",
            "issued_at": now.isoformat(), "not_before": (now - timedelta(minutes=5)).isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(),
            "license_status": "ACTIVE", "installation_status": "ACTIVE", "subscription_status": "ACTIVE",
            "allowed_device_count": 2, "device_key_fingerprint": fingerprint,
            "entitlements": {"max_devices": 2},
            "offline_policy": {
                "check_in_interval_seconds": 86400, "retry_interval_seconds": 3600,
                "offline_grace_seconds": 1209600, "warning_start_seconds": 864000,
                "hard_expiry_behavior": "RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA",
                "clock_rollback_tolerance_seconds": 300, "assertion_refresh_threshold_seconds": 86400},
            "contract_version": "v1",
        }
        sig = self.owner_key.sign(canonicalize_bytes(payload))
        envelope = {"payload": payload, "signing_key_id": self.key_id, "algorithm": "ed25519",
                    "assertion_version": 1, "signature": base64.b64encode(sig).decode("ascii")}
        return jsonify({"result": "SUCCESS", "installation_id": installation_id, "signed_assertion": envelope})
