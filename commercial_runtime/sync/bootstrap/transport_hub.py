"""The device's side of the LAN hub's `POST /bootstrap`, and the one function that
turns a RESYNC_REQUIRED into a staged copy.

Same wire discipline as `/pull`: a signed body (the SyncRelayClient's own
`_signed_body`: fresh nonce and timestamp, Ed25519 over the canonical bytes) over
the pinned TLS session the client already holds. The answer is the zip itself;
a refusal is JSON with a `reason_code` and maps to `BootstrapError` /
`RelayRejected` so callers handle it like any other relay refusal.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from commercial_runtime.sync.bootstrap import errors, installer
from commercial_runtime.sync.bootstrap.errors import BootstrapError
from commercial_runtime.sync.relay_client import RelayRejected

logger = logging.getLogger(__name__)

BOOTSTRAP_PATH = "/api/sync/v1/bootstrap"


@dataclass(frozen=True)
class FetchedBootstrap:
    data: bytes
    as_of: int
    registry_fast_forward: int


def fetch_bootstrap(client, *, schema_version: int, stream: str = "retail") -> FetchedBootstrap:
    """Ask the hub `client` points at for a copy of the shop. Raises
    `RelayRejected` for a hub-side authentication/validation refusal and
    `BootstrapError` for a hub that cannot build one right now."""
    if not hasattr(client, "post_signed_raw"):
        raise BootstrapError(errors.PRODUCER_FAILED, "this relay client cannot request a bootstrap")
    response = client.post_signed_raw(
        BOOTSTRAP_PATH, {"stream": stream, "schema_version": int(schema_version)})
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code != 200 or "json" in content_type:
        try:
            body = response.json()
        except ValueError:
            raise BootstrapError(
                errors.PRODUCER_FAILED, f"the hub answered HTTP {response.status_code} with no reason") from None
        reason = body.get("reason_code") if isinstance(body, dict) else None
        if response.status_code == 409 and reason:
            raise BootstrapError(str(reason), str(body.get("message") or reason))
        raise RelayRejected(
            str(reason or "BOOTSTRAP_FAILED"), f"the hub refused the bootstrap request: {reason}",
            {k: v for k, v in (body or {}).items() if k != "reason_code"} if isinstance(body, dict) else {})
    try:
        as_of = int(response.headers["X-Aura-As-Of"])
        fast_forward = int(response.headers.get("X-Aura-Registry-Fast-Forward") or 0)
    except (KeyError, ValueError):
        raise BootstrapError(errors.BAD_PACKAGE, "the hub's answer names no relay position") from None
    return FetchedBootstrap(data=response.content, as_of=as_of, registry_fast_forward=fast_forward)


def request_and_stage(
    client,
    *,
    spec,
    app_data_dir,
    relay_url: str,
    trust_store,
    own_licence: Optional[str],
    expected_company_id: Optional[str],
) -> dict:
    """RESYNC_REQUIRED -> a verified copy parked for the next start. Returns the
    pending record. Raises `BootstrapError` / `RelayRejected`; stages nothing on
    any failure. The hub has already set this device's cursor rows to the copy's
    `as_of` by the time the bytes arrive (see the route), so even a copy that
    then fails verification here costs only a retry, never a pinned prune.

    The copy's own manifest `as_of` is cross-checked against the hub's header: a
    hub (or anything on the path) that claims a different position than the
    signed manifest is lying about one of them, and the signed one is the truth."""
    fetched = fetch_bootstrap(client, schema_version=spec.schema_version)
    record = installer.stage(
        fetched.data, spec=spec, app_data_dir=app_data_dir, relay_url=relay_url,
        trust_store=trust_store, own_licence=own_licence, expected_company_id=expected_company_id,
        registry_fast_forward=fetched.registry_fast_forward)
    if int(record["as_of"]) != fetched.as_of:
        installer.pending_path(app_data_dir).unlink(missing_ok=True)
        import shutil
        shutil.rmtree(installer.staging_dir(app_data_dir), ignore_errors=True)
        raise BootstrapError(
            errors.BAD_PACKAGE,
            f"the hub's header says position {fetched.as_of} but the signed copy says {record['as_of']}")
    return record
