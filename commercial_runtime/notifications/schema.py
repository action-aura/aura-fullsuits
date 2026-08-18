"""Outbound email -- schema for the tables this module owns.

Called from each product's own schema.py migrate_fn (see
products/retail/backend/database/schema.py's
`_migrate_add_notifications_foundation`), inside their existing
commercial_runtime/security/migration_safety.py::ensure_schema_version(...)
call -- identical wiring to
commercial_runtime/einvoicing/schema.py::apply_einvoicing_schema, which this
file is a direct structural copy of.

Every statement below is CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT
EXISTS. Nothing here ever ALTERs, DROPs, or writes to an existing product
table. An install that never configures AURA_SMTP_HOST (see smtp_client.py)
ends up with these tables present but permanently empty -- exactly like
einvoicing's own schema module, apply_notifications_schema() itself never
inserts a row.

`company_id` is declared TEXT here, not INTEGER (unlike einvoicing's own
einvoice_* tables, which predate the UUID migration) -- this module lands
after Retail's company-scoped tables (reorder_requests, supplier_contacts)
already moved to UUID-shaped ids, so TEXT matches the newer convention. This
is a soft distinction only: SQLite does not enforce declared column types on
non-INTEGER-PRIMARY-KEY columns, so either declaration would store either
shape of value without error, but TEXT documents the actual expected shape
for a reader.
"""
import sqlite3


def apply_notifications_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        -- Per-company key/value config for this module -- recipient
        -- addresses and the per-company on/off toggle. SMTP TRANSPORT
        -- config (host/port/credentials) is deliberately NOT here -- see
        -- smtp_client.py's module docstring for why that lives in env vars
        -- instead, one set per installation, not per company.
        CREATE TABLE IF NOT EXISTS email_settings (
            company_id TEXT    NOT NULL,
            skey       TEXT    NOT NULL,
            svalue     TEXT,
            updated_at TEXT,
            PRIMARY KEY (company_id, skey)
        );

        -- One row per email this install has ever queued. Recipient/
        -- subject/body are captured IN FULL at enqueue time (unlike
        -- einvoice_outbox, which stores only identifiers and rebuilds the
        -- UBL document from the source sale row at submission time) --
        -- email content has no external authority-mandated canonical form
        -- to regenerate, and the trigger that queues an email (a completed
        -- sale, a low-stock crossing, an explicit report request) may not
        -- still have the same data available by the time the worker gets
        -- to it, so capturing the finished message up front is simpler and
        -- strictly safer here.
        CREATE TABLE IF NOT EXISTS email_outbox (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id         TEXT    NOT NULL,
            email_type         TEXT    NOT NULL,   -- 'low_stock_alert' | 'report_summary' | 'verification'
            recipient          TEXT    NOT NULL,
            subject            TEXT    NOT NULL,
            body_text          TEXT    NOT NULL,
            body_html          TEXT,
            template_id        TEXT,                -- optional free-form tag, no templating engine behind it
            status             TEXT    NOT NULL DEFAULT 'QUEUED',
            attempt_count      INTEGER NOT NULL DEFAULT 0,
            next_attempt_at    TEXT,
            lease_expires_at   TEXT,                 -- crash-recovery lease while SENDING
            last_error         TEXT,                 -- sanitized exception type name, never a raw SMTP transcript
            created_at         TEXT    NOT NULL,
            updated_at         TEXT    NOT NULL,
            sent_at            TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_email_outbox_due
            ON email_outbox(status, next_attempt_at);
        CREATE INDEX IF NOT EXISTS idx_email_outbox_company
            ON email_outbox(company_id, status);

        -- Verification tokens (foundation only -- see tokens.py's module
        -- docstring for exactly what is and is not wired yet). token_hash
        -- only -- the raw token is embedded in the queued email body and is
        -- never itself persisted, mirroring how licensing_contracts never
        -- stores a plaintext secret it can avoid storing.
        CREATE TABLE IF NOT EXISTS email_verification_tokens (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id   TEXT    NOT NULL,
            recipient    TEXT    NOT NULL,
            purpose      TEXT    NOT NULL,   -- e.g. 'verify_email' -- free-form, no fixed vocabulary yet
            token_hash   TEXT    NOT NULL,
            email_outbox_id INTEGER,          -- the queued email carrying this token, if any
            expires_at   TEXT    NOT NULL,
            consumed_at  TEXT,
            created_at   TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_email_verification_tokens_lookup
            ON email_verification_tokens(company_id, recipient, purpose, consumed_at);

        -- WhatsApp -- same two-table shape as email_settings/email_outbox
        -- above (this module's docstring already names WhatsApp as one of
        -- the channels it exists to eventually house). Transport config
        -- (phone_number_id/access_token) is env-var-only for the same
        -- reason SMTP host/credentials are -- see whatsapp_client.py's
        -- module docstring -- one set per installation, not per company.
        CREATE TABLE IF NOT EXISTS whatsapp_settings (
            company_id TEXT    NOT NULL,
            skey       TEXT    NOT NULL,
            svalue     TEXT,
            updated_at TEXT,
            PRIMARY KEY (company_id, skey)
        );

        -- One row per WhatsApp message this install has ever queued.
        -- template_name/language_code/component_params_json instead of
        -- email_outbox's subject/body_text/body_html: WhatsApp sends a
        -- pre-approved message TEMPLATE, never free text, for a business-
        -- initiated message (see whatsapp_client.py's module docstring for
        -- the real Cloud API constraint this reflects) -- there is no
        -- "body" to store, only the template name and its ordered
        -- placeholder values, captured in full at enqueue time for the
        -- same reason email_outbox captures its content up front (the
        -- triggering event may not still have the data by send time).
        CREATE TABLE IF NOT EXISTS whatsapp_outbox (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id            TEXT    NOT NULL,
            message_type          TEXT    NOT NULL,   -- 'low_stock_alert' | 'report_summary' -- mirrors email_type's vocabulary
            recipient_phone_e164  TEXT    NOT NULL,
            template_name         TEXT    NOT NULL,
            language_code         TEXT    NOT NULL DEFAULT 'en_US',
            component_params_json TEXT,                -- JSON array of ordered {{1}},{{2}},... body values, or NULL
            status                TEXT    NOT NULL DEFAULT 'QUEUED',
            attempt_count         INTEGER NOT NULL DEFAULT 0,
            next_attempt_at       TEXT,
            lease_expires_at      TEXT,
            -- Written ONLY by whatsapp_worker._describe_send_failure(): the
            -- HTTP status, Meta's numeric error code, and Meta's own message/
            -- error_data.details text, collapsed to one line and capped at
            -- _MAX_STORED_ERROR_CHARS. It used to hold nothing but the
            -- exception class name; it now carries upstream text on purpose
            -- (a failure was undiagnosable otherwise), so the contract this
            -- comment pins is narrower than "sanitized": never credentials --
            -- _redact_credentials() enforces that -- and never an unbounded
            -- raw API transcript. Anything added here must keep both, because
            -- GET /api/notifications/whatsapp/outbox serves this column behind
            -- _require_session() only, not _require_admin().
            last_error            TEXT,
            wamid                 TEXT,                 -- WhatsApp's own message id, once sent
            created_at            TEXT    NOT NULL,
            updated_at            TEXT    NOT NULL,
            sent_at               TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_whatsapp_outbox_due
            ON whatsapp_outbox(status, next_attempt_at);
        CREATE INDEX IF NOT EXISTS idx_whatsapp_outbox_company
            ON whatsapp_outbox(company_id, status);
        """
    )


def apply_whatsapp_recipients_schema(conn: sqlite3.Connection) -> None:
    """Separate function, separate call site (products/retail/backend/
    database/schema.py's own v11->v12 migration), NOT folded into
    apply_notifications_schema() above -- that function's exact behavior is
    pinned by existing tests/migrations at earlier schema versions, so it
    stays byte-identical and this table gets its own idempotent entry point
    instead of growing that one's executescript.

    whatsapp_recipients -- "who gets which report", answering the real gap
    whatsapp_settings.low_stock_recipient_phone left open: that column is a
    single phone number, one purpose, one per company. A retail install
    needs MANY numbers (owner, per-branch manager, accountant, ...), each
    subscribed to a subset of report types, some scoped to one branch and
    some to the whole company. `id` is a client-generated UUID TEXT, matching
    every other post-UUID-migration company-scoped table in this lineage
    (customers/suppliers/products), not AUTOINCREMENT.

    `role_label` is a free-form TEXT, not a foreign key into any permissions
    table -- this codebase has no real RBAC yet (see root CLAUDE.md: "only a
    bare `role` string, no permission matrix"), so this column is a display
    label a shop owner types in ("Owner", "Downtown Manager"), never
    something the routing logic branches on.

    `branch_id` is INTEGER (nullable) to match `branches.id`'s own declared
    type elsewhere in this codebase (e.g. reorder_requests.branch_id) --
    NULL means "all branches", not "unset"; see whatsapp_recipients.py's
    recipients_for() for the exact matching rule this enables.

    `report_types_json` is a JSON array of report-type keys (see
    whatsapp_settings.py's REPORT_TYPES) rather than one row per
    (recipient, report_type) pair -- the set per recipient is small (at most
    4 today) and always read/written as a whole from the settings UI, so a
    join table would add write complexity (multi-row transactions on every
    save) for no query this module actually needs (nothing filters WHERE
    report_type=X across recipients at the SQL layer; whatsapp_recipients.py
    decodes and filters in Python, over a handful of rows per company).
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS whatsapp_recipients (
            id                 TEXT    PRIMARY KEY,
            company_id         TEXT    NOT NULL,
            branch_id          INTEGER,                      -- NULL = all branches
            display_name       TEXT    NOT NULL,
            role_label         TEXT,                          -- free-form; no RBAC matrix exists to key off
            phone_e164         TEXT    NOT NULL,
            report_types_json  TEXT    NOT NULL DEFAULT '[]', -- JSON array, subset of whatsapp_settings.REPORT_TYPES
            language_code      TEXT    NOT NULL DEFAULT 'en_US',
            status             TEXT    NOT NULL DEFAULT 'active',  -- 'active' | 'inactive'
            created_at         TEXT    NOT NULL,
            updated_at         TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_whatsapp_recipients_company
            ON whatsapp_recipients(company_id, status);
        """
    )
