"""Uploaded files (expense attachments, release artifacts) must live on named
volumes in the oracle-free compose file, mounted at the paths config.py
actually writes to. Found 2026-10-10: they sat in the container's writable
layer and every `up -d --build` redeploy deleted them. The expected paths are
read from the real config defaults, not retyped here.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_DIR = Path(__file__).resolve().parents[1]
_COMPOSE = _DIR / "deploy" / "oracle-free" / "docker-compose.yml"
_DOCKERFILE = _DIR / "Dockerfile.staging"
_IMAGE_WORKDIR = "/app/owner"  # Dockerfile.staging WORKDIR; config defaults are cwd-relative


def _owner_service() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"]["owner"]


def _volume_for(owner: dict, container_path: str) -> str | None:
    for entry in owner["volumes"]:
        source, _, target = entry.partition(":")
        if target.split(":")[0] == container_path:
            return source
    return None


def _default_dirs() -> dict[str, str]:
    from app.config import BaseConfig

    # BaseConfig defaults are os.getcwd()-relative; in the image cwd is /app/owner.
    cwd = str(Path.cwd())
    return {
        "OWNER_EXPENSE_ATTACHMENT_DIR": BaseConfig.EXPENSE_ATTACHMENT_DIRECTORY.replace(cwd, _IMAGE_WORKDIR).replace("\\", "/"),
        "OWNER_RELEASE_ARTIFACT_DIR": BaseConfig.RELEASE_ARTIFACT_DIRECTORY.replace(cwd, _IMAGE_WORKDIR).replace("\\", "/"),
    }


def test_upload_directories_are_named_volumes_at_the_configured_paths():
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    owner = compose["services"]["owner"]
    for env_key, path in _default_dirs().items():
        assert owner["environment"][env_key] == path, f"{env_key} must be pinned to the config default {path}"
        volume = _volume_for(owner, path)
        assert volume, f"no volume mounted at {path} on the owner service"
        assert volume in compose["volumes"], f"{volume} is mounted but not declared as a top-level named volume"


def test_dockerfile_creates_upload_directories_so_volumes_inherit_aura_ownership():
    text = _DOCKERFILE.read_text(encoding="utf-8")
    for path in _default_dirs().values():
        assert path in text, f"Dockerfile.staging must mkdir {path} before the chown, or the aura user cannot write to a fresh volume"
