"""
Per-device login / "one Admin Device" -- unit tests for
commercial_runtime/identity/device_context.py.

Standalone, same spirit as test_device_registry.py: builds its own
in-memory sqlite3 connection (row_factory=sqlite3.Row, matching
registry_db.get_conn()'s real behaviour) via
device_registry.apply_identity_device_schema() directly, and points
AURA_APP_DATA at a pytest tmp_path rather than going through real product
boot state.

The single most important test in this file is
test_local_device_fingerprint_reads_without_importing_cryptography below --
it is the Android/Chaquopy regression guard described in device_context.py's
module docstring (a real, previously-shipped crash: eager import of
anything touching `cryptography` broke app.py's import on Android). Do not
weaken or remove it.

Run:
    pytest commercial_runtime/identity/tests/test_device_context.py -v
"""
import json
import sqlite3
import sys

import pytest

from commercial_runtime.identity import device_context
from commercial_runtime.identity.device_context import (
    DeviceCompanyMismatchError,
    LocalDeviceStateCorruptError,
    binding_enforced,
    local_device_fingerprint,
    local_device_uuid,
    resolve_local_device,
)
from commercial_runtime.identity.device_registry import (
    apply_identity_device_schema,
    get_device,
)


@pytest.fixture(autouse=True)
def _reset_process_cache():
    """resolve_local_device() caches its result in a module-level variable
    (device_context._cached_device) so repeated calls in the same process
    don't re-hit the database -- see that function's docstring. That cache
    would otherwise leak between test functions in this file, since
    products/run_all_tests.py runs every test *file* as its own process but
    all test *functions* within a file share one interpreter. Reset before
    and after every test so each test starts from a clean process state."""
    device_context._cached_device = None
    yield
    device_context._cached_device = None


@pytest.fixture
def app_data(tmp_path, monkeypatch):
    """Points AURA_APP_DATA at an isolated tmp_path for the duration of the
    test, matching how a real install's env var is set -- device_context.py
    resolves this fresh on every call (not at import time), so monkeypatch
    is sufficient without needing to reload the module."""
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))
    return tmp_path


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_identity_device_schema(c)
    return c


def _write_device_key_meta(app_data, *, fingerprint="fp-abc123", status="ACTIVE"):
    licensing_dir = app_data / "licensing"
    licensing_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "algorithm": "ed25519",
        "public_key_fingerprint": fingerprint,
        "created_at": "2026-08-10T00:00:00+00:00",
        "status": status,
        "owner_installation_id": None,
    }
    (licensing_dir / "device_key_meta.json").write_text(json.dumps(meta), encoding="utf-8")


# ── local_device_fingerprint() ────────────────────────────────────────────

def test_local_device_fingerprint_reads_without_importing_cryptography(app_data):
    """THE Android/Chaquopy regression guard. device_identity.py imports
    `cryptography` at module scope; if device_context.py ever imports that
    module (directly or transitively), `cryptography` (or
    device_identity.py itself) shows up as newly imported by this call.
    This is the single most important test in this file.

    Deliberately compares a before/after sys.modules snapshot rather than a
    bare `'cryptography' not in sys.modules` assertion: this suite runs
    (via `python -m pytest commercial_runtime -q`) in ONE shared process
    alongside licensing_contracts' own tests, which DO legitimately import
    `cryptography` (e.g. test_device_identity.py) -- exactly the
    cross-file-pollution shape products/run_all_tests.py's docstring
    describes for AURA_APP_DATA. By test order, `cryptography` may already
    be sitting in sys.modules for reasons that have nothing to do with this
    module. Only a "did THIS call add it" delta check is order-independent
    and still catches the real regression."""
    _write_device_key_meta(app_data, fingerprint="fp-real-one")

    modules_before = set(sys.modules)
    result = local_device_fingerprint()
    newly_imported = set(sys.modules) - modules_before

    assert result == "fp-real-one"
    assert not any(m == "cryptography" or m.startswith("cryptography.") for m in newly_imported), (
        "local_device_fingerprint() must never trigger a cryptography import -- "
        "an eager import of anything touching cryptography crashed app.py on Android "
        "(ModuleNotFoundError('cryptography')); this module must stay safe to import "
        f"eagerly there. Newly imported modules: {sorted(newly_imported)}"
    )
    assert "commercial_runtime.licensing_contracts.device_identity" not in newly_imported


def test_local_device_fingerprint_none_when_no_licensing_dir(app_data):
    assert local_device_fingerprint() is None


def test_local_device_fingerprint_none_when_status_not_active(app_data):
    _write_device_key_meta(app_data, status="RESET_PENDING")
    assert local_device_fingerprint() is None

    _write_device_key_meta(app_data, status="REVOKED_LOCALLY")
    assert local_device_fingerprint() is None


def test_local_device_fingerprint_none_when_field_absent(app_data):
    licensing_dir = app_data / "licensing"
    licensing_dir.mkdir(parents=True, exist_ok=True)
    (licensing_dir / "device_key_meta.json").write_text(
        json.dumps({"algorithm": "ed25519", "status": "ACTIVE"}), encoding="utf-8"
    )
    assert local_device_fingerprint() is None


def test_local_device_fingerprint_none_when_meta_file_corrupt(app_data):
    licensing_dir = app_data / "licensing"
    licensing_dir.mkdir(parents=True, exist_ok=True)
    (licensing_dir / "device_key_meta.json").write_text("{not valid json", encoding="utf-8")
    assert local_device_fingerprint() is None


# ── local_device_uuid() ──────────────────────────────────────────────────

def test_uuid_fallback_generated_and_persisted_on_first_call(app_data):
    result = local_device_uuid()

    assert result
    persisted_path = app_data / "device" / "local_device.json"
    assert persisted_path.exists()
    assert json.loads(persisted_path.read_text(encoding="utf-8"))["device_uuid"] == result


def test_uuid_fallback_returns_same_value_on_subsequent_calls(app_data):
    first = local_device_uuid()
    second = local_device_uuid()
    third = local_device_uuid()

    assert first == second == third


def test_corrupt_local_device_json_raises_and_never_regenerates(app_data):
    device_dir = app_data / "device"
    device_dir.mkdir(parents=True, exist_ok=True)
    (device_dir / "local_device.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(LocalDeviceStateCorruptError):
        local_device_uuid()

    # Must not have silently overwritten the corrupt file with a fresh UUID.
    assert (device_dir / "local_device.json").read_text(encoding="utf-8") == "{not valid json"


def test_local_device_json_missing_field_raises(app_data):
    device_dir = app_data / "device"
    device_dir.mkdir(parents=True, exist_ok=True)
    (device_dir / "local_device.json").write_text(json.dumps({"not_device_uuid": "x"}), encoding="utf-8")

    with pytest.raises(LocalDeviceStateCorruptError):
        local_device_uuid()


# ── resolve_local_device() ───────────────────────────────────────────────

def test_resolve_local_device_creates_row_via_uuid_fallback(app_data, conn):
    row = resolve_local_device(conn, "company-1", device_label="Till 1", platform="WINDOWS")

    assert row["company_id"] == "company-1"
    assert row["device_label"] == "Till 1"
    assert row["device_fingerprint"] is None
    assert get_device(conn, row["id"]) is not None


def test_fingerprint_appearing_later_updates_existing_row_not_a_second_one(app_data, conn):
    # First resolve: licensing not configured yet -> UUID fallback identity.
    first = resolve_local_device(conn, "company-1")
    assert first["device_fingerprint"] is None

    # Licensing activates between calls; simulate a fresh resolution (e.g.
    # a new request in a new process) by clearing the per-process cache --
    # resolve_local_device() intentionally does NOT re-hit the DB when the
    # cached company_id already matches, so a real second call in the same
    # process wouldn't observe the new fingerprint either without this.
    device_context._cached_device = None
    _write_device_key_meta(app_data, fingerprint="fp-activated-later")

    second = resolve_local_device(conn, "company-1")

    assert second["id"] == first["id"], "must reuse the same stable local UUID as the row id"
    assert second["device_fingerprint"] == "fp-activated-later"

    all_rows = conn.execute("SELECT * FROM devices").fetchall()
    assert len(all_rows) == 1, "a fingerprint becoming available later must update the existing row, not create a second one"


def test_company_id_change_on_existing_device_refuses_to_rebind(app_data, conn):
    first = resolve_local_device(conn, "company-1")
    device_context._cached_device = None

    with pytest.raises(DeviceCompanyMismatchError):
        resolve_local_device(conn, "company-2")

    # Must not have silently rebound the row to the new company_id.
    unchanged = get_device(conn, first["id"])
    assert unchanged["company_id"] == "company-1"


def test_resolve_local_device_caches_within_process(app_data, conn):
    first = resolve_local_device(conn, "company-1", device_label="Till 1")
    # Mutate the label directly in the DB to prove the second call returns
    # the cached dict rather than re-querying.
    conn.execute("UPDATE devices SET device_label='Changed' WHERE id=?", (first["id"],))
    conn.commit()

    second = resolve_local_device(conn, "company-1")

    assert second["device_label"] == "Till 1", "same company_id call should return the cached row, not re-hit the DB"


# ── binding_enforced() ───────────────────────────────────────────────────

@pytest.mark.parametrize("raw_value", [None, "", "0"])
def test_binding_enforced_false_for_unset_empty_or_zero(monkeypatch, raw_value):
    if raw_value is None:
        monkeypatch.delenv("AURA_DEVICE_BINDING_ENFORCED", raising=False)
    else:
        monkeypatch.setenv("AURA_DEVICE_BINDING_ENFORCED", raw_value)

    assert binding_enforced() is False


@pytest.mark.parametrize("raw_value", ["1", "true", "True", "YES", "on"])
def test_binding_enforced_true_for_truthy_values(monkeypatch, raw_value):
    monkeypatch.setenv("AURA_DEVICE_BINDING_ENFORCED", raw_value)

    assert binding_enforced() is True
