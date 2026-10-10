"""The internal verify-descriptor route exists ONLY on Android with a secret.

Run against the REAL products/retail/backend/app.py in a subprocess (module
globals such as LICENSING_PLATFORM are read at import time, and the live URL map
is the only honest evidence: a unit test of the blueprint cannot tell whether
app.py registers it on the wrong platform). Each case boots the app against a
throwaway data dir and prints the routes it really registered.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "products" / "retail" / "backend"
RULE = "/api/updates/_internal/verify-descriptor"

_PROBE = r"""
import json, os, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import app as A
print("ROUTES=" + json.dumps(sorted(r.rule for r in A.app.url_map.iter_rules() if "_internal/verify-descriptor" in r.rule)))
"""


def registered_routes(platform, secret):
    env = {k: v for k, v in os.environ.items()
           if k not in ("AURA_DEV", "AURA_SYNC_RELAY_URL", "AURA_OWNER_LICENSING_URL", "AURA_INTERNAL_SHARED_SECRET")}
    with tempfile.TemporaryDirectory() as data:
        env.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND), AURA_APP_DATA=data, AURA_PLATFORM=platform)
        if secret:
            env["AURA_INTERNAL_SHARED_SECRET"] = secret
        done = subprocess.run([sys.executable, "-c", _PROBE, str(ROOT), str(BACKEND)],
                              env=env, capture_output=True, text=True, timeout=240)
    assert done.returncode == 0, done.stderr[-2000:]
    line = next(l for l in done.stdout.splitlines() if l.startswith("ROUTES="))
    return json.loads(line[len("ROUTES="):])


@pytest.mark.parametrize("platform,secret,expected", [
    ("ANDROID", "wiring-test-secret", [RULE]),
    ("ANDROID", "", []),
    ("WINDOWS", "wiring-test-secret", []),
    ("WINDOWS", "", []),
])
def test_route_is_registered_only_on_android_with_a_secret(platform, secret, expected):
    assert registered_routes(platform, secret) == expected
