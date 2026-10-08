"""Update channel -- the REAL product client against the REAL Owner routes.

Why this file exists: each half of the update channel was built against a
stand-in for the other. Owner's discovery tests verify the descriptor with
Owner's own canonicalizer and Owner's own key loader; the product's tests
verify descriptors signed by a fake Owner. Both suites can be green while the
two sides disagree on the one thing that matters -- whether a descriptor Owner
actually signs is one the product actually accepts (canonical bytes, key
encoding, field names, the request_id echo). Only running the two together
can show that, so this test drives commercial_runtime's LicensingClient and
UpdateService through Owner's Flask app: check -> authorize -> download ->
verify -> install, with nothing faked except the network hop (a requests-
shaped adapter over the Flask test client) and the installer launch.

Lives under owner/tests because it needs Owner's database fixtures; it imports
commercial_runtime from the repo root, which is the only coupling and is
deliberate.
"""
from __future__ import annotations

import hashlib
import json as jsonlib
import sys
import uuid as uuid_mod
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from commercial_runtime.licensing_contracts.client import LicensingClient, LicensingClientConfig  # noqa: E402
from commercial_runtime.updates.descriptor import UpdateError  # noqa: E402
from commercial_runtime.updates.service import UpdateService  # noqa: E402

from app.extensions import db_session  # noqa: E402
from app.models.catalog import Platform, Product, ProductVersion, ReleaseChannel  # noqa: E402
from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff  # noqa: E402

BASE = "http://owner.test/api/licensing/v1"


class _Response:
    def __init__(self, flask_response):
        self._r = flask_response
        self.status_code = flask_response.status_code
        self.headers = dict(flask_response.headers)

    def json(self):
        return self._r.get_json()

    def iter_content(self, chunk_size=1024):
        data = self._r.get_data()
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]

    def close(self):
        self._r.close()


class _FlaskSession:
    """requests.Session-shaped: only .request() is used by LicensingClient."""

    def __init__(self, client):
        self._client = client
        self.calls: list[str] = []

    def request(self, method, url, json=None, **_kwargs):
        assert url.startswith(BASE), url
        path = "/api/licensing/v1" + url[len(BASE):]
        self.calls.append(f"{method} {path.split('/download/')[0]}")
        if json is None:
            return _Response(self._client.open(path, method=method))
        return _Response(self._client.open(path, method=method, data=jsonlib.dumps(json), content_type="application/json"))


class _Signer:
    def __init__(self, private_key):
        self._key = private_key

    def sign(self, canonical_bytes: bytes) -> str:
        import base64

        return base64.b64encode(self._key.sign(canonical_bytes)).decode("ascii")


class _TrustStore:
    """The product's OwnerTrustStore exposes get_public_key_b64(key_id); this
    one is filled from Owner's real GET /signing-keys answer, the same source a
    real install's trust store is built from."""

    def __init__(self, keys: dict[str, str]):
        self._keys = keys

    def get_public_key_b64(self, key_id):
        return self._keys.get(key_id)


def _activate(app, client):
    actor_id = make_staff(app, "xstack@example.com")
    _license_id, full_key = make_license(app, actor_id, product_code="AURA_CLINIC")
    private_key = make_device_keypair()
    resp = client.post(
        "/api/licensing/v1/activations",
        data=jsonlib.dumps(build_activation_body(private_key, full_key=full_key, installation_id="xstack-dev", product_code="AURA_CLINIC")),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["installation_id"], private_key


def _publish(app, version: str, content: bytes) -> None:
    from app.releases import storage

    with app.app_context():
        product = db_session.execute(select(Product).where(Product.product_code == "AURA_CLINIC")).scalars().first()
        platform = db_session.execute(select(Platform).where(Platform.platform_code == "WINDOWS")).scalars().first()
        channel = db_session.execute(select(ReleaseChannel).where(ReleaseChannel.channel_code == "STABLE")).scalars().first()
        artifact_path = f"test/{uuid_mod.uuid4().hex}.bin"
        storage.write_artifact_bytes(artifact_path, content)
        db_session.add(ProductVersion(
            product_id=product.id, platform_id=platform.id, release_channel_id=channel.id, version=version,
            artifact_checksum_sha256=hashlib.sha256(content).hexdigest(), artifact_path=artifact_path,
            artifact_size_bytes=len(content), publication_state="PUBLISHED", published_at=datetime.now(timezone.utc),
        ))
        db_session.commit()


def _owner_keys(client) -> dict[str, str]:
    manifest = client.get("/api/licensing/v1/signing-keys").get_json()
    return {k["key_id"]: k["public_key"] for k in manifest["keys"]}


def _service(tmp_path, session, installation_id, private_key, keys, launches):
    return UpdateService(
        app_data_dir=tmp_path,
        client_factory=lambda: LicensingClient(LicensingClientConfig(base_url=BASE, max_retries=0), session=session),
        trust_store_factory=lambda: _TrustStore(keys),
        signer_factory=lambda: _Signer(private_key),
        state_repository_factory=lambda: SimpleNamespace(load=lambda: SimpleNamespace(owner_installation_id=installation_id)),
        app_version="1.0.0-rc.8",
        release_channel="rc",
        installer_launcher=lambda path, args: launches.append((path, list(args))),
        is_installable_build=lambda: True,
        install_dir=str(tmp_path),
    )


def test_real_client_checks_downloads_verifies_and_installs_from_real_owner(app, client, seeded, signing_key, tmp_path):
    installation_id, private_key = _activate(app, client)
    content = b"MZ fake installer " + uuid_mod.uuid4().bytes * 64
    _publish(app, "1.0.0", content)
    session, launches = _FlaskSession(client), []
    service = _service(tmp_path, session, installation_id, private_key, _owner_keys(client), launches)

    status = service.check()
    assert status["available"]["version"] == "1.0.0", status
    assert status["phase"] == "idle"

    service.download()
    service.wait_for_download(30)
    status = service.status()
    assert status["phase"] == "ready", status
    installer = tmp_path / "updates" / "AuraRetail-Setup-1.0.0.exe"
    assert installer.read_bytes() == content

    service.install()
    assert len(launches) == 1
    assert launches[0][0] == str(installer)
    assert "/VERYSILENT" in launches[0][1] and "/RELAUNCH=1" in launches[0][1]
    # The whole path went over Owner's real routes, in order.
    assert session.calls == [
        "POST /api/licensing/v1/releases/latest",
        "POST /api/licensing/v1/releases/authorize-download",
        "GET /api/licensing/v1/releases",
    ]


def test_real_owner_descriptor_is_refused_under_a_key_this_install_does_not_trust(app, client, seeded, signing_key, tmp_path):
    """The allow-half above only means something if the product would refuse
    the same real answer when the signature does not check out. Same Owner,
    same descriptor -- the install just trusts a different key."""
    installation_id, private_key = _activate(app, client)
    _publish(app, "1.0.0", b"MZ another fake installer")
    keys = _owner_keys(client)
    from cryptography.hazmat.primitives import serialization
    import base64

    stranger = make_device_keypair().public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    wrong_keys = {key_id: base64.b64encode(stranger).decode("ascii") for key_id in keys}
    launches: list = []
    service = _service(tmp_path, _FlaskSession(client), installation_id, private_key, wrong_keys, launches)

    with pytest.raises(UpdateError) as exc:
        service.check()
    assert exc.value.code == "DESCRIPTOR_UNTRUSTED"
    assert service.status()["available"] is None
    assert launches == []
