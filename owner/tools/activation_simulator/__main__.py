"""Aura Owner -- Product-Side Activation Simulator (Part U).

Independent of Retail and Clinic -- imports nothing from products/* or
commercial_runtime/*. Reuses only Owner's own shared, published contract
primitive (app.licensing_service.canonical) so the simulator signs/verifies
using the EXACT same canonicalization the server uses, rather than risking a
second, subtly-different reimplementation. Everything else (HTTP calls,
signature math, state file) is self-contained here.

THIS IS A SIMULATOR. It uses only synthetic test licenses. It never logs a
full license key by default. Generated device private keys are written only
under owner/tools/activation_simulator/.device/ (gitignored) and are never
committed.

Commands:
    python -m owner.tools.activation_simulator init-device
    python -m owner.tools.activation_simulator activate --license-key <KEY>
    python -m owner.tools.activation_simulator check-in
    python -m owner.tools.activation_simulator verify-assertion
    python -m owner.tools.activation_simulator replay-test
    python -m owner.tools.activation_simulator invalid-signature-test
    python -m owner.tools.activation_simulator deactivate
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import uuid
from datetime import datetime, timezone

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_OWNER_DIR = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
sys.path.insert(0, _OWNER_DIR)

from app.licensing_service.canonical import canonicalize_bytes  # noqa: E402

DEVICE_DIR = os.path.join(_THIS_DIR, ".device")
KEY_PATH = os.path.join(DEVICE_DIR, "device.pem")
STATE_PATH = os.path.join(DEVICE_DIR, "state.json")

_SIMULATOR_BANNER = "[aura-owner activation SIMULATOR -- synthetic test data only]"


def _base_url() -> str:
    return os.environ.get("OWNER_SIMULATOR_BASE_URL", "http://127.0.0.1:5551")


def _load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def _save_state(state: dict) -> None:
    os.makedirs(DEVICE_DIR, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def _load_private_key() -> Ed25519PrivateKey:
    with open(KEY_PATH, "rb") as fh:
        return serialization.load_pem_private_key(fh.read(), password=None)


def _public_key_b64(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def _sign(private_key: Ed25519PrivateKey, body: dict) -> dict:
    signable = {k: v for k, v in body.items() if k != "signature"}
    signature = private_key.sign(canonicalize_bytes(signable))
    return {**body, "signature": base64.b64encode(signature).decode("ascii")}


def _base_fields() -> dict:
    return {
        "contract_version": "v1",
        "request_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "nonce": uuid.uuid4().hex,
    }


def cmd_init_device(args) -> None:
    print(_SIMULATOR_BANNER)
    if os.path.exists(KEY_PATH) and not args.force:
        print(f"Device key already exists at {KEY_PATH} -- pass --force to regenerate (this invalidates the old device identity).")
        return
    os.makedirs(DEVICE_DIR, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(private_key.private_bytes(encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.PKCS8, encryption_algorithm=serialization.NoEncryption()))
    _save_state({"public_key": _public_key_b64(private_key)})
    print(f"Device key pair generated. Private key stored locally at {KEY_PATH} (never uploaded, never logged).")


def cmd_activate(args) -> None:
    print(_SIMULATOR_BANNER)
    private_key = _load_private_key()
    body = _base_fields()
    body.update({
        "product_code": args.product_code,
        "platform": args.platform,
        "app_version": "1.0.0-simulator",
        "installation_id": args.installation_id or f"simulator-{uuid.uuid4().hex[:8]}",
        "device_public_key": _public_key_b64(private_key),
        "device_public_key_algorithm": "ed25519",
        "license_key": args.license_key,
        "idempotency_key": str(uuid.uuid4()),
    })
    signed = _sign(private_key, body)
    resp = requests.post(f"{_base_url()}/api/licensing/v1/activations", json=signed, timeout=15)
    data = resp.json()
    print(f"HTTP {resp.status_code} -- reason_code={data.get('reason_code')} decision={data.get('decision')}")
    if resp.status_code == 200:
        state = _load_state()
        state["installation_id"] = data["installation_id"]
        state["latest_assertion"] = data["signed_assertion"]
        _save_state(state)
        print(f"Activated. installation_id={data['installation_id']}")
    # The full license key is a CLI argument for this one call only -- never
    # written to state.json, never printed again after this line.


def cmd_check_in(args) -> None:
    print(_SIMULATOR_BANNER)
    state = _load_state()
    if "installation_id" not in state:
        print("No installation_id in local state -- run 'activate' first.")
        return
    private_key = _load_private_key()
    body = _base_fields()
    body["installation_id"] = state["installation_id"]
    signed = _sign(private_key, body)
    resp = requests.post(f"{_base_url()}/api/licensing/v1/check-ins", json=signed, timeout=15)
    data = resp.json()
    print(f"HTTP {resp.status_code} -- reason_code={data.get('reason_code')} decision={data.get('decision')}")
    if resp.status_code == 200:
        state["latest_assertion"] = data["signed_assertion"]
        _save_state(state)


def cmd_verify_assertion(args) -> None:
    print(_SIMULATOR_BANNER)
    state = _load_state()
    envelope = state.get("latest_assertion")
    if envelope is None:
        print("No assertion stored locally -- run 'activate' or 'check-in' first.")
        return
    resp = requests.get(f"{_base_url()}/api/licensing/v1/signing-keys", timeout=15)
    keys = {k["key_id"]: k for k in resp.json()["keys"]}
    key_info = keys.get(envelope["signing_key_id"])
    if key_info is None:
        print(f"VERIFY FAILED: signing key {envelope['signing_key_id']} not found in published key set.")
        return
    if key_info["status"] == "REVOKED":
        print("VERIFY FAILED: signing key has been revoked -- never trusted regardless of signature validity.")
        return
    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(key_info["public_key"]))
    canonical_bytes = canonicalize_bytes(envelope["payload"])
    try:
        public_key.verify(base64.b64decode(envelope["signature"]), canonical_bytes)
        now = datetime.now(timezone.utc)
        not_before = datetime.fromisoformat(envelope["payload"]["not_before"])
        expires_at = datetime.fromisoformat(envelope["payload"]["expires_at"])
        if not (not_before <= now <= expires_at):
            print("VERIFY FAILED: signature valid but assertion is outside its validity window.")
            return
        print("VERIFY OK -- signature valid, assertion within its validity window, signing key not revoked.")
        print(json.dumps({k: v for k, v in envelope["payload"].items() if k != "entitlements"}, indent=2))
    except Exception as exc:  # noqa: BLE001 -- simulator diagnostic output only
        print(f"VERIFY FAILED: {exc}")


def cmd_replay_test(args) -> None:
    print(_SIMULATOR_BANNER)
    state = _load_state()
    if "installation_id" not in state:
        print("No installation_id in local state -- run 'activate' first.")
        return
    private_key = _load_private_key()
    body = _base_fields()
    body["installation_id"] = state["installation_id"]
    signed = _sign(private_key, body)

    resp1 = requests.post(f"{_base_url()}/api/licensing/v1/check-ins", json=signed, timeout=15)
    print(f"First send: HTTP {resp1.status_code} -- {resp1.json().get('reason_code')}")
    resp2 = requests.post(f"{_base_url()}/api/licensing/v1/check-ins", json=signed, timeout=15)
    print(f"Replay (identical request): HTTP {resp2.status_code} -- {resp2.json().get('reason_code')}")
    if resp2.status_code == 400 and resp2.json().get("reason_code") == "NONCE_REUSED":
        print("REPLAY PROTECTION CONFIRMED: the second, byte-identical request was rejected.")
    else:
        print("WARNING: replay was not rejected as expected.")


def cmd_invalid_signature_test(args) -> None:
    print(_SIMULATOR_BANNER)
    state = _load_state()
    if "installation_id" not in state:
        print("No installation_id in local state -- run 'activate' first.")
        return
    body = _base_fields()
    body["installation_id"] = state["installation_id"]
    body["signature"] = base64.b64encode(b"\x00" * 64).decode("ascii")  # deliberately garbage
    resp = requests.post(f"{_base_url()}/api/licensing/v1/check-ins", json=body, timeout=15)
    data = resp.json()
    print(f"HTTP {resp.status_code} -- reason_code={data.get('reason_code')}")
    if resp.status_code == 400 and data.get("reason_code") == "INVALID_SIGNATURE":
        print("SIGNATURE VERIFICATION CONFIRMED: a garbage signature was correctly rejected.")
    else:
        print("WARNING: invalid signature was not rejected as expected.")


def cmd_deactivate(args) -> None:
    print(_SIMULATOR_BANNER)
    state = _load_state()
    if "installation_id" not in state:
        print("No installation_id in local state -- nothing to deactivate.")
        return
    private_key = _load_private_key()
    body = _base_fields()
    body["installation_id"] = state["installation_id"]
    body["idempotency_key"] = str(uuid.uuid4())
    signed = _sign(private_key, body)
    resp = requests.post(f"{_base_url()}/api/licensing/v1/deactivations", json=signed, timeout=15)
    data = resp.json()
    print(f"HTTP {resp.status_code} -- reason_code={data.get('reason_code')}")
    if resp.status_code == 200:
        state.pop("latest_assertion", None)
        _save_state(state)


def main() -> None:
    parser = argparse.ArgumentParser(prog="activation_simulator", description=_SIMULATOR_BANNER)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-device", help="Generate a local Ed25519 device key pair (never uploaded).")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init_device)

    p = sub.add_parser("activate", help="Submit an initial activation request.")
    p.add_argument("--license-key", required=True, help="Full synthetic license key (test data only).")
    p.add_argument("--product-code", default="AURA_CLINIC")
    p.add_argument("--platform", default="WINDOWS")
    p.add_argument("--installation-id", default=None)
    p.set_defaults(func=cmd_activate)

    p = sub.add_parser("check-in", help="Submit an authenticated check-in (no license key needed).")
    p.set_defaults(func=cmd_check_in)

    p = sub.add_parser("verify-assertion", help="Verify the locally stored assertion against Owner's published public keys.")
    p.set_defaults(func=cmd_verify_assertion)

    p = sub.add_parser("replay-test", help="Send the same signed check-in twice and confirm the second is rejected.")
    p.set_defaults(func=cmd_replay_test)

    p = sub.add_parser("invalid-signature-test", help="Send a check-in with a garbage signature and confirm rejection.")
    p.set_defaults(func=cmd_invalid_signature_test)

    p = sub.add_parser("deactivate", help="Deactivate the current installation.")
    p.set_defaults(func=cmd_deactivate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
