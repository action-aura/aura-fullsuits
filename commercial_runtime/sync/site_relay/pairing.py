"""Pairing-code issuance and consumption for the LAN site relay's QR pairing
flow (`docs/launch-readiness/lan-restaurant-design.md` sec4, "B. Discovery
and pairing: QR to trust"). This is the missing piece that makes the site
relay actually usable by a shop: everything else in this package (`auth.py`,
`routes.py`, `store.py`) already lets a PAIRED device push/pull, but until
this module exists the only way to pair one at all is to call
`store.pair_device(...)` against the hub's SQLite by hand. This module is
what turns that into the design doc's target experience, verbatim: "On the
till press Connect a device. Scan the code with the tablet." No IP addresses
are ever typed or seen.

CODES LIVE IN MEMORY, DELIBERATELY -- NOT IN A TABLE, AND NOT IN
`site_paired_devices` OR ANY NEW SIXTH-PLUS TABLE. A pairing code is valid
for `PAIRING_CODE_TTL_SECONDS` (five minutes) and exists for exactly one
purpose: to be scanned and consumed once, right after an operator taps
"Connect a device". A hub restart SHOULD invalidate every outstanding code
-- an operator who restarted the till is not mid-pairing any more, and there
is no scenario in which a code issued before a restart should still be
honored after one. Persisting pairing codes would mean a schema migration
for state whose entire purpose is to NOT survive, and -- worse -- would
leave a still-live, unexpired credential sitting on disk after a crash,
readable by anything that can read the database file, for up to five
minutes after the crash. This is the rare case where in-memory is the
CORRECT durability choice, not the lazy one: the property a pairing code
needs ("forgotten on restart") is exactly what in-memory storage gives for
free, and exactly what a table would have to work to defeat.

SECURITY PROPERTIES, each non-negotiable, each explained here rather than
merely asserted:

  * A CODE ONLY EXISTS BECAUSE A HUMAN ASKED FOR ONE. `issue()` is called
    exactly when the operator taps "Connect a device" in Settings -- nothing
    else in this codebase calls it, and nothing should. With no outstanding
    code, `POST /pair` (routes.py) refuses EVERY request it receives, because
    `consume()` finds nothing to match against. That is the property that
    stops this endpoint being a standing open door on the LAN: it is only
    ever briefly "live" while a real person is actively pairing a real
    device, never continuously.

  * HIGH ENTROPY. `secrets.token_urlsafe(24)` -- 24 bytes (192 bits) of
    `secrets`-module randomness, base64url-encoded. The code IS the actual
    secret protecting `POST /pair` (see routes.py's own docstring on that
    route for why TLS does not protect it at all): anyone who can route to
    the hub's LAN port can open a connection and POST to that endpoint, so
    the pairing code has to be the whole of the authentication, not a
    convenience layered on top of something else. 192 bits of entropy over a
    five-minute window makes online guessing hopeless regardless of request
    rate.

  * SINGLE USE. `consume()` removes the code from the store BEFORE it can
    report success, under the SAME lock that guards every other operation on
    this store -- so two requests racing to redeem one code can never both
    win. Whichever caller's `consume()` call observes the code still present
    is the one and only caller that gets to remove it; the loser's lookup
    (performed under the identical lock, so it cannot interleave mid-check)
    finds nothing and is rejected exactly like an unknown code. There is no
    window in which the code is "spent but still present" or "present but
    already spoken for" -- it is atomically one or the other.

  * CONSTANT-TIME COMPARISON. `consume()` never uses `==` or a dict/set
    membership test to decide whether a submitted code matches an
    outstanding one -- both are hash- or short-circuit-based comparisons in
    CPython and are not designed to resist a timing side channel. Every
    outstanding code is compared against the submitted code with
    `hmac.compare_digest`, which takes time independent of how many leading
    bytes match. No code is ever written to a log line or included in a
    `PairingError` message anywhere in this module -- the reason codes below
    are the only thing that ever leaves this store about a rejected attempt.

  * SAME REASON CODE FOR "UNKNOWN" AND "ALREADY USED". `consume()` raises
    `PairingError("PAIRING_CODE_INVALID")` for BOTH a code that was never
    issued at all AND a code that was issued but has already been redeemed
    (it is removed from the store on first successful consumption, so a
    second attempt finds nothing distinguishable from "never existed").
    Returning a different reason for each would let an attacker probing
    codes learn whether a given guess was ever a real, live code -- which is
    itself useful information for narrowing a brute-force attempt, however
    small the marginal advantage. Collapsing both outcomes to one code
    denies that signal entirely. Expiry gets its OWN, different reason
    (`PAIRING_CODE_EXPIRED`) because a stale-but-once-real code is not a
    secrecy question in the same way -- see `consume()`'s own docstring.

  * EXPIRY CHECKED ON CONSUME, AND `purge_expired()` SO THE STORE CANNOT
    GROW UNBOUNDED. A code past `PAIRING_CODE_TTL_SECONDS` is rejected (and
    removed) the moment anyone tries to consume it; `purge_expired()` is a
    separate, explicit sweep for codes that were issued and simply never
    redeemed at all (an operator who tapped "Connect a device" and then gave
    up) -- without it, an install where pairing is used occasionally over
    months would accumulate one dead dict entry per abandoned attempt
    forever. Nothing in this module calls `purge_expired()` on a timer; that
    is a caller's responsibility (mirrors `replay.py`'s own `prune_nonces`,
    which is likewise never self-scheduling), because this module has no
    business owning a background thread.
"""
from __future__ import annotations

import hmac
import secrets
import threading
from datetime import datetime, timezone
from typing import Optional

# Five minutes. See the module docstring's "CODES LIVE IN MEMORY,
# DELIBERATELY" section for why this is short and why a restart -- not just
# this timer -- is also expected to invalidate every outstanding code.
PAIRING_CODE_TTL_SECONDS = 300


class PairingError(Exception):
    """Raised by `PairingCodeStore.consume`. Carries a `reason_code` string
    a caller (routes.py's `POST /pair` handler) returns to the client
    verbatim as `{"reason_code": ...}`, matching this package's established
    convention (see `auth.py`'s `SiteAuthError` for the identical shape).
    Never carries the submitted code itself anywhere -- not in `reason_code`,
    not in the exception's string representation -- see the module
    docstring's constant-time-comparison bullet for why a code must never
    appear in anything that might be logged."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


class PairingCodeStore:
    """An in-memory, single-process store of outstanding pairing codes.
    See the module docstring for why in-memory is the correct choice here,
    not merely the convenient one.

    One instance is meant to live for the lifetime of the hub process (the
    same shape as, e.g., a `SyncService` instance) and be handed to
    `make_site_relay_blueprint(pairing_codes=...)` at registration time. A
    fresh instance -- which is exactly what a process restart produces --
    starts with zero outstanding codes, which is the mechanism by which a
    restart invalidates every code that was outstanding before it.

    Every public method accepts an injectable `now` (defaulting to real UTC
    `now`) purely as a deterministic-testing seam, mirroring every other
    `now=None`-defaulted parameter in this codebase's sync/licensing modules
    (e.g. `replay.py`'s `validate_timestamp`/`consume_nonce`). Production
    callers never pass it.
    """

    def __init__(self) -> None:
        # code -> the UTC datetime it was issued at. Guarded by `_lock` for
        # every read AND write -- see `consume`'s docstring for why even a
        # read (checking whether a code exists) must happen under the same
        # lock as the removal that follows it, not as two separate steps.
        self._codes: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def issue(self, *, now: Optional[datetime] = None) -> str:
        """Mints one new pairing code and records it as outstanding from
        `now` (real UTC time in production). Called exactly once per
        "Connect a device" tap -- see the module docstring's "A CODE ONLY
        EXISTS BECAUSE A HUMAN ASKED FOR ONE" section. Does not invalidate
        any other code already outstanding (an operator pairing two devices
        in quick succession, one QR shown after the other, is a normal
        flow, not a conflict)."""
        now = now or datetime.now(timezone.utc)
        code = secrets.token_urlsafe(24)
        with self._lock:
            self._codes[code] = now
        return code

    def consume(self, code: str, *, now: Optional[datetime] = None) -> None:
        """Redeems `code` -- the single-use, constant-time-checked
        operation this whole module exists to provide correctly. Raises
        `PairingError("PAIRING_CODE_INVALID")` if `code` does not match any
        currently-outstanding code (whether because it was never issued, or
        because it was issued and already consumed -- see the module
        docstring for why both collapse to the identical reason), or
        `PairingError("PAIRING_CODE_EXPIRED")` if it matches an outstanding
        code whose `PAIRING_CODE_TTL_SECONDS` window has elapsed.

        THE WHOLE FIND-AND-REMOVE OPERATION HAPPENS UNDER ONE LOCK
        ACQUISITION, not a lock-protected read followed by a separately
        lock-protected delete -- this is what makes single-use hold even
        under concurrent callers racing on the identical code. If the check
        and the removal were two separate critical sections, two threads
        could both observe the code present during their respective checks
        before either one removes it, and both would then proceed to treat
        the code as successfully consumed. Locking around the ENTIRE
        find-then-remove sequence means only one caller can ever be the one
        that finds the code still present; every other racing caller's own
        lookup (which cannot begin until the winner's lock section has
        finished, and therefore already deleted the code) finds nothing and
        is rejected exactly like an unknown code.

        Every outstanding code is compared against `code` with
        `hmac.compare_digest`, one candidate at a time -- never with `in`,
        `==`, or a dict/set membership test -- so redeeming a code never
        depends on a comparison whose timing could leak how many leading
        bytes of a submitted guess were correct. See the module docstring's
        constant-time-comparison bullet.

        Expiry is checked (and, either way, the matched code is still
        removed -- an expired code is exactly as spent as a successfully
        redeemed one; leaving it in the store would let a later, still-
        within-some-other-window consume() attempt resurrect it) only AFTER
        a match is found, using the MATCHED code's own recorded issue time,
        never the caller-submitted `code` string itself (which carries no
        timestamp of its own)."""
        now = now or datetime.now(timezone.utc)
        with self._lock:
            matched_code: Optional[str] = None
            issued_at: Optional[datetime] = None
            for candidate, candidate_issued_at in self._codes.items():
                if hmac.compare_digest(candidate, code):
                    matched_code = candidate
                    issued_at = candidate_issued_at
                    break

            if matched_code is None:
                raise PairingError("PAIRING_CODE_INVALID")

            # Remove BEFORE returning/raising -- single-use holds
            # regardless of whether the code turns out to also be expired.
            del self._codes[matched_code]

        elapsed = (now - issued_at).total_seconds()
        if elapsed > PAIRING_CODE_TTL_SECONDS:
            raise PairingError("PAIRING_CODE_EXPIRED")

    def purge_expired(self, *, now: Optional[datetime] = None) -> int:
        """Removes every outstanding code whose TTL has elapsed, whether or
        not anyone ever attempts to consume it, and returns how many were
        removed. See the module docstring's expiry bullet for why this
        exists as a separate sweep from the expiry check inside `consume`:
        an abandoned pairing attempt (operator taps "Connect a device", QR
        is shown, nobody ever scans it) leaves a code that `consume` will
        never be called on at all -- this is the only thing that ever
        reclaims it."""
        now = now or datetime.now(timezone.utc)
        with self._lock:
            expired = [
                code for code, issued_at in self._codes.items()
                if (now - issued_at).total_seconds() > PAIRING_CODE_TTL_SECONDS
            ]
            for code in expired:
                del self._codes[code]
            return len(expired)

    def outstanding(self, *, now: Optional[datetime] = None) -> int:
        """The number of currently valid (issued, not yet consumed, not yet
        expired) pairing codes. Does not mutate the store -- an expired-but-
        not-yet-swept code is simply not counted, rather than being removed
        as a side effect of merely asking how many are outstanding (removal
        is `purge_expired`'s job alone, so a caller that only wants to know
        "is anything still pending" never has to reason about this method
        having side effects)."""
        now = now or datetime.now(timezone.utc)
        with self._lock:
            return sum(
                1 for issued_at in self._codes.values()
                if (now - issued_at).total_seconds() <= PAIRING_CODE_TTL_SECONDS
            )


def pairing_payload(
    *,
    base_url: str,
    spki_pin: str,
    hub_installation_id: str,
    hub_device_public_key: str,
    pairing_code: str,
) -> dict:
    """Builds the dict that becomes the QR code's encoded contents (design
    doc sec4: "the hub's Settings shows a QR encoding {site relay URL, hub
    TLS public-key (SPKI) pin, hub installation_id, a short-lived pairing
    code}"). Pure data shaping -- no I/O, no rendering of the QR image
    itself (that is a UI-layer concern, out of scope for this module).

    FIVE FIELDS, TWO OF WHICH LOOK REDUNDANT NEXT TO EACH OTHER AND ARE NOT:

      * `spki_pin` authenticates the TLS CONNECTION the scanning device is
        about to open to `base_url` -- see `tls_identity.py`'s module
        docstring: clients pin this hub's certificate's public key instead
        of trusting a CA or a hostname, because the hub's IP/hostname can
        change (DHCP) while its key does not.

      * `hub_device_public_key` is a COMPLETELY DIFFERENT key, for a
        completely different job: this hub's own Ed25519 SIGNING key (the
        same key `site_paired_devices` stores for every OTHER device, and
        the same key type `auth.py::authenticate` verifies push/pull
        signatures against). It is included here so the newly-paired device
        can later verify the AUTHENTICITY of the hub's UDP addressing
        beacon (design sec4, "Address changes... a signed UDP broadcast
        beacon... Paired devices verify the signature against the hub key
        they learned at pairing") -- a completely separate mechanism from
        the TLS connection the SPKI pin secures. Without this paragraph the
        two keys look like an accidental duplication of the same fact;
        they are not merely different values, they secure two entirely
        different channels (a TCP/TLS handshake versus a connectionless
        UDP datagram) against two entirely different threats.

    `pairing_code` is included in plaintext -- it is meant to be read by
    whatever camera scans the QR, so there is nothing to protect it FROM at
    this stage; its short lifetime and single-use consumption (see
    `PairingCodeStore`) are what keep it safe, not secrecy of the QR image
    itself.
    """
    return {
        "base_url": base_url,
        "spki_pin": spki_pin,
        "hub_installation_id": hub_installation_id,
        "hub_device_public_key": hub_device_public_key,
        "pairing_code": pairing_code,
    }
