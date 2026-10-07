"""Which relay is the CLOUD, and which is a LAN hub -- kept apart on purpose.

REHEARSAL-1007 B1 (CRITICAL), measured 2026-10-07 on two real Windows tills:
till B auto-joined till A's LAN hub, and `autojoin.discover_and_join` saved
A's LAN address (`https://192.168.1.219:5443`) into the ONE field that also
holds Owner's cloud relay address (`licensing_state.sync_relay_base_url`).
Moments later B won the hub election, and its forwarder -- the only path a
hub's books have to Owner -- read that same field as "the cloud" and spent
every tick dialling a LAN till over ordinary TLS: `push_error=
TLS_VERIFICATION_FAILED pull_error=TLS_VERIFICATION_FAILED`, forever, while
`/sync/health` said `healthy: true, pending_count: 0` (its own books were
"syncing" fine -- into its own site log). Staff created on A never reached
B. A restart did not help: the overwritten field is persistent.

The root cause is one field doing two jobs. This module gives each its own
home:

  * the CLOUD relay is `sync_relay_base_url` (what Owner said at
    activation) or an operator's `AURA_SYNC_RELAY_URL`, and nothing else
    ever writes it -- autojoin no longer does;
  * the LAN hub a till joined lives in `JoinedHubStore`, a small sidecar
    file next to the hub's own TLS identity, which nothing treats as a sync
    target.

`is_lan_hub_url` exists for the installs that were ALREADY damaged before
this fix (the owner's rehearsal tills among them): their persisted "cloud"
value is a LAN hub address and must be recognised as such rather than
dialled. It is a recogniser, not a guesser -- see its own docstring for the
exact signals and why each one cannot match a real Owner relay.
"""
from __future__ import annotations

import ipaddress
import json
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

# The hub port a till listens on when nobody overrides it (config.py's
# `SITE_RELAY_PORT` default). Kept here as a second source for the legacy
# recogniser only: a shop whose tills were installed with the default can
# carry a corrupted `https://<lan-ip>:5443` even on a till whose own
# `AURA_SITE_RELAY_PORT` was later changed.
DEFAULT_HUB_PORT = 5443

JOINED_HUB_FILENAME = "joined_hub.json"


def _normalise(url: str) -> Optional[tuple]:
    """(scheme, host, port) of `url`, lower-cased, default ports filled in,
    path ignored -- or None for anything that does not parse as http(s)."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
        scheme = (parts.scheme or "").lower()
        host = (parts.hostname or "").lower()
        port = parts.port
    except (AttributeError, ValueError):
        return None
    if scheme not in ("http", "https") or not host:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return (scheme, host, port)


def same_relay(a: Optional[str], b: Optional[str]) -> bool:
    """True when two relay URLs name the same scheme/host/port."""
    if not a or not b:
        return False
    na, nb = _normalise(a), _normalise(b)
    return na is not None and na == nb


def is_lan_hub_url(
    url: Optional[str],
    *,
    hub_ports: Iterable[int] = (DEFAULT_HUB_PORT,),
    known_hub_urls: Iterable[Optional[str]] = (),
) -> bool:
    """True if `url` is a LAN site-relay hub address, never a cloud relay.

    Two signals, either is enough:

      1. It names the same scheme/host/port as a hub this till KNOWS about
         -- the hub it joined (`JoinedHubStore`) or one of its own
         advertised hub addresses. Exact, no heuristics.
      2. The shape a hub address always has and an Owner relay never does:
         `https://`, a host that is an IP LITERAL in a private, link-local or
         loopback range, on a hub port. A hub has no DNS name (its address
         changes with every DHCP lease -- design sec4, "identity lives in
         keys, not addresses") and serves only on its hub port; Owner's
         relay is reached by name, or -- in a lab -- over plain
         `http://127.0.0.1:<port>`, which is `http`, so it never matches.

    An empty/None `url` is not a hub address (it is "nothing configured").
    """
    if not url:
        return False
    for known in known_hub_urls:
        if same_relay(url, known):
            return True
    norm = _normalise(url)
    if norm is None:
        return False
    scheme, host, port = norm
    if scheme != "https":
        return False
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False  # a DNS name: hubs never have one
    if not (ip.is_private or ip.is_link_local or ip.is_loopback):
        return False
    return port in {int(p) for p in hub_ports if p}


class JoinedHubStore:
    """The LAN hub this till last joined, as a small JSON file -- the
    "current LAN upstream" half of the split described in the module
    docstring. Written ONLY by `autojoin.discover_and_join` after a 200 from
    `/join`; read by autojoin's ALREADY_JOINED short-circuit and by the
    legacy recogniser (`is_lan_hub_url`'s signal 1).

    Never a sync target: a desktop till's `SyncRelayClient` has no pinned
    transport, so it could not reach a hub's self-signed listener even if
    something pointed it there (that is a separate, pre-existing gap -- see
    the B1 report). Losing this file costs one extra `/join` round trip on
    the next tick and nothing else; that is why a read failure is "no
    record", never an error.
    """

    def __init__(self, path: Path):
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Optional[dict]:
        try:
            body = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(body, dict) or not isinstance(body.get("url"), str):
            return None
        return body

    def url(self) -> Optional[str]:
        record = self.load()
        return record.get("url") if record else None

    def save(self, *, url: str, spki_pin: str, hub_installation_id: str) -> None:
        """Atomic replace (temp file + `os.replace`), so a crash mid-write
        leaves either the old record or the new one, never half of one."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "url": url,
            "spki_pin": spki_pin,
            "hub_installation_id": hub_installation_id,
            "joined_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_bytes(json.dumps(body, sort_keys=True).encode("utf-8"))
        os.replace(tmp, self._path)
