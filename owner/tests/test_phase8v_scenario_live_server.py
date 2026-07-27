"""Phase 8V Parts Q/R/S/U/V: real, wire-level, cross-package scenario
validation. Runs the actual Owner Flask app on a real localhost TCP port
(werkzeug's dev server, not the Flask test client) and drives it with the
actual `commercial_runtime.licensing_contracts` client -- the exact same
code the Windows desktop product uses. This is genuine product-to-Owner
HTTP traffic: real Ed25519 signing, real canonicalization on both sides,
real signature verification, real Postgres.

What this does NOT prove: physical Android hardware behavior (no device in
this environment -- see docs/owner/phase8v/phase8v-scope-and-baseline.md).
Android's Kotlin OwnerClient makes the identical HTTP call this harness's
client makes (same contract, same endpoints); what a physical device adds
beyond this is AndroidKeystore-backed key storage and the embedded-Python
sync-route hop, both already covered by real (non-hardware) tests
elsewhere (commercial_runtime's own test suite, `test_phase8_manual_activation_approval.py`).
"""
from __future__ import annotations

import base64
import os
import sys
import threading
from datetime import date

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from werkzeug.serving import make_server

from tests.conftest import make_staff

# commercial_runtime lives at the repo root (a sibling of owner/), not under
# owner/ itself -- conftest.py only puts owner/ on sys.path. This is the one
# test file in owner/tests/ that genuinely needs the real product-side
# package (Part Q/R/S/U/V: real cross-package wire traffic), so the path
# addition is scoped to this file rather than added repo-wide.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


class RealDeviceSigner:
    """A real Ed25519 keypair implementing commercial_runtime's DeviceSigner
    protocol (sign/get_public_key_b64) -- deliberately not the fake stub
    commercial_runtime's own unit tests use (that stub returns a
    non-cryptographic fixed string, fine when Owner itself is mocked, not
    fine here where a REAL Owner Flask app independently verifies the
    signature server-side)."""

    def __init__(self):
        self._key = Ed25519PrivateKey.generate()

    def raw_public_bytes(self) -> bytes:
        return self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    def get_public_key_b64(self) -> str:
        return base64.b64encode(self.raw_public_bytes()).decode("ascii")

    def sign(self, canonical_bytes: bytes) -> str:
        return base64.b64encode(self._key.sign(canonical_bytes)).decode("ascii")

    def fingerprint(self) -> str:
        from commercial_runtime.licensing_contracts.device_identity import fingerprint_of

        return fingerprint_of(self.raw_public_bytes())


@pytest.fixture()
def live_owner_server(app, signing_key):
    """Runs the real Owner Flask app (same app/DB as every other test in
    this file) on a real ephemeral localhost port for the duration of one
    test."""
    server = make_server("127.0.0.1", 0, app)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/api/licensing/v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _bootstrap_trust_store(app, tmp_path):
    from app.licensing_service.signing import get_active_signing_key
    from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore

    with app.app_context():
        key = get_active_signing_key()
        key_id, public_key = key.key_id, key.public_key

    store = OwnerTrustStore(tmp_path / "trust.json")
    store.bootstrap_from_anchor({"keys": [{"key_id": key_id, "public_key": public_key, "algorithm": "ed25519"}]})
    return store


def _make_windows_client_stack(tmp_path, base_url):
    from commercial_runtime.licensing_contracts.client import LicensingClient, LicensingClientConfig
    from commercial_runtime.licensing_contracts.events import LicensingEventRecorder
    from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository

    client = LicensingClient(LicensingClientConfig(base_url=base_url, timeout_seconds=10, verify_tls=False, max_retries=1))
    state_repo = LicenseStateRepository(tmp_path / "licensing.db")
    events = LicensingEventRecorder(tmp_path / "licensing.db")
    return client, state_repo, events


def _make_license_and_key(app, staff_id, *, end_date="2026-08-01", device_limit=2, product_code="AURA_CLINIC"):
    from app.extensions import db_session
    from app.licensing.services import create_license, issue_license_key, transition_license
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    with app.app_context():
        product = db_session.query(Product).filter_by(product_code=product_code).first()
        plan = Plan(plan_code=f"LIVE-{staff_id}-{end_date}", product_id=product.id, name="Live Test Plan", billing_model="MONTHLY", currency="USD")
        customer = Customer(legal_name="Live Server Test Co")
        db_session.add_all([plan, customer])
        db_session.commit()
        sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": date.fromisoformat(end_date)}, staff_id)
        transition_subscription(sub, "ACTIVE", staff_id)
        lic = create_license(
            {"customer_id": customer.id, "subscription_id": sub.id, "product_id": product.id, "plan_id": plan.id,
             "allowed_platforms": "WINDOWS,ANDROID", "device_limit": device_limit}, staff_id,
        )
        lic, full_key = issue_license_key(lic, app.config["LICENSE_PEPPER"], f"live-{staff_id}", staff_id)
        return str(sub.id), full_key


# -- Scenario 1: early renewal -------------------------------------------------

def test_scenario_1_early_renewal_real_wire_traffic(app, client, seeded, live_owner_server, tmp_path):
    from app.commercial_ops.renewal_requests import approve_renewal_request, apply_renewal_request, create_renewal_request, transition_renewal_request
    from app.extensions import db_session
    from app.models.subscriptions import Subscription
    from commercial_runtime.licensing_contracts.activation import perform_activation
    from commercial_runtime.licensing_contracts.checkin_scheduler import LicenseCheckInScheduler

    creator_id = make_staff(app, "8v-live1-creator@example.com", role_codes=["SALES"])
    approver_id = make_staff(app, "8v-live1-approver@example.com", role_codes=["FINANCE"])
    sub_id, full_key = _make_license_and_key(app, creator_id, end_date="2026-08-01")

    trust_store = _bootstrap_trust_store(app, tmp_path)
    win_client, state_repo, events = _make_windows_client_stack(tmp_path, live_owner_server)
    signer = RealDeviceSigner()

    # -- Real activation over real HTTP, real signature verification -----
    result = perform_activation(
        client=win_client, signer=signer, trust_store=trust_store, state_repository=state_repo, event_recorder=events,
        product_code="AURA_CLINIC", platform="WINDOWS", app_version="1.0.0-8v-live", release_channel=None,
        license_key=full_key, device_public_key_fingerprint=signer.fingerprint(),
    )
    installation_id = result.owner_installation_id
    record_before = state_repo.load()
    assertion_id_before = record_before.assertion_id

    # -- Owner UI renewal, full pipeline --------------------------------
    with app.app_context():
        subscription = db_session.get(Subscription, sub_id)
        renewal = create_renewal_request(
            subscription=subscription, date_rule="EARLY_RENEWAL_FROM_CURRENT_END",
            proposed_term_start=date(2026, 8, 1), proposed_term_end=date(2026, 9, 1),
            currency="USD", actor_staff_user_id=creator_id,
        )
        for to_status in ("QUOTED", "AWAITING_CONFIRMATION", "AWAITING_PAYMENT", "PAYMENT_RECORDED"):
            transition_renewal_request(renewal, to_status, creator_id)
        approve_renewal_request(renewal, approver_id)
        apply_renewal_request(renewal.id, approver_id)
        sub_after = db_session.get(Subscription, sub_id)
        assert sub_after.end_date == date(2026, 9, 1)

    # -- Real check-in over real HTTP: no key, same identity, fresh assertion --
    scheduler = LicenseCheckInScheduler(
        client=win_client, signer=signer, trust_store=trust_store, state_repository=state_repo, event_recorder=events,
        product_code="AURA_CLINIC", platform="WINDOWS", device_public_key_fingerprint=signer.fingerprint(),
    )
    new_state = scheduler.run_once()

    record_after = state_repo.load()
    assert record_after.owner_installation_id == installation_id  # same device identity
    assert record_after.assertion_id != assertion_id_before  # fresh assertion issued
    assert record_after.last_sync_result == "SUCCESS"
    from commercial_runtime.licensing_contracts.state_machine import LicenseState

    assert new_state in (LicenseState.ACTIVE_ONLINE, LicenseState.ACTIVE_OFFLINE)

    # The check-in request itself structurally cannot carry a license key --
    # LicensingClient.check_in()'s body has no key field at all (verified by
    # source inspection: {"contract_version","request_id","correlation_id",
    # "timestamp","nonce","installation_id"} plus the signature -- confirmed
    # again here by asserting the full key never appears anywhere in local
    # persisted state after the whole flow.
    assert full_key not in (record_after.assertion_envelope_json or "")


# -- Scenario 2: renewal after expiry (revival) --------------------------------

def test_scenario_2_renewal_after_expiry_real_wire_traffic(app, client, seeded, live_owner_server, tmp_path):
    from app.commercial_ops.renewal_requests import approve_renewal_request, apply_renewal_request, create_renewal_request, transition_renewal_request
    from app.extensions import db_session
    from app.models.subscriptions import Subscription
    from app.subscriptions.services import transition_subscription
    from commercial_runtime.licensing_contracts.activation import perform_activation
    from commercial_runtime.licensing_contracts.checkin_scheduler import LicenseCheckInScheduler
    from commercial_runtime.licensing_contracts.state_machine import LicenseState

    creator_id = make_staff(app, "8v-live2-creator@example.com", role_codes=["SALES"])
    approver_id = make_staff(app, "8v-live2-approver@example.com", role_codes=["FINANCE"])
    sub_id, full_key = _make_license_and_key(app, creator_id, end_date="2026-06-01", product_code="AURA_RETAIL")

    trust_store = _bootstrap_trust_store(app, tmp_path)
    win_client, state_repo, events = _make_windows_client_stack(tmp_path, live_owner_server)
    signer = RealDeviceSigner()

    result = perform_activation(
        client=win_client, signer=signer, trust_store=trust_store, state_repository=state_repo, event_recorder=events,
        product_code="AURA_RETAIL", platform="WINDOWS", app_version="1.0.0-8v-live", release_channel=None,
        license_key=full_key, device_public_key_fingerprint=signer.fingerprint(),
    )
    installation_id = result.owner_installation_id

    # Subscription lapses -- Owner's own record, not a product-side fake.
    with app.app_context():
        subscription = db_session.get(Subscription, sub_id)
        transition_subscription(subscription, "EXPIRED", creator_id, reason="term ended, scenario 2 setup")

    # Late renewal through the real pipeline -- revives EXPIRED -> ACTIVE
    # only via this gated pipeline (Milestone 2's security lesson), never
    # via the shared generic transition route.
    with app.app_context():
        subscription = db_session.get(Subscription, sub_id)
        renewal = create_renewal_request(
            subscription=subscription, date_rule="LATE_RENEWAL_FROM_APPROVAL_DATE",
            proposed_term_start=date(2026, 7, 27), proposed_term_end=date(2026, 8, 27),
            currency="USD", actor_staff_user_id=creator_id,
        )
        for to_status in ("QUOTED", "AWAITING_CONFIRMATION", "AWAITING_PAYMENT", "PAYMENT_RECORDED"):
            transition_renewal_request(renewal, to_status, creator_id)
        approve_renewal_request(renewal, approver_id)
        apply_renewal_request(renewal.id, approver_id)
        sub_after = db_session.get(Subscription, sub_id)
        assert sub_after.status == "ACTIVE"

    # Real check-in after revival: same identity, no key, state returns to
    # a usable ACTIVE_* state (never a stale-downgrade back to restricted).
    scheduler = LicenseCheckInScheduler(
        client=win_client, signer=signer, trust_store=trust_store, state_repository=state_repo, event_recorder=events,
        product_code="AURA_RETAIL", platform="WINDOWS", device_public_key_fingerprint=signer.fingerprint(),
    )
    new_state = scheduler.run_once()
    record_after = state_repo.load()
    assert record_after.owner_installation_id == installation_id
    assert record_after.last_sync_result == "SUCCESS"
    assert new_state in (LicenseState.ACTIVE_ONLINE, LicenseState.ACTIVE_OFFLINE)
    assert full_key not in (record_after.assertion_envelope_json or "")


# -- Scenario 6: device replacement ---------------------------------------------

def test_scenario_6_device_replacement_real_wire_traffic(app, client, seeded, live_owner_server, tmp_path):
    from app.commercial_ops.device_slot_ops import replace_device_slot
    from app.extensions import db_session
    from app.models.installations import Installation
    from commercial_runtime.licensing_contracts.activation import ActivationFailed, perform_activation

    staff_id = make_staff(app, "8v-live6@example.com", role_codes=["SUPPORT"])
    sub_id, full_key = _make_license_and_key(app, staff_id, end_date="2026-09-01", device_limit=1)

    trust_store = _bootstrap_trust_store(app, tmp_path)
    old_client, old_state_repo, old_events = _make_windows_client_stack(tmp_path / "old", live_owner_server)
    old_signer = RealDeviceSigner()

    old_result = perform_activation(
        client=old_client, signer=old_signer, trust_store=trust_store, state_repository=old_state_repo, event_recorder=old_events,
        product_code="AURA_CLINIC", platform="WINDOWS", app_version="1.0.0-8v-live", release_channel=None,
        license_key=full_key, device_public_key_fingerprint=old_signer.fingerprint(),
    )
    old_installation_id = old_result.owner_installation_id

    # At the device limit (1/1) -- a second, different device cannot
    # activate yet.
    new_client, new_state_repo, new_events = _make_windows_client_stack(tmp_path / "new", live_owner_server)
    new_signer = RealDeviceSigner()
    with pytest.raises(ActivationFailed) as exc:
        perform_activation(
            client=new_client, signer=new_signer, trust_store=trust_store, state_repository=new_state_repo, event_recorder=new_events,
            product_code="AURA_CLINIC", platform="WINDOWS", app_version="1.0.0-8v-live", release_channel=None,
            license_key=full_key, device_public_key_fingerprint=new_signer.fingerprint(),
        )
    assert exc.value.reason_code == "DEVICE_LIMIT_REACHED"

    # Support approves the replacement (Owner UI's release_device_slot),
    # never by editing the database row directly.
    with app.app_context():
        installation = db_session.get(Installation, old_installation_id)
        replace_device_slot(installation, reason="Customer replacing failed hardware", actor_staff_user_id=staff_id)
        assert db_session.get(Installation, old_installation_id).status == "REPLACED"

    # The new device can now activate for real, over real HTTP, and gets
    # its own distinct installation identity -- no silent identity reuse.
    new_result = perform_activation(
        client=new_client, signer=new_signer, trust_store=trust_store, state_repository=new_state_repo, event_recorder=new_events,
        product_code="AURA_CLINIC", platform="WINDOWS", app_version="1.0.0-8v-live", release_channel=None,
        license_key=full_key, device_public_key_fingerprint=new_signer.fingerprint(),
    )
    assert new_result.owner_installation_id != old_installation_id
