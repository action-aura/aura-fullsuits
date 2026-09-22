"""JoFotara e-invoicing -- tests for the 2026-09-22 opt-in default flip.

DEFAULTS['tax_regime'] moved from 'jordan' to 'none' (settings.py), and
apply_einvoicing_schema() (schema.py) grew a one-time data-preservation
migration alongside it. This file is the new, dedicated coverage for both
halves of that change -- deliberately separate from test_settings.py (which
already owns the general four-layer precedence tests, updated in the same
change to stop pinning the old default) so the "did the flip actually ship
opt-in, and did it actually preserve installs that were already filing"
claims have one obvious home each.

Two things this file proves, matching the two failure modes the change is
about:

  1. THE FLIP ITSELF: a fresh company, or any company with no evidence of
     prior use, resolves is_enabled() False and never reaches
     OutboxRepository.enqueue() -- proven at the is_enabled()/enqueue
     boundary this module owns (see _maybe_enqueue_sale below for why not
     through a real retail sale).
  2. THE PRESERVATION MIGRATION: a company that already has evidence of
     using the feature (an einvoice_outbox row, or an einvoice_sequence past
     zero) gets an explicit tax_regime='jordan' row written for it by
     apply_einvoicing_schema(), is never overwritten if it already has an
     explicit row of its own, and the whole thing is idempotent.

Both migration tests below simulate an existing install by inserting
evidence rows DIRECTLY via SQL (representing data already on disk before
this change shipped) and then re-invoking apply_einvoicing_schema() --
exactly what happens on the next schema-version-triggered migration on a
real install, since products/retail/backend/database/schema.py's
_migrate_retail_schema calls apply_einvoicing_schema() unconditionally,
every time ANY migration runs (see that call site's own comment).
"""
import sqlite3

import pytest

from commercial_runtime.einvoicing import settings
from commercial_runtime.einvoicing.outbox import OutboxRepository
from commercial_runtime.einvoicing.schema import apply_einvoicing_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    apply_einvoicing_schema(c)
    return c


def _maybe_enqueue_sale(conn, app_data_dir, company_id, *, invoice_ref, source_id, einvoice_no):
    """Mirrors products/retail/backend/core/retail/einvoice_adapter.py's
    enqueue_sale() gate -- the ONLY thing retail's real adapter does before
    ever calling OutboxRepository.enqueue() is check settings.is_enabled().
    Reproduced locally rather than imported: importing retail's adapter here
    would pull a product-specific import into this product-independent
    module, the same reason commercial_runtime/tests/einvoicing_migration_
    test.py's own docstring gives for keeping real product boots out of this
    suite (retail's and clinic's app.py/database modules can't share a
    process, and this module never imports either product). The full,
    real-sale version of this same claim is products/retail/tests/
    retail_einvoicing_test.py::test_disabled_sale_response_has_no_einvoice_key.
    """
    if not settings.is_enabled(conn, app_data_dir, company_id):
        return None
    return OutboxRepository(conn).enqueue(
        company_id=company_id, invoice_ref=invoice_ref, source_type='sale', source_id=source_id,
        local_document_no=f'SALE-{source_id}', einvoice_no=einvoice_no, invoice_family='income',
        payment_type='cash', currency='JOD', provider='unconfigured',
    )


def _insert_outbox_evidence(conn, company_id, invoice_ref):
    """Evidence rule (a): a row in einvoice_outbox -- this company enqueued
    at least one real document. Inserted directly via SQL to simulate a row
    that already existed on disk before this change shipped (not something
    this test's own code path is expected to have written)."""
    now = '2026-01-01T00:00:00'
    conn.execute(
        "INSERT INTO einvoice_outbox (company_id, invoice_ref, source_type, source_id, einvoice_no, "
        "invoice_family, payment_type, currency, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (company_id, invoice_ref, 'sale', 1, 'INC-000001', 'income', 'cash', 'JOD', 'QUEUED', now, now),
    )


def _insert_sequence_evidence(conn, company_id, last_no):
    """Evidence rule (b): an einvoice_sequence row past zero -- this company
    has issued at least one gapless e-invoice number. sequence.py reserves
    the number BEFORE outbox.py enqueues the row, so a crash between the two
    leaves exactly this shape (sequence advanced, no outbox row yet) --
    evidence rule (a) alone would miss it."""
    conn.execute(
        "INSERT INTO einvoice_sequence (company_id, series, last_no) VALUES (?,?,?)",
        (company_id, 'income', last_no),
    )


# ─── the flip itself: opt-in, not opt-out ───────────────────────────────

def test_fresh_install_resolves_disabled_with_no_settings_rows_at_all(conn, tmp_path):
    """Baseline for everything else in this file: zero rows in
    einvoice_settings for this company -- not even an 'enabled' row -- must
    resolve is_enabled() False. (Same claim as test_settings.py's
    test_default_tax_regime_is_none_and_feature_resolves_disabled; repeated
    here, self-contained, because the rest of this file's evidence/migration
    tests build directly on top of this baseline.)"""
    company_id = 500
    row_count = conn.execute(
        "SELECT COUNT(*) FROM einvoice_settings WHERE company_id=?", (company_id,)
    ).fetchone()[0]
    assert row_count == 0
    assert settings.is_enabled(conn, str(tmp_path), company_id) is False


def test_fresh_install_enqueues_nothing_at_the_adapter_boundary(conn, tmp_path):
    """THEREFORE: not just is_enabled()==False in isolation, but the actual
    consequence a shop would notice -- a sale for a fresh company must never
    reach OutboxRepository.enqueue() at all, so einvoice_outbox stays empty
    and no e-invoice number is ever minted for it."""
    result = _maybe_enqueue_sale(
        conn, str(tmp_path), 500, invoice_ref='ref-fresh-1', source_id=1, einvoice_no='INC-000001',
    )
    assert result is None, "a fresh company's sale must not enqueue an outbox row"
    count = conn.execute("SELECT COUNT(*) FROM einvoice_outbox").fetchone()[0]
    assert count == 0


def test_explicit_jordan_after_the_flip_turns_it_back_on(conn, tmp_path):
    """THE OWNER'S SWITCH-ON PATH, proven rather than assumed: a company with
    NO evidence of prior use at all (a genuinely fresh install, or one the
    owner is turning on for the first time after wiring real ISTD
    credentials) must still be able to opt in by writing tax_regime='jordan'
    explicitly -- this is the whole reason the regime is a value an operator
    can STATE, not a one-way default. If this test alone regressed, the
    opt-in default flip would have made the feature unreachable, not merely
    off."""
    company_id = 505
    assert settings.is_enabled(conn, str(tmp_path), company_id) is False, "precondition: fresh, off by default"

    settings.set_setting(conn, company_id, 'tax_regime', 'jordan')

    assert settings.get_setting(conn, company_id, 'tax_regime') == 'jordan'
    assert settings.is_enabled(conn, str(tmp_path), company_id) is True

    enqueued_id = _maybe_enqueue_sale(
        conn, str(tmp_path), company_id, invoice_ref='ref-switched-on', source_id=1, einvoice_no='INC-000001',
    )
    assert enqueued_id is not None, "explicitly opting in must actually reopen the enqueue path, not just the flag"


def test_unrecognised_regime_value_still_resolves_off(conn, tmp_path):
    """The fail-closed property this whole layer depends on is unchanged by
    the default flip: only the exact literal 'jordan' opts a company in. Also
    exercises the migration re-run against this shape -- an unrecognised
    value in einvoice_settings is not evidence (the preservation migration's
    evidence query never looks at einvoice_settings at all), so a re-run must
    leave it exactly as an operator wrote it."""
    company_id = 506
    settings.set_setting(conn, company_id, 'enabled', '1')
    settings.set_setting(conn, company_id, 'tax_regime', 'jorda')  # typo

    apply_einvoicing_schema(conn)  # migration re-run must not touch this either

    assert settings.get_setting(conn, company_id, 'tax_regime') == 'jorda'
    assert settings.is_enabled(conn, str(tmp_path), company_id) is False


# ─── the preservation migration: installs already filing keep working ───

def test_prior_outbox_evidence_gets_migrated_to_jordan_and_resolves_enabled(conn, tmp_path):
    """Step 3's whole point, evidence rule (a): a company with a row in
    einvoice_outbox -- it enqueued at least one real document under the OLD
    default -- must not be silently switched off by this upgrade."""
    company_id = 501
    _insert_outbox_evidence(conn, company_id, invoice_ref='pre-existing-ref')
    assert settings.get_setting(conn, company_id, 'tax_regime') == 'none', (
        "precondition: no explicit row yet, so it currently reads the new default"
    )

    apply_einvoicing_schema(conn)  # re-run, as a real upgrade's migrate_fn would do

    assert settings.get_setting(conn, company_id, 'tax_regime') == 'jordan'
    assert settings.is_enabled(conn, str(tmp_path), company_id) is True


def test_prior_sequence_evidence_gets_migrated_to_jordan_and_resolves_enabled(conn, tmp_path):
    """Evidence rule (b): a company can have an advanced einvoice_sequence
    without (yet) having an einvoice_outbox row for it -- sequence.py
    reserves the number before outbox.py enqueues the row, so a crash
    between the two leaves exactly this shape. Evidence rule (a) alone would
    miss it, which is why the migration's evidence query checks both."""
    company_id = 502
    _insert_sequence_evidence(conn, company_id, last_no=3)

    apply_einvoicing_schema(conn)

    assert settings.get_setting(conn, company_id, 'tax_regime') == 'jordan'
    assert settings.is_enabled(conn, str(tmp_path), company_id) is True


def test_explicit_none_is_never_overwritten_by_the_migration(conn, tmp_path):
    """THE GUARD THAT MATTERS MOST HERE: a company that explicitly opted OUT
    (tax_regime='none' on its own row, not merely absent) must stay opted
    out even though it also has outbox evidence -- e.g. a shop that used the
    feature once and then explicitly turned it off must not be silently
    turned back on by an upgrade either. INSERT OR IGNORE, keyed on
    einvoice_settings' own PRIMARY KEY (company_id, skey), is what makes
    this hold: an explicit row of ANY value already occupies that key."""
    company_id = 503
    _insert_outbox_evidence(conn, company_id, invoice_ref='ref-then-opted-out')
    settings.set_setting(conn, company_id, 'tax_regime', 'none')

    apply_einvoicing_schema(conn)

    assert settings.get_setting(conn, company_id, 'tax_regime') == 'none', (
        "the migration overwrote an explicit opt-out -- the INSERT OR IGNORE guard is broken"
    )
    assert settings.is_enabled(conn, str(tmp_path), company_id) is False


def test_migration_is_idempotent_no_duplicate_rows_on_rerun(conn, tmp_path):
    """Run the migration path three times against the same evidence and
    prove: (a) the resolved value never changes, (b) there is still exactly
    ONE tax_regime row for this company. einvoice_settings' own PRIMARY KEY
    (company_id, skey) makes a genuine duplicate impossible to create by
    construction -- so this also stands as a check that the migration really
    does use INSERT OR IGNORE and not some other INSERT that would raise
    IntegrityError here instead of silently no-op'ing."""
    company_id = 504
    _insert_outbox_evidence(conn, company_id, invoice_ref='ref-idempotent')

    apply_einvoicing_schema(conn)
    apply_einvoicing_schema(conn)
    apply_einvoicing_schema(conn)

    rows = conn.execute(
        "SELECT svalue FROM einvoice_settings WHERE company_id=? AND skey='tax_regime'", (company_id,)
    ).fetchall()
    assert len(rows) == 1, f"expected exactly one tax_regime row, found {len(rows)}"
    assert rows[0][0] == 'jordan'
    assert settings.is_enabled(conn, str(tmp_path), company_id) is True
