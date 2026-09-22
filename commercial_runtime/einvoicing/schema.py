"""JoFotara e-invoicing -- schema for the tables this module owns.

Called from each product's own schema.py migrate_fn (see
products/retail/backend/database/schema.py and
products/clinic/backend/database/schema.py), inside their existing
commercial_runtime/security/migration_safety.py::ensure_schema_version(...)
call -- so the pre-migration backup, integrity checks, and PRAGMA
user_version bump this wave relies on are the same, already-tested
infrastructure every other schema change in this repo goes through.

Every statement below is CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT
EXISTS. Nothing here ever ALTERs, DROPs, or writes to an existing Retail or
Clinic table -- see docs/einvoicing/phase1/invoice-numbering-audit.md for why
this module keeps its own sequence instead of touching sale_number /
invoice_number, and jofotara-integration-architecture.md for why these
tables live inside retail.db / clinic.db rather than a separate database
file (commercial_runtime/backup/service.py only snapshots
<product_code>.db + registry.db today; a separate einvoicing.db would not be
backed up).

An install that never enables this feature ends up with these tables
present but permanently empty. The one exception, added 2026-09-22 alongside
DEFAULTS['tax_regime'] flipping from 'jordan' to 'none' in settings.py, is
_preserve_prior_regime_for_installs_with_evidence() below: a ONE-TIME DATA
migration (not schema) that writes an explicit tax_regime='jordan' row for
any company that already has evidence of using the feature, so that upgrade
does not silently switch a genuinely-filing shop off. See that function's own
docstring, and settings.py's DEFAULTS['tax_regime'] comment, for the full
reasoning. A fresh install has no outbox and no advanced sequence, so this
finds no evidence and writes nothing -- "permanently empty" above is still
accurate for every install that was never actually using the feature, which
per that comment is every install that exists today.
"""
import sqlite3
from datetime import datetime, timezone


def apply_einvoicing_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        -- Per-company key/value config for this module. A dedicated store
        -- (not retail_settings, which Clinic doesn't even have) so the kill
        -- switch can reason about "all e-invoicing state" as one set of
        -- tables without touching product settings.
        CREATE TABLE IF NOT EXISTS einvoice_settings (
            company_id INTEGER NOT NULL,
            skey       TEXT    NOT NULL,
            svalue     TEXT,
            updated_at TEXT,
            PRIMARY KEY (company_id, skey)
        );

        -- The gapless, per-company sequence actually submitted to ISTD.
        -- Deliberately independent of sales.sale_number /
        -- clinic_invoices.invoice_number -- see
        -- docs/einvoicing/phase1/invoice-numbering-audit.md.
        CREATE TABLE IF NOT EXISTS einvoice_sequence (
            company_id INTEGER NOT NULL,
            series     TEXT    NOT NULL,   -- 'income' | 'general_sales'
            last_no    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (company_id, series)
        );

        CREATE TABLE IF NOT EXISTS einvoice_outbox (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id         INTEGER NOT NULL,
            invoice_ref        TEXT    NOT NULL,   -- idempotency key, e.g. 'AURA_RETAIL:sale:1234'
            source_type        TEXT    NOT NULL,   -- 'sale' | 'clinic_invoice' | 'return'
            source_id          INTEGER NOT NULL,
            local_document_no  TEXT,               -- sale_number / invoice_number, cross-reference only
            einvoice_no        TEXT    NOT NULL,    -- from einvoice_sequence, gapless
            invoice_family     TEXT    NOT NULL,    -- 'income' | 'general_sales'
            payment_type       TEXT    NOT NULL,    -- 'cash' | 'credit'
            currency           TEXT    NOT NULL DEFAULT 'JOD',
            document_xml       TEXT,                -- canonical UBL, the compliance evidence
            document_sha256    TEXT,
            status             TEXT    NOT NULL DEFAULT 'QUEUED',
            attempt_count      INTEGER NOT NULL DEFAULT 0,
            next_attempt_at    TEXT,
            lease_expires_at   TEXT,                -- crash-recovery lease while SUBMITTING
            submit_started_at  TEXT,                -- set BEFORE the network call
            provider           TEXT,                -- 'mock' | 'direct_istd'
            provider_uuid      TEXT,                -- ISTD-returned UUID
            qr_payload         TEXT,
            qr_image_base64    TEXT,
            last_reason_code   TEXT,
            last_detail        TEXT,                -- sanitized, never a raw response body
            created_at         TEXT    NOT NULL,
            updated_at         TEXT    NOT NULL,
            cleared_at         TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_einvoice_outbox_ref
            ON einvoice_outbox(invoice_ref);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_einvoice_outbox_no
            ON einvoice_outbox(company_id, invoice_family, einvoice_no);
        CREATE INDEX IF NOT EXISTS idx_einvoice_outbox_due
            ON einvoice_outbox(status, next_attempt_at);
        CREATE INDEX IF NOT EXISTS idx_einvoice_outbox_source
            ON einvoice_outbox(source_type, source_id);

        -- Buyer tax identifiers. A separate table, deliberately: adding
        -- tax_id/national_id columns to customers / clinic_patients would be
        -- an ALTER on a live pilot table and would survive the kill switch.
        -- This does neither.
        CREATE TABLE IF NOT EXISTS einvoice_party_ids (
            company_id INTEGER NOT NULL,
            party_type TEXT    NOT NULL,   -- 'customer' | 'patient'
            party_id   INTEGER NOT NULL,
            id_scheme  TEXT    NOT NULL,   -- 'TIN' | 'NIN' | 'PN'
            id_value   TEXT    NOT NULL,
            updated_at TEXT,
            PRIMARY KEY (company_id, party_type, party_id, id_scheme)
        );

        -- One row per submission ATTEMPT. Identifiers and outcomes only --
        -- no money amounts, no buyer PII, no secrets (guarded in
        -- einvoicing/audit.py, mirroring licensing_contracts/events.py's
        -- FORBIDDEN_DETAIL_MARKERS pattern).
        CREATE TABLE IF NOT EXISTS einvoice_audit (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id   INTEGER NOT NULL,
            invoice_ref  TEXT,
            attempt_no   INTEGER,
            event        TEXT    NOT NULL,
            outcome      TEXT,
            reason_code  TEXT,
            provider     TEXT,
            http_status  INTEGER,
            duration_ms  INTEGER,
            occurred_at  TEXT    NOT NULL,
            details_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_einvoice_audit_ref ON einvoice_audit(invoice_ref);
        """
    )
    _preserve_prior_regime_for_installs_with_evidence(conn)


def _preserve_prior_regime_for_installs_with_evidence(conn: sqlite3.Connection) -> None:
    """ONE-TIME DATA migration, added 2026-09-22 in the same change that
    flipped settings.py's DEFAULTS['tax_regime'] from 'jordan' to 'none'.

    THE PROBLEM IT SOLVES: before this change, every install with no
    einvoice_settings row read 'jordan' by fallback (DEFAULTS). After it,
    the same absent-row install reads 'none' instead -- correct for an
    install that was never really filing (see settings.py's DEFAULTS
    ['tax_regime'] comment for why the old default never actually achieved
    compliance), but WRONG for a company that has genuinely been using the
    feature with an explicit provider wired in: that company's tax_regime
    row is *also* usually absent (nothing in this module ever wrote one on
    its behalf -- 'jordan' was simply the fallback), so it would be
    silently switched off by this same upgrade. That is exactly the
    still-valid half of the old comment this migration exists to answer:
    an install that IS using the feature must keep working.

    THE EVIDENCE RULE: a company counts as "already using the feature" if
    it has EITHER
      (a) any row in einvoice_outbox (it enqueued at least one document), OR
      (b) an einvoice_sequence row with last_no > 0 (it has issued at least
          one gapless e-invoice number).
    These are the two tables this module's own sequence.py/outbox.py write
    to on the real enqueue path, and both are self-contained to this
    module (no read of any Retail/Clinic table), matching the rest of this
    file's product-independence. A company can have (b) without (a) yet --
    sequence.py reserves the number before outbox.py enqueues the row -- so
    checking only outbox would miss a company that crashed between the two;
    checking only the sequence would miss nothing sequence-related but is
    checked anyway for defense in depth. Deliberately NOT using
    einvoice_audit as evidence: an audit row is written on submission
    attempts, is best-effort logging (see audit.py), and using it as the
    ONLY signal would be circular with the very evidence (a) and (b)
    already capture more reliably.

    THE GUARD: `INSERT OR IGNORE`, keyed on this table's own
    PRIMARY KEY (company_id, skey), so a company that already has ANY
    explicit tax_regime row of its own -- 'jordan', 'none', or anything
    else an operator wrote -- is never touched. This is what makes the
    migration idempotent (a second run finds the same evidence and the
    same INSERT OR IGNORE no-ops the second time -- no duplicate rows, the
    PRIMARY KEY forbids it) and what makes it safe to run on every call to
    apply_einvoicing_schema() -- including a fresh install with none of the
    evidence tables populated yet (they were just CREATEd above, so both
    queries below run against tables that exist and are simply empty) --
    not just the first.
    """
    now = datetime.now(timezone.utc).isoformat()
    companies_with_evidence = conn.execute(
        """
        SELECT company_id FROM einvoice_outbox
        UNION
        SELECT company_id FROM einvoice_sequence WHERE last_no > 0
        """
    ).fetchall()
    for (company_id,) in companies_with_evidence:
        conn.execute(
            "INSERT OR IGNORE INTO einvoice_settings (company_id, skey, svalue, updated_at) "
            "VALUES (?, 'tax_regime', 'jordan', ?)",
            (company_id, now),
        )
