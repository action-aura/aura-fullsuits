"""Generates android/aura-retail/app/src/test/resources/authority_cross_language.json.

AUDIT-033 / S-AUTH (D3, Android). One fixture, read by BOTH languages:
  * Python (test_sync_authority_android.py) checks the file is still what Python
    would produce today, and that a signature of the Kotlin shape verifies in
    `authority.verify_event`;
  * Kotlin (AuthoritySigningTest) signs the same tbs with the same fixed seed
    and must produce the identical signature (Ed25519 is deterministic).

Run once (the lease inside carries a random assertion id, so re-running changes
the file and the Kotlin constants move with it -- they live in the file, not in
code):
    .venv/Scripts/python.exe commercial_runtime/sync/tests/_make_authority_cross_fixture.py
"""
import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commercial_runtime.sync import authority
from commercial_runtime.sync.tests._signing import LICENCE, make_lease

OUT = Path(__file__).resolve().parents[3] / "android" / "aura-retail" / "app" / "src" / "test" / "resources" / "authority_cross_language.json"
DEVICE_SEED = bytes(range(1, 33))
OWNER_SEED = bytes(range(101, 133))
DEV = "11111111-2222-4333-8444-555555555555"
INST = "inst-cross-language"


def _raw_pub(key) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def build(attest: bool = True) -> dict:
    """`attest=False` is the pre-fix shape (a lease without `registry_device_id`);
    only the negative test uses it -- the committed file is always attested."""
    device_key = Ed25519PrivateKey.from_private_bytes(DEVICE_SEED)
    owner_key = Ed25519PrivateKey.from_private_bytes(OWNER_SEED)
    pk = _raw_pub(device_key)
    # AUDIT-033 fix round (verifier V finding #1): a receiver trusts a signer only
    # when its Owner-signed lease attests the registry device id the event
    # claims (`device_id_not_attested` otherwise). Owner pins and echoes that id;
    # the fixture models Owner, so the lease names DEV.
    lease = make_lease(owner_key, licence=LICENCE, inst=INST, fingerprint=hashlib.sha256(pk).hexdigest(),
                       registry_device_id=DEV if attest else None)
    signer = authority.LocalSigner(licence=LICENCE, inst=INST, dev=DEV,
                                   pk_b64=base64.b64encode(pk).decode(), lease=lease, sign_fn=None)
    event = {"id": "evt-1", "entity_type": "till_allocation", "entity_id": "alloc-1",
             "event_type": "update", "created_at": "2026-10-01 10:00:00",
             "payload": {"terminal_uid": DEV, "epoch": 2, "ratio": 2.0, "half": 1.5,
                         "big": 9007199254740993, "note": "caf\u00e9"}}
    prepared, tbs = authority.prepare_unsigned_auth(event, signer)
    sig = base64.b64encode(device_key.sign(tbs)).decode()
    return {
        "device_seed_b64": base64.b64encode(DEVICE_SEED).decode(),
        "owner_pub_b64": base64.b64encode(_raw_pub(owner_key)).decode(),
        "licence": LICENCE, "inst": INST, "dev": DEV,
        "device_pub_b64": base64.b64encode(pk).decode(),
        "page_event": dict(prepared, auth_tbs_b64=base64.b64encode(tbs).decode()),
        "tbs_b64": base64.b64encode(tbs).decode(),
        "sig_b64": sig,
    }


if __name__ == "__main__":
    OUT.write_bytes((json.dumps(build(), indent=1, ensure_ascii=True) + "\n").encode())
    print("wrote", OUT)
