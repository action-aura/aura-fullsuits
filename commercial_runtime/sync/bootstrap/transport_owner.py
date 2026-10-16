"""The device's side of Owner's bootstrap MAILBOX (`/api/sync/v1/bootstrap/...`,
owner/app/sync/bootstrap_routes.py). SYNC-BOOT Phase 3.

Two roles, one module:

  REQUESTER (`OwnerRequester`)   a device whose cloud pull was refused with
      RESYNC_REQUIRED. It makes a single-use X25519 key, asks Owner for a copy,
      POLLS (a producer is another computer in the shop: it may take minutes, or
      until that computer is switched on), downloads the encrypted chunks,
      checks every hash, decrypts, hands the zip to `installer.stage` (which
      verifies the signed manifest and refuses a copy of another shop / a newer
      schema / an open cash drawer) and finally ACKS so Owner deletes its bytes.
      Install happens at the NEXT start, exactly as for a hub copy.

  PRODUCER (`produce_for`)       a device that holds the shop's data and was told,
      in its pull response, that another device is waiting. It claims the request
      (first claim wins, 30-minute lease), builds the copy with the Phase 2
      producer, encrypts it to the requester's key, uploads the chunks and
      completes. The copy never exists in plaintext anywhere but on the two
      devices.

WHY THE ACK COMES RIGHT AFTER STAGING, NOT AFTER THE INSTALL. The staged file on
the requester's own disk is the durable copy from then on; nothing Owner still
holds adds safety, and every hour it stays there is a whole shop's data sitting
on a server. If the install is later refused (e.g. an open drawer) the staged
file waits locally; if it is lost, the requester simply asks again.

THE REQUESTER'S PRIVATE KEY lives in `sync_bootstrap_owner.json` beside the
databases (which hold the same data in the clear), so a restart mid-wait resumes
the SAME request instead of abandoning a copy a producer may be building for it.
It is deleted the moment the copy is staged.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from commercial_runtime.sync.bootstrap import crypto, errors, installer
from commercial_runtime.sync.bootstrap.errors import BootstrapError
from commercial_runtime.sync.relay_client import RelayRejected, SyncRelayClientError

logger = logging.getLogger(__name__)

API = "/api/sync/v1/bootstrap"
STATE_NAME = "sync_bootstrap_owner.json"
#: A chunk is up to 4 MiB of plaintext, ~5.6 MB of base64 inside the signed body.
CHUNK_TIMEOUT_SECONDS = 180.0
DEFAULT_POLL_SECONDS = 20.0
MAX_POLL_SECONDS = 300.0
#: After this many consecutive failed attempts the thread stops until the next start.
MAX_FAILURES = 5


def state_path(app_data_dir) -> Path:
    return Path(app_data_dir) / STATE_NAME


class MailboxApi:
    """Thin, mockable wrapper: one method per Owner route."""

    def __init__(self, client):
        self._client = client

    def _post(self, verb: str, body: dict, timeout: Optional[float] = None) -> dict:
        return self._client.post_signed_json(f"{API}/{verb}", body, timeout_seconds=timeout)

    def request(self, *, stream: str, reason: str, enc_pubkey: str, schema_version: int,
                app_version: Optional[str] = None) -> dict:
        body = {"stream": stream, "reason": reason, "schema_version": int(schema_version)}
        if enc_pubkey:
            body["enc_pubkey"] = enc_pubkey
        if app_version:
            body["app_version"] = app_version
        return self._post("request", body)

    def status(self, request_id: str) -> dict:
        return self._post("status", {"request_id": request_id})

    def claim(self, request_id: str, schema_version: int) -> dict:
        return self._post("claim", {"request_id": request_id, "schema_version": int(schema_version)})

    def upload_chunk(self, request_id: str, index: int, data: bytes) -> dict:
        return self._post("chunk", {"request_id": request_id, "index": index,
                                    "data": base64.b64encode(data).decode("ascii")}, CHUNK_TIMEOUT_SECONDS)

    def complete(self, request_id: str, *, chunk_count: int, as_of: int, encryption: dict) -> dict:
        return self._post("complete", {"request_id": request_id, "chunk_count": chunk_count,
                                       "as_of": as_of, "encryption": encryption})

    def download_chunk(self, request_id: str, index: int) -> dict:
        return self._post("download", {"request_id": request_id, "index": index}, CHUNK_TIMEOUT_SECONDS)

    def ack(self, request_id: str) -> dict:
        return self._post("ack", {"request_id": request_id})

    def fulfil(self, request_id: str) -> dict:
        return self._post("fulfil", {"request_id": request_id})


# ── requester ───────────────────────────────────────────────────────────────

class OwnerRequester:
    """One RESYNC_REQUIRED -> one staged copy. `step()` does at most one unit of
    progress and never blocks on a peer; `run()` is the polling loop."""

    def __init__(self, api: MailboxApi, *, spec, app_data_dir, relay_url: str, trust_store,
                 own_licence: Optional[str], expected_company_id: Optional[str],
                 reason: str = "resync", app_version: Optional[str] = None,
                 after_staged: Optional[Callable[[dict], None]] = None):
        self._api = api
        self._spec = spec
        self._app = Path(app_data_dir)
        self._relay_url = relay_url
        self._trust_store = trust_store
        self._own_licence = own_licence
        self._expected_company_id = expected_company_id
        self._reason = reason
        self._app_version = app_version
        self._after_staged = after_staged

    # -- state -------------------------------------------------------------
    def _load(self) -> Optional[dict]:
        try:
            data = json.loads(state_path(self._app).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("relay_url") != self._relay_url:
            # A request made on another relay means nothing to this one.
            return None
        return data

    def _save(self, state: dict) -> None:
        path = state_path(self._app)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    def _discard(self) -> None:
        state_path(self._app).unlink(missing_ok=True)

    # -- one unit of progress ---------------------------------------------------
    def step(self) -> str:
        """-> 'requested' | 'waiting' | 'staged' | 'retry'. Raises RelayRejected /
        SyncRelayClientError (transport) / BootstrapError (the copy was refused)."""
        state = self._load()
        if state is None:
            keys = crypto.new_requester_keys()
            view = self._api.request(
                stream="retail", reason=self._reason, enc_pubkey=keys.public_b64,
                schema_version=self._spec.schema_version, app_version=self._app_version)
            state = {"request_id": view["request_id"], "private_b64": keys.private_b64,
                     "public_b64": keys.public_b64, "relay_url": self._relay_url}
            self._save(state)
            if view.get("status") == "OPEN":
                return "requested"
        else:
            try:
                view = self._api.status(state["request_id"])
            except RelayRejected as exc:
                if exc.reason_code == "BOOTSTRAP_NOT_FOUND":
                    self._discard()
                    return "retry"
                raise
        status = view.get("status")
        if status in ("OPEN", "CLAIMED"):
            return "waiting"
        if status == "READY":
            return self._fetch_and_stage(state, view)
        if status in ("DELIVERED", "FULFILLED") and state.get("staged"):
            self._discard()
            return "staged"
        # EXPIRED / FAILED / a delivered copy this device no longer has: start over.
        self._discard()
        return "retry"

    def _fetch_and_stage(self, state: dict, view: dict) -> str:
        request_id = state["request_id"]
        manifest = view.get("manifest") or {}
        params = manifest.get("encryption") or {}
        hashes = manifest.get("chunk_sha256") or []
        count = manifest.get("chunk_count")
        if not isinstance(count, int) or count < 1 or len(hashes) != count:
            raise BootstrapError(errors.BAD_PACKAGE, "Owner's manifest names no usable chunk list")
        decryptor = crypto.ChunkDecryptor(state["private_b64"], params, request_id)
        if decryptor.total != count:
            raise BootstrapError(errors.BAD_PACKAGE, "the chunk count and the encryption parameters disagree")
        pieces = []
        for i in range(count):
            answer = self._api.download_chunk(request_id, i)
            blob = base64.b64decode(str(answer.get("data") or ""), validate=True)
            digest = hashlib.sha256(blob).hexdigest()
            # Owner's own hash, then the hash recorded when the producer finished:
            # a chunk altered on the mailbox disk, or swapped in transit, stops here
            # (GCM would also catch it; this fails earlier and says which chunk).
            if digest != answer.get("sha256") or digest != hashes[i]:
                raise BootstrapError(errors.CHECKSUM_MISMATCH, f"chunk {i} does not match its recorded hash")
            pieces.append(decryptor.decrypt_chunk(i, blob))
        data = b"".join(pieces)
        size = params.get("plaintext_size")
        if isinstance(size, int) and len(data) != size:
            raise BootstrapError(errors.CHECKSUM_MISMATCH, "the decrypted size does not match the declared size")
        record = installer.stage(
            data, spec=self._spec, app_data_dir=self._app, relay_url=self._relay_url,
            trust_store=self._trust_store, own_licence=self._own_licence,
            expected_company_id=self._expected_company_id, registry_fast_forward=None)
        state["staged"] = True
        state["private_b64"] = ""          # the copy is on disk now; the key has no further use
        self._save(state)
        try:
            self._api.ack(request_id)
        except (RelayRejected, SyncRelayClientError):
            # Staged is what matters; an unacked blob is swept by Owner after 72 h.
            logger.warning("Bootstrap: staged the copy but could not ack it to Owner (it will expire there).",
                           exc_info=True)
        self._discard()
        if self._after_staged is not None:
            self._after_staged(record)
        return "staged"

    # -- loop --------------------------------------------------------------
    def run(self, *, poll_seconds: float = DEFAULT_POLL_SECONDS, sleep: Callable[[float], None] = time.sleep,
            stop: Optional[threading.Event] = None, max_failures: int = MAX_FAILURES) -> str:
        """Poll until staged. -> 'staged' | 'gave_up' | 'stopped'. A failure
        (Owner unreachable, a copy refused) is retried with growing waits; after
        `max_failures` in a row it stops until the next start rather than hammer
        Owner or re-download a copy that keeps being refused."""
        failures = 0
        wait = poll_seconds
        while stop is None or not stop.is_set():
            try:
                outcome = self.step()
                failures, wait = 0, poll_seconds
                if outcome == "staged":
                    return outcome
                if outcome == "retry":
                    continue
            except (BootstrapError, RelayRejected, SyncRelayClientError) as exc:
                failures += 1
                logger.warning("Bootstrap request step failed (%s) [%d/%d]",
                               getattr(exc, "reason_code", type(exc).__name__), failures, max_failures)
                if failures >= max_failures:
                    return "gave_up"
                wait = min(MAX_POLL_SECONDS, wait * 2)
            sleep(wait)
        return "stopped"


# ── producer ────────────────────────────────────────────────────────────────

def produce_for(api: MailboxApi, request: dict, *, build: Callable[[str], object], schema_version: int) -> str:
    """Claim, build, encrypt, upload, complete. `build(requester_installation_id)`
    returns the Phase 2 `BuiltBootstrap` (data + as_of). -> 'completed' or the
    reason code of a refusal that is a normal outcome (ALREADY_CLAIMED,
    PRODUCER_BEHIND, SCHEMA_NEWER, AS_OF_TOO_OLD, ...). Anything unexpected raises."""
    request_id = request["request_id"]
    try:
        claimed = api.claim(request_id, schema_version)
    except RelayRejected as exc:
        return exc.reason_code
    enc_pubkey = claimed.get("enc_pubkey")
    if not enc_pubkey:
        return "NO_ENC_PUBKEY"
    built = build(str(request.get("requester") or ""))
    params, chunks = crypto.encrypt_package(built.data, enc_pubkey, request_id)
    count = 0
    for index, chunk in enumerate(chunks):
        api.upload_chunk(request_id, index, chunk)
        count += 1
    try:
        api.complete(request_id, chunk_count=count, as_of=built.as_of, encryption=params.as_dict())
    except RelayRejected as exc:
        return exc.reason_code
    return "completed"
