"""/api/updates/*: who may do what, and what a disabled install looks like.

Both directions of the guard are proved: an administrator is let through AND a
cashier is refused, and for the cashier we assert the service was never
touched (an outcome alone -- 403 -- cannot tell 'the guard ran' from 'something
else refused').
"""
import pytest
from flask import Flask

from commercial_runtime.updates import routes as routes_module
from commercial_runtime.updates.routes import build_update_service, make_updates_blueprint
from commercial_runtime.updates.service import UpdateService
from commercial_runtime.updates.tests._fakes import (
    FakeOwnerClient,
    FakeStateRepository,
    Launcher,
    OwnerKey,
    make_trust_store,
)

CURRENT = "1.0.0-rc.8"


class CountingService(UpdateService):
    """A real UpdateService that also counts entry into each action."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.entered = []

    def check(self):
        self.entered.append("check")
        return super().check()

    def download(self):
        self.entered.append("download")
        return super().download()

    def install(self):
        self.entered.append("install")
        return super().install()


def build_app(service):
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(make_updates_blueprint(service, current_version=CURRENT))
    return app


def login(client, role):
    with client.session_transaction() as s:
        s["mt_user_id"] = "u1"
        s["mt_role"] = role


@pytest.fixture
def enabled(tmp_path):
    key = OwnerKey()
    owner = FakeOwnerClient(key)
    launcher = Launcher()
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    service = CountingService(
        app_data_dir=tmp_path, client_factory=lambda: owner,
        trust_store_factory=lambda: make_trust_store(tmp_path, key),
        signer_factory=lambda: object(), state_repository_factory=lambda: FakeStateRepository(),
        app_version=CURRENT, release_channel="rc", installer_launcher=launcher,
        is_installable_build=lambda: True, install_dir=str(install_dir))
    return build_app(service), service, owner, launcher


# ── authentication / authorisation, both directions ────────────────────────
def test_status_requires_a_signed_in_user(enabled):
    app, *_ = enabled
    assert app.test_client().get("/api/updates/status").status_code == 401


def test_a_cashier_can_read_status(enabled):
    app, *_ = enabled
    c = app.test_client()
    login(c, "cashier")
    r = c.get("/api/updates/status")
    assert r.status_code == 200 and r.get_json()["enabled"] is True


@pytest.mark.parametrize("path", ["check", "download", "install"])
def test_a_cashier_cannot_run_any_action_and_the_service_is_never_entered(enabled, path):
    app, service, owner, launcher = enabled
    c = app.test_client()
    login(c, "cashier")
    assert c.post("/api/updates/" + path).status_code == 403
    assert service.entered == []                   # the guard stopped it BEFORE the service
    assert owner.find_calls == [] and launcher.calls == []


@pytest.mark.parametrize("path", ["check", "download", "install"])
def test_an_anonymous_caller_gets_401_on_every_action(enabled, path):
    app, service, *_ = enabled
    assert app.test_client().post("/api/updates/" + path).status_code == 401
    assert service.entered == []


def test_an_administrator_can_run_check_download_install(enabled):
    app, service, owner, launcher = enabled
    c = app.test_client()
    login(c, "admin")
    r = c.post("/api/updates/check")
    assert r.status_code == 200 and r.get_json()["available"]["version"] == "1.0.0-rc.9"
    assert len(owner.find_calls) == 1
    r = c.post("/api/updates/download")
    assert r.status_code == 202
    service.wait_for_download()
    assert c.get("/api/updates/status").get_json()["phase"] == "ready"
    r = c.post("/api/updates/install")
    assert r.status_code == 202 and r.get_json() == {"result": "INSTALLING"}
    assert len(launcher.calls) == 1


def test_a_refused_action_is_a_409_with_a_code(enabled):
    app, service, owner, launcher = enabled
    c = app.test_client()
    login(c, "admin")
    r = c.post("/api/updates/install")             # nothing downloaded yet
    assert r.status_code == 409 and r.get_json() == {"error_code": "NOT_READY"}
    assert launcher.calls == []
    r = c.post("/api/updates/download")             # nothing on offer yet
    assert r.status_code == 409 and r.get_json() == {"error_code": "NOTHING_TO_DOWNLOAD"}


def test_a_failed_check_is_a_200_that_carries_the_error_code(enabled):
    app, service, owner, _ = enabled
    owner.descriptor_overrides = {"installation_id": "someone-else"}
    c = app.test_client()
    login(c, "admin")
    r = c.post("/api/updates/check")
    assert r.status_code == 200 and r.get_json()["error_code"] == "DESCRIPTOR_MISMATCH"


def test_status_shape_when_an_update_is_available(enabled):
    app, *_ = enabled
    c = app.test_client()
    login(c, "admin")
    c.post("/api/updates/check")
    body = c.get("/api/updates/status").get_json()
    assert set(body) == {"enabled", "reason_disabled", "current_version", "checked_at", "available", "phase",
                         "progress", "error_code", "can_install"}
    assert set(body["available"]) == {"version", "release_notes", "size_bytes", "required", "below_min_supported"}
    assert body["current_version"] == CURRENT and body["reason_disabled"] is None


# ── disabled: no Owner URL ─────────────────────────────────────────────────
def test_no_owner_url_means_no_service_and_no_client_is_ever_built(tmp_path, monkeypatch):
    built = []
    monkeypatch.setattr(routes_module, "LicensingClient", lambda *a, **k: built.append(1))
    service = build_update_service(
        app_data_dir=str(tmp_path), owner_base_url="", verify_tls=True, timeout_seconds=5,
        device_identity_factory=lambda d: None, release_channel="rc", app_version=CURRENT)
    assert service is None
    app = build_app(service)
    c = app.test_client()
    login(c, "admin")
    body = c.get("/api/updates/status").get_json()
    assert body["enabled"] is False and body["reason_disabled"] == "OWNER_NOT_CONFIGURED"
    assert body["available"] is None and body["can_install"] is False and body["current_version"] == CURRENT
    for path in ("check", "download", "install"):
        r = c.post("/api/updates/" + path)
        assert r.status_code == 409 and r.get_json()["error_code"] == "UPDATES_DISABLED"
    assert built == []                              # ZERO client constructions: no network, ever
    assert not (tmp_path / "updates").exists()      # and nothing written to disk


def test_disabled_actions_still_ask_authentication_first(tmp_path):
    app = build_app(None)
    c = app.test_client()
    login(c, "cashier")
    assert c.post("/api/updates/check").status_code == 403    # not 409: a cashier learns nothing about config


def test_an_owner_url_with_an_unactivated_install_is_disabled_as_not_activated(tmp_path, monkeypatch):
    built = []
    monkeypatch.setattr(routes_module, "LicensingClient", lambda *a, **k: built.append(1))
    service = build_update_service(
        app_data_dir=str(tmp_path), owner_base_url="https://owner.example/api/licensing/v1", verify_tls=True,
        timeout_seconds=5, device_identity_factory=lambda d: None, release_channel="rc", app_version=CURRENT)
    assert service is not None
    app = build_app(service)
    c = app.test_client()
    login(c, "admin")
    body = c.get("/api/updates/status").get_json()
    assert body["enabled"] is False and body["reason_disabled"] == "NOT_ACTIVATED"
    r = c.post("/api/updates/check")
    assert r.status_code == 409 and r.get_json()["reason_disabled"] == "NOT_ACTIVATED"
    assert built == []


def test_the_built_service_uses_the_licensing_directory_and_database(tmp_path):
    seen = {}

    def identity_factory(licensing_dir):
        seen["dir"] = licensing_dir
        return object()

    service = build_update_service(
        app_data_dir=str(tmp_path), owner_base_url="https://owner.example/api/licensing/v1", verify_tls=True,
        timeout_seconds=5, device_identity_factory=identity_factory, release_channel="rc", app_version=CURRENT)
    service._signer_factory()
    # The SAME directory the licensing blueprint passes its identity factory --
    # one device key, one trust store, never a second set.
    assert seen["dir"] == tmp_path / "licensing"
