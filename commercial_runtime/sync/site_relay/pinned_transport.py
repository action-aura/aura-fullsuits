"""A `requests` transport that trusts exactly one TLS public key (SPKI pin)
and nothing else -- the client side of §5 "Layer 1 -- transport" in
docs/launch-readiness/lan-restaurant-design.md.

Why this file has to exist at all, restated from the design doc because it
is the justification for every dangerous-looking line below: every
push/pull body is already Ed25519-signed (relay_client.py), which protects
the UPLOAD direction -- a tampered or forged push is caught by signature
verification server-side. But PULL RESPONSES ARE UNSIGNED. Over cleartext
HTTP, anyone on the shop wifi could sit between a tablet and the hub, and
inject fabricated events into that tablet's pull response -- fake sales,
fake stock adjustments, anything the client would then apply as if the hub
had said it. TLS closes that hole, but the hub's certificate is self-signed
and its IP address is not stable (DHCP), so ordinary chain-of-trust/
hostname verification cannot be used the normal way. Pinning the server's
public key instead of verifying its certificate chain is what makes TLS
usable here at all -- see tls_identity.py's module docstring for the other
half of this reasoning.
"""
from __future__ import annotations

import hmac
import ssl
from typing import Any, Optional

import requests
import requests.adapters
from cryptography import x509
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.poolmanager import PoolKey, PoolManager, _default_key_normalizer

from .tls_identity import spki_pin_from_certificate


class SpkiPinMismatch(Exception):
    """Raised when a TLS peer's certificate does not carry the pinned
    SubjectPublicKeyInfo. This is a security decision, not a transport
    hiccup: it must reach the caller as itself, never silently retried past
    and never collapsed into a generic requests.exceptions.SSLError that a
    caller might treat as "flaky network, try again" (see
    SpkiPinnedAdapter.send() below, and
    test_pinned_session_fails_closed_against_the_wrong_pin's mutation
    proof)."""


class _PinnedHTTPSConnection(HTTPSConnection):
    """Overrides connect() to enforce the SPKI pin against whatever
    certificate the peer actually presents, AFTER the ordinary TLS
    handshake completes. See SpkiPinnedAdapter's docstring for how
    chain/hostname verification is disabled for this connection -- that is
    only safe because this override is mandatory and unconditional."""

    def __init__(self, *args: Any, expected_pin: str, **kwargs: Any) -> None:
        self._expected_pin = expected_pin
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        # super().connect() performs the real TLS handshake (TCP connect +
        # TLS wrap) using whatever ssl_context/cert_reqs this connection was
        # built with -- see SpkiPinnedAdapter.init_poolmanager for how that
        # is configured to skip chain/hostname verification. It does NOT
        # skip the handshake itself; a genuinely broken/absent TLS peer
        # still fails here exactly as it would for a normal connection.
        super().connect()

        # binary_form=True is essential: unlike the dict form of
        # getpeercert(), it returns the peer's raw DER certificate bytes
        # regardless of whether the ssl module itself considers the
        # certificate "verified" (which it never will here, since
        # verify_mode is CERT_NONE) -- exactly why the plan calls for this
        # specific form rather than the ordinary structured one.
        der_cert = self.sock.getpeercert(binary_form=True)
        if not der_cert:
            raise SpkiPinMismatch(
                f"TLS peer {self.host}:{self.port} did not present a certificate "
                f"to pin against -- refusing the connection."
            )

        cert = x509.load_der_x509_certificate(der_cert)
        # Shared with tls_identity.py's own pin derivation -- see that
        # module's spki_pin_from_certificate docstring for why a second,
        # independently written copy of this hash construction would be a
        # silent-acceptance bug waiting to happen, not merely a style
        # nitpick.
        actual_pin = spki_pin_from_certificate(cert)

        # Constant-time comparison. The pin itself is not secret (it is
        # handed out openly on the pairing QR code), so this is not
        # defending against an attacker learning the pin via timing -- it is
        # simply that there is no reason to prefer a short-circuiting `==`
        # for a security-relevant equality check when hmac.compare_digest is
        # one import away and costs nothing.
        if not hmac.compare_digest(actual_pin, self._expected_pin):
            raise SpkiPinMismatch(
                f"TLS peer {self.host}:{self.port} presented a certificate whose "
                f"SPKI pin does not match the pinned identity. Refusing the "
                f"connection -- this is either the wrong host or an attacker on "
                f"the network."
            )


class _PinnedHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _PinnedHTTPSConnection


def _pool_key_normalizer_ignoring_pin(request_context: dict) -> PoolKey:
    """PoolManager caches open connection pools keyed by a PoolKey namedtuple
    built (by urllib3.poolmanager._default_key_normalizer) directly from the
    request context dict, via `key_class(**context)` -- and PoolKey's fields
    are fixed to urllib3's own known connection kwargs. Our `expected_pin`
    entry (threaded through request_context purely so it reaches
    _PinnedHTTPSConnection.__init__, see SpkiPinnedAdapter.init_poolmanager)
    is not one of them, so the stock normalizer raises
    `TypeError: PoolKey.__new__() got an unexpected keyword argument
    'key_expected_pin'` the moment a request is actually made. Stripping it
    out before delegating is safe: one SpkiPinnedAdapter instance is always
    built for exactly one pin, so `expected_pin` never varies across pools
    opened by the same adapter and dropping it from the cache key cannot
    cause a pool for one pin to be reused for another."""
    context = dict(request_context)
    context.pop("expected_pin", None)
    return _default_key_normalizer(PoolKey, context)


class SpkiPinnedAdapter(requests.adapters.HTTPAdapter):
    """A requests.HTTPAdapter that only ever hands out connections whose
    peer certificate's SPKI matches `expected_pin`.

    *** THE SINGLE MOST DANGEROUS LINE IN THIS FILE ***
    init_poolmanager() below configures every connection this adapter opens
    with check_hostname=False and verify_mode=CERT_NONE (i.e. no chain
    verification, no hostname check -- ordinary TLS certificate validation
    is fully OFF). That is only safe because _PinnedHTTPSConnection.connect()
    above unconditionally raises SpkiPinMismatch when the presented
    certificate's SPKI does not match. The pin check is the ENTIRE security
    boundary for this adapter. If that check is ever made optional, removed,
    wrapped in a try/except that swallows it, or short-circuited by a config
    flag, this class silently degrades into "trust any TLS server on the
    network" -- which is strictly WORSE than plain HTTP, because plain HTTP
    at least doesn't look encrypted and safe to the next person who reads
    this code. Do not relax anything in this file without re-reading this
    paragraph.
    """

    def __init__(self, expected_pin: str, **kwargs: Any) -> None:
        self._expected_pin = expected_pin

        # Built once per adapter instance and reused for every connection it
        # opens. PROTOCOL_TLS_CLIENT defaults check_hostname=True and
        # verify_mode=CERT_REQUIRED, so both must be flipped off explicitly
        # -- and in THIS order: Python's ssl module raises ValueError if you
        # try to set verify_mode=CERT_NONE while check_hostname is still
        # True, so check_hostname must be cleared first.
        self._ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._ssl_context.check_hostname = False
        self._ssl_context.verify_mode = ssl.CERT_NONE

        super().__init__(**kwargs)

    def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **pool_kwargs: Any) -> None:
        # cert_reqs/assert_hostname are set here TOO, not only via
        # self._ssl_context, because urllib3 re-derives and OVERWRITES
        # ssl_context.verify_mode from a `cert_reqs` value at handshake time
        # regardless of what the context object already had (see
        # urllib3.connection._ssl_wrap_socket_and_match_hostname). If
        # `cert_reqs` were left unset here, requests' own per-request pool
        # kwargs (built from Session.verify) could silently re-enable chain
        # verification against our self-signed certificate and break every
        # connection closed, not open -- but relying on that fail-closed
        # behavior instead of stating our intent explicitly here would be
        # fragile. pinned_session() below additionally sets Session.verify =
        # False so this never even becomes a question in normal use; this is
        # the second, explicit layer of the same guarantee.
        pool_kwargs.setdefault("ssl_context", self._ssl_context)
        pool_kwargs.setdefault("cert_reqs", ssl.CERT_NONE)
        pool_kwargs.setdefault("assert_hostname", False)
        # Threaded through to _PinnedHTTPSConnection.__init__ via urllib3's
        # own **conn_kw plumbing (HTTPConnectionPool stores unrecognized
        # kwargs in self.conn_kw and forwards them to ConnectionCls(...)) --
        # not a bespoke mechanism, just how urllib3 already passes
        # connection-level config down.
        pool_kwargs["expected_pin"] = self._expected_pin

        self.poolmanager = PoolManager(num_pools=connections, maxsize=maxsize, block=block, **pool_kwargs)

        # Deliberately assigning a NEW dict here rather than mutating
        # self.poolmanager.pool_classes_by_scheme in place. PoolManager
        # aliases that attribute directly to a shared module-level dict
        # object at construction time -- `self.pool_classes_by_scheme =
        # pool_classes_by_scheme` (no .copy()) -- so an in-place mutation
        # (`poolmanager.pool_classes_by_scheme["https"] = ...`) would swap
        # in our pinned HTTPS pool class for EVERY PoolManager in this
        # process, including ones built by completely unrelated code (e.g.
        # the cloud relay client's own `requests` session), silently
        # breaking their normal certificate verification. Assigning a fresh
        # dict here only ever affects this one poolmanager instance.
        self.poolmanager.pool_classes_by_scheme = dict(
            self.poolmanager.pool_classes_by_scheme,
            https=_PinnedHTTPSConnectionPool,
        )
        # key_fn_by_scheme IS already a per-instance copy
        # (`PoolManager.__init__` does `.copy()` for this one, unlike
        # pool_classes_by_scheme above), so mutating it in place here is
        # safe and does not need the same fresh-dict treatment. See
        # _pool_key_normalizer_ignoring_pin's docstring for why the stock
        # normalizer cannot be used unmodified once `expected_pin` is in the
        # request context.
        self.poolmanager.key_fn_by_scheme["https"] = _pool_key_normalizer_ignoring_pin

    def send(self, request: Any, **kwargs: Any) -> Any:
        try:
            return super().send(request, **kwargs)
        except requests.exceptions.RequestException as exc:
            # requests/urllib3 wrap whatever connect() raises inside layers
            # of their own exceptions (typically
            # urllib3.exceptions.MaxRetryError, then
            # requests.exceptions.ConnectionError) rather than propagating it
            # untouched -- requests.adapters.HTTPAdapter.send()'s own except
            # clauses only special-case urllib3's *SSLError* type, so a
            # SpkiPinMismatch raised from connect() would otherwise surface
            # as a generic requests.exceptions.ConnectionError, which a
            # careless caller could retry past exactly like a transient
            # network blip. Unwrap it and re-raise it AS ITSELF so it can
            # never be mistaken for one.
            pin_mismatch = _find_spki_pin_mismatch(exc)
            if pin_mismatch is not None:
                raise pin_mismatch from exc
            raise


def _find_spki_pin_mismatch(exc: BaseException) -> Optional[SpkiPinMismatch]:
    """Searches the full failure shape urllib3/requests may have built
    around a SpkiPinMismatch raised deep inside connect(): the exception
    chain (__cause__/__context__), urllib3.exceptions.MaxRetryError's
    `.reason` attribute, and the `.args` requests.exceptions.RequestException
    subclasses are constructed with (e.g. ConnectionError(underlying_exc,
    request=...) puts `underlying_exc` in .args, not necessarily as
    __cause__). Walking only __cause__ would miss it; walking only .args
    would miss a different wrapping shape -- both are checked so this does
    not silently stop working the next time an urllib3/requests version
    changes exactly how it nests the original error.
    """
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))

        if isinstance(current, SpkiPinMismatch):
            return current

        for attr in ("__cause__", "__context__"):
            nxt = getattr(current, attr, None)
            if isinstance(nxt, BaseException):
                stack.append(nxt)

        reason = getattr(current, "reason", None)
        if isinstance(reason, BaseException):
            stack.append(reason)

        for arg in getattr(current, "args", ()):
            if isinstance(arg, BaseException):
                stack.append(arg)

    return None


class _PinnedSession(requests.Session):
    """Thin Session subclass whose only job is applying a default timeout
    when the caller doesn't specify one -- pinned_session()'s `timeout`
    parameter. Plain requests.Session has no notion of a session-level
    default timeout (every call must pass one explicitly), so this fills
    that gap rather than making every call site remember to pass it."""

    def __init__(self, default_timeout: Optional[float]) -> None:
        super().__init__()
        self._default_timeout = default_timeout

    def request(self, method: str, url: str, **kwargs: Any) -> Any:  # type: ignore[override]
        if self._default_timeout is not None and kwargs.get("timeout") is None:
            kwargs["timeout"] = self._default_timeout
        return super().request(method, url, **kwargs)


def pinned_session(expected_pin: str, *, timeout: Optional[float] = None) -> requests.Session:
    """A Session that will reach ONLY a server presenting the pinned public
    key.

    session.verify = False below is intentional and is the reason the
    adapter's own belt-and-braces cert_reqs/assert_hostname settings (see
    SpkiPinnedAdapter.init_poolmanager) never actually need to fight against
    requests' default CERT_REQUIRED behavior: with Session.verify left at
    its default True, requests would build its OWN default (CA-verifying)
    SSLContext per request and hand it to urllib3 as a higher-priority
    per-request override, which would then try to chain-verify this
    self-signed certificate and fail every single connection. Turning
    ordinary verification off here is what makes room for SPKI pinning to be
    the verification -- it does not turn off verification and leave nothing
    in its place; _PinnedHTTPSConnection.connect() is unconditionally still
    enforced (see SpkiPinnedAdapter's docstring for what "unconditionally"
    is protecting against).
    """
    session = _PinnedSession(default_timeout=timeout)
    session.mount("https://", SpkiPinnedAdapter(expected_pin))
    session.verify = False
    return session
