"""Automatic, licence-proven device membership for the LAN site relay
(retail schema v30, `docs/launch-readiness/lan-restaurant-design.md`).

THE OWNER'S RULING THIS IMPLEMENTS: pairing must be FULLY AUTOMATIC. No QR
code, no pasted payload, no IP address, no port number -- nothing a shop
owner ever has to type or even look at. Devices on one LAN find each other
(discovery/addressing is a separate, not-this-file concern -- see
`beacon.py`) and the ONLY thing that decides whether two of them belong to
the SAME SHOP is whether they hold an Owner-signed licence assertion naming
the SAME `license_public_id`. The manual `pairing.py` / `POST /pair` flow
(an operator-issued, single-use code) stays as a fallback, but this module
is the primary path from here on.

THE MECHANISM. Every activated device already holds an Owner-signed
assertion envelope, verifiable completely offline against the trust anchor
bundled in every install:

  * stored at `LicenseStateRecord.assertion_envelope_json`
    (`commercial_runtime/licensing_contracts/state_repository.py`);
  * its payload carries `license_public_id`, `installation_public_id` and
    `device_key_fingerprint` (`assertion_verifier.py`'s
    `ALLOWED_PAYLOAD_FIELDS`);
  * verified against the trust anchor via
    `assertion_verifier.verify_assertion` / `trust_store.OwnerTrustStore`,
    exactly like every other Owner-signed artefact in this codebase
    (`roster.py`'s roster envelope is the closest sibling).

So two devices are in the same shop **iff their assertions carry the same
`license_public_id`** -- checkable by both sides with zero Owner contact,
which is what makes this work even on a shop's LAN with no internet at all
(the same operating assumption `lan-restaurant-design.md` sec7 already makes
for the rest of this package).

THIS MODULE DOES NOT TOUCH THE NETWORK OR THE DATABASE. `verify_membership`
is pure verification logic -- the caller (`routes.py`'s `POST /join`) is the
one that decides what to do with a verified result (write `site_paired_
devices` via `store.pair_device`, exactly as `/pair` does). This mirrors
`roster.py`'s `verify_roster_envelope` / `store_roster` split: verification
and persistence are two different concerns with two different test surfaces.

WHY `verify_assertion` IS CALLED RATHER THAN RE-IMPLEMENTED. `verify_
membership` below is deliberately a THIN caller of `assertion_verifier.
verify_assertion` -- the same function every other Owner-signed-assertion
consumer in this codebase already uses (`activation.py`, `checkin_
scheduler.py`) -- rather than a second, parallel signature/expiry/allowlist
checker. `verify_assertion`'s signature is:

    verify_assertion(envelope, *, trust_store, expected_product_code,
                      expected_platform, expected_installation_id,
                      expected_device_key_fingerprint, trusted_now)
                      -> VerifiedAssertion

Reusing it means every one of its OWN guarantees -- untrusted-key rejection,
signature verification, clock-skew-tolerant expiry, the forbidden-field/
forbidden-marker payload allowlist -- comes for free and stays in lockstep
with every other caller, rather than being a second copy that could drift.

`expected_product_code`/`expected_platform` ARE DELIBERATELY SELF-
REFERENTIAL HERE, NOT HARDCODED AND NOT PART OF THE SHOP BOUNDARY. The join
mechanism's whole stated contract is "the ONLY thing that decides which shop
a device belongs to is the licence it shares with the others" -- product
code and platform are not mentioned anywhere in that contract, and this
module has no business inventing a second boundary `verify_assertion`
was never asked to enforce here (a Windows till and an Android tablet on the
same licence must both be able to join the same hub). But `verify_assertion`
requires both arguments unconditionally -- there is no optional/None form.
So both are read from the SAME envelope's own payload before calling it,
which makes `verify_assertion`'s internal product/platform comparison a
trivial self-match: it always passes, on purpose, and therefore never
decides anything. It still runs `verify_assertion`'s one-and-only supported
code path (no special-cased "skip this check" branch to introduce and keep
in sync), and if `verify_assertion`'s own contract ever changes to make one
of these fields load-bearing for a reason unrelated to shop identity, this
call site does not have to change at all.

WHAT VERIFY_MEMBERSHIP DOES, IN ORDER -- EVERY STEP LOAD-BEARING:

  1. `verify_assertion(...)` against the trust store. An assertion not
     signed by a key this installation trusts is refused (propagates
     `UNKNOWN_SIGNING_KEY`, or whatever other reason code `verify_assertion`
     raises -- expired, not-yet-valid, malformed, forbidden field, and so
     on). This same call also enforces checks 3 and 4 below via its
     `expected_installation_id`/`expected_device_key_fingerprint`
     parameters -- see "WHY verify_assertion IS CALLED" above for why this
     is one call, not three.
  2. The verified payload's `license_public_id` must equal
     `expected_license_public_id` (the caller's OWN licence, resolved
     locally -- see `routes.py`'s `license_public_id_provider`). THIS IS THE
     SHOP BOUNDARY. A different licence is a different shop, full stop:
     refused with `LICENSE_MISMATCH`. This is checked ONLY after step 1 has
     already proven the envelope is a genuine, current, Owner-signed
     assertion -- reading `license_public_id` out of an unverified envelope
     first would mean branching on attacker-controlled bytes before they
     have been proven to come from Owner at all, the same discipline
     `roster.py`'s own docstring states for its payload fields.
  3. The verified payload's `installation_public_id` must equal
     `claimed_installation_id` -- enforced BY step 1's `expected_
     installation_id` argument, not a second, separate check. Raised as
     `ASSERTION_INSTALLATION_MISMATCH` (`verify_assertion`'s own reason
     code) if the device presents an assertion for one installation while
     claiming to BE a different one.
  4. THE DEVICE-KEY-FINGERPRINT CHECK -- THE ONE WHOSE ABSENCE TURNS THIS
     WHOLE DESIGN INTO AN OPEN DOOR. An assertion envelope IS NOT SECRET --
     it sits in a local SQLite column
     (`LicenseStateRecord.assertion_envelope_json`) and nothing about it
     needs to be kept confidential to do its normal job of proving licence
     membership to Owner. Without this check, anyone who can read (or
     simply be handed) another device's assertion could present it here
     alongside THEIR OWN public key and be admitted as if they were that
     other device, because everything else about the assertion -- the
     signature, the licence, the installation id -- would still verify.
     The defense is: `verified.payload["device_key_fingerprint"]` must
     match the fingerprint of the key the caller ACTUALLY presented in this
     very request (`claimed_device_public_key`), computed with `device_
     identity.fingerprint_of` -- the SAME derivation Owner's own
     `device_identity.py::fingerprint_of` uses (SHA-256 of the raw 32-byte
     Ed25519 public key), found and reused here rather than invented a
     second time. This is enforced by passing that computed fingerprint as
     `expected_device_key_fingerprint` into step 1's `verify_assertion`
     call, which raises `ASSERTION_DEVICE_MISMATCH` on a mismatch -- the
     exact reason code Owner's own "stolen assertion replayed from a
     different device" test pins in
     `licensing_contracts/tests/test_assertion_verifier.py`. Attacker B
     holding A's genuine, validly-signed, same-licence assertion plus B's
     OWN public key can never pass this: A's assertion carries A's device's
     fingerprint, which will never equal the fingerprint of B's key.

`trusted_now` IS THE INJECTABLE CLOCK PARAMETER, NOT `datetime.now()` CALLED
INSIDE THIS MODULE -- matching the exact idiom `verify_assertion`'s own two
real production callers already use
(`licensing_contracts/activation.py`, `licensing_contracts/
checkin_scheduler.py`: both resolve `trusted_now=datetime.now(timezone.utc)`
at their OWN call site and pass it in, rather than have `verify_assertion`
reach for the wall clock itself). `routes.py`'s `POST /join` handler follows
the identical pattern: it computes `datetime.now(timezone.utc)` once, at the
top of the request, and threads it through here -- which is also what makes
this module trivially testable with a fixed clock (see
`test_site_relay_join.py`'s expiry tests) without any monkeypatching.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime

from commercial_runtime.licensing_contracts.assertion_verifier import (
    AssertionVerificationError,
    verify_assertion,
)
from commercial_runtime.licensing_contracts.device_identity import fingerprint_of
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore


class JoinError(Exception):
    """Raised by `verify_membership`. Carries a `reason_code` string a
    caller (`routes.py`'s `POST /join` handler) returns to the client
    verbatim as `{"reason_code": ...}`, matching this package's established
    convention (see `auth.py`'s `SiteAuthError`, `pairing.py`'s
    `PairingError`, `roster.py`'s `RosterError`)."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


def hub_identity(*, installation_id: str, device_public_key: str, assertion_envelope: dict) -> dict:
    """Builds this hub's own identity payload for `GET /identity`
    (`routes.py`). Pure data shaping -- no I/O, no verification of anything.

    Everything in the returned dict is already public (see `routes.py`'s own
    docstring on `GET /identity` for why that route is deliberately
    unauthenticated): an installation id, an Ed25519 public key, and an
    Owner-signed assertion envelope that is worthless to anyone who does not
    also hold the matching private key (see this module's own "THE
    DEVICE-KEY-FINGERPRINT CHECK" section -- an assertion alone can never be
    used to join anywhere without the key it names)."""
    return {
        "installation_id": installation_id,
        "device_public_key": device_public_key,
        "assertion": assertion_envelope,
    }


def verify_membership(
    envelope: dict,
    *,
    trust_store: OwnerTrustStore,
    expected_license_public_id: str,
    claimed_installation_id: str,
    claimed_device_public_key: str,
    trusted_now: datetime,
) -> dict:
    """Independently verifies that `envelope` is a currently-valid,
    Owner-signed licence assertion belonging to THIS licence
    (`expected_license_public_id`), naming `claimed_installation_id`, and
    bound to the key `claimed_device_public_key` actually presented alongside
    it. Returns the verified assertion's PAYLOAD dict on success. Raises
    `JoinError` with a specific `reason_code` on any failure -- never returns
    a partially-trusted result, matching `verify_assertion`'s own stated
    promise (see this module's docstring for the full, numbered check
    sequence and why each one is load-bearing).
    """
    # ── The device-key fingerprint of the key the caller ACTUALLY presented
    # in THIS request -- computed BEFORE the assertion is even looked at, so
    # it can be fed into verify_assertion's own comparison rather than
    # trusting anything the envelope itself claims about the key. Malformed
    # base64 or a key of the wrong length is INVALID_REQUEST, not a
    # verification failure -- routes.py already rejects this shape earlier
    # (see that module's own docstring, step 2), but this function is
    # re-checked here too so it never depends on being called only from
    # behind that specific route.
    try:
        raw_public_key = base64.b64decode(claimed_device_public_key, validate=True)
    except (TypeError, ValueError, binascii.Error) as exc:
        raise JoinError("INVALID_REQUEST", f"device_public_key is not valid base64: {exc}") from exc
    if len(raw_public_key) != 32:
        raise JoinError("INVALID_REQUEST", "device_public_key must decode to exactly 32 bytes.")
    claimed_fingerprint = fingerprint_of(raw_public_key)

    # ── product_code/platform: read from the SAME envelope's own payload so
    # verify_assertion's mandatory comparison is a self-match and therefore
    # never decides membership -- see the module docstring's "DELIBERATELY
    # SELF-REFERENTIAL" section for why this is not a shortcut around a real
    # check, but the documented absence of one that was never part of this
    # design's shop-boundary contract. A malformed envelope (no dict
    # payload) simply yields None/None here; verify_assertion will reject
    # the envelope for its actual shape problem before it would ever reach
    # its own product/platform comparison.
    raw_payload = envelope.get("payload") if isinstance(envelope, dict) else None
    expected_product_code = raw_payload.get("product_code") if isinstance(raw_payload, dict) else None
    expected_platform = raw_payload.get("platform") if isinstance(raw_payload, dict) else None

    # ── Checks 1, 3, 4 (see module docstring): untrusted-key rejection,
    # signature, expiry, and the installation-id / device-fingerprint
    # cross-checks -- all in this one call, all of verify_assertion's own
    # guarantees, none re-implemented here.
    try:
        verified = verify_assertion(
            envelope,
            trust_store=trust_store,
            expected_product_code=expected_product_code,
            expected_platform=expected_platform,
            expected_installation_id=claimed_installation_id,
            expected_device_key_fingerprint=claimed_fingerprint,
            trusted_now=trusted_now,
        )
    except AssertionVerificationError as exc:
        raise JoinError(exc.reason_code, str(exc)) from exc

    # ── Check 2 -- THE SHOP BOUNDARY. Only reached once the envelope has
    # already been proven genuine, current, and bound to the presented key
    # (steps 1/3/4 above) -- never read license_public_id out of an
    # unverified envelope before that (see module docstring).
    if verified.payload.get("license_public_id") != expected_license_public_id:
        # LICENSE_MISMATCH is minted here, not reused from
        # licensing_contracts/reason_codes.py -- checked against that
        # module's PUBLIC_REASON_CODES/LOCAL_REASON_CODES first (neither
        # lists it). It fits neither: it is not a code Owner ever sends a
        # client (PUBLIC_REASON_CODES documents Owner's own vocabulary) and
        # it is not a licensing-client-side local failure either -- it is
        # produced by, and only meaningful to, this hub's own LAN-facing
        # join gate, exactly the same reasoning `auth.py` gives for minting
        # its own `INSTALLATION_REVOKED` rather than reusing something
        # adjacent.
        raise JoinError(
            "LICENSE_MISMATCH",
            "Assertion license_public_id does not match this hub's licence -- different shop.",
        )

    return verified.payload
