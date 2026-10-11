"""TestingConfig artifact directories must not depend on the process cwd
(ported from the August master lineage, f89948d4).

Run from the repo root, the old os.getcwd()/var/* default put test-generated
Ed25519 private-key PEMs in an untracked <repo>/var/. This imports the config
in a fresh interpreter whose cwd is a different directory, with the override
env vars removed, and asserts every default sits under owner/.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

OWNER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OVERRIDES = (
    "OWNER_TEST_SIGNING_KEY_DIRECTORY", "OWNER_TEST_EXPENSE_ATTACHMENT_DIR", "OWNER_TEST_RELEASE_ARTIFACT_DIR",
)
_PROBE = (
    "import sys; sys.path.insert(0, sys.argv[1]);"
    "from app.config import TestingConfig as C;"
    "print(C.SIGNING_KEY_DIRECTORY); print(C.EXPENSE_ATTACHMENT_DIRECTORY); print(C.RELEASE_ARTIFACT_DIRECTORY)"
)


def test_testing_config_artifact_dirs_are_anchored_to_owner_root_not_cwd():
    env = {k: v for k, v in os.environ.items() if k not in _OVERRIDES}
    with tempfile.TemporaryDirectory() as elsewhere:
        out = subprocess.run(
            [sys.executable, "-c", _PROBE, OWNER_ROOT], cwd=elsewhere, env=env,
            capture_output=True, text=True, check=True,
        ).stdout.split()
        assert len(out) == 3
        for path in out:
            assert os.path.commonpath([os.path.realpath(path), os.path.realpath(OWNER_ROOT)]) == os.path.realpath(OWNER_ROOT), path
            assert os.path.commonpath([os.path.realpath(path), os.path.realpath(elsewhere)]) != os.path.realpath(elsewhere), path