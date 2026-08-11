"""Phase 9R M11 -- private release artifact storage.

Local/test adapter only, per the governing instruction's own explicit
allowance: "A local/test storage adapter may validate service behavior.
Actual external object-storage delivery remains NOT VERIFIED until real
credentials and infrastructure exist" (infrastructure-availability-audit.md
#6). Reading is only ever reachable through a validated
ReleaseDownloadAuthorization (app/releases/distribution.py) -- the client
never supplies a raw path or object key, only an opaque short-lived token,
so there is no client-facing path-traversal surface. The realpath
containment check below is defense in depth against a malformed
artifact_path already stored server-side (e.g. a future import bug), not a
response to any client input.
"""
from __future__ import annotations

import os

from flask import current_app


class ArtifactNotFound(RuntimeError):
    pass


def _storage_root() -> str:
    root = current_app.config["RELEASE_ARTIFACT_DIRECTORY"]
    os.makedirs(root, exist_ok=True)
    return root


def read_artifact_bytes(artifact_path: str) -> bytes:
    root = os.path.realpath(_storage_root())
    candidate = os.path.realpath(os.path.join(root, artifact_path))
    if os.path.commonpath([root, candidate]) != root:
        # Defense in depth (see module docstring) -- a stored artifact_path
        # somehow resolved outside the storage root.
        raise ArtifactNotFound(artifact_path)
    if not os.path.isfile(candidate):
        raise ArtifactNotFound(artifact_path)
    with open(candidate, "rb") as f:
        return f.read()


def write_artifact_bytes(artifact_path: str, content: bytes) -> None:
    """Test/local-adapter helper only -- a real deployment writes artifacts
    to external object storage (M11's own NOT VERIFIED note), never to this
    local directory."""
    root = os.path.realpath(_storage_root())
    candidate = os.path.realpath(os.path.join(root, artifact_path))
    if os.path.commonpath([root, candidate]) != root:
        raise ArtifactNotFound(artifact_path)
    os.makedirs(os.path.dirname(candidate), exist_ok=True)
    with open(candidate, "wb") as f:
        f.write(content)
