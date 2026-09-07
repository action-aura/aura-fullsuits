import sqlite3
import sys

import pytest
from flask import Flask

from commercial_runtime.einvoicing.outbox import OutboxRepository
from commercial_runtime.einvoicing.providers.mock import MockProvider
from commercial_runtime.einvoicing.routes import make_einvoicing_blueprint
from commercial_runtime.einvoicing.schema import apply_einvoicing_schema

# `platform` reaches routes.py for exactly one thing: get_secret_box picks
# the Windows DPAPI box for 'WINDOWS' and the portable app-secret AES-GCM box
# for anything else. The DPAPI box is real crypt32 and cannot run on Linux
# (CI, 2026-09-07: "module 'ctypes' has no attribute 'windll'" in the three
# credential route tests). Every route behaviour this file proves -- the
# secret is never echoed, wipe really wipes, enable/disable drive the worker
# -- is backend-independent, so the fixture follows the host: the same
# routes run against DPAPI on Windows and against the portable box on Linux,
# instead of being skipped there.
PLATFORM = 'WINDOWS' if sys.platform == 'win32' else 'ANDROID'


@pytest.fixture
def app_and_db(tmp_path):
    db_path = str(tmp_path / 'product.db')
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    apply_einvoicing_schema(conn)
    conn.commit()
    conn.close()

    def conn_factory():
        c = sqlite3.connect(db_path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'test-secret'
    app.config['TESTING'] = True
    bp = make_einvoicing_blueprint(
        product_code='AURA_TEST', platform=PLATFORM, app_data_dir=str(tmp_path),
        conn_factory=conn_factory, get_worker=None, provider=MockProvider(),
    )
    app.register_blueprint(bp)
    return app, db_path, str(tmp_path)


class _FakeWorker:
    """Records start/stop calls without touching real threads -- proves
    routes.py calls get_worker(company_id) correctly on enable/disable."""
    def __init__(self):
        self.started_with = None
        self.stop_calls = 0

    def start(self, interval_seconds):
        self.started_with = interval_seconds

    def stop(self):
        self.stop_calls += 1

    def run_once(self):
        return {'ran': True, 'claimed': 0}


@pytest.fixture
def app_with_worker_registry(tmp_path):
    db_path = str(tmp_path / 'product.db')
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    apply_einvoicing_schema(conn)
    conn.commit()
    conn.close()

    def conn_factory():
        c = sqlite3.connect(db_path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    workers = {}

    def get_worker(company_id):
        if company_id not in workers:
            workers[company_id] = _FakeWorker()
        return workers[company_id]

    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'test-secret'
    app.config['TESTING'] = True
    bp = make_einvoicing_blueprint(
        product_code='AURA_TEST', platform=PLATFORM, app_data_dir=str(tmp_path),
        conn_factory=conn_factory, get_worker=get_worker, provider=MockProvider(),
    )
    app.register_blueprint(bp)
    return app, workers


def _client(app_and_db):
    app, _, _ = app_and_db
    return app.test_client()


def _login(client, *, admin=True, company_id=1):
    with client.session_transaction() as sess:
        sess['mt_user_id'] = 'user-1'
        sess['mt_role'] = 'admin' if admin else 'staff'
        sess['company_id'] = company_id


# ─── auth gates ─────────────────────────────────────────────────────────

def test_status_requires_authentication(app_and_db):
    client = _client(app_and_db)
    r = client.get('/api/einvoicing/status')
    assert r.status_code == 401


def test_status_reachable_by_non_admin_session(app_and_db):
    client = _client(app_and_db)
    _login(client, admin=False)
    r = client.get('/api/einvoicing/status')
    assert r.status_code == 200


def test_settings_get_requires_admin(app_and_db):
    client = _client(app_and_db)
    _login(client, admin=False)
    r = client.get('/api/einvoicing/settings')
    assert r.status_code == 403


def test_settings_post_requires_admin(app_and_db):
    client = _client(app_and_db)
    _login(client, admin=False)
    r = client.post('/api/einvoicing/settings', json={'enabled': '1'})
    assert r.status_code == 403


def test_killswitch_requires_admin(app_and_db):
    client = _client(app_and_db)
    _login(client, admin=False)
    r = client.post('/api/einvoicing/killswitch', json={})
    assert r.status_code == 403


def test_outbox_list_reachable_by_non_admin(app_and_db):
    client = _client(app_and_db)
    _login(client, admin=False)
    r = client.get('/api/einvoicing/outbox')
    assert r.status_code == 200


def test_outbox_retry_requires_admin(app_and_db):
    client = _client(app_and_db)
    _login(client, admin=False)
    r = client.post('/api/einvoicing/outbox/ref-1/retry')
    assert r.status_code == 403


# ─── disabled-by-default ────────────────────────────────────────────────

def test_status_shows_disabled_by_default(app_and_db):
    client = _client(app_and_db)
    _login(client)
    r = client.get('/api/einvoicing/status')
    assert r.status_code == 200
    data = r.get_json()['data']
    assert data['enabled'] is False
    assert data['provider'] == 'mock'
    assert data['counts_by_state']['QUEUED'] == 0


def test_enabling_via_settings_reflects_in_status(app_and_db):
    client = _client(app_and_db)
    _login(client)
    r = client.post('/api/einvoicing/settings', json={'enabled': '1'})
    assert r.status_code == 200
    status = client.get('/api/einvoicing/status').get_json()['data']
    assert status['enabled'] is True


def test_settings_post_rejects_unknown_key(app_and_db):
    client = _client(app_and_db)
    _login(client)
    r = client.post('/api/einvoicing/settings', json={'not_a_real_setting': 'x'})
    assert r.status_code == 400


# ─── credentials never echoed ───────────────────────────────────────────

def test_post_credentials_never_echoes_the_secret(app_and_db):
    client = _client(app_and_db)
    _login(client)
    r = client.post('/api/einvoicing/credentials', json={'client_id': 'abc123', 'client_secret': 'super-secret-xyz'})
    assert r.status_code == 200
    body_text = r.get_data(as_text=True)
    assert 'super-secret-xyz' not in body_text
    data = r.get_json()['data']
    assert data['configured'] is True
    assert data['client_id_last4'] == 'c123'


def test_get_settings_credentials_section_never_contains_secret(app_and_db):
    client = _client(app_and_db)
    _login(client)
    client.post('/api/einvoicing/credentials', json={'client_id': 'abc123', 'client_secret': 'super-secret-xyz'})
    r = client.get('/api/einvoicing/settings')
    body_text = r.get_data(as_text=True)
    assert 'super-secret-xyz' not in body_text


def test_post_credentials_requires_both_fields(app_and_db):
    client = _client(app_and_db)
    _login(client)
    r = client.post('/api/einvoicing/credentials', json={'client_id': 'abc123'})
    assert r.status_code == 400


def test_delete_credentials_wipes(app_and_db):
    client = _client(app_and_db)
    _login(client)
    client.post('/api/einvoicing/credentials', json={'client_id': 'abc123', 'client_secret': 'sekret'})
    r = client.delete('/api/einvoicing/credentials')
    assert r.status_code == 200
    settings_resp = client.get('/api/einvoicing/settings').get_json()['data']
    assert settings_resp['credentials']['configured'] is False


# ─── outbox 404 shapes ───────────────────────────────────────────────────

def test_get_missing_outbox_entry_404(app_and_db):
    client = _client(app_and_db)
    _login(client)
    r = client.get('/api/einvoicing/outbox/does-not-exist')
    assert r.status_code == 404


def test_retry_missing_outbox_entry_404(app_and_db):
    client = _client(app_and_db)
    _login(client)
    r = client.post('/api/einvoicing/outbox/does-not-exist/retry')
    assert r.status_code == 404


def test_retry_non_failed_entry_rejected(app_and_db):
    app, db_path, app_data_dir = app_and_db
    conn = sqlite3.connect(db_path)
    OutboxRepository(conn).enqueue(
        company_id=1, invoice_ref='ref-1', source_type='sale', source_id=1,
        local_document_no='S-1', einvoice_no='INC-1', invoice_family='income',
        payment_type='cash', currency='JOD', provider='mock',
    )
    conn.commit()
    conn.close()

    client = app.test_client()
    _login(client)
    r = client.post('/api/einvoicing/outbox/ref-1/retry')
    assert r.status_code == 400  # still QUEUED, not FAILED_PERMANENT


def test_cancel_missing_outbox_entry_404(app_and_db):
    client = _client(app_and_db)
    _login(client)
    r = client.post('/api/einvoicing/outbox/does-not-exist/cancel')
    assert r.status_code == 404


def test_qr_404_when_not_cleared(app_and_db):
    app, db_path, app_data_dir = app_and_db
    conn = sqlite3.connect(db_path)
    OutboxRepository(conn).enqueue(
        company_id=1, invoice_ref='ref-1', source_type='sale', source_id=1,
        local_document_no='S-1', einvoice_no='INC-1', invoice_family='income',
        payment_type='cash', currency='JOD', provider='mock',
    )
    conn.commit()
    conn.close()

    client = app.test_client()
    _login(client)
    r = client.get('/api/einvoicing/qr/ref-1.svg')
    assert r.status_code == 404


def test_qr_svg_and_png_respect_requested_extension_when_locally_rendered(app_and_db):
    app, db_path, app_data_dir = app_and_db
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO einvoice_outbox (company_id, invoice_ref, source_type, source_id, einvoice_no, "
        "invoice_family, payment_type, currency, status, qr_payload, created_at, updated_at) "
        "VALUES (1,'ref-1','sale',1,'INC-1','income','cash','JOD','CLEARED','payload-data','x','x')"
    )
    conn.commit()
    conn.close()

    client = app.test_client()
    _login(client)
    r_svg = client.get('/api/einvoicing/qr/ref-1.svg')
    assert r_svg.status_code == 200
    assert r_svg.mimetype == 'image/svg+xml'

    r_png = client.get('/api/einvoicing/qr/ref-1.png')
    assert r_png.status_code == 200
    assert r_png.mimetype == 'image/png'


# ─── run-once without a configured worker ───────────────────────────────

def test_run_once_without_worker_returns_503(app_and_db):
    client = _client(app_and_db)
    _login(client)
    r = client.post('/api/einvoicing/outbox/run-once')
    assert r.status_code == 503


def test_selftest_with_mock_provider_clears(app_and_db):
    client = _client(app_and_db)
    _login(client)
    r = client.get('/api/einvoicing/selftest')
    assert r.status_code == 200
    assert r.get_json()['data']['outcome'] == 'CLEARED'


# ─── killswitch endpoints ────────────────────────────────────────────────

def test_killswitch_set_and_clear(app_and_db):
    client = _client(app_and_db)
    _login(client)
    r1 = client.post('/api/einvoicing/killswitch', json={'reason': 'test-pause'})
    assert r1.status_code == 200
    status = client.get('/api/einvoicing/status').get_json()['data']
    assert status['killswitch']['disabled'] is True
    assert status['killswitch']['reason'] == 'test-pause'

    r2 = client.delete('/api/einvoicing/killswitch')
    assert r2.status_code == 200
    status2 = client.get('/api/einvoicing/status').get_json()['data']
    assert status2['killswitch']['disabled'] is False


# ─── per-company worker registry (multi-tenant correctness) ─────────────

def test_enabling_starts_the_worker_for_that_company_only(app_with_worker_registry):
    app, workers = app_with_worker_registry
    client = app.test_client()
    _login(client, company_id=1)
    client.post('/api/einvoicing/settings', json={'enabled': '1'})
    assert workers[1].started_with is not None
    assert 1 in workers
    assert 2 not in workers, "enabling for company 1 must not touch any other company's worker"


def test_disabling_stops_only_that_companys_worker(app_with_worker_registry):
    app, workers = app_with_worker_registry
    client = app.test_client()
    _login(client, company_id=1)
    client.post('/api/einvoicing/settings', json={'enabled': '1'})
    client.post('/api/einvoicing/settings', json={'enabled': '0'})
    assert workers[1].stop_calls == 1


def test_two_companies_get_independent_workers(app_with_worker_registry):
    app, workers = app_with_worker_registry
    client_a = app.test_client()
    _login(client_a, company_id=1)
    client_a.post('/api/einvoicing/settings', json={'enabled': '1'})

    client_b = app.test_client()
    _login(client_b, company_id=2)
    client_b.post('/api/einvoicing/settings', json={'enabled': '1'})

    assert workers[1] is not workers[2]
    assert workers[1].started_with is not None
    assert workers[2].started_with is not None


def test_run_once_uses_the_requesting_companys_worker(app_with_worker_registry):
    app, workers = app_with_worker_registry
    client = app.test_client()
    _login(client, company_id=1)
    r = client.post('/api/einvoicing/outbox/run-once')
    assert r.status_code == 200
    assert 1 in workers
