"""Retail's table classes for a SYNC-BOOT bootstrap copy (plan section 5.3).

`commercial_runtime/sync/bootstrap/classes.py` defines the vocabulary and the
shared producer/installer; shared code must not import products, so the MAP from
table name to class is here. The rule for a new table is in the test that pins it
(`retail_bootstrap_classes_test.py`): a table with no entry fails that test and
the producer refuses to ship it.

HOW A TABLE IS DECIDED. Ask what travels in a sync event, and whose the data is:

  SYNCED         carried by sync events (the shop's business). Copy as is.
  DERIVED        computed from SYNCED rows (balances, exception lists). Copy as is,
                 so a copy and a replay of the same history agree.
  AUTHORITY      signed by the main computer. NEVER copied from a peer: a copy would
                 bypass the signing that stops one computer rewriting another's
                 staff (AUDIT-033/S-AUTH). Dropped, and returned by the main's
                 signed re-announce: till allocations, numbering books, branch
                 aliases, till-release acknowledgements.
  LOCAL_CARRY    this computer's own, not on the wire: its next invoice number, PIN
                 lockouts, e-invoice sequence and credentials, e-mail and WhatsApp
                 queues, its hub bookkeeping. The producer's rows are dropped from
                 the copy; the requester's own are carried over, so its numbering
                 does not restart and its credentials do not vanish.
  LOCAL_DROP     the producer's local logs; nothing of the requester's is carried
                 either (audit trail, conflict log, held carts, the outbox -- the
                 outbox is handled separately by the installer, see below).
  BLOCK_IF_ROWS  LOCAL_CARRY, and an install is refused while the requester has an
                 OPEN row (an open cash drawer is uncounted money that exists
                 nowhere else).
  ROW_LEVEL      `retail_settings`, whose rows differ by key (see RETAIL_ROW_RULES).

Honest limit, recorded rather than hidden: LOCAL_CARRY rows keep the integer ids and
branch ids of the requester's OLD database. For the case this exists for -- a
late joiner that has no business data of its own -- they are empty or
id-independent. A device that is both far behind AND holds its own local-only
money rows (cash movements, expenses) carries them across id spaces that no longer
match; that combination is unproved (ROADMAP "SYNC-BOOT Phase 2").
"""
from __future__ import annotations

from commercial_runtime.sync.bootstrap import classes as c
from commercial_runtime.sync.bootstrap.classes import BootstrapSpec, RowRule

RETAIL_TABLE_CLASSES = {
    # ── SYNCED: the shop's business ─────────────────────────────────────────
    "branches": c.SYNCED,
    "categories": c.SYNCED,
    "customers": c.SYNCED,
    "suppliers": c.SYNCED,
    "supplier_contacts": c.SYNCED,        # children of suppliers; copied with them so no contact dangles
    "products": c.SYNCED,
    "product_modifier_groups": c.SYNCED,
    "modifier_groups": c.SYNCED,
    "modifier_options": c.SYNCED,
    "promotions": c.SYNCED,
    "sales": c.SYNCED,
    "sale_items": c.SYNCED,
    "sale_item_modifiers": c.SYNCED,
    "sale_item_promotions": c.SYNCED,
    "payments": c.SYNCED,
    "returns": c.SYNCED,
    "return_items": c.SYNCED,
    "return_refund_legs": c.SYNCED,
    "sales_quotations": c.SYNCED,
    "sales_quotation_lines": c.SYNCED,
    "purchase_orders": c.SYNCED,
    "purchase_order_items": c.SYNCED,
    "purchase_order_receipts": c.SYNCED,
    "purchase_order_receipt_lines": c.SYNCED,
    "stock_transfers": c.SYNCED,
    "stock_transfer_items": c.SYNCED,
    "cheques": c.SYNCED,
    "cheque_events": c.SYNCED,
    "reorder_requests": c.SYNCED,
    "inventory_movements": c.SYNCED,
    "loyalty_ledger": c.SYNCED,
    "party_balance_adjustments": c.SYNCED,
    "gl_accounts": c.SYNCED,              # journals reference accounts by id: they travel together
    "gl_journals": c.SYNCED,
    "gl_journal_lines": c.SYNCED,
    "sync_apply_quarantine": c.SYNCED,    # events the producer PARKED: they stay parked here, not lost
    # ── DERIVED: recomputable from the above ────────────────────────────────
    "inventory_balances": c.DERIVED,
    "stock_exceptions": c.DERIVED,
    "cheque_reconcile_pending": c.DERIVED,
    # ── AUTHORITY: dropped, returned signed by the main computer ────────────
    "doc_series": c.AUTHORITY,
    "till_allocations": c.AUTHORITY,
    "branch_uid_aliases": c.AUTHORITY,
    # ── ROW_LEVEL ───────────────────────────────────────────────────────────
    "retail_settings": c.ROW_LEVEL,
    # ── LOCAL_CARRY: this computer's own ────────────────────────────────────
    "doc_series_counter": c.LOCAL_CARRY,  # its next number in each numbering book
    "doc_sequences": c.LOCAL_CARRY,       # legacy per-type counters
    "pin_approval_attempts": c.LOCAL_CARRY,
    "tax_rates": c.LOCAL_CARRY,
    "payment_methods": c.LOCAL_CARRY,
    "cash_movements": c.LOCAL_CARRY,
    "expenses": c.LOCAL_CARRY,
    "einvoice_audit": c.LOCAL_CARRY,
    "einvoice_outbox": c.LOCAL_CARRY,
    "einvoice_party_ids": c.LOCAL_CARRY,
    "einvoice_sequence": c.LOCAL_CARRY,   # the tax authority's own sequence: never reused across computers
    "einvoice_settings": c.LOCAL_CARRY,   # credentials
    "email_outbox": c.LOCAL_CARRY,
    "email_settings": c.LOCAL_CARRY,      # SMTP credentials
    "email_verification_tokens": c.LOCAL_CARRY,
    "whatsapp_outbox": c.LOCAL_CARRY,
    "whatsapp_recipients": c.LOCAL_CARRY,
    "whatsapp_settings": c.LOCAL_CARRY,   # credentials
    "sync_freshness": c.LOCAL_CARRY,
    "site_device_cursors": c.LOCAL_CARRY,  # the hub's bookkeeping about ITS paired devices
    "site_forward_cursor": c.LOCAL_CARRY,
    "site_paired_devices": c.LOCAL_CARRY,
    "site_prune_state": c.LOCAL_CARRY,
    "site_roster": c.LOCAL_CARRY,
    "site_sync_events": c.LOCAL_CARRY,    # the hub's relay log is the hub's, never a device's
    "site_sync_nonces": c.LOCAL_CARRY,
    # ── BLOCK_IF_ROWS ───────────────────────────────────────────────────────
    "cash_sessions": c.BLOCK_IF_ROWS,
    # ── LOCAL_DROP ──────────────────────────────────────────────────────────
    "audit_log": c.LOCAL_DROP,
    "sync_conflicts": c.LOCAL_DROP,
    "held_sales": c.LOCAL_DROP,           # an unfinished cart is not the shop's record
    "journal_entries": c.LOCAL_DROP,      # dead table (schema.py: "never had a single INSERT or SELECT")
    "party_balance_upgrade_report": c.LOCAL_DROP,  # the producer's own migration report
    "sync_outbox": c.LOCAL_DROP,          # the producer's unpushed events are the producer's; the REQUESTER's are carried by installer._carry_outbox
    "sync_cursor": c.LOCAL_DROP,          # rewritten to (as_of, relay_url) by the installer
}

#: `retail_settings` rows by key. `numbering_till:<terminal>` is a computer's own
#: document-number counter (written emit-free; a peer can never legitimately set
#: it -- test_retail_setting_device_local_skip). `blob_*` is never on the wire.
#: `till_release_ack:` is signed by the till being released or the main
#: (authority.RELEASE_ACK_PREFIX). Everything else is a shop-wide setting that
#: `retail_setting` events carry.
RETAIL_ROW_RULES = {
    "retail_settings": RowRule(
        key_column="skey",
        prefix_classes=(
            ("numbering_till:", c.LOCAL_CARRY),
            ("blob_", c.LOCAL_CARRY),
            ("till_release_ack:", c.AUTHORITY),
        ),
        default_class=c.SYNCED,
    ),
}

#: An open cash session blocks an install (see classes.BLOCK_IF_ROWS).
RETAIL_BLOCK_WHERE = {"cash_sessions": "status = 'open'"}


def retail_spec(schema_version: int) -> BootstrapSpec:
    """The retail spec for THIS build (`schema_version` = RETAIL_SCHEMA_VERSION,
    passed in so this module never imports schema.py and cannot cycle)."""
    return BootstrapSpec(
        product="retail",
        db_name="retail.db",
        table_classes=RETAIL_TABLE_CLASSES,
        schema_version=schema_version,
        row_rules=RETAIL_ROW_RULES,
        block_where=RETAIL_BLOCK_WHERE,
    )
