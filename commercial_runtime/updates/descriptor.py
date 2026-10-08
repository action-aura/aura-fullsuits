"""Verification of Owner's signed release descriptor.

The descriptor answers "is there a newer build, and what are its checksum and
size?". Everything the rest of the update flow trusts about the installer
comes from here, so this module FAILS CLOSED: any doubt is an UpdateError, and
the caller never sees a half-trusted offer.

Reuses the licence's crypto on purpose (OwnerTrustStore for the key set,
device_identity.verify_signature for Ed25519, canonical.canonicalize_bytes for
the signed bytes) -- a second implementation of any of those would be a second
place for the two sides to disagree.
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Optional

from commercial_runtime.licensing_contracts.canonical import CanonicalizationError, canonicalize_bytes
from commercial_runtime.licensing_contracts.device_identity import verify_signature

from .versioning import is_newer, parse_semver

MAX_INSTALLER_BYTES = 1_073_741_824  # 1 GiB: far above any real installer, far below "fill the disk"
_SHA256_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)


class UpdateError(Exception):
    """Carries a short, stable `code` the UI maps to a plain-words message."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class ReleaseOffer:
    product_version_id: str
    version: str
    build_number: Optional[int]
    release_channel: Optional[str]
    checksum_sha256: str
    size_bytes: int
    release_notes: Optional[str]
    published_at: Optional[str]
    update_required: bool
    below_min_supported: bool


def _untrusted(message: str) -> UpdateError:
    return UpdateError("DESCRIPTOR_UNTRUSTED", message)


def _invalid(message: str) -> UpdateError:
    return UpdateError("DESCRIPTOR_INVALID", message)


def _verify_envelope(envelope, trust_store) -> dict:
    """Returns the payload only after the signature has been checked."""
    if not isinstance(envelope, dict):
        raise _untrusted("descriptor envelope is not an object")
    payload = envelope.get("payload")
    key_id = envelope.get("signing_key_id")
    signature_b64 = envelope.get("signature")
    if not isinstance(payload, dict) or not isinstance(key_id, str) or not isinstance(signature_b64, str):
        raise _untrusted("descriptor envelope is incomplete")
    if envelope.get("algorithm") != "ed25519":
        raise _untrusted("unsupported signature algorithm")
    public_key_b64 = trust_store.get_public_key_b64(key_id)
    if not public_key_b64:
        # An unknown key is never fetched on the spot: trust only grows through
        # the licence's own rotation-manifest path, never because an update
        # answer asked for it.
        raise _untrusted("signing key is not trusted by this install")
    try:
        signature = base64.b64decode(signature_b64, validate=True)
        public_key = base64.b64decode(public_key_b64, validate=True)
        canonical = canonicalize_bytes(payload)
    except (binascii.Error, ValueError, CanonicalizationError) as exc:
        raise _untrusted("descriptor could not be decoded: %s" % exc) from exc
    if not verify_signature(public_key, canonical, signature):
        raise _untrusted("descriptor signature does not verify")
    return payload


def verify_release_descriptor(
    envelope,
    *,
    trust_store,
    expected_installation_id: str,
    expected_request_id: str,
    current_version: str,
) -> Optional[ReleaseOffer]:
    """Returns a ReleaseOffer, or None when Owner says nothing newer exists.

    Order matters: the signature is checked FIRST, so nothing in an unsigned
    body (not even "update_available: false") is acted on.
    """
    payload = _verify_envelope(envelope, trust_store)

    # Bound to THIS install and THIS request: a validly signed answer that was
    # meant for another device, or captured from an earlier poll, is refused.
    if payload.get("installation_id") != expected_installation_id:
        raise UpdateError("DESCRIPTOR_MISMATCH", "descriptor is for a different installation")
    if payload.get("request_id") != expected_request_id:
        raise UpdateError("DESCRIPTOR_MISMATCH", "descriptor answers a different request")

    release = payload.get("release")
    if payload.get("update_available") is not True or release is None:
        return None
    if not isinstance(release, dict):
        raise _invalid("release is not an object")

    version = release.get("version")
    try:
        parse_semver(version)
        parse_semver(current_version)
    except ValueError as exc:
        raise _invalid("version is not a semantic version: %s" % exc) from exc
    # Client-side downgrade guard, independent of what Owner claims. An Owner
    # (or an attacker replaying an old signed answer) must not be able to walk
    # an install backwards or sideways onto the same build.
    if not is_newer(version, current_version):
        raise _invalid("offered version %s is not newer than %s" % (version, current_version))

    checksum = release.get("artifact_checksum_sha256")
    if not isinstance(checksum, str) or _SHA256_RE.fullmatch(checksum) is None:
        raise _invalid("checksum is not 64 lowercase hex characters")
    size = release.get("artifact_size_bytes")
    # bool is an int subclass; True would pass as size 1 without this.
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_INSTALLER_BYTES:
        raise _invalid("size is outside the accepted range")
    product_version_id = release.get("product_version_id")
    if not isinstance(product_version_id, str) or not product_version_id:
        raise _invalid("product_version_id is missing")

    build_number = release.get("build_number")
    if isinstance(build_number, bool) or not (build_number is None or isinstance(build_number, int)):
        build_number = None
    notes = release.get("release_notes")
    return ReleaseOffer(
        product_version_id=product_version_id,
        version=version,
        build_number=build_number,
        release_channel=release.get("release_channel") if isinstance(release.get("release_channel"), str) else None,
        checksum_sha256=checksum,
        size_bytes=size,
        release_notes=notes if isinstance(notes, str) else None,
        published_at=release.get("published_at") if isinstance(release.get("published_at"), str) else None,
        update_required=payload.get("update_required") is True,
        below_min_supported=payload.get("below_min_supported") is True,
    )
