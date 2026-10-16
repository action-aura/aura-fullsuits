"""SYNC-BOOT Phase 3: the bootstrap mailbox routes, through the REAL activation,
signing and push/pull routes (no hand-inserted installations).

MUTATION PROOFS (each was run for real; the red output is quoted in the commit
message that introduced this file):
  M1 cross-licence check removed -- `_load` stops filtering on license_id
                                 -> test_another_licence_cannot_see_*  red.
  M2 ack does not delete         -- drop `_remove_blob` in bootstrap_ack
                                 -> test_ack_deletes_the_blob_*        red.
  M3 claim race unguarded        -- the conditional UPDATE becomes an
                                 unconditional one -> test_two_producers_*
                                 (sequential AND concurrent) red.
  M4 signature check skipped     -- _authenticate's verify_signature forced True
                                 -> test_a_forged_signature_is_refused red.
  M5 one-open-request rule gone  -- open_or_create_request always inserts
                                 -> test_one_open_request_*            red.
  M6 fulfil before READY allowed -- drop the TOO_EARLY check
                                 -> test_authority_fulfil_*            red.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# The owner test environment does not put the repo root on sys.path; the
# encryption module is shared product code (commercial_runtime/).
_REPO = str(Path(__file__).resolve().parents[2])
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from commercial_runtime.sync.bootstrap import crypto  # noqa: E402
from commercial_runtime.sync.bootstrap.errors import BootstrapError  # noqa: E402

from tests.conftest import make_staff  # noqa: E402
from tests.test_sync_pruning import _do_activation, _make_event, _push, _signed_body  # noqa: E402


@pytest.fixture(autouse=True)
def box(app, tmp_path):
    """Each test gets its own empty blob directory."""
    app.config["SYNC_BOOTSTRAP_DIRECTORY"] = str(tmp_path / "bootstrap")
    return Path(app.config["SYNC_BOOTSTRAP_DIRECTORY"])


def _post(client, key, inst, path, extra=None):
    return client.post(
        f"/api/sync/v1/bootstrap/{path}", data=json.dumps(_signed_body(key, inst, extra or {})),
        content_type="application/json")


def _pull(client, key, inst, since=0, **extra):
    return client.post(
        "/api/sync/v1/pull", data=json.dumps(_signed_body(key, inst, {"since": since, **extra})),
        content_type="application/json")


def _shop(app, client, n=3):
    """n devices on ONE licence -> (license_id, [(installation_id, key), ...])."""
    actor = make_staff(app, f"boot-{uuid.uuid4().hex[:6]}@example.com")
    license_id, full_key, inst, key = _do_activation(app, client, actor, device_limit=n)
    out = [(inst, key)]
    for _ in range(n - 1):
        _, _, i, k = _do_activation(app, client, actor, license_id=license_id, full_key=full_key, device_limit=n)
        out.append((i, k))
    return license_id, out


def _catch_up(client, who, upto=10**6):
    """Each device pulls the retail stream so it has a server-side retail cursor."""
    for inst, key in who:
        assert _pull(client, key, inst, 0, stream="retail").status_code == 200


def _request(client, key, inst, stream="retail", keys=None, **extra):
    keys = keys or crypto.new_requester_keys()
    body = {"stream": stream, "enc_pubkey": keys.public_b64, "schema_version": 56, **extra}
    r = _post(client, key, inst, "request", body)
    assert r.status_code == 200, r.get_json()
    return r.get_json(), keys


def _claim(client, key, inst, rid, schema_version=56):
    return _post(client, key, inst, "claim", {"request_id": rid, "schema_version": schema_version})


def _produce(client, key, inst, rid, keys_pub, plaintext, *, as_of=1):
    """Claim already done: encrypt, upload every chunk, complete."""
    params, chunks = crypto.encrypt_package(plaintext, keys_pub, rid)
    chunks = list(chunks)
    for i, c in enumerate(chunks):
        r = _post(client, key, inst, "chunk", {"request_id": rid, "index": i, "data": base64.b64encode(c).decode()})
        assert r.status_code == 200, r.get_json()
    return _post(client, key, inst, "complete", {
        "request_id": rid, "chunk_count": len(chunks), "encryption": params.as_dict(), "as_of": as_of})


def _row(app, rid):
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncBootstrapRequest

        r = db_session.get(SyncBootstrapRequest, uuid.UUID(rid))
        db_session.refresh(r)
        return r.status, r.producer_installation_id, dict(r.manifest or {})


def _set(app, rid, **cols):
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncBootstrapRequest

        r = db_session.get(SyncBootstrapRequest, uuid.UUID(rid))
        for k, v in cols.items():
            setattr(r, k, v)
        db_session.commit()


def _mark_pruned(app, license_id, through):
    res = app.test_cli_runner().invoke(args=["sync", "mark-pruned", "--license", str(license_id), "--through", str(through)])
    assert res.exit_code == 0, res.output


@pytest.fixture(autouse=True)
def _monkey_chunks(monkeypatch):
    # Several chunks from a few KB: the chunk logic is size-independent.
    monkeypatch.setattr(crypto, "CHUNK_SIZE", 1024)


# ---------------------------------------------------------------- auth
@pytest.mark.parametrize("path", ["request", "status", "claim", "chunk", "complete", "download", "ack", "fulfil"])
def test_every_route_needs_a_signed_body(app, client, seeded, signing_key, path):
    r = client.post(f"/api/sync/v1/bootstrap/{path}", data=json.dumps({"request_id": str(uuid.uuid4())}),
                    content_type="application/json")
    assert r.status_code == 400 and r.get_json()["reason_code"] == "INVALID_REQUEST"


def test_a_forged_signature_is_refused(app, client, seeded, signing_key):
    """M4. Both directions: the real key works, a different key does not."""
    _, ((a, ka), (b, kb), _c) = _shop(app, client)
    keys = crypto.new_requester_keys()
    ok = _post(client, ka, a, "request", {"stream": "retail", "enc_pubkey": keys.public_b64})
    assert ok.status_code == 200
    forged = _post(client, kb, a, "request", {"stream": "retail", "enc_pubkey": keys.public_b64})   # B's key, A's id
    assert forged.status_code == 400 and forged.get_json()["reason_code"] == "INVALID_SIGNATURE"


def test_a_replayed_body_is_refused(app, client, seeded, signing_key):
    _, ((a, ka), *_r) = _shop(app, client)
    keys = crypto.new_requester_keys()
    body = json.dumps(_signed_body(ka, a, {"stream": "retail", "enc_pubkey": keys.public_b64}))
    assert client.post("/api/sync/v1/bootstrap/request", data=body, content_type="application/json").status_code == 200
    again = client.post("/api/sync/v1/bootstrap/request", data=body, content_type="application/json")
    assert again.status_code == 400


# ---------------------------------------------------------------- request
def test_one_open_request_per_device_and_stream(app, client, seeded, signing_key):
    """M5. The same request twice is ONE row; retail and registry are separate."""
    _, ((a, ka), *_r) = _shop(app, client)
    keys = crypto.new_requester_keys()
    first, _ = _request(client, ka, a, keys=keys)
    second, _ = _request(client, ka, a, keys=keys)
    assert first["request_id"] == second["request_id"] and second["status"] == "OPEN"
    reg, _ = _request(client, ka, a, stream="registry")
    assert reg["request_id"] != first["request_id"]
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncBootstrapRequest

        assert db_session.query(SyncBootstrapRequest).filter(
            SyncBootstrapRequest.requester_installation_id == uuid.UUID(a)).count() == 2


def test_a_new_key_supersedes_the_old_request(app, client, seeded, signing_key):
    """The device lost its private key (restart before staging): the old copy could
    never be opened, so it is FAILED and its bytes removed."""
    _, ((a, ka), (b, kb), _c) = _shop(app, client)
    _catch_up(client, [(b, kb)])
    old, _ = _request(client, ka, a)
    assert _claim(client, kb, b, old["request_id"]).status_code == 200
    new, _ = _request(client, ka, a)
    assert new["request_id"] != old["request_id"]
    assert _row(app, old["request_id"])[0] == "FAILED"


def test_min_as_of_is_the_requesters_own_top_event(app, client, seeded, signing_key):
    _, ((a, ka), (b, kb), _c) = _shop(app, client)
    for _i in range(3):
        assert _push(client, ka, a, [_make_event()]).status_code == 200
    assert _push(client, kb, b, [_make_event()]).status_code == 200
    view, _ = _request(client, ka, a)
    assert view["min_as_of"] == 3                       # A's own events are seq 1..3, B's is 4


def test_a_retail_request_needs_a_real_key(app, client, seeded, signing_key):
    _, ((a, ka), *_r) = _shop(app, client)
    for bad in ("", "not-base64!!", base64.b64encode(b"short").decode()):
        r = _post(client, ka, a, "request", {"stream": "retail", "enc_pubkey": bad})
        assert r.status_code == 400 and r.get_json()["reason_code"] == "INVALID_ENC_PUBKEY"
    assert _post(client, ka, a, "request", {"stream": "nope", "enc_pubkey": "x"}).status_code == 400


# ---------------------------------------------------------------- claim
def test_two_producers_one_winner(app, client, seeded, signing_key):
    """M3 (sequential half): first claim wins, the second is told ALREADY_CLAIMED."""
    _, ((a, ka), (b, kb), (c, kc)) = _shop(app, client)
    _catch_up(client, [(b, kb), (c, kc)])
    view, _ = _request(client, ka, a)
    first = _claim(client, kb, b, view["request_id"])
    second = _claim(client, kc, c, view["request_id"])
    assert first.status_code == 200 and first.get_json()["status"] == "CLAIMED"
    assert second.status_code == 409 and second.get_json()["reason_code"] == "ALREADY_CLAIMED"
    assert _row(app, view["request_id"])[1] == uuid.UUID(b)
    # The same producer asking again is idempotent, not a conflict.
    assert _claim(client, kb, b, view["request_id"]).status_code == 200


def test_two_producers_racing_concurrently_still_one_winner(app, client, seeded, signing_key):
    """M3 (concurrent half): the conditional UPDATE is what guarantees it."""
    _, ((a, ka), (b, kb), (c, kc)) = _shop(app, client)
    _catch_up(client, [(b, kb), (c, kc)])
    view, _ = _request(client, ka, a)
    bodies = {
        "b": json.dumps(_signed_body(kb, b, {"request_id": view["request_id"], "schema_version": 56})),
        "c": json.dumps(_signed_body(kc, c, {"request_id": view["request_id"], "schema_version": 56})),
    }
    out, barrier = {}, threading.Barrier(2)

    def go(who):
        cl = app.test_client()
        barrier.wait()
        out[who] = cl.post("/api/sync/v1/bootstrap/claim", data=bodies[who], content_type="application/json").status_code

    threads = [threading.Thread(target=go, args=(w,)) for w in bodies]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(out.values()) == [200, 409], out


def test_the_requester_cannot_claim_its_own_request(app, client, seeded, signing_key):
    _, ((a, ka), *_r) = _shop(app, client)
    _catch_up(client, [(a, ka)])
    view, _ = _request(client, ka, a)
    r = _claim(client, ka, a, view["request_id"])
    assert r.status_code == 400 and r.get_json()["reason_code"] == "SELF_CLAIM"


def test_a_producer_behind_the_requesters_own_events_is_refused(app, client, seeded, signing_key):
    """Deny-half AND allow-half: B has not pulled A's events (cursor 0 < min_as_of 2)
    so its copy would miss them; once B has caught up it may claim."""
    _, ((a, ka), (b, kb), _c) = _shop(app, client)
    for _i in range(2):
        _push(client, ka, a, [_make_event()])
    view, _ = _request(client, ka, a)
    assert view["min_as_of"] == 2
    behind = _claim(client, kb, b, view["request_id"])
    assert behind.status_code == 409 and behind.get_json()["reason_code"] == "PRODUCER_BEHIND"
    assert _pull(client, kb, b, 0, stream="retail").status_code == 200       # B now holds A's events
    assert _claim(client, kb, b, view["request_id"]).status_code == 200


def test_a_producer_on_a_newer_schema_is_refused(app, client, seeded, signing_key):
    _, ((a, ka), (b, kb), _c) = _shop(app, client)
    _catch_up(client, [(b, kb)])
    view, _ = _request(client, ka, a)              # requester is schema 56
    r = _claim(client, kb, b, view["request_id"], schema_version=57)
    assert r.status_code == 409 and r.get_json()["reason_code"] == "SCHEMA_NEWER"
    assert _claim(client, kb, b, view["request_id"], schema_version=56).status_code == 200


def test_a_lapsed_lease_lets_the_next_producer_win_and_wipes_the_first_ones_bytes(app, client, seeded, signing_key, box):
    _, ((a, ka), (b, kb), (c, kc)) = _shop(app, client)
    _catch_up(client, [(b, kb), (c, kc)])
    view, keys = _request(client, ka, a)
    rid = view["request_id"]
    assert _claim(client, kb, b, rid).status_code == 200
    params, chunks = crypto.encrypt_package(b"x" * 10, keys.public_b64, rid)
    assert _post(client, kb, b, "chunk", {"request_id": rid, "index": 0,
                                           "data": base64.b64encode(list(chunks)[0]).decode()}).status_code == 200
    assert any((box / rid).glob("*.chunk"))
    _set(app, rid, claim_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    # B can no longer upload; C can claim, and B's partial bytes are gone.
    assert _post(client, kb, b, "chunk", {"request_id": rid, "index": 1, "data": "AAAA"}).status_code == 409
    assert _claim(client, kc, c, rid).status_code == 200
    assert not list((box / rid).glob("*.chunk"))


# ---------------------------------------------------------------- the data half
def _ready_request(app, client, shop, plaintext=b"THE-SHOPS-DATABASE" * 200, as_of=1):
    (a, ka), (b, kb), _c = shop
    _catch_up(client, [(b, kb)])
    view, keys = _request(client, ka, a)
    rid = view["request_id"]
    assert _claim(client, kb, b, rid).status_code == 200
    done = _produce(client, kb, b, rid, keys.public_b64, plaintext, as_of=as_of)
    assert done.status_code == 200, done.get_json()
    return rid, keys, plaintext


def _download_all(client, key, inst, rid, manifest):
    out = []
    for i in range(manifest["chunk_count"]):
        r = _post(client, key, inst, "download", {"request_id": rid, "index": i})
        assert r.status_code == 200, r.get_json()
        out.append(r.get_json())
    return out


def test_the_whole_data_half_round_trips_and_owner_never_holds_plaintext(app, client, seeded, signing_key, box):
    _, shop = _shop(app, client)
    (a, ka) = shop[0]
    rid, keys, plaintext = _ready_request(app, client, shop)
    status = _post(client, ka, a, "status", {"request_id": rid}).get_json()
    assert status["status"] == "READY" and status["manifest"]["chunk_count"] == len(list((box / rid).glob("*.chunk")))
    on_disk = b"".join(p.read_bytes() for p in sorted((box / rid).glob("*.chunk")))
    assert b"THE-SHOPS-DATABASE" not in on_disk                      # ciphertext only
    pieces = _download_all(client, ka, a, rid, status["manifest"])
    for i, p in enumerate(pieces):
        assert hashlib.sha256(base64.b64decode(p["data"])).hexdigest() == status["manifest"]["chunk_sha256"][i]
    got = crypto.decrypt_package([base64.b64decode(p["data"]) for p in pieces], keys.private_b64,
                                 status["manifest"]["encryption"], rid)
    assert got == plaintext


def test_ack_deletes_the_blob_and_a_retried_ack_is_harmless(app, client, seeded, signing_key, box):
    """M2. The bytes stop existing the moment the requester acks."""
    _, shop = _shop(app, client)
    (a, ka) = shop[0]
    rid, _keys, _pt = _ready_request(app, client, shop)
    assert any((box / rid).glob("*.chunk"))
    ack = _post(client, ka, a, "ack", {"request_id": rid})
    assert ack.status_code == 200 and ack.get_json()["status"] == "DELIVERED"
    assert not (box / rid).exists()
    assert _post(client, ka, a, "ack", {"request_id": rid}).get_json()["status"] == "DELIVERED"
    again = _post(client, ka, a, "download", {"request_id": rid, "index": 0})
    assert again.status_code == 409 and again.get_json()["reason_code"] == "NOT_READY"


def test_only_the_requester_can_download_or_ack(app, client, seeded, signing_key):
    _, shop = _shop(app, client)
    (b, kb), (c, kc) = shop[1], shop[2]
    rid, _keys, _pt = _ready_request(app, client, shop)
    for who, key in ((b, kb), (c, kc)):                              # the producer and a bystander
        assert _post(client, key, who, "download", {"request_id": rid, "index": 0}).status_code == 404
        assert _post(client, key, who, "ack", {"request_id": rid}).status_code == 404
        assert _post(client, key, who, "status", {"request_id": rid}).status_code == 404


def test_download_is_refused_until_ready(app, client, seeded, signing_key):
    _, ((a, ka), *_r) = _shop(app, client)
    view, _ = _request(client, ka, a)
    r = _post(client, ka, a, "download", {"request_id": view["request_id"], "index": 0})
    assert r.status_code == 409 and r.get_json()["reason_code"] == "NOT_READY"


def test_complete_refuses_missing_chunks_and_a_copy_older_than_the_requesters_own_events(app, client, seeded, signing_key, box):
    _, ((a, ka), (b, kb), _c) = _shop(app, client)
    for _i in range(3):
        _push(client, ka, a, [_make_event()])
    _catch_up(client, [(b, kb)])
    view, keys = _request(client, ka, a)
    rid = view["request_id"]
    assert _claim(client, kb, b, rid).status_code == 200
    params, chunks = crypto.encrypt_package(b"y" * 3000, keys.public_b64, rid)
    chunks = list(chunks)
    _post(client, kb, b, "chunk", {"request_id": rid, "index": 0, "data": base64.b64encode(chunks[0]).decode()})
    short = _post(client, kb, b, "complete", {"request_id": rid, "chunk_count": len(chunks),
                                              "encryption": params.as_dict(), "as_of": 3})
    assert short.status_code == 409 and short.get_json()["reason_code"] == "CHUNKS_INCOMPLETE"
    for i, ch in enumerate(chunks[1:], start=1):
        _post(client, kb, b, "chunk", {"request_id": rid, "index": i, "data": base64.b64encode(ch).decode()})
    stale = _post(client, kb, b, "complete", {"request_id": rid, "chunk_count": len(chunks),
                                              "encryption": params.as_dict(), "as_of": 2})   # < min_as_of 3
    assert stale.status_code == 409 and stale.get_json()["reason_code"] == "AS_OF_TOO_OLD"
    assert _row(app, rid)[0] == "OPEN" and not (box / rid).exists()                          # handed back
    assert _claim(client, kb, b, rid).status_code == 200                                      # allow-half


def test_oversized_uploads_are_refused(app, client, seeded, signing_key):
    app.config["SYNC_BOOTSTRAP_MAX_BYTES"] = 100
    _, ((a, ka), (b, kb), _c) = _shop(app, client)
    _catch_up(client, [(b, kb)])
    view, _ = _request(client, ka, a)
    rid = view["request_id"]
    _claim(client, kb, b, rid)
    ok = _post(client, kb, b, "chunk", {"request_id": rid, "index": 0, "data": base64.b64encode(b"a" * 60).decode()})
    assert ok.status_code == 200
    over = _post(client, kb, b, "chunk", {"request_id": rid, "index": 1, "data": base64.b64encode(b"a" * 60).decode()})
    assert over.status_code == 413


def test_only_the_claim_holder_can_upload(app, client, seeded, signing_key):
    _, ((a, ka), (b, kb), (c, kc)) = _shop(app, client)
    _catch_up(client, [(b, kb)])
    view, _ = _request(client, ka, a)
    rid = view["request_id"]
    _claim(client, kb, b, rid)
    r = _post(client, kc, c, "chunk", {"request_id": rid, "index": 0, "data": "AAAA"})
    assert r.status_code == 403 and r.get_json()["reason_code"] == "NOT_CLAIM_HOLDER"


# ---------------------------------------------------------------- tamper / wrong key
def test_a_chunk_altered_on_the_mailbox_disk_is_refused(app, client, seeded, signing_key, box):
    """Two layers. (1) Owner stored each chunk's sha256, so a file edited on disk no
    longer matches what the requester is told. (2) Even if an attacker also rewrites
    the stored hash, AES-GCM refuses the altered bytes."""
    _, shop = _shop(app, client)
    (a, ka) = shop[0]
    rid, keys, _pt = _ready_request(app, client, shop)
    victim = sorted((box / rid).glob("*.chunk"))[1]
    data = bytearray(victim.read_bytes())
    data[3] ^= 0xFF
    victim.write_bytes(bytes(data))
    status = _post(client, ka, a, "status", {"request_id": rid}).get_json()
    pieces = _download_all(client, ka, a, rid, status["manifest"])
    blobs = [base64.b64decode(p["data"]) for p in pieces]
    assert hashlib.sha256(blobs[1]).hexdigest() != status["manifest"]["chunk_sha256"][1]        # layer 1
    with pytest.raises(BootstrapError):                                                          # layer 2
        crypto.decrypt_package(blobs, keys.private_b64, status["manifest"]["encryption"], rid)


def test_a_copy_encrypted_to_another_key_cannot_be_opened_by_the_requester(app, client, seeded, signing_key):
    """A producer (or anything in the middle) that encrypts to a key that is not the
    requester's produces bytes the requester's private key refuses."""
    _, ((a, ka), (b, kb), _c) = _shop(app, client)
    _catch_up(client, [(b, kb)])
    view, keys = _request(client, ka, a)
    rid = view["request_id"]
    _claim(client, kb, b, rid)
    stranger = crypto.new_requester_keys()
    assert _produce(client, kb, b, rid, stranger.public_b64, b"z" * 2000).status_code == 200
    status = _post(client, ka, a, "status", {"request_id": rid}).get_json()
    pieces = _download_all(client, ka, a, rid, status["manifest"])
    with pytest.raises(BootstrapError):
        crypto.decrypt_package([base64.b64decode(p["data"]) for p in pieces], keys.private_b64,
                               status["manifest"]["encryption"], rid)


# ---------------------------------------------------------------- expiry
def test_a_request_nobody_finishes_expires_and_its_bytes_go(app, client, seeded, signing_key, box):
    _, shop = _shop(app, client)
    (a, ka) = shop[0]
    rid, _keys, _pt = _ready_request(app, client, shop)
    assert (box / rid).exists()
    _set(app, rid, expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
    status = _post(client, ka, a, "status", {"request_id": rid}).get_json()
    assert status["status"] == "EXPIRED" and not (box / rid).exists()
    dl = _post(client, ka, a, "download", {"request_id": rid, "index": 0})
    assert dl.status_code == 409
    fresh, _ = _request(client, ka, a)                                   # the device may ask again
    assert fresh["request_id"] != rid and fresh["status"] == "OPEN"


def test_housekeeping_sweep_expires_resets_leases_and_deletes_orphan_blobs(app, client, seeded, signing_key, box):
    _, ((a, ka), (b, kb), (c, kc)) = _shop(app, client)
    _catch_up(client, [(a, ka), (b, kb), (c, kc)])        # a producer needs a retail cursor to claim
    stale, _ = _request(client, ka, a)
    lapsed, _ = _request(client, kc, c)
    live, _ = _request(client, kb, b)
    assert _claim(client, kb, b, stale["request_id"]).status_code == 200
    assert _claim(client, ka, a, lapsed["request_id"]).status_code == 200      # A produces for C
    (box / "orphan-dir").mkdir(parents=True)
    (box / "orphan-dir" / "000000.chunk").write_bytes(b"left over")
    _set(app, stale["request_id"], expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    _set(app, lapsed["request_id"], claim_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    from app.sync import bootstrap_routes
    with app.app_context():
        dry = bootstrap_routes.sweep(apply=False)
    assert dry == {"expired": 1, "leases_reset": 1, "blobs_removed": 3}, dry
    assert (box / "orphan-dir").exists() and _row(app, stale["request_id"])[0] == "CLAIMED"     # dry run touches nothing
    with app.app_context():
        done = bootstrap_routes.sweep(apply=True)
    assert done == dry
    assert _row(app, stale["request_id"])[0] == "EXPIRED"
    assert _row(app, lapsed["request_id"])[0] == "OPEN"
    assert _row(app, live["request_id"])[0] == "OPEN"
    assert not (box / "orphan-dir").exists() and not (box / stale["request_id"]).exists()


# ---------------------------------------------------------------- isolation
def test_another_licence_cannot_see_claim_download_or_fulfil_a_request(app, client, seeded, signing_key):
    """M1. A request id from another licence is 'not found' on EVERY route, and its
    owner can still use it (allow-half)."""
    _, shop = _shop(app, client)
    (a, ka) = shop[0]
    rid, _keys, _pt = _ready_request(app, client, shop)
    _, ((x, kx), *_r) = _shop(app, client, n=1)
    _pull(client, kx, x, 0, stream="retail")
    for path, extra in (("status", {}), ("claim", {"schema_version": 56}), ("download", {"index": 0}),
                        ("ack", {}), ("fulfil", {}), ("complete", {"chunk_count": 1, "as_of": 1, "encryption": {}}),
                        ("chunk", {"index": 0, "data": "AAAA"})):
        r = _post(client, kx, x, path, {"request_id": rid, **extra})
        assert r.status_code in (404,), (path, r.status_code, r.get_json())
    assert _post(client, ka, a, "status", {"request_id": rid}).status_code == 200


def test_a_foreign_licences_requests_are_not_listed_to_my_pull(app, client, seeded, signing_key):
    _, ((a, ka), *_r) = _shop(app, client)
    _request(client, ka, a)
    _, ((x, kx), *_r2) = _shop(app, client, n=1)
    r = _pull(client, kx, x, 0, stream="retail", caps=["bootstrap_producer_v1"])
    assert r.status_code == 200 and "bootstrap_requests" not in r.get_json()


# ---------------------------------------------------------------- pull integration
def test_requests_are_listed_only_to_producer_capable_pullers(app, client, seeded, signing_key):
    _, ((a, ka), (b, kb), _c) = _shop(app, client)
    view, _ = _request(client, ka, a)
    plain = _pull(client, kb, b, 0, stream="retail")
    assert "bootstrap_requests" not in plain.get_json()
    capable = _pull(client, kb, b, 0, stream="retail", caps=["bootstrap_producer_v1"])
    listed = capable.get_json()["bootstrap_requests"]
    assert [x["request_id"] for x in listed] == [view["request_id"]]
    assert listed[0]["stream"] == "retail" and listed[0]["status"] == "OPEN" and listed[0]["requester"] == a
    own = _pull(client, ka, a, 0, stream="retail", caps=["bootstrap_producer_v1"])
    assert "bootstrap_requests" not in own.get_json()                  # never told about my own request


def test_include_own_returns_my_own_events_once_and_is_echoed(app, client, seeded, signing_key):
    _, ((a, ka), *_r) = _shop(app, client)
    assert _push(client, ka, a, [_make_event(), _make_event()]).status_code == 200
    plain = _pull(client, ka, a, 0, stream="retail").get_json()
    assert plain["events"] == [] and "include_own" not in plain         # normal: never my own
    own = _pull(client, ka, a, 0, stream="retail", include_own=True).get_json()
    assert len(own["events"]) == 2 and own["include_own"] is True


# ---------------------------------------------------------------- the authority half
def test_registry_pull_below_the_pruned_point_fast_forwards_and_records_a_request(app, client, seeded, signing_key):
    license_id, ((a, ka), (b, kb), _c) = _shop(app, client)
    for _i in range(4):
        _push(client, ka, a, [_make_event()])
    _mark_pruned(app, license_id, 2)
    retail = _pull(client, kb, b, 0, stream="retail")
    assert retail.status_code == 409 and "fast_forward_to" not in retail.get_json()
    reg = _pull(client, kb, b, 0, stream="registry")
    body = reg.get_json()
    assert reg.status_code == 409 and body["reason_code"] == "RESYNC_REQUIRED"
    assert body["fast_forward_to"] == 4 and body["pruned_through"] == 2
    again = _pull(client, kb, b, 0, stream="registry")
    assert again.status_code == 409
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncBootstrapRequest

        rows = db_session.query(SyncBootstrapRequest).filter(
            SyncBootstrapRequest.requester_installation_id == uuid.UUID(b)).all()
        assert [(r.stream, r.status, r.fast_forward_to) for r in rows] == [("registry", "OPEN", 4)]
        rid = str(rows[0].id)
    # The main (A) hears about it and fulfils it; the requester's own pull is not told.
    listed = _pull(client, ka, a, 4, stream="retail", caps=["bootstrap_producer_v1"]).get_json()["bootstrap_requests"]
    assert [(x["request_id"], x["stream"]) for x in listed] == [(rid, "registry")]
    assert _post(client, kb, b, "fulfil", {"request_id": rid}).get_json()["reason_code"] == "SELF_FULFIL"
    done = _post(client, ka, a, "fulfil", {"request_id": rid})
    assert done.status_code == 200 and done.get_json()["status"] == "FULFILLED"
    gone = _pull(client, ka, a, 4, stream="retail", caps=["bootstrap_producer_v1"]).get_json()
    assert "bootstrap_requests" not in gone


def test_authority_fulfil_waits_for_the_copy_and_settles_in_either_order(app, client, seeded, signing_key):
    """M6. The re-announce must land AFTER the copy exists (its events then sit above
    the copy's as_of): fulfil before READY is TOO_EARLY. The two halves may finish in
    either order; FULFILLED means both."""
    _, ((a, ka), (b, kb), (c, kc)) = _shop(app, client)
    _catch_up(client, [(b, kb), (c, kc)])
    view, keys = _request(client, ka, a)
    rid = view["request_id"]
    early = _post(client, kc, c, "fulfil", {"request_id": rid})
    assert early.status_code == 409 and early.get_json()["reason_code"] == "TOO_EARLY"
    assert _claim(client, kb, b, rid).status_code == 200
    assert _produce(client, kb, b, rid, keys.public_b64, b"d" * 1500).status_code == 200
    # Order 1: authority first (still READY), then the ack.
    listed = _pull(client, kc, c, 0, stream="retail", caps=["bootstrap_producer_v1"]).get_json()["bootstrap_requests"]
    assert [x["status"] for x in listed] == ["READY"]
    fulfilled = _post(client, kc, c, "fulfil", {"request_id": rid}).get_json()
    assert fulfilled["status"] == "READY" and fulfilled["authority_fulfilled"] is True
    assert _post(client, ka, a, "ack", {"request_id": rid}).get_json()["status"] == "FULFILLED"
    # Order 2: ack first, then the authority half.
    view2, keys2 = _request(client, kb, b)
    rid2 = view2["request_id"]
    _catch_up(client, [(c, kc)])
    assert _claim(client, kc, c, rid2).status_code == 200
    assert _produce(client, kc, c, rid2, keys2.public_b64, b"e" * 1500).status_code == 200
    assert _post(client, kb, b, "ack", {"request_id": rid2}).get_json()["status"] == "DELIVERED"
    assert _post(client, ka, a, "fulfil", {"request_id": rid2}).get_json()["status"] == "FULFILLED"


# ---------------------------------------------------------------- Phase 3 additions
def test_a_copy_below_owners_own_prune_point_is_refused_and_the_request_reopens(app, client, seeded, signing_key, box):
    """A producer that is itself stuck behind the prune holds a copy the requester could never
    resume from (it would be told RESYNC_REQUIRED again, forever). Both directions: as_of below
    pruned_through -> AS_OF_TOO_OLD + OPEN + bytes gone; as_of at/above it -> READY.
    MUTATION: drop `or as_of < pruned_through` in bootstrap_complete -> the first half goes red."""
    license_id, ((a, ka), (b, kb), (c, kc)) = _shop(app, client)
    for _i in range(4):
        _push(client, ka, a, [_make_event()])
    _catch_up(client, [(a, ka), (b, kb), (c, kc)])
    _mark_pruned(app, license_id, 3)
    view, keys = _request(client, kc, c)
    rid = view["request_id"]
    claimed = _claim(client, kb, b, rid)
    assert claimed.status_code == 200, claimed.get_json()
    assert claimed.get_json()["enc_pubkey"] == keys.public_b64        # the producer learns the key to encrypt to
    low = _produce(client, kb, b, rid, keys.public_b64, b"x" * 3000, as_of=2)
    assert low.status_code == 409 and low.get_json()["reason_code"] == "AS_OF_TOO_OLD", low.get_json()
    assert low.get_json()["min_as_of"] == 3
    assert _row(app, rid)[0] == "OPEN" and not (box / rid).exists()
    assert _claim(client, kb, b, rid).status_code == 200
    ok = _produce(client, kb, b, rid, keys.public_b64, b"x" * 3000, as_of=3)
    assert ok.status_code == 200 and _row(app, rid)[0] == "READY"
