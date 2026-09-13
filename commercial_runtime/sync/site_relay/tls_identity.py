"""The hub's long-lived TLS identity for the LAN site relay (§5, "Layer 1 --
transport" of docs/launch-readiness/lan-restaurant-design.md).

The one sentence that drives every decision in this file: clients pin the
hub's public key (SPKI), not its certificate and not its hostname -- so the
certificate itself is disposable and its hostname/IP is irrelevant. Nothing
here is validated the ordinary way (chain-of-trust, expiry, hostname) by
anything that matters; the pin IS the identity. That is what makes it safe
for this cert to be self-signed, long-lived, and reissuable without
re-pairing every device on the LAN.

See pinned_transport.py for the client side that actually enforces the pin;
this module only generates/loads the hub's keypair and computes the pin
value both sides must agree on.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import ipaddress
import os
from pathlib import Path
from typing import Tuple, Union

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.x509.oid import NameOID

_COMMON_NAME_DEFAULT = "Aura Site Relay"
_DEFAULT_VALID_DAYS = 3650

_KEY_FILENAME = "site_relay_key.pem"
_CERT_FILENAME = "site_relay_cert.pem"

# Small backdate on not-valid-before so a hub whose wall clock is a few
# minutes fast (or a client with slight clock skew evaluating the cert's
# ordinary bounds, e.g. a browser someone points at the hub for debugging)
# doesn't reject a certificate that was, from the hub's own perspective,
# generated "just now". This is a courtesy for ordinary X.509 validators --
# it has nothing to do with the actual security boundary, which is the pin
# (see the module docstring). Kept small and separate from valid_days so it
# never has to be reasoned about together with the real (multi-year) validity
# window.
_NOT_BEFORE_BACKDATE = datetime.timedelta(hours=1)

PathLike = Union[str, "os.PathLike[str]"]


class SiteTlsIdentityError(Exception):
    """Raised when an on-disk site TLS identity exists but cannot be
    trusted (unparseable key or certificate). Deliberately NEVER handled by
    silently regenerating a fresh identity -- a fresh keypair means a fresh
    SPKI pin, which would instantly and silently strand every LAN device
    that was ever paired against the old one (they would all start failing
    closed against the hub with no diagnosable-from-the-tablet-side reason).
    This mirrors commercial_runtime/licensing_contracts/device_identity.py's
    LocalStateCorruptError -- an unreadable security credential is an
    operator decision, never a silent auto-fix.
    """


def spki_pin_from_certificate(cert: x509.Certificate) -> str:
    """The single, shared derivation of a pin from an already-loaded
    certificate object -- base64(SHA-256(SubjectPublicKeyInfo DER)).

    Both this module (computing the pin to hand out at pairing time) and
    pinned_transport.py (computing the pin of whatever certificate a LAN
    peer actually presents mid-handshake) call THIS function rather than
    each independently re-deriving the digest. A second, independently
    written copy of this logic is exactly the kind of thing that drifts
    silently -- e.g. one copy hashing the whole certificate DER instead of
    just the SubjectPublicKeyInfo -- and the failure mode of that drift is
    not a crash, it is silent acceptance of the wrong key (see
    test_reissued_cert_over_the_same_key_has_the_same_pin's mutation proof
    in test_site_relay_pinning.py, which exists specifically to catch this).

    Hashing the SubjectPublicKeyInfo rather than the whole certificate DER
    is the entire point of "pin the key, not the cert": a certificate
    reissued over the SAME key (new serial, new SANs, new validity window --
    e.g. because the hub's IP changed on a DHCP lease renewal) produces a
    completely different whole-certificate DER, which would break every
    paired device's pin the moment the cert was ever reissued. The
    SubjectPublicKeyInfo is unchanged as long as the key itself didn't
    change, so the pin survives reissuance -- which is exactly the property
    the design doc's IP-SAN-changes-on-DHCP-reboot argument depends on.
    """
    spki_der = cert.public_key().public_bytes(
        encoding=Encoding.DER,
        format=PublicFormat.SubjectPublicKeyInfo,
    )
    digest = hashlib.sha256(spki_der).digest()
    return base64.b64encode(digest).decode("ascii")


def generate_site_tls_identity(
    *, common_name: str = _COMMON_NAME_DEFAULT, valid_days: int = _DEFAULT_VALID_DAYS
) -> Tuple[bytes, bytes]:
    """Returns (private_key_pem, certificate_pem) for a new self-signed TLS
    identity for the hub's LAN listener.

    Key type: EC SECP256R1 -- deliberately NOT Ed25519 and NOT RSA.

    Not Ed25519: this is a TLS certificate that must be accepted by whatever
    TLS stack terminates the LAN connection on every client -- Android/
    OkHttp, this same desktop `requests`/urllib3 stack on a second till, and
    potentially a browser someone points at the hub while debugging. TLS
    support for Ed25519 *certificates* (as opposed to Ed25519 used for
    signing, e.g. the device identity keys in
    commercial_runtime/licensing_contracts/device_identity.py) is still
    uneven across those stacks and OS TLS library versions. EC P-256 is the
    safe, universally-supported choice for a TLS leaf certificate. Do not
    "upgrade" this to Ed25519 to match the device signing keys -- they are a
    completely different key system solving a completely different problem
    (Ed25519 there signs discrete sync request bodies; this key exists only
    to run a TLS handshake). Not RSA either: RSA keys of a size worth using
    today are needlessly large and slow to generate on a till-class machine
    for no benefit over EC here -- there is no compatibility reason to
    prefer RSA for this cert.

    valid_days defaults to 3650 (~10 years) and is deliberately long-lived.
    This certificate is never validated by expiry (or by hostname) by
    anything that matters -- see the module docstring: it is pinned by SPKI,
    not chain-verified. A short-lived cert would therefore buy no additional
    security here; it would only create a real outage on the day it lapses,
    in a shop that may have no internet connection and nobody technical on
    site to notice or renew it. Long-lived is the only sane choice for a
    self-signed, pinned-not-verified certificate in this deployment shape.

    SANs for localhost/127.0.0.1/::1 are included purely as a courtesy so an
    ordinary TLS client doing textbook hostname verification against the
    hub's own loopback listener (e.g. the hub's own SyncService talking to
    its own site relay over http://127.0.0.1 today, or a developer poking
    the LAN port with a normal browser/curl for debugging) doesn't trip over
    a hostname mismatch. They are NOT the security boundary and are not
    trusted for anything a LAN device relies on -- the hub's real LAN
    address is whatever DHCP hands it, which is never in this SAN list, and
    devices never check the SAN at all (see pinned_transport.py, which sets
    check_hostname=False unconditionally). Never reason about this
    certificate's trustworthiness in terms of its SANs.
    """
    private_key = ec.generate_private_key(ec.SECP256R1())

    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)

    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _NOT_BEFORE_BACKDATE)
        .not_valid_after(now + datetime.timedelta(days=valid_days))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.IPAddress(ipaddress.ip_address("::1")),
                ]
            ),
            critical=False,
        )
        # Explicitly not a CA: this cert must never be usable to mint other
        # certificates that some validator might chain-trust; it only ever
        # identifies itself, and only ever via the pin.
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )

    private_key_pem = private_key.private_bytes(
        encoding=Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    certificate_pem = certificate.public_bytes(Encoding.PEM)
    return private_key_pem, certificate_pem


def spki_pin(certificate_pem: bytes) -> str:
    """The base64 SHA-256 of the certificate's SubjectPublicKeyInfo -- the
    value a client pins. Same construction as HPKP/`pin-sha256`. See
    spki_pin_from_certificate's docstring for why SPKI and not the whole
    certificate DER; this is the PEM-bytes-in convenience wrapper around
    that one shared derivation."""
    cert = x509.load_pem_x509_certificate(certificate_pem)
    return spki_pin_from_certificate(cert)


def _load_existing_identity(key_path: Path, cert_path: Path):
    """Returns (key_path, cert_path, pin) if both files exist and parse, else
    None. Never partially trusts one file without the other -- a cert with
    no matching key file (or vice versa) is exactly the "exists but is not
    genuinely usable" case that must raise, not silently regenerate (see
    SiteTlsIdentityError)."""
    if not (key_path.exists() and cert_path.exists()):
        return None

    key_bytes = key_path.read_bytes()
    cert_bytes = cert_path.read_bytes()
    try:
        # Loaded only to prove the key file is genuinely a parseable,
        # unencrypted private key -- the object itself isn't otherwise used
        # here (the pin is derived from the certificate's public key, which
        # load_or_create_site_tls_identity's callers read straight off disk
        # via cert_path when they set up the TLS listener).
        serialization.load_pem_private_key(key_bytes, password=None)
        cert = x509.load_pem_x509_certificate(cert_bytes)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: any parse
        # failure of a security credential must route to the same fail-
        # closed SiteTlsIdentityError, not be misclassified by exception type.
        raise SiteTlsIdentityError(
            f"Existing site TLS identity in {key_path.parent} could not be parsed "
            f"({exc!r}). Refusing to silently regenerate it -- a fresh keypair means "
            f"a fresh SPKI pin, which would instantly strand every device already "
            f"paired to this hub. Back up and remove both {key_path.name} and "
            f"{cert_path.name} manually only if a fresh identity (and a full "
            f"device re-pairing round) is genuinely intended."
        ) from exc

    pin = spki_pin_from_certificate(cert)
    return key_path, cert_path, pin


def load_or_create_site_tls_identity(directory: PathLike) -> Tuple[Path, Path, str]:
    """Returns (key_path, cert_path, pin). Creates the pair once, on first
    call for a given directory, and reuses it forever after -- idempotent by
    design, never regenerating an identity that already exists and parses
    (see _load_existing_identity / SiteTlsIdentityError: regenerating would
    silently invalidate every paired device's pin at once, with no error
    surfaced anywhere a human would see it until devices mysteriously stop
    syncing).

    The private key is written with NO passphrase -- it must be readable by
    the hub process unattended at boot, with nobody present to type a
    passphrase into a POS till at 6am. Its confidentiality instead rests on
    file permissions plus, in practice, the fact that it lives inside the
    app's own per-install data directory rather than anywhere shared.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    key_path = directory / _KEY_FILENAME
    cert_path = directory / _CERT_FILENAME

    existing = _load_existing_identity(key_path, cert_path)
    if existing is not None:
        return existing

    private_key_pem, certificate_pem = generate_site_tls_identity()

    # O_EXCL: refuse to clobber a key file that appears between our
    # existence check above and this write (e.g. another process on the
    # same box also calling this for the first time at the same moment).
    # Whichever process's O_CREAT|O_EXCL wins gets to define the identity
    # every paired device will trust; the loser must read back what the
    # winner wrote rather than overwrite it out from under it -- exactly
    # the same race-safety reasoning as
    # WindowsDpapiDeviceIdentityProvider.generate_new_key() in
    # commercial_runtime/licensing_contracts/device_identity.py.
    #
    # os.O_BINARY matters on Windows for the same documented reason as that
    # file: os.open() defaults to text mode there, which would silently
    # translate 0x0A bytes in the PEM body -- PEM is base64 text with real
    # newlines, so this mostly self-heals, but there is no reason to rely on
    # that when the fix is a zero-cost flag that's a no-op on POSIX anyway.
    binary_flag = getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary_flag, 0o600)
        try:
            os.write(fd, private_key_pem)
        finally:
            os.close(fd)
    except FileExistsError:
        existing = _load_existing_identity(key_path, cert_path)
        if existing is not None:
            return existing
        # The other writer's key file exists but its cert file doesn't yet
        # (we lost the race mid-write) -- an extremely narrow window given
        # this only ever runs once at hub boot. Surface it rather than
        # guess; a caller can simply retry load_or_create_site_tls_identity.
        raise

    # Belt-and-braces re-assertion of owner-only permissions: O_CREAT's mode
    # argument is subject to the process umask on POSIX, so the requested
    # 0o600 above is not guaranteed as-is. On Windows, os.chmod() does not
    # implement real ACL semantics the way POSIX permission bits do -- this
    # is a best-effort restriction, not a strong guarantee, and the honest
    # real protection on Windows is that this file lives inside the app's
    # own per-install data directory rather than anywhere another local
    # account would look. Never log the key material in this function or
    # anywhere near it.
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass

    # The certificate is not secret (it only carries the public key + SANs,
    # exactly what a client learns anyway from the TLS handshake itself), so
    # no O_EXCL/chmod dance is needed for it -- only the private key file's
    # confidentiality matters.
    cert_path.write_bytes(certificate_pem)

    pin = spki_pin(certificate_pem)
    return key_path, cert_path, pin
