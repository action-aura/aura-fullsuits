import json

import pytest

from commercial_runtime.licensing_contracts.trust_anchor_loader import (
    TrustAnchorLoadError,
    load_bundled_trust_anchor,
)


def test_missing_file_raises(tmp_path):
    with pytest.raises(TrustAnchorLoadError):
        load_bundled_trust_anchor(tmp_path / "does_not_exist.json")


def test_malformed_json_raises(tmp_path):
    path = tmp_path / "trust_anchor.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(TrustAnchorLoadError):
        load_bundled_trust_anchor(path)


def test_empty_keys_list_raises(tmp_path):
    path = tmp_path / "trust_anchor.json"
    path.write_text(json.dumps({"keys": []}), encoding="utf-8")
    with pytest.raises(TrustAnchorLoadError):
        load_bundled_trust_anchor(path)


def test_malformed_key_entry_raises(tmp_path):
    path = tmp_path / "trust_anchor.json"
    path.write_text(json.dumps({"keys": [{"key_id": "x"}]}), encoding="utf-8")
    with pytest.raises(TrustAnchorLoadError):
        load_bundled_trust_anchor(path)


def test_valid_anchor_loads(tmp_path):
    path = tmp_path / "trust_anchor.json"
    payload = {"keys": [{"key_id": "owner-1", "public_key": "BBBB", "algorithm": "ed25519"}]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_bundled_trust_anchor(path)
    assert loaded == payload
