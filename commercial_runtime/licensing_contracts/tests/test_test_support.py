from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository
from commercial_runtime.licensing_contracts.test_support import seed_active_license


def test_seed_active_license_produces_active_online_state(tmp_path):
    app_data_dir = str(tmp_path / "appdata")
    seed_active_license(app_data_dir, product_code="AURA_CLINIC", platform="WINDOWS")

    from pathlib import Path

    repo = LicenseStateRepository(Path(app_data_dir) / "database" / "subsystems" / "licensing.db")
    record = repo.load()
    assert record.current_state == "ACTIVE_ONLINE"
    assert record.product_code == "AURA_CLINIC"
