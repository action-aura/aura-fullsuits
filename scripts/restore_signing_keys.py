#!/usr/bin/env python
"""retail-hardware-viewports key-management task -- RESTORE half.

"A backup nobody has restored is not a backup." This script is the reason
scripts/backup_signing_keys.py's manifest carries a recorded public key and
a canary signature per entry, not just ciphertext: restore does not stop at
"the file decrypted and looked like a PEM" -- it re-derives the restored
private key's OWN public key and checks it against what was recorded at
backup time, then checks that the restored key can reproduce the ORIGINAL
key's canary signature. Both are deterministic properties of the exact same
Ed25519 key material, so this is real cryptographic proof the restored
private key IS the one that was backed up, not merely "a valid-looking
key" -- see signing_key_backup_crypto.decrypt_and_verify_entry's own
docstring for the full reasoning, and the mutation proofs in this task's
report for what each check actually catches.

By default this writes restored `{key_id}.pem` files into --out-dir with
the same paranoia owner/app/licensing_service/signing.py's
generate_signing_key() already applies to a freshly-generated key: 0o600
permissions, O_EXCL (refuses to silently overwrite an existing file at the
destination). Pass --force to allow overwriting an existing restore
destination deliberately (e.g. re-running a verification drill).

--verify-only skips writing any PEM to disk at all -- decrypts into memory,
runs the same proof, discards the plaintext, and reports pass/fail per
key_id. This is the mode a scheduled "prove the backup still restores"
drill should use: it proves the backup is good without ever creating a new
copy of live key material on disk.

Usage:
    python scripts/restore_signing_keys.py \\
        --in /secure/offline/signing-keys-backup-2026-09-22.json \\
        --out-dir /path/to/restored-signing-keys

    # verification drill, writes nothing to disk:
    AURA_SIGNING_KEY_BACKUP_PASSPHRASE=... python scripts/restore_signing_keys.py \\
        --in backup.json --verify-only
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signing_key_backup_crypto import (  # noqa: E402
    KEY_ID_RE,
    SigningKeyBackupError,
    decrypt_and_verify_entry,
    derive_fernet_key,
)

_DEFAULT_PASSPHRASE_ENV_VAR = "AURA_SIGNING_KEY_BACKUP_PASSPHRASE"


def _read_passphrase(env_var: str) -> str:
    value = os.environ.get(env_var, "")
    if value:
        return value
    if sys.stdin.isatty():
        return getpass.getpass(f"Signing-key backup passphrase (or set {env_var}): ")
    raise SystemExit(
        f"No passphrase available: {env_var} is not set and stdin is not a terminal "
        "(non-interactive run). Set the environment variable explicitly."
    )


def restore_signing_keys(manifest: dict, passphrase: str, out_dir: Path | None, force: bool) -> dict:
    """Pure with respect to the passphrase/manifest it's given (no env
    reads, no prompting) -- tests drive this directly. `out_dir=None` is
    verify-only mode: decrypts and proves every key, writes nothing.

    Returns {"restored": [key_id...], "failed": [{"key_id", "reason"}...]}.
    Never raises for a single key's failure -- one bad entry in a multi-key
    backup must not hide the result for every other key, same reasoning as
    the export side's per-key skip.
    """
    if manifest.get("format_version") != 1:
        raise SigningKeyBackupError(f"Unsupported backup format_version: {manifest.get('format_version')!r}")

    kdf = manifest.get("kdf", {})
    if kdf.get("name") != "scrypt":
        raise SigningKeyBackupError(f"Unsupported KDF: {kdf.get('name')!r}")
    salt = base64.b64decode(kdf["salt_b64"])
    fernet_key = derive_fernet_key(passphrase, salt)

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    restored = []
    failed = []
    for entry in manifest.get("keys", []):
        key_id = entry.get("key_id", "<unknown>")
        try:
            pem_bytes = decrypt_and_verify_entry(entry, fernet_key)
        except SigningKeyBackupError as exc:
            failed.append({"key_id": key_id, "reason": str(exc)})
            continue

        if out_dir is not None:
            # Re-validate key_id here too (decrypt_and_verify_entry already
            # does, but this is the call site that actually turns it into a
            # filesystem path -- defense in depth, same spirit as
            # signing.py's _safe_key_path being checked at every call site
            # that constructs a path from a key_id).
            if not KEY_ID_RE.match(key_id):
                failed.append({"key_id": key_id, "reason": "unsafe key_id, refusing to write a file for it"})
                continue
            dest = out_dir / f"{key_id}.pem"
            flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if force else os.O_EXCL)
            try:
                fd = os.open(dest, flags, 0o600)
            except FileExistsError:
                failed.append({"key_id": key_id, "reason": f"{dest} already exists (pass --force to overwrite)"})
                continue
            with os.fdopen(fd, "wb") as fh:
                fh.write(pem_bytes)

        restored.append(key_id)

    return {"restored": restored, "failed": failed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--in", dest="in_path", required=True, help="Path to a backup JSON file produced by backup_signing_keys.py.")
    parser.add_argument("--out-dir", default=None, help="Directory to write restored *.pem files into. Omit with --verify-only.")
    parser.add_argument("--verify-only", action="store_true", help="Decrypt and prove every key; write nothing to disk.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing *.pem file at the restore destination.")
    parser.add_argument("--passphrase-env", default=_DEFAULT_PASSPHRASE_ENV_VAR)
    args = parser.parse_args()

    if not args.verify_only and not args.out_dir:
        raise SystemExit("--out-dir is required unless --verify-only is given.")

    manifest = json.loads(Path(args.in_path).read_text(encoding="utf-8"))
    passphrase = _read_passphrase(args.passphrase_env)
    try:
        result = restore_signing_keys(
            manifest, passphrase, out_dir=None if args.verify_only else Path(args.out_dir), force=args.force
        )
    finally:
        passphrase = None  # noqa: F841 -- best-effort local clearing

    print(json.dumps({
        "status": "OK" if result["restored"] and not result["failed"] else ("PARTIAL" if result["restored"] else "FAILED"),
        "restored_key_ids": result["restored"],
        "failed": result["failed"],
        "mode": "verify-only" if args.verify_only else f"restored to {args.out_dir}",
    }, indent=2))

    return 0 if result["restored"] and not result["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
