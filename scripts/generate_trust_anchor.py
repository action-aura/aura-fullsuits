#!/usr/bin/env python
"""Phase 7 Part D -- build-time trust-anchor generator.

Run once per release cut, against the specific Owner instance a build is
being produced for, by a release engineer over a controlled channel (never
invoked automatically at product first-run -- that would be exactly the
trust-on-first-use pattern Part D forbids). Writes trust_anchor.json, which
is then bundled into each product build (commercial_runtime/
licensing_contracts/trust_anchor.json for Windows/Python; staged into
Android's assets/ the same way products/*/frontend already is via
stageAuraAssets, per product-trust-bootstrap-design.md).

Usage:
    python scripts/generate_trust_anchor.py \\
        --owner-url http://127.0.0.1:5551/api/licensing/v1 \\
        --out commercial_runtime/licensing_contracts/trust_anchor.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commercial_runtime.licensing_contracts.client import (  # noqa: E402
    LicensingClient,
    LicensingClientConfig,
    LicensingClientError,
)


def generate(owner_url: str, verify_tls: bool = True) -> dict:
    client = LicensingClient(LicensingClientConfig(base_url=owner_url, verify_tls=verify_tls))
    try:
        manifest = client.fetch_signing_keys()
    except LicensingClientError as exc:
        raise SystemExit(f"Could not reach Owner at {owner_url!r}: {exc}") from exc

    active_keys = [k for k in manifest.get("keys", []) if k.get("status") == "ACTIVE"]
    if not active_keys:
        raise SystemExit(
            "Owner returned no ACTIVE signing key -- generate and activate one first "
            "(flask licensing generate-signing-key / activate-signing-key) before cutting a build."
        )

    return {
        "keys": [
            {"key_id": k["key_id"], "public_key": k["public_key"], "algorithm": k["algorithm"]}
            for k in active_keys
        ]
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-url", required=True, help="Owner's external API base, e.g. http://127.0.0.1:5551/api/licensing/v1")
    parser.add_argument("--out", required=True, help="Output path for trust_anchor.json")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification -- local dev only, never for a real release cut")
    args = parser.parse_args()

    anchor = generate(args.owner_url, verify_tls=not args.insecure)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(anchor, indent=2), encoding="utf-8")
    print(f"Wrote trust anchor with {len(anchor['keys'])} key(s) to {out_path}")


if __name__ == "__main__":
    main()
