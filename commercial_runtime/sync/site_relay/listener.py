"""The hub's LAN-facing TLS listener: a SECOND server, serving ONLY the site
relay's push/pull contract, on its own port, with its own pinned identity.

See `docs/launch-readiness/lan-restaurant-design.md` sec3 ("Listener") and
sec5 ("Layer 1 -- transport"). Two decisions in this file are load-bearing
and both are easy to "simplify" into a serious security regression later, so
both are argued here rather than left implicit.

WHY A SEPARATE FLASK APP, NOT THE EXISTING ONE
----------------------------------------------
`products/retail/backend/app.py` builds one Flask app carrying the whole
product: the POS UI, the session cookie, auth, every business route. It is
served on 127.0.0.1 ONLY (`_run_server`), and that loopback binding is the
sole thing standing between a shop's wifi and that entire surface.

The tempting shortcut is to register the site-relay blueprint on that app and
rebind it to 0.0.0.0. That would hand every device on the cafe wifi -- and
every customer's phone on the same SSID -- the product's whole UI and session
surface, in exchange for saving a few lines here. The design doc refuses it
in as many words: "Do not silently rebind the existing 127.0.0.1 server --
the UI surface stays loopback."

So this module builds a MINIMAL Flask app whose entire route table is the
site relay blueprint, and serves that. The LAN is offered the sync contract
and nothing else. If someone later adds a route to this app, they are adding
it to the LAN-exposed surface, and the narrowness of `build_site_relay_app`
below is what makes that visible rather than accidental.

WHY THE STANDARD LIBRARY'S WSGI SERVER AND NOT WAITRESS
------------------------------------------------------
This codebase is emphatic that a real shop must never be served by a
development server -- see `app.py::_run_server`, which REFUSES to start a
frozen build if waitress is missing rather than fall back to Flask's dev
server. That rule is correct and is not being weakened here, so it is worth
being precise about why it does not transfer to this listener.

waitress cannot terminate TLS. It has no `ssl_context`; it is designed to sit
behind a reverse proxy that does TLS for it. There is no reverse proxy on a
till, and adding one (nginx, or a new Python TLS server dependency) to a
shipped desktop product is a packaging and update decision that belongs to
whoever owns releases, not to this module.

What is left in the standard library is `wsgiref` plus `ssl`, which is what
this uses, with `ThreadingMixIn` so one slow client cannot block the others.
The reason that is adequate HERE and not for the UI is load class, and the
numbers are worth writing down rather than asserting:

    the UI server takes a request per barcode scan, per key press in search,
    per screen -- sustained, bursty, concurrent, with a human waiting;

    this listener takes one push and one pull per paired device per sync
    tick, and the tick default is 10 seconds (`SyncService.start`'s
    `interval_seconds=10.0`). A busy restaurant with a till, three tablets
    and a kitchen device is five devices -- about one request per second,
    total, with no human waiting on any individual one.

If this listener ever grows a route that a human waits on, or starts serving
the UI, that reasoning expires and it must move to a real server. Say so
here so the next person does not discover the assumption by measuring it.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
It does not decide WHETHER to run -- `products/retail/backend/app.py` does,
from `SITE_RELAY_ENABLED`, which is off unless explicitly set to '1'. A hub
is an opt-in role, and this module is inert until something calls `start`.
"""
from __future__ import annotations

import logging
import socket
import ssl
import threading
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from .routes import make_site_relay_blueprint
from .tls_identity import load_or_create_site_tls_identity

_log = logging.getLogger(__name__)

# TLS 1.2 floor. The clients are our own -- desktop `requests`/urllib3 and
# Android OkHttp, both of which have spoken 1.2+ for years -- so there is no
# legacy peer to accommodate, and nothing is gained by allowing 1.0/1.1
# beyond widening the attack surface of a listener that sits on shop wifi.
_MINIMUM_TLS_VERSION = ssl.TLSVersion.TLSv1_2


class _QuietHandler(WSGIRequestHandler):
    """`wsgiref`'s default handler does two things that are actively wrong on
    a POS till, both of which cost real wall-clock per request.

    `address_string()` calls `socket.getfqdn()` on the peer address, i.e. a
    reverse DNS lookup, per request. On a shop LAN with no working reverse
    zone that blocks until the resolver gives up -- seconds, sometimes, for a
    log line nobody reads. Returning the raw address is both faster and more
    useful: an IP is what an operator can actually match against a device.

    `log_message()` writes a line to stderr per request. At one request per
    device per tick, forever, that is an unbounded write to a stream a
    packaged build may not even have. Routed through the module logger
    instead, at DEBUG, so it is available when someone is diagnosing and
    silent when nobody is."""

    def address_string(self):
        return self.client_address[0]

    def log_message(self, format, *args):  # noqa: A002 - signature is fixed by the base class
        _log.debug("site relay %s - %s", self.address_string(), format % args)


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    """One thread per connection, and they are daemons deliberately: a paired
    device that goes away mid-request (a tablet carried out of wifi range,
    which happens constantly in a restaurant) must never keep the till's
    process alive at shutdown. `allow_reuse_address` lets the hub restart
    without waiting out TIME_WAIT on its own port, which otherwise turns
    every restart into a minute of "the hub is down" for every device."""

    daemon_threads = True
    allow_reuse_address = True


class SiteRelayHandle:
    """Everything a caller needs to operate a running hub, in one object.

    `start_site_relay` used to return a bare `(server, pin)` tuple. That was
    enough to serve sync and nothing else -- it could not mint a pairing code
    or tell an operator which devices are connected, so the pairing and beacon
    machinery had no way to be reached and was, in practice, dead code. This
    handle is what `admin_routes.py` is given.

    `installation_id`, `device_public_key` and `signer` are OPTIONAL because a
    hub can legitimately be running before the till has activated: the relay
    binds at boot, activation may happen minutes later on a first-run machine.
    Pairing needs the installation id (it goes in the QR) and the beacon needs
    the signer, so both degrade rather than crash -- see `beacon_started`.
    """

    def __init__(self, *, server, pin, port, pairing_codes, get_conn,
                 installation_id=None, device_public_key=None, beacon=None):
        self.server = server
        self.pin = pin
        self.port = port
        self.pairing_codes = pairing_codes
        self.get_conn = get_conn
        self.installation_id = installation_id
        self.device_public_key = device_public_key
        self.beacon = beacon

    @property
    def beacon_started(self) -> bool:
        return self.beacon is not None

    def addresses(self):
        return local_lan_addresses()

    def primary_url(self) -> str:
        """The URL to put on a pairing QR.

        Best-effort by design: the address is a CONVENIENCE, not the identity.
        A device that later finds the hub has moved learns the new address from
        the signed beacon and keeps trusting the same pinned key (design §4,
        "Identity lives in keys, not addresses"). So picking the wrong
        interface here costs a retry, not a broken pairing.
        """
        addresses = self.addresses()
        host = addresses[0] if addresses else '127.0.0.1'
        return f"https://{host}:{self.port}"


def build_site_relay_app(*, get_conn, **blueprint_kwargs):
    """A Flask app whose ENTIRE route table is the site relay blueprint.

    Imported lazily so this module can be imported (and unit-tested) without
    Flask being importable, matching how `app.py` treats its own optional
    subsystems.

    Nothing else may be registered here. See the module docstring: every
    route on this app is a route exposed to the shop's wifi."""
    from flask import Flask

    relay_app = Flask(__name__)
    relay_app.register_blueprint(make_site_relay_blueprint(get_conn=get_conn, **blueprint_kwargs))
    return relay_app


def build_tls_context(cert_path, key_path):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = _MINIMUM_TLS_VERSION
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return context


def start_site_relay(*, get_conn, host, port, identity_dir,
                     housekeeping=True, nonce_ttl_seconds=None,
                     installation_id=None, device_public_key=None, signer=None,
                     beacon_enabled=True, **blueprint_kwargs):
    """Bind the LAN listener and serve it on a background daemon thread.

    Returns a `SiteRelayHandle` -- the server, the SPKI pin a client must pin
    to reach this hub, the pairing-code store, and the beacon if one started.
    The caller is responsible for surfacing any of it to an operator (see
    `admin_routes.py`); this function just makes sure it all exists.

    The identity is created ONCE, on first call, and reused forever after
    (`load_or_create_site_tls_identity`). That matters more than it looks:
    regenerating the keypair would silently invalidate the pin every already-
    paired device is holding, and the symptom on each of those devices is not
    an error message but sync quietly ceasing to work.

    `port=0` is honoured and is how tests bind an ephemeral port -- read the
    real port back off `server.server_address[1]` afterwards.
    """
    from .pairing import PairingCodeStore

    identity_dir = Path(identity_dir)
    key_path, cert_path, pin = load_or_create_site_tls_identity(identity_dir)

    # Constructed here, not by the caller, for the same reason housekeeping is
    # started here: a pairing store the caller has to remember to create and
    # thread through is a pairing store that ends up None in production while
    # every test passes it explicitly.
    pairing_codes = PairingCodeStore()
    relay_app = build_site_relay_app(get_conn=get_conn,
                                     pairing_codes=pairing_codes,
                                     **blueprint_kwargs)
    server = make_server(host, port, relay_app,
                         server_class=_ThreadingWSGIServer,
                         handler_class=_QuietHandler)

    # The LISTENING socket is wrapped, so every socket returned by accept()
    # is already a TLS socket -- there is no window in which a connection is
    # served in the clear. Wrapping per-accepted-socket instead would leave
    # exactly that window, and it is the classic way this recipe is got
    # wrong.
    server.socket = build_tls_context(cert_path, key_path).wrap_socket(server.socket, server_side=True)

    thread = threading.Thread(target=server.serve_forever,
                              name="aura-site-relay", daemon=True)
    thread.start()

    # Started here rather than left to the caller, deliberately: pruning that
    # something has to remember to switch on is pruning that does not happen.
    # A hub is exactly the install whose nonce store and site log grow, so the
    # sweep belongs to the same act as binding the listener.
    if housekeeping:
        from .replay import DEFAULT_NONCE_TTL_SECONDS
        start_housekeeping(get_conn=get_conn,
                           nonce_ttl_seconds=nonce_ttl_seconds or DEFAULT_NONCE_TTL_SECONDS)

    handle = SiteRelayHandle(
        server=server, pin=pin, port=server.server_address[1],
        pairing_codes=pairing_codes, get_conn=get_conn,
        installation_id=installation_id, device_public_key=device_public_key,
    )

    # The beacon needs a signer and an identity to sign AS. A hub that has not
    # activated yet has neither, and that is a normal transient state on a
    # first-run machine rather than an error -- it simply does not broadcast
    # until it has an identity, and paired devices fall back to their
    # last-known address exactly as design §4 says they do when broadcast is
    # unavailable for any other reason.
    if beacon_enabled and signer is not None and installation_id:
        try:
            from .beacon import BeaconBroadcaster
            broadcaster = BeaconBroadcaster(
                installation_id=installation_id,
                base_url_fn=handle.primary_url,
                spki_pin=pin,
                signer=signer,
            )
            broadcaster.start()
            handle.beacon = broadcaster
        except Exception:
            # Never fatal. A machine that refuses broadcast still serves sync
            # perfectly to any device that knows where it is; losing the
            # beacon costs re-pairing after a DHCP change, not the shop.
            _log.warning("Site relay: the addressing beacon failed to start. "
                         "Sync is unaffected, but devices will not learn a new "
                         "address automatically if this hub's IP changes.",
                         exc_info=True)

    _log.info(
        "Site relay listening on https://%s:%d (SPKI pin %s). This process is now "
        "reachable from the local network on that port; the UI server is unaffected "
        "and stays on loopback. Addressing beacon: %s.",
        host, handle.port, pin, "on" if handle.beacon_started else "off",
    )
    return handle


# Hourly. The two things this sweeps -- expired replay nonces and settled
# site-log rows -- both grow with traffic and neither has a natural ceiling,
# but neither grows fast enough to need attention sooner than this. Running it
# more often would take the single-writer SQLite lock more often for no gain;
# running it daily would let a busy shop accumulate a day of nonces it can
# never match again.
HOUSEKEEPING_INTERVAL_SECONDS = 3600.0


def start_housekeeping(*, get_conn, nonce_ttl_seconds,
                       interval_seconds=HOUSEKEEPING_INTERVAL_SECONDS):
    """Periodically forget what the hub no longer needs. Returns a stop Event.

    WITHOUT THIS, `pruning.py` IS DEAD CODE and both tables grow forever. That
    is worth stating plainly because the failure is invisible for months: a
    till works perfectly while `site_sync_events` climbs into the millions,
    and the first symptom is a shop wondering why its database file is
    gigabytes.

    Every sweep is wrapped so an exception cannot kill the thread. Housekeeping
    failing is not a reason to stop trying -- the usual cause is transient
    (the write lock was held by a sale at that instant), and the correct
    response is to try again next hour rather than to silently stop pruning
    for the lifetime of the process. It is logged at WARNING so a sweep that
    fails EVERY hour is still discoverable, rather than swallowed.

    Counts are logged rather than a bare "housekeeping ran", because the
    difference between "pruned 4,000 rows" and "pruned 0 rows, every hour,
    since March" is the whole diagnostic value of the line.
    """
    from . import pruning

    stop = threading.Event()

    def _loop():
        # An immediate first sweep would fire during boot, competing with
        # migrations and the first sync tick for the write lock. Wait one
        # interval: nothing here is urgent, and a hub that has just started
        # has nothing to prune anyway.
        while not stop.wait(interval_seconds):
            conn = None
            try:
                conn = get_conn()
                result = pruning.prune_nonces_and_log(
                    conn, nonce_ttl_seconds=nonce_ttl_seconds)
                conn.commit()
                _log.info("Site relay housekeeping: pruned %d nonce(s), %d event(s).",
                          result["nonces_pruned"], result["events_pruned"])
            except Exception:
                if conn is not None:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                _log.warning("Site relay housekeeping sweep failed; will retry "
                             "at the next interval.", exc_info=True)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

    threading.Thread(target=_loop, name="aura-site-relay-housekeeping",
                     daemon=True).start()
    return stop


def local_lan_addresses():
    """Best-effort list of this machine's non-loopback addresses, for showing
    an operator where the hub actually is.

    Deliberately NOT used to decide anything -- addressing is learned by
    paired devices from the beacon and pinned by KEY, never by address (see
    the design doc sec4: "Identity lives in keys, not addresses"). This exists
    only so a setup screen can print something a human can sanity-check, and
    so a support call has a starting point.

    `gethostbyname_ex` is unreliable on machines with several interfaces and
    returns nothing useful on some Windows configurations, so its failure is
    not an error -- an empty list just means the screen shows no hint."""
    try:
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
    except OSError:
        return []
    return [a for a in addresses if not a.startswith("127.")]
