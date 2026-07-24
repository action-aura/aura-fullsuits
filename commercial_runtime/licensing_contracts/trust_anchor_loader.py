"""Loads the bundled trust_anchor.json produced by
scripts/generate_trust_anchor.py at build time (Part D step 1). Kept as its
own tiny module, separate from trust_store.py, so the "read a file bundled
at build time" concern stays distinct from "the runtime trust-state
machine" concern -- a future alternate bundling mechanism (e.g. reading from
a Windows resource or an Android asset instead of a loose JSON file) only
has to change this module.
"""
from __future__ import annotations

import json
from pathlib import Path


class TrustAnchorLoadError(ValueError):
    pass


def load_bundled_trust_anchor(path: Path) -> dict:
    if not path.exists():
        raise TrustAnchorLoadError(
            f"No bundled trust anchor found at {path} -- this build was not cut with "
            "scripts/generate_trust_anchor.py. The external licensing API cannot be used."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TrustAnchorLoadError(f"Bundled trust anchor at {path} is not valid JSON: {exc}") from exc

    keys = data.get("keys")
    if not isinstance(keys, list) or not keys:
        raise TrustAnchorLoadError(f"Bundled trust anchor at {path} contains no keys.")
    for entry in keys:
        if not all(k in entry for k in ("key_id", "public_key", "algorithm")):
            raise TrustAnchorLoadError(f"Bundled trust anchor at {path} has a malformed key entry: {entry!r}.")

    return data
