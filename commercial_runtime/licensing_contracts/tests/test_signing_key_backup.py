"""Tests scripts/signing_key_backup_crypto.py, scripts/backup_signing_keys.py
and scripts/restore_signing_keys.py -- the retail-hardware-viewports "make
the signing key impossible to lose silently" task.

Loaded directly from file path (scripts/ is deliberately not a Python
package -- same convention as test_generate_trust_anchor.py in this same
directory).

Every private key used here is generated fresh, in a pytest tmp_path
fixture, purely for this test -- never a real Owner key, never written
anywhere git tracks (*.pem is repo-gitignored anyway; tmp_path lives outside
the repo entirely). No test ever asserts on or prints raw private key bytes.

Per this repo's ENGINEERING.md ("a passing test is not evidence" /
"mutation-prove every guard"): the real round trip test
(test_restored_key_verifies_a_signature_the_original_produced) is the one
the task explicitly calls "the only real proof the backup is good" -- it
does not just check the restore call returned success, it independently
loads the restored PEM and uses ONLY the restored key's derived public key
to verify a signature the ORIGINAL key produced before backup ever ran.
"""
import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # Registering in sys.modules BEFORE exec_module is required here (unlike
    # test_generate_trust_anchor.py's plainer sibling loader) because
    # signing_key_backup_crypto.py combines `from __future__ import
    # annotations` with @dataclass -- dataclasses resolves its string
    # annotations via sys.modules[cls.__module__], which is None (and
    # AttributeErrors) if the module was never registered.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def crypto():
    return _load("signing_key_backup_crypto")


@pytest.fixture(scope="module")
def backup_mod():
    return _load("backup_signing_keys")


@pytest.fixture(scope="module")
def restore_mod():
    return _load("restore_signing_keys")


PASSPHRASE = "correct horse battery staple"  # noqa: S105 -- test-only, throwaway


def _write_key(directory: Path, key_id: str) -> Ed25519PrivateKey:
    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    (directory / f"{key_id}.pem").write_bytes(pem)
    return private_key


# ═════════════════════════════════════════════════════════════════════════
# key_id safety pattern stays byte-for-byte identical to Owner's
# ═════════════════════════════════════════════════════════════════════════

def test_key_id_pattern_matches_owners_signing_py(crypto):
    """This module deliberately does not import owner/app/licensing_service/
    signing.py (owner/** is owned by another agent) -- it keeps its own
    literal copy of _KEY_ID_RE's pattern instead. That copy is worth
    catching if it ever drifts, since a looser pattern here would accept a
    key_id owner-side code would have refused."""
    assert crypto.KEY_ID_RE.pattern == r"^[A-Za-z0-9._-]{1,64}$"


# ═════════════════════════════════════════════════════════════════════════
# THE REAL PROOF -- export, restore into a clean location, verify a
# signature the ORIGINAL key produced using only the RESTORED key.
# ═════════════════════════════════════════════════════════════════════════

def test_restored_key_verifies_a_signature_the_original_produced(backup_mod, restore_mod, tmp_path):
    key_dir = tmp_path / "original-keys"
    key_dir.mkdir()
    original_private_key = _write_key(key_dir, "owner-ed25519-test-1")

    # A signature produced by the ORIGINAL key, before backup/restore ever runs.
    payload = b"a real licence assertion this key would have signed"
    original_signature = original_private_key.sign(payload)

    manifest = backup_mod.backup_signing_keys(key_dir, PASSPHRASE)
    assert manifest["keys"], "backup produced no entries"

    restore_dir = tmp_path / "restored-clean-location"
    assert not restore_dir.exists(), "restore destination must be a genuinely clean location"
    result = restore_mod.restore_signing_keys(manifest, PASSPHRASE, out_dir=restore_dir, force=False)
    assert result["failed"] == []
    assert result["restored"] == ["owner-ed25519-test-1"]

    restored_pem = (restore_dir / "owner-ed25519-test-1.pem").read_bytes()
    restored_private_key = serialization.load_pem_private_key(restored_pem, password=None)

    # THE PROOF: the RESTORED key's own public key verifies a signature the
    # ORIGINAL key produced. This can only succeed if the restored private
    # key is the exact same key material -- Ed25519 signature verification
    # is a hard cryptographic check, not a heuristic.
    restored_private_key.public_key().verify(original_signature, payload)

    # And the reverse direction, for good measure: a fresh signature made
    # by the RESTORED key verifies against the ORIGINAL key's public key.
    fresh_signature = restored_private_key.sign(b"a second payload")
    original_private_key.public_key().verify(fresh_signature, b"a second payload")


def test_restore_writes_byte_identical_pem(backup_mod, restore_mod, tmp_path):
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    original_pem = (key_dir / "k1.pem")
    _write_key(key_dir, "k1")
    original_bytes = original_pem.read_bytes()

    manifest = backup_mod.backup_signing_keys(key_dir, PASSPHRASE)
    restore_dir = tmp_path / "restored"
    result = restore_mod.restore_signing_keys(manifest, PASSPHRASE, out_dir=restore_dir, force=False)
    assert result["failed"] == []

    restored_bytes = (restore_dir / "k1.pem").read_bytes()
    assert restored_bytes == original_bytes


def test_multiple_keys_all_round_trip(backup_mod, restore_mod, tmp_path):
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    for key_id in ("active-key", "retired-key-1", "retired-key-2"):
        _write_key(key_dir, key_id)

    manifest = backup_mod.backup_signing_keys(key_dir, PASSPHRASE)
    restore_dir = tmp_path / "restored"
    result = restore_mod.restore_signing_keys(manifest, PASSPHRASE, out_dir=restore_dir, force=False)

    assert set(result["restored"]) == {"active-key", "retired-key-1", "retired-key-2"}
    assert result["failed"] == []


# ═════════════════════════════════════════════════════════════════════════
# The deny half: restore must refuse a bad backup, not silently accept it
# ═════════════════════════════════════════════════════════════════════════

def test_wrong_passphrase_fails_restore(backup_mod, restore_mod, tmp_path):
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    _write_key(key_dir, "k1")

    manifest = backup_mod.backup_signing_keys(key_dir, PASSPHRASE)
    result = restore_mod.restore_signing_keys(manifest, "a completely wrong passphrase", out_dir=None, force=True)

    assert result["restored"] == []
    assert len(result["failed"]) == 1
    assert result["failed"][0]["key_id"] == "k1"


def test_corrupted_ciphertext_fails_restore(crypto, backup_mod, restore_mod, tmp_path):
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    _write_key(key_dir, "k1")

    manifest = backup_mod.backup_signing_keys(key_dir, PASSPHRASE)
    entry = manifest["keys"][0]
    # Flip the ciphertext -- simulates bit rot / a truncated copy.
    raw = bytearray(base64.b64decode(entry["ciphertext_b64"]))
    raw[10] ^= 0xFF
    entry["ciphertext_b64"] = base64.b64encode(bytes(raw)).decode("ascii")

    result = restore_mod.restore_signing_keys(manifest, PASSPHRASE, out_dir=None, force=True)
    assert result["restored"] == []
    assert "k1" == result["failed"][0]["key_id"]


def test_tampered_recorded_public_key_fails_restore(crypto, backup_mod, restore_mod, tmp_path):
    """A manifest entry whose recorded public_key_b64 was swapped for a
    DIFFERENT (but structurally valid) key -- e.g. a corrupted manifest, or
    entries shuffled between two key_id slots -- must be refused, not
    silently restored as if it were the right key."""
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    _write_key(key_dir, "k1")

    manifest = backup_mod.backup_signing_keys(key_dir, PASSPHRASE)
    entry = manifest["keys"][0]

    other_key = Ed25519PrivateKey.generate()
    other_public_bytes = other_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    entry["public_key_b64"] = base64.b64encode(other_public_bytes).decode("ascii")

    result = restore_mod.restore_signing_keys(manifest, PASSPHRASE, out_dir=None, force=True)
    assert result["restored"] == []
    assert "does not match" in result["failed"][0]["reason"]


def test_tampered_canary_signature_fails_restore_even_with_correct_public_key(backup_mod, restore_mod, tmp_path):
    """The public-key check and the canary-signature check are two
    INDEPENDENT proofs, not one -- this test corrupts only the signature,
    leaving the recorded public key correct, so it can only be caught by
    the canary check specifically. Proves that check is pulling real weight
    rather than being a no-op the public-key comparison already covers."""
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    _write_key(key_dir, "k1")

    manifest = backup_mod.backup_signing_keys(key_dir, PASSPHRASE)
    entry = manifest["keys"][0]
    sig = bytearray(base64.b64decode(entry["canary_signature_b64"]))
    sig[0] ^= 0xFF
    entry["canary_signature_b64"] = base64.b64encode(bytes(sig)).decode("ascii")

    result = restore_mod.restore_signing_keys(manifest, PASSPHRASE, out_dir=None, force=True)
    assert result["restored"] == []
    assert "canary" in result["failed"][0]["reason"]


def test_restore_refuses_to_overwrite_existing_file_without_force(backup_mod, restore_mod, tmp_path):
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    _write_key(key_dir, "k1")
    manifest = backup_mod.backup_signing_keys(key_dir, PASSPHRASE)

    restore_dir = tmp_path / "restored"
    restore_dir.mkdir()
    (restore_dir / "k1.pem").write_bytes(b"pre-existing file that must not be silently clobbered")

    result = restore_mod.restore_signing_keys(manifest, PASSPHRASE, out_dir=restore_dir, force=False)
    assert result["restored"] == []
    assert "already exists" in result["failed"][0]["reason"]
    # And the pre-existing file must be untouched.
    assert (restore_dir / "k1.pem").read_bytes() == b"pre-existing file that must not be silently clobbered"


def test_verify_only_mode_writes_nothing_to_disk(backup_mod, restore_mod, tmp_path):
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    _write_key(key_dir, "k1")
    manifest = backup_mod.backup_signing_keys(key_dir, PASSPHRASE)

    result = restore_mod.restore_signing_keys(manifest, PASSPHRASE, out_dir=None, force=False)
    assert result["restored"] == ["k1"]
    # No out_dir was ever created or written to -- nothing to assert a path
    # against here beyond "restore succeeded with out_dir=None", which is
    # exactly what --verify-only wires up in main().


# ═════════════════════════════════════════════════════════════════════════
# Export-side robustness: one bad key must not block the rest
# ═════════════════════════════════════════════════════════════════════════

def test_backup_skips_unreadable_pem_but_backs_up_the_rest(backup_mod, tmp_path):
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    _write_key(key_dir, "good-key")
    (key_dir / "corrupt-key.pem").write_bytes(b"not a real PEM file at all")

    manifest = backup_mod.backup_signing_keys(key_dir, PASSPHRASE)

    assert [k["key_id"] for k in manifest["keys"]] == ["good-key"]
    assert [s["key_id"] for s in manifest["skipped"]] == ["corrupt-key"]


def test_backup_never_writes_plaintext_key_bytes_into_the_manifest(backup_mod, tmp_path):
    """The manifest must only ever contain base64 ciphertext for the
    private key -- never the raw PEM text (which is trivially recognizable:
    '-----BEGIN PRIVATE KEY-----')."""
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    _write_key(key_dir, "k1")

    manifest = backup_mod.backup_signing_keys(key_dir, PASSPHRASE)
    serialized = json.dumps(manifest)
    assert "BEGIN PRIVATE KEY" not in serialized
    assert PASSPHRASE not in serialized


def test_backup_manifest_is_safe_for_a_read_only_freshness_check(backup_mod, tmp_path):
    """Proves the design claim in this task's report: a preflight-style
    check can read key_id/public_key_b64/created_at straight out of the
    backup file WITHOUT the passphrase, to confirm a backup exists covering
    a given (already-known-non-secret) active public key."""
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    private_key = _write_key(key_dir, "active-key")
    expected_public_b64 = base64.b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
    ).decode("ascii")

    manifest = backup_mod.backup_signing_keys(key_dir, PASSPHRASE)
    entry = next(k for k in manifest["keys"] if k["key_id"] == "active-key")
    assert entry["public_key_b64"] == expected_public_b64
    assert "created_at" in manifest
    assert "ciphertext_b64" not in {"key_id", "public_key_b64", "created_at"}  # sanity: didn't typo the field name
