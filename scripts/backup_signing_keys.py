#!/usr/bin/env python
"""retail-hardware-viewports key-management task -- EXPORT half.

Backs up every `{key_id}.pem` private key file in an Owner signing-key
directory (OWNER_SIGNING_KEY_DIRECTORY -- owner/app/licensing_service/
signing.py's own storage model) into a single encrypted, passphrase-
protected JSON archive.

WHY THIS EXISTS. owner/docker-compose.yml's own comment on the
`owner_signing_keys` volume: "Back this up via a separate, explicitly
secured process before any real deployment" -- and
docs/owner/phase6/server-signing-key-management.md's Backups section: "A
separate, explicitly-secured key-backup process ... is a deployment-
operator responsibility documented here but not automated by this phase."
Nothing enforced or even checked that anyone ever did this, retired keys
must stay on disk forever for cross-signing continuity (so the exposure
only grows), and losing the PEM bricks every future activation with
UNKNOWN_SIGNING_KEY across the whole installed base. This script is the
automation that was missing.

WHAT A BACKUP FILE CONTAINS, IN THE CLEAR (never secret, by design --
Owner already publishes all of this in every trust-anchor/key-set
manifest): key_id, the derived Ed25519 public key, a canary signature over
a fixed message, a UTC timestamp, and the scrypt KDF parameters/salt used
to derive the encryption key from an operator passphrase. ONLY
`ciphertext_b64` per entry is opaque without that passphrase -- the private
key bytes never touch this file, or any log, or stdout, unencrypted.

A backup file this script produces is therefore SAFE for something like
Owner's `flask commercial preflight` to read and reason about (is there a
backup covering the currently ACTIVE key, how fresh is it) without ever
needing the passphrase -- see this task's report for exactly what check to
add there (deliberately not added here; another agent owns owner/** while
this was written).

THE PASSPHRASE IS NEVER ACCEPTED AS A COMMAND-LINE ARGUMENT -- it would
land in shell history and any process list that can read argv (`ps`, Task
Manager). Same discipline this repo's deploy/staging/backup.py already
documents for PGPASSWORD: read from a named environment variable, or
prompt interactively via getpass() if none is set and a human is attached.

Usage:
    python scripts/backup_signing_keys.py \\
        --key-directory /path/to/signing-keys \\
        --out /secure/offline/signing-keys-backup-2026-09-22.json

    # non-interactive (CI / scheduled):
    AURA_SIGNING_KEY_BACKUP_PASSPHRASE=... python scripts/backup_signing_keys.py --key-directory ... --out ...

Restore + the real round-trip verification live in
scripts/restore_signing_keys.py -- "a backup nobody has restored is not a
backup", so that script is not optional tooling, it is the other half of
this one.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signing_key_backup_crypto import (  # noqa: E402
    SigningKeyBackupError,
    build_key_entry,
    derive_fernet_key,
    new_salt,
)

_DEFAULT_PASSPHRASE_ENV_VAR = "AURA_SIGNING_KEY_BACKUP_PASSPHRASE"


def _read_passphrase(env_var: str) -> str:
    value = os.environ.get(env_var, "")
    if value:
        return value
    if sys.stdin.isatty():
        first = getpass.getpass(f"Signing-key backup passphrase (or set {env_var}): ")
        confirm = getpass.getpass("Confirm passphrase: ")
        if first != confirm:
            raise SystemExit("Passphrases did not match.")
        return first
    raise SystemExit(
        f"No passphrase available: {env_var} is not set and stdin is not a terminal "
        "(non-interactive run). Set the environment variable explicitly."
    )


def backup_signing_keys(key_directory: Path, passphrase: str) -> dict:
    """Returns the backup manifest dict (also what gets written to --out).
    Pure with respect to the filesystem it's given -- no env reads, no
    passphrase prompting -- so tests can drive it directly against a
    tmp_path fixture."""
    if not key_directory.is_dir():
        raise SigningKeyBackupError(f"Signing key directory does not exist: {key_directory}")

    salt = new_salt()
    fernet_key = derive_fernet_key(passphrase, salt)

    entries = []
    skipped = []
    for pem_path in sorted(key_directory.glob("*.pem")):
        key_id = pem_path.stem
        try:
            pem_bytes = pem_path.read_bytes()
            entry = build_key_entry(key_id, pem_bytes, fernet_key)
        except SigningKeyBackupError as exc:
            # One unreadable/malformed key must never block backing up the
            # rest -- mirrors export_signed_keyset_manifest's own
            # per-key try/except continue in owner/app/licensing_service/
            # signing.py, applied independently here since this module does
            # not import owner/**.
            skipped.append({"key_id": key_id, "reason": str(exc)})
            print(f"WARNING: skipping {pem_path.name}: {exc}", file=sys.stderr)
            continue
        entries.append(entry)

    manifest = {
        "format_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "kdf": {"name": "scrypt", "salt_b64": base64.b64encode(salt).decode("ascii"), "n": 2**14, "r": 8, "p": 1},
        "keys": [
            {
                "key_id": e.key_id,
                "public_key_b64": e.public_key_b64,
                "canary_signature_b64": e.canary_signature_b64,
                "ciphertext_b64": e.ciphertext_b64,
            }
            for e in entries
        ],
        "skipped": skipped,
    }
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--key-directory",
        default=os.environ.get("OWNER_SIGNING_KEY_DIRECTORY", ""),
        help="Directory containing *.pem signing-key files (default: $OWNER_SIGNING_KEY_DIRECTORY).",
    )
    parser.add_argument("--out", required=True, help="Path to write the encrypted backup JSON file.")
    parser.add_argument(
        "--passphrase-env",
        default=_DEFAULT_PASSPHRASE_ENV_VAR,
        help=f"Environment variable to read the backup passphrase from (default: {_DEFAULT_PASSPHRASE_ENV_VAR}). "
        "Never pass a passphrase as a bare CLI argument.",
    )
    args = parser.parse_args()

    if not args.key_directory:
        raise SystemExit("--key-directory is required (or set OWNER_SIGNING_KEY_DIRECTORY).")

    passphrase = _read_passphrase(args.passphrase_env)
    try:
        manifest = backup_signing_keys(Path(args.key_directory), passphrase)
    finally:
        passphrase = None  # noqa: F841 -- best-effort local clearing, matches routes.py's license_key discipline

    if not manifest["keys"]:
        print("No signing keys were backed up (directory empty, or every key failed to load).", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(json.dumps({
        "status": "OK",
        "backed_up_key_ids": [k["key_id"] for k in manifest["keys"]],
        "skipped_key_ids": [s["key_id"] for s in manifest["skipped"]],
        "out": str(out_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
