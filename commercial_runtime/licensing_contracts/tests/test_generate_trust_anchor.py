"""Tests scripts/generate_trust_anchor.py by loading it directly from its
file path (scripts/ is deliberately not a Python package -- these are
one-off release-engineering entry points, not an importable library)."""
import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "generate_trust_anchor.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("generate_trust_anchor", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, json_body):
        self.status_code = 200
        self._json_body = json_body
        self.headers = {}

    def json(self):
        return self._json_body


class FakeSession:
    def __init__(self, response_json):
        self._response = FakeResponse(response_json)
        self.calls = []

    def request(self, method, url, json=None, timeout=None, verify=None, headers=None):
        self.calls.append({"method": method, "url": url})
        return self._response


@pytest.fixture
def mod():
    return _load_module()


def test_generate_filters_to_active_keys_only(mod, monkeypatch):
    fake_manifest = {
        "schema_version": 1,
        "keys": [
            {"key_id": "retired-1", "public_key": "AAAA", "algorithm": "ed25519", "status": "RETIRED"},
            {"key_id": "active-1", "public_key": "BBBB", "algorithm": "ed25519", "status": "ACTIVE"},
        ],
    }
    session = FakeSession(fake_manifest)
    client = mod.LicensingClient(
        mod.LicensingClientConfig(base_url="https://owner.example/api/licensing/v1"), session=session
    )
    monkeypatch.setattr(mod, "LicensingClient", lambda config, **kw: client)

    anchor = mod.generate("https://owner.example/api/licensing/v1")
    assert anchor == {"keys": [{"key_id": "active-1", "public_key": "BBBB", "algorithm": "ed25519"}]}


def test_generate_raises_when_no_active_key(mod, monkeypatch):
    fake_manifest = {"schema_version": 1, "keys": [{"key_id": "retired-1", "public_key": "AAAA", "algorithm": "ed25519", "status": "RETIRED"}]}
    session = FakeSession(fake_manifest)
    client = mod.LicensingClient(
        mod.LicensingClientConfig(base_url="https://owner.example/api/licensing/v1"), session=session
    )
    monkeypatch.setattr(mod, "LicensingClient", lambda config, **kw: client)

    with pytest.raises(SystemExit):
        mod.generate("https://owner.example/api/licensing/v1")


def test_main_writes_expected_json_file(mod, monkeypatch, tmp_path):
    fake_manifest = {"schema_version": 1, "keys": [{"key_id": "active-1", "public_key": "BBBB", "algorithm": "ed25519", "status": "ACTIVE"}]}
    session = FakeSession(fake_manifest)
    client = mod.LicensingClient(
        mod.LicensingClientConfig(base_url="https://owner.example/api/licensing/v1"), session=session
    )
    monkeypatch.setattr(mod, "LicensingClient", lambda config, **kw: client)

    out_path = tmp_path / "trust_anchor.json"
    monkeypatch.setattr(
        "sys.argv",
        ["generate_trust_anchor.py", "--owner-url", "https://owner.example/api/licensing/v1", "--out", str(out_path)],
    )
    mod.main()

    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["keys"][0]["key_id"] == "active-1"
