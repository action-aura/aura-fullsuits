"""JoFotara e-invoicing -- default-ON + UnconfiguredProvider guard tests.

Jordan has mandated e-invoicing since 2024-05-31, so settings.py's
DEFAULTS['enabled'] flipped from '0' to '1' -- a fresh install/company now
records the obligation without anyone touching a toggle. That default flip
is dangerous on its own: products/retail/backend/app.py and
products/clinic/backend/app.py both used to hard-wire MockProvider (which
reports CLEARED, with a QR, without ever contacting anybody) as the live
provider. Simply turning the DB setting on would have made every shop print
a receipt claiming "Jordan e-invoice cleared" for a document no tax
authority ever saw.

The fix has two independent parts, both covered here:
  1. providers/unconfigured.py's UnconfiguredProvider is the new shipped
     default -- it queues, it never claims clearance.
  2. worker.py's OutboxWorker.run_once() refuses to lease or touch a single
     row when the configured provider's `is_configured` is False, so an
     unconfigured install can never walk a real invoice to FAILED_PERMANENT
     just because nobody has registered on the JoFotara portal yet.

Style mirrors test_settings.py (precedence over is_enabled) and
test_worker.py (a real OutboxWorker against a real sqlite file).
"""
import sqlite3

import pytest

from commercial_runtime.einvoicing import killswitch, settings
from commercial_runtime.einvoicing.document import BuyerId, EInvoiceDocument, EInvoiceLine
from commercial_runtime.einvoicing.outbox import OutboxRepository
from commercial_runtime.einvoicing.providers.base import EInvoiceProviderConfigError
from commercial_runtime.einvoicing.providers.mock import MockProvider
from commercial_runtime.einvoicing.providers.unconfigured import UnconfiguredProvider
from commercial_runtime.einvoicing.schema import apply_einvoicing_schema
from commercial_runtime.einvoicing.worker import OutboxWorker


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    apply_einvoicing_schema(c)
    return c


# ─── settings.py: the feature now defaults ON ───────────────────────────

def test_company_with_no_settings_row_is_enabled_by_default(conn):
    """A brand-new install/company that has never touched
    /api/einvoicing/settings must already be enabled -- the whole point of
    the mandate-driven default flip is that nobody has to find a toggle."""
    assert settings.get_setting(conn, company_id=1, key='enabled') == '1'
    assert settings.is_enabled(conn, app_data_dir='/tmp/unused', company_id=1) is True


def test_env_var_disable_still_beats_the_new_default(conn, tmp_path, monkeypatch):
    monkeypatch.setenv('AURA_EINVOICING_DISABLED', '1')
    assert settings.is_enabled(conn, str(tmp_path), 1) is False


def test_killswitch_disable_still_beats_the_new_default(conn, tmp_path, monkeypatch):
    monkeypatch.delenv('AURA_EINVOICING_DISABLED', raising=False)
    killswitch.set_disabled(str(tmp_path), reason='drill')
    assert settings.is_enabled(conn, str(tmp_path), 1) is False


def test_explicit_per_company_disable_still_beats_the_new_default(conn, tmp_path, monkeypatch):
    monkeypatch.delenv('AURA_EINVOICING_DISABLED', raising=False)
    settings.set_setting(conn, 1, 'enabled', '0')
    assert settings.is_enabled(conn, str(tmp_path), 1) is False


# ─── providers/unconfigured.py ──────────────────────────────────────────

def test_unconfigured_provider_is_not_configured():
    assert UnconfiguredProvider.is_configured is False
    assert UnconfiguredProvider().is_configured is False


def test_unconfigured_provider_submit_invoice_raises_config_error():
    with pytest.raises(EInvoiceProviderConfigError):
        UnconfiguredProvider().submit_invoice('ref-1', object())


def test_unconfigured_provider_check_status_raises_config_error():
    with pytest.raises(EInvoiceProviderConfigError):
        UnconfiguredProvider().check_status('ref-1', None)


# ─── worker.py: the guard that matters most ─────────────────────────────

def _conn_factory(db_path):
    def _make():
        c = sqlite3.connect(db_path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        return c
    return _make


def _setup_db(tmp_path):
    db_path = str(tmp_path / 'product.db')
    db_conn = sqlite3.connect(db_path)
    db_conn.execute("PRAGMA journal_mode=WAL")
    apply_einvoicing_schema(db_conn)
    db_conn.commit()
    db_conn.close()
    return db_path


def _document_builder(conn, row):
    return EInvoiceDocument(
        company_id=1, einvoice_no=row['einvoice_no'], local_document_no=row['local_document_no'],
        invoice_family='income', payment_type='cash', currency='JOD',
        issue_datetime='2026-08-04T12:00:00', seller_name='Shop', seller_tin='1',
        buyer_name='Buyer', buyer_id=BuyerId('TIN', '2'),
        lines=(EInvoiceLine('Item', 1, 10.0, 0.0, 0.0, 10.0),),
        subtotal=10.0, discount_total=0.0, tax_total=0.0, grand_total=10.0,
    )


def _make_worker(db_path, tmp_path, provider, **overrides):
    kwargs = dict(
        conn_factory=_conn_factory(db_path),
        app_data_dir=str(tmp_path),
        company_id=1,
        provider=provider,
        document_builder=_document_builder,
        rate_limit_seconds=0,
    )
    kwargs.update(overrides)
    return OutboxWorker(**kwargs)


def _enqueue_directly(db_path, ref='ref-1', einvoice_no='INC-000001'):
    db_conn = sqlite3.connect(db_path)
    OutboxRepository(db_conn).enqueue(
        company_id=1, invoice_ref=ref, source_type='sale', source_id=1,
        local_document_no='SALE-1', einvoice_no=einvoice_no, invoice_family='income',
        payment_type='cash', currency='JOD', provider='unconfigured',
    )
    db_conn.commit()
    db_conn.close()


def _row_state(db_path, ref='ref-1'):
    db_conn = sqlite3.connect(db_path)
    row = db_conn.execute(
        "SELECT status, attempt_count FROM einvoice_outbox WHERE invoice_ref=?", (ref,)
    ).fetchone()
    db_conn.close()
    return row


def test_unconfigured_provider_leaves_a_queued_row_untouched_on_a_single_tick(tmp_path):
    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path)
    before = _row_state(db_path)
    assert before == ('QUEUED', 0)

    worker = _make_worker(db_path, tmp_path, UnconfiguredProvider())
    result = worker.run_once()

    assert result == {'ran': False, 'reason': 'unconfigured'}
    after = _row_state(db_path)
    assert after == before, "an unconfigured provider must never lease or mutate a queued row"


def test_unconfigured_provider_can_never_walk_a_row_to_failed_permanent(tmp_path):
    """The whole point of the guard: without it, _apply_retry would count
    every tick against attempt_count and eventually mark the row
    FAILED_PERMANENT once attempt_count >= max_attempts (default 20) --
    destroying an invoice that is simply waiting for the shop to connect a
    real provider. Tick well past max_attempts and prove it never moves."""
    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path)
    max_attempts = int(settings.DEFAULTS['max_attempts'])

    worker = _make_worker(db_path, tmp_path, UnconfiguredProvider())
    for _ in range(max_attempts + 5):
        result = worker.run_once()
        assert result == {'ran': False, 'reason': 'unconfigured'}

    status, attempt_count = _row_state(db_path)
    assert status == 'QUEUED', "an unconfigured provider must never advance a row out of QUEUED"
    assert attempt_count == 0, "an unconfigured provider must never be charged against the retry budget"


def test_mock_provider_control_proves_the_same_row_does_progress(tmp_path):
    """Control for the two tests above: swap in MockProvider (is_configured
    True) against an otherwise identical setup and prove the row DOES move.
    Without this, the two tests above could be passing merely because their
    fixture holds every row still regardless of provider -- this proves
    it's specifically the is_configured guard doing the holding."""
    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path)
    before = _row_state(db_path)
    assert before == ('QUEUED', 0)

    worker = _make_worker(db_path, tmp_path, MockProvider())
    result = worker.run_once()

    assert result['ran'] is True
    assert result['outcomes']['cleared'] == 1
    status, attempt_count = _row_state(db_path)
    assert status == 'CLEARED', "control failed: MockProvider must actually progress the row"
    assert attempt_count == 0


# ─── worker.py: materialising enabled_at for a default-only-enabled company ─
#
# settings.set_setting() only ever stamps enabled_at when someone explicitly
# writes enabled='1'. A company enabled purely by the new DEFAULT (no row at
# all) has enabled_at stuck at '' forever, and
# einvoice_adapter.reconcile_missing_sales's own `if not enabled_at_value:
# return` guard silently turns off the crash-recovery sweep for exactly the
# shops the mandate-driven default exists to protect. The worker now
# materialises the default into a real, stamped row the first tick that
# reaches past the is_configured guard (see worker.py's run_once -- this
# also happens to be the first tick where reconcile_fn ever actually gets a
# chance to run, since an unconfigured provider returns before this point).

def _settings_conn(db_path):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    return c


def test_a_tick_with_a_configured_provider_materializes_enabled_at_for_a_default_only_company(tmp_path):
    db_path = _setup_db(tmp_path)
    # No explicit settings.set_setting(..., 'enabled', ...) call anywhere --
    # this company is enabled purely by DEFAULTS['enabled']='1'.
    sconn = _settings_conn(db_path)
    assert settings.enabled_at(sconn, 1) is None
    sconn.close()

    worker = _make_worker(db_path, tmp_path, MockProvider())
    result = worker.run_once()
    assert result['ran'] is True  # proves the tick actually reached past both early returns

    sconn = _settings_conn(db_path)
    assert settings.enabled_at(sconn, 1), "a tick that reaches reconcile_fn must materialize enabled_at"
    assert settings.get_setting(sconn, 1, 'enabled') == '1', "the default must become a real, persisted row"
    sconn.close()


def test_a_tick_never_resurrects_a_company_that_explicitly_disabled_itself(tmp_path):
    """Both-directions proof for the same guard: materializing the default
    for an ENABLED-by-default company must never also apply to a company
    that explicitly turned the feature off -- that company must stay
    disabled, with no enabled_at stamped, forever."""
    db_path = _setup_db(tmp_path)
    sconn = _settings_conn(db_path)
    settings.set_setting(sconn, 1, 'enabled', '0')
    sconn.commit()
    assert settings.enabled_at(sconn, 1) is None
    sconn.close()

    worker = _make_worker(db_path, tmp_path, MockProvider())
    result = worker.run_once()
    assert result == {'ran': False, 'reason': 'disabled'}

    sconn = _settings_conn(db_path)
    assert settings.enabled_at(sconn, 1) is None, "an explicitly disabled company must never get enabled_at stamped"
    assert settings.is_enabled(sconn, str(tmp_path), 1) is False, "a tick must never resurrect an explicitly disabled company"
    sconn.close()
