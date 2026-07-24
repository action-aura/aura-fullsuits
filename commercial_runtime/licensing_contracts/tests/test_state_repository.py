import pytest

from commercial_runtime.licensing_contracts.state_repository import (
    LICENSING_SCHEMA_VERSION,
    LicenseStateRecord,
    LicenseStateRepository,
    LicenseStateRepositoryError,
)


@pytest.fixture
def repo(tmp_path):
    return LicenseStateRepository(tmp_path / "database" / "subsystems" / "licensing.db")


def _record(**overrides):
    defaults = dict(
        licensing_schema_version=LICENSING_SCHEMA_VERSION,
        product_code="AURA_RETAIL",
        platform="WINDOWS",
        current_state="ACTIVATION_REQUIRED",
    )
    defaults.update(overrides)
    return LicenseStateRecord(**defaults)


def test_load_returns_none_before_any_save(repo):
    assert repo.load() is None


def test_save_then_load_round_trip(repo):
    repo.save(_record(owner_installation_id="inst-1"))
    loaded = repo.load()
    assert loaded.owner_installation_id == "inst-1"
    assert loaded.current_state == "ACTIVATION_REQUIRED"
    assert loaded.product_code == "AURA_RETAIL"


def test_save_is_upsert_single_row(repo):
    repo.save(_record(current_state="ACTIVATION_REQUIRED"))
    repo.save(_record(current_state="ACTIVE_ONLINE", owner_installation_id="inst-2"))
    loaded = repo.load()
    assert loaded.current_state == "ACTIVE_ONLINE"
    assert loaded.owner_installation_id == "inst-2"

    with repo._conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM licensing_state").fetchone()["c"]
    assert count == 1


def test_update_state_requires_existing_record(repo):
    with pytest.raises(LicenseStateRepositoryError):
        repo.update_state("ACTIVE_ONLINE")


def test_update_state_only_changes_state_field(repo):
    repo.save(_record(owner_installation_id="inst-1"))
    repo.update_state("SUSPENDED")
    loaded = repo.load()
    assert loaded.current_state == "SUSPENDED"
    assert loaded.owner_installation_id == "inst-1"  # unchanged


def test_reset_clears_the_row(repo):
    repo.save(_record())
    repo.reset()
    assert repo.load() is None


def test_reset_only_touches_licensing_state_table(tmp_path):
    db_path = tmp_path / "database" / "subsystems" / "licensing.db"
    repo = LicenseStateRepository(db_path)
    repo.save(_record())
    with repo._conn() as conn:
        conn.execute("CREATE TABLE unrelated_product_table (id INTEGER PRIMARY KEY, data TEXT)")
        conn.execute("INSERT INTO unrelated_product_table (data) VALUES ('customer record')")
    repo.reset()
    with repo._conn() as conn:
        remaining = conn.execute("SELECT data FROM unrelated_product_table").fetchall()
    assert len(remaining) == 1
    assert remaining[0]["data"] == "customer record"


def test_repository_never_has_a_license_key_column(repo):
    with repo._conn() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(licensing_state)")}
    forbidden = {"license_key", "key_secret_hmac", "device_private_key", "pepper"}
    assert columns.isdisjoint(forbidden)


def test_schema_persists_across_repository_instances(tmp_path):
    db_path = tmp_path / "database" / "subsystems" / "licensing.db"
    repo1 = LicenseStateRepository(db_path)
    repo1.save(_record(owner_installation_id="inst-persisted"))
    repo2 = LicenseStateRepository(db_path)
    assert repo2.load().owner_installation_id == "inst-persisted"
