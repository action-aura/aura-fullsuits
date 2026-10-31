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


# ---- streaming (new customer flow, S4): the website's 250 MB installer download ----
#
# read_artifact_bytes() loads the whole file; fine for a 40 MB device update, not for
# an installer served to the website. These two never hold more than one chunk.

STREAM_CHUNK_BYTES = 256 * 1024

# (real path, size, mtime_ns) -> sha256 already computed for exactly that file state.
# Re-hashing 250 MB on every fetch of the same unchanged file would be wasted disk
# reads; any change to the file changes size or mtime and so misses the cache.
_VERIFIED: dict = {}


def _resolve(artifact_path: str) -> str:
    root = os.path.realpath(_storage_root())
    candidate = os.path.realpath(os.path.join(root, artifact_path))
    if os.path.commonpath([root, candidate]) != root or not os.path.isfile(candidate):
        raise ArtifactNotFound(artifact_path)
    return candidate


def artifact_sha256(artifact_path: str) -> tuple[str, int]:
    """(sha256 hex, size) of the stored file, read in chunks (cached per file state)."""
    import hashlib

    path = _resolve(artifact_path)
    info = os.stat(path)
    key = (path, info.st_size, info.st_mtime_ns)
    cached = _VERIFIED.get(key)
    if cached is not None:
        return cached, info.st_size
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(STREAM_CHUNK_BYTES), b""):
            digest.update(chunk)
    if len(_VERIFIED) > 64:
        _VERIFIED.clear()
    _VERIFIED[key] = digest.hexdigest()
    return _VERIFIED[key], info.st_size


def iter_artifact(artifact_path: str):
    """Returns an iterator over the file in chunks; the file closes when the consumer
    stops (finished, disconnected, or errored).

    The path is resolved HERE, eagerly, not inside the generator: a WSGI server
    iterates the response after Flask has popped the request/app context, and
    `current_app` (used by _resolve) no longer exists by then. Found by the first
    streaming test run (RuntimeError: Working outside of application context)."""
    path = _resolve(artifact_path)

    def chunks():
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(STREAM_CHUNK_BYTES), b""):
                yield chunk

    return chunks()
