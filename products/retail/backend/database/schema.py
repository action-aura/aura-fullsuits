"""
Aura Retail -- database schema and connection.

Owns retail.db: branches, categories, products, inventory_movements,
inventory_balances, customers, suppliers, purchase_orders,
purchase_order_items, sales, sale_items, returns, return_items, payments,
tax_rates, journal_entries, audit_log. `retail_settings` and `doc_sequences`
are created lazily by api/retail_api.py's `_ensure_credit_schema` (unchanged
from source).

Extracted verbatim (schema + seed) from Action Aura Enterprise's
database/subsystem_db.py `init_retail`/`_seed_retail` (lines 4732-4941), which
also owned schema for every other subsystem in that monolith -- see
docs/migration/dependency-map.md §1. Only the retail-relevant slice plus the
generic connection helpers it needs are kept here.
"""
import os
import sqlite3
import random
from datetime import datetime, timedelta, timezone

_app_data = os.environ.get('AURA_APP_DATA')
if _app_data:
    BASE_DIR = os.path.join(_app_data, 'database')
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SUBSYS_DIR = os.path.join(BASE_DIR, 'subsystems')

# Wave 1B (Part K): see products/clinic/backend/database/schema.py's
# identical CLINIC_SCHEMA_VERSION for the full rationale.
# Multi-device sync foundation (2026-08-06), v1 -> v2: categories.id moves
# from INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY (client-
# generated UUID) so two offline devices can create categories without ever
# colliding on id. See _migrate_categories_to_uuid below.
# Multi-device sync foundation (2026-08-07), v2 -> v3: products.category_id's
# FOREIGN KEY gains ON DELETE SET NULL. See
# _migrate_products_category_fk_on_delete_set_null below for why a bare
# `REFERENCES categories(id)` permanently wedges sync on any device holding a
# product in a category some OTHER device deleted.
# Multi-device sync foundation (2026-08-07), v3 -> v4: products.id moves from
# INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY (client-generated
# UUID), same reason categories got this in v1 -> v2: two offline devices
# must never collide on an id. Every table with a declared FK to products(id)
# -- inventory_movements, inventory_balances, sale_items,
# purchase_order_items, return_items -- is rebuilt in the same transaction
# and has its product_id values remapped. See _migrate_products_to_uuid below.
# Still v4: customers.id gets the identical INTEGER -> TEXT UUID treatment,
# plus a new `status` column (customers never had one) and the credit_mode/
# credit_limit/credit_balance columns folded in as first-class columns. No
# table has a declared FK to customers(id) (sales.customer_id and
# payments.party_id are both loose, undeclared columns), so this rides along
# on the same v4 bump rather than needing one of its own. See
# _migrate_customers_to_uuid below.
# Still v4: suppliers.id gets the identical INTEGER -> TEXT UUID treatment,
# plus the payment_terms/credit_balance columns _ensure_credit_schema adds at
# runtime folded in as first-class columns. Unlike customers, this table DOES
# have a declared FK pointing at it (purchase_orders.supplier_id
# REFERENCES suppliers(id)), so this migration carries the same rename-away
# hazard as products/categories -- see _migrate_suppliers_to_uuid below.
# v5: adds products.supplier_id (TEXT, referencing suppliers(id) which is
# already UUID by this point) -- see _migrate_products_add_supplier_fk below.
# v6: PO-preview-by-supplier foundation (Thursday demo, Stream B). Adds
# supplier_contacts (a supplier can have several named contacts -- orders/
# accounts/general -- each with its own channel), plus split-group/routing/
# idempotency columns on purchase_orders (the writers that populate them land
# later this week; this bump only adds the columns) and suppliers.
# min_order_value (used by the split preview's MOQ warning). Pure additive
# migration -- ADD COLUMN + CREATE TABLE only, nothing renamed or retyped, so
# unlike v1-v5 this needs no _new-table rebuild. See
# _migrate_add_supplier_contacts_and_po_split below.
# v7 (docs/einvoicing/phase1/): adds the einvoice_* tables used by opt-in
# Jordan JoFotara e-invoicing -- CREATE TABLE IF NOT EXISTS only, nothing
# existing is ALTERed, read, or written. This arrived on master as ITS v2
# (master numbered v1 -> v2 einvoicing -> v3 products.supplier_id INTEGER)
# while this lineage independently ran v1 -> v6; the two histories were
# merged on 2026-08-10 (feat/retail-mobile-build-baseline). This lineage's
# numbering wins and master's v2/v3 are superseded, never re-run: every step
# in _migrate_retail_schema gates on the LIVE schema (PRAGMA table_info /
# foreign_key_list), never on the version integer, so a database carrying
# master's v3 marker migrates correctly even though "3" meant something
# different there. The bump to 7 is load-bearing, not cosmetic:
# ensure_schema_version returns early on current >= target, so a database
# already at 6 from this lineage would NEVER receive the einvoice_* tables
# if this stayed at 6.
# v8 (reorder automation foundation, feat/reorder-automation-foundation):
# adds products.reorder_method (TEXT DEFAULT 'none' -- per-product opt-in;
# an install that never sets this on any product gets zero behavior change)
# and the reorder_requests table (client-generated UUID id, same convention
# as every other sync-eligible entity since the multi-device sync
# foundation -- never autoincrement, so this table CAN be synced through
# Owner's relay, unlike purchase_orders; see
# _migrate_add_reorder_automation_foundation below for the full reasoning).
# Pure additive migration -- ADD COLUMN + CREATE TABLE only, same shape as
# v5/v6, so no _new-table rebuild is needed here either.
# v9 (feat/email-outbox-foundation): adds the email_* tables owned by
# commercial_runtime/notifications -- CREATE TABLE IF NOT EXISTS only,
# nothing existing is ALTERed, read, or written. Same "opt-in, invisible
# until configured" shape as v7's einvoice_* addition: an install that never
# sets AURA_SMTP_HOST (commercial_runtime/notifications/smtp_client.py) or
# any company's `email_settings.enabled` gets zero behavior change -- the
# reorder-automation post-sale hook (core/retail/reorder_hook.py) now also
# queues a low-stock alert email, but only when both of those are true, so
# an install that never configures either one sees byte-for-byte the same
# reorder_requests-only behavior v8 already shipped.
# v10 (feat/shift-cash-drawer): real shift / cash-drawer management -- cash
# float in/out tracking plus X (mid-shift, non-destructive) and Z (end-of-
# shift, locking) reports. Two brand-new tables only, same additive shape as
# v8/v9 (CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS, nothing
# existing dropped or retyped):
#   - `cash_sessions`: one row per opened-to-closed till session
#     (company_id/branch_id-scoped, id is a client-generated UUID -- same
#     convention as reorder_requests/supplier_contacts, never autoincrement,
#     so this table CAN ride the sync outbox later without hitting the
#     purchase_orders-style UUID-parse wall reorder_requests' own v8 comment
#     explains). A partial UNIQUE index (idx_cash_sessions_one_open_per_branch)
#     enforces "at most one OPEN session per branch" as a real constraint,
#     mirroring idx_reorder_requests_open's technique exactly.
#   - `cash_movements`: one row per float-in/float-out/paid-in/paid-out event
#     against an open session. Scoped ONLY via session_id (no company_id/
#     branch_id of its own) -- a movement has no meaning outside the session
#     it belongs to, so the FK is the only scope it needs, same shape as
#     sale_items scoping through sale_id rather than repeating company_id.
# PLUS two nullable additive columns -- `sales.session_id` and
# `returns.session_id` (both TEXT, REFERENCES cash_sessions(id)) -- so a
# completed sale/return can be attributed to the exact till session it
# happened in. Stamped at write time in retail_api.py::create_sale /
# create_return via the new `_open_cash_session_id()` helper, which NEVER
# raises and defaults to NULL on any failure -- an install that never opens a
# cash session sees byte-for-byte the same sales/returns behavior as before
# this migration (see retail_cash_drawer_regression_test.py). This is a
# direct FK stamp, not a branch+time-range lookup at report time -- a
# time-range query silently breaks the instant a session spans midnight, or
# whenever two sessions on the same branch sit back-to-back with no visible
# gap between them; a stamped id is unambiguous regardless of wall-clock
# weirdness (DST, backdated imports, clock skew across devices), the same
# reasoning `sync_outbox` already relies on entity ids for, never timestamps,
# to identify what a relayed event is actually about.
# v11 (feat/pos-hold-resume-sale): adds the held_sales table (park/resume a
# sale on the POS screen). CREATE TABLE IF NOT EXISTS only -- no existing
# table is ALTERed, read, or written, same "pure additive" shape as v8/v9.
#
# *** RENUMBERED AT INTEGRATION TIME, TWICE -- this branch originally
# shipped as v4, cut from an old point of feat/retail-mobile-build-baseline
# (RETAIL_SCHEMA_VERSION was 3 there at the time; see git history on
# feat/pos-hold-resume-sale for that original commit's own collision-risk
# note, which correctly predicted this renumbering would be needed). The
# ROADMAP.md ledger (2026-08-12) reserved v9 for this feature, but
# feat/email-outbox-foundation claimed v9 first (merged into this lineage
# ahead of held-sales landing) -- verified directly by reading this file at
# feat/email-outbox-foundation's tip before renumbering, not assumed from
# the ledger alone. This branch was first rebased onto that v9 tip and
# renumbered to v10 -- but before that landed, feat/shift-cash-drawer
# (commit 41d24cf, cash_sessions/cash_movements/X-Z reports) independently
# claimed v10 for real, directly on the SAME v9 base (83b5bcb), and that
# commit exists with its own passing tests. So v10 is now taken by a
# sibling branch this task is explicitly scoped to never touch or merge
# with -- v11 is the actual next-free number, verified the same way (read
# feat/shift-cash-drawer's schema.py directly, not inferred). There is
# consequently a deliberate GAP at v10 in THIS branch's own migration
# chain -- it never claims or runs anything for "v10" because that number
# belongs to a different, unmerged branch's schema change. This is
# harmless: ensure_schema_version only cares that the target integer is
# higher than whatever a real database's PRAGMA user_version currently is,
# never that every integer up to it was independently used by THIS
# lineage (see this file's own v1-v3 vs. master's superseded v2/v3
# numbering, above, for the precedent). Whoever eventually reconciles
# feat/pos-hold-resume-sale with feat/shift-cash-drawer at real merge time
# will need to pick a final relative order and may renumber one of them
# again -- that is their decision, not made here.
#
# `held_sales.customer_id` is TEXT here (not the original commit's INTEGER)
# to match `customers.id`, which is UUID TEXT on this lineage since the
# multi-device sync foundation's v1->v4 migrations (see
# _migrate_customers_to_uuid below) -- an INTEGER-declared column would
# still physically store a UUID string fine (SQLite column affinity is
# soft), but a declared type that lies about the real shape of the data is
# a bug waiting to bite the next reader/writer, so it's fixed here rather
# than carried forward. See _migrate_add_held_sales below for the
# corresponding table definition and subsystem-retail.js's _saveHold() for
# the matching frontend fix (was `+customerId`, a numeric coercion that
# silently turned every UUID customer id into NaN -> null -- the exact
# `+`-coercion-on-an-id-field bug pattern ROADMAP.md's 2026-08-12 entry
# flagged whoever reconciled this branch to grep for).
# v11 -> v12 (feat/retail-mobile-build-baseline, whatsapp-recipients):
# adds whatsapp_recipients (commercial_runtime/notifications/schema.py::
# apply_whatsapp_recipients_schema) -- multi-recipient, role/branch-scoped
# WhatsApp report routing. Pure additive CREATE TABLE IF NOT EXISTS, same
# shape as every notifications-owned table before it; no existing table is
# touched. See _migrate_add_whatsapp_recipients below.
# v12 -> v13 (launch-readiness Phase 2, ROADMAP.md 2026-08-21 reservation):
# identity and attribution columns. THE FIRST MIGRATION IN THIS FILE THAT
# ALTERS THE TABLES HOLDING A REAL SHOP'S SALES HISTORY -- every step before
# it either created new tables (v6-v12) or rebuilt catalogue/party tables
# (v1-v5). It is consequently additive ONLY: ALTER TABLE ADD COLUMN,
# CREATE [UNIQUE] INDEX IF NOT EXISTS and guarded UPDATEs, never the
# DROP+RENAME rebuild _migrate_products_to_uuid uses. That rebuild's risk
# profile is deliberately not repeated here: it copies every row through a
# fresh table with PRAGMA foreign_keys temporarily OFF, and a mistake in the
# column list silently drops real data with no error and a passing
# integrity_check -- see _legacy_supplier_link's docstring for a case where
# exactly that happened on this very lineage. Three groups of columns:
#   - `uid` + a partial UNIQUE index on branches/sales/sale_items/returns/
#     return_items/inventory_movements/payments. The WIRE identity, exactly
#     as registry v3 gave `users` one (commercial_runtime/identity/
#     account_schema.py). The local autoincrement `id` stays the primary key
#     and stays a private detail of this install.
#   - `actor_user_uid`/`terminal_id`/`created_at_utc` on sales/returns/
#     inventory_movements/cash_sessions/cash_movements -- WHO, WHERE and
#     WHEN-in-real-time, as three structured columns beside (never instead
#     of) the existing free-text cashier/created_by/opened_by.
#   - `row_version`/`updated_at_utc`/`deleted_at_utc` on the four catalogue
#     tables (categories/products/customers/suppliers) and reorder_requests
#     -- the reject-stale marker plus a soft tombstone, matching registry
#     v3's identical triple on `users`.
# v13 -> v14 (same reservation): the company_id rebind. `company_id` is
# derived once, locally, at onboarding as md5(admin_email)
# (commercial_runtime/identity/onboarding_routes.py::create_admin), which is
# a pure function of an email address and therefore means nothing to Owner.
# Owner's relay scopes every sync event by `license_id`
# (owner/app/sync/routes.py: `SyncEvent.license_id == license_id`, resolved
# server-side from the VERIFIED installation, never from body input), and
# that same value is what Owner signs into every assertion as
# `license_public_id` (owner/app/licensing_service/assertions.py:
# `"license_public_id": str(license_row.id)`). So the Owner-issued tenant key
# this database has to converge on is `license_public_id`, and v14 is the
# ability to move onto it. Nothing about v14 requires a licence to exist:
# licensing is OFF by default in this product (config.py -- with
# OWNER_LICENSING_BASE_URL unset the app runs fully unlocked), so the common
# case is an install that reaches v14 long before it ever activates. v14
# therefore advances unconditionally and the rebind is a no-op when there is
# nothing to rebind to. See _migrate_rebind_company_id_to_owner_issued and
# rebind_company_id below.
# v14 -> v15 (launch-readiness Phase 3, docs/launch-readiness/
# phase3-ledger-truth.md, ROADMAP.md's 2026-08-21 reservation): the ledger
# becomes able to reproduce the cache. `inventory_balances.quantity_on_hand`
# is a STORED number five writers keep in step with `inventory_movements` by
# hand; core/retail/stock_reconciliation.py can already MEASURE the two
# disagreeing, but on a pre-existing install the ledger simply has no rows
# behind most balances at all -- the stock was there before the movement
# table was ever written to, or it arrived through the importer's absolute
# SET, or through _seed_retail. v15 seeds ONE `opening_count` movement per
# unexplained key carrying the RESIDUAL -- quantity_on_hand minus whatever
# the ledger already accounts for there -- and then GATES: it recomputes
# drift afterwards and refuses to advance the version marker if any
# REPAIRABLE row still disagrees. That gate is the point of the step; the
# seeding is only what makes passing it possible. Phase 5 (devices exchanging
# movements and each recomputing its own balances) is unsafe without it -- on
# an install whose ledger cannot reproduce its balances, sync replaces one
# correct-looking number with a different correct-looking number and nobody
# can say which was right. Four consequences that look like details and are
# not:
#   - THE RESIDUAL, not the absence of history. The seeding originally
#     covered only keys with ZERO movements, and that skipped the COMMON
#     legacy key: the importer wrote an absolute SET with no movement row
#     (git 13c1c92, api/import_api.py:1146) and every sale since wrote one,
#     so the ordinary shop has sales and no opening. It was refused, the POS
#     could not boot (app.py calls init_retail() unconditionally), and the
#     refusal's own recovery advice then turned shelves of [112, 57, 7] into
#     [-8, -3, -1]. An opening count IS the residual by definition; the
#     zero-movement case is its special case.
#   - "repairable" excludes the two STRUCTURAL impossibilities: a movement
#     whose branch_id is NULL (inventory_balances.branch_id is NOT NULL, so
#     no balance row could hold it) and a balance whose product row is gone
#     (inventory_movements.product_id has an enforced FK, so no movement row
#     can be written for it). A gate demanding GLOBAL zero drift wedges such
#     an install out of EVERY future migration -- the permanent wedge the v13
#     duplicate-uid defect produced, reached from two more directions. So
#     NULL-branch rows are resolved where the answer is unambiguous (a
#     company with exactly one branch has only one place they can belong),
#     and what stays ambiguous -- along with every orphan balance -- is
#     counted, recorded and excluded. Such an install STILL ADVANCES, with
#     visible queues instead of a guess or a brick.
#   - the gate still REFUSES a negative residual: the ledger accounting for
#     more goods than the shelf shows is not something an opening count can
#     explain, and seeding it anyway would make the gate incapable of
#     failing.
#   - the seeding is COMMITTED before the gate can raise, and
#     stock_reconciliation.repair_drift refuses to LOWER any balance until
#     this gate has passed. See _migrate_seed_opening_counts_and_gate_drift
#     for why either one alone leaves the documented recovery able to wipe a
#     shop's stock.
# v15 -> v16 (launch-readiness Phase 4, ROADMAP.md's 2026-08-21 reservation):
# the cash drawer stops belonging to a BRANCH and starts belonging to a
# TERMINAL. Today `idx_cash_sessions_one_open_per_branch` enforces at most one
# open till session per (company_id, branch_id), which is the wrong shape for
# a shop running a desktop and a phone at the same counter: the second device
# cannot open a drawer at all, and every sale it rings is stamped with
# `session_id` = whatever session IS open on that branch (api/retail_api.py::
# _open_cash_session_id looks up by company+branch+status). The end-of-day Z
# report then reconciles one physical drawer against money that was never in
# it. That is a defect TODAY, on a single-database install, which is why this
# phase sits BEFORE the sync widening rather than inside it -- nothing here
# needs two devices to be sharing a database to be wrong.
#
# THIS IS THE FIRST NON-ADDITIVE STEP IN THE WHOLE CHAIN. Every migration
# before it either created a table, added a column, or wrote rows; this one
# DROPS a unique index and creates a different one. `CREATE UNIQUE INDEX`
# evaluates against the data that is already there, so on an install that
# already holds two open sessions the create RAISES -- and `app.py`'s
# `init_app()` calls `init_retail()` unconditionally with no handler, which is
# precisely how the v15 defect turned into a POS that would not boot. So the
# conflict is DETECTED AND RESOLVED BEFORE the index is created, never
# discovered by the index refusing to build. Four pieces:
#   - `terminal_id` (v13's column, NULL on everything older than it) is
#     backfilled ON OPEN SESSIONS ONLY, from `local_terminal_id()` -- which is
#     `peek_local_device_uuid()`, the same install-stable identity
#     `device_registry.devices` is keyed by and the same one `_stamp()`
#     already writes at open time. No second notion of "which terminal is
#     this" is invented here; inventing one would guarantee that
#     `cash_sessions.terminal_id` and `devices.id` disagree the first time
#     anybody joined them. Closed history keeps its NULL for v13's reason
#     exactly: this device cannot prove it is the till that held a drawer
#     from before the column existed. An OPEN drawer is different -- it is
#     open HERE, NOW, on this install, and its money is on this counter.
#   - a session that CROSSES A BUSINESS DATE is force-ended. A shift nobody
#     ended is a fact about the shop, not an error to hide, so it is recorded
#     as an UNVERIFIED end (`ended_reason` = 'unverified_stale_business_date',
#     `ended_by` = 'System', `closing_float_counted` and `variance` left NULL
#     because nobody counted it and no variance was ever computed) rather than
#     laundered into a clean close. Not one figure on the row is rewritten.
#   - `ended_at`/`ended_by` land beside `closed_at`/`closed_by` for the
#     ENDED / CLOSED split: a drawer that has been COUNTED is not the same
#     thing as one whose variance has been ACCEPTED, and `retail.cash.approve`
#     (commercial_runtime/identity/user_accounts.py::CAP_CASH_APPROVE) is the
#     authority for the second question, deliberately withheld from every role
#     that holds `retail.cash.close`. Existing `status='closed'` rows are NOT
#     rewritten to 'ended': under the pre-v16 model closing WAS the counting
#     act, so their `closed_at`/`closed_by` is restated into the new columns
#     (the same instant and the same person the row already carried, moved
#     into the column the new code path reads) and `status` is left exactly as
#     it is. Rewriting a shop's financial history to say "never approved" --
#     about an approval step that did not exist when those drawers were
#     counted -- would be a non-additive DML pass over years of closed shifts
#     to record the absence of an authority nobody was ever asked for.
#   - only THEN is `idx_cash_sessions_one_open_per_terminal`
#     (UNIQUE(company_id, terminal_id) WHERE status='open') created, and only
#     after it exists is `idx_cash_sessions_one_open_per_branch` dropped. That
#     order is deliberate: at no instant is the table left with neither
#     constraint, so an interruption between the two leaves the OLD guarantee
#     standing rather than none at all.
# See _migrate_bind_cash_drawer_to_terminal below for the step-by-step
# reasoning, including why an open session whose terminal cannot be named
# stays open rather than being ended.
#
# v16 -> v17 (launch-readiness Phase 6, "catalogue correctness", stage 6a-i;
# docs/launch-readiness/phase6-catalogue-correctness.md; ROADMAP.md's
# 2026-08-21 reservation): retail v17 lands the FOUNDATION for reject-stale
# catalogue sync, not the gate itself. Two additive/destructive pieces, see
# _migrate_add_sync_conflicts_and_drop_quantity_reserved below:
#   - `sync_conflicts` (CREATE TABLE IF NOT EXISTS, empty at creation): the
#     visible landing spot a rejected stale write will land in once stage
#     6a-ii turns the gate on, replacing what would otherwise be a silent
#     drop (design doc §6). Created now, ahead of any writer, so that stage
#     only has to fill it in rather than design it under time pressure.
#   - `inventory_balances.quantity_reserved` is DROPPED. Confirmed dead by
#     three prior audits and by grep: no code path in this product's history
#     reads or writes it. `inventory_balances` is fully derivable from
#     `inventory_movements` (Phase 3's ledger-is-truth invariant --
#     core/retail/stock_reconciliation.compute_drift/repair_drift), so even
#     total loss of this column is recoverable, which is what makes it the
#     lowest-risk destructive migration available in this chain. SQLite
#     3.35+ supports `ALTER TABLE ... DROP COLUMN` natively (this codebase
#     targets 3.50.4), so no DROP+RENAME table rebuild is needed.
#
# `stock_exceptions` is DELIBERATELY NOT created here even though it was
# reserved alongside `sync_conflicts` in the same ROADMAP.md entry: nothing
# in Phase 6's scope writes it -- it belongs to the oversell exception queue,
# a feature no stage of this phase implements -- and a shipped table with no
# writer actively misleads the next reader into assuming the feature exists.
# It stays reserved for the phase that actually implements that queue.
#
# THE REAL WORK OF v17, deliberately NOT a schema change: `row_version` has
# existed on these five tables since v13 and nothing has ever bumped it, so
# reject-stale on top of it (stage 6a-ii) would silently discard every
# catalogue write from every device on day one -- see the design doc's "The
# finding that reshapes this phase". Making every catalogue write site in
# retail_api.py/import_api.py/reorder_hook.py actually bump `row_version` and
# stamp `updated_at_utc` is stage 6a-i's other half, and it is ordinary
# application code, not a migration -- there is nothing in THIS function
# about it. The apply side (commercial_runtime/sync/sync_service.py) is
# deliberately UNTOUCHED this stage: it keeps its current
# `ON CONFLICT(id) DO UPDATE` last-write-wins posture for all five catalogue
# types until the bump has been proven live. Switching it to reject-stale is
# stage 6a-ii and does not happen here.
#
# v17 -> v18 (launch-readiness Phase 7, "offline UX", stage 7a; docs/
# launch-readiness/phase7-offline-ux.md "FINDING 1"; ROADMAP.md's
# 2026-08-28 "retail schema v18 CLAIMED for Phase 7" entry): ONE new
# single-row table, `sync_freshness` -- see _migrate_add_sync_freshness
# below for the full reasoning. In short: `SyncService`'s
# `self._health[...]["last_success_at"]` (commercial_runtime/sync/
# sync_service.py, `_fresh_half_health`) has always been in-memory only, so
# it resets to None on every app restart -- and every Phase 7 rule that
# measures elapsed time since the last successful sync (30-minute
# stale-stock hiding, the 24-hour warning, the 72-hour hard stop) is
# defined against that clock. A till restarted every morning -- the normal
# way a shop opens -- would never accumulate enough in-memory elapsed time
# for any of those to fire; the hard stop in particular would never fire at
# all. This table gives the clock a durable home.
#
# `stock_exceptions` (stage 7d, the oversell exception queue) also claims
# v18 per the same ROADMAP.md entry, but is DELIBERATELY NOT created by
# this stage -- same reasoning as v17's own `stock_exceptions` note above:
# nothing in stage 7a writes it, and a shipped table with no writer
# misleads the next reader into assuming the feature exists.
#
# CORRECTION: the paragraph above said "whoever implements 7d appends its
# own migration step to this same v18 chain" -- that aged into being wrong
# the moment stage 7c-ii claimed v19 for `sync_freshness.offline_override_
# at` (see the v18 -> v19 paragraph immediately below, and ROADMAP.md's
# 2026-08-28 "retail schema v19 CLAIMED" entry). `stock_exceptions` landed
# two versions later than this paragraph originally said it would, at v20
# (see the v19 -> v20 paragraph below), not v18. Corrected rather than
# silently fixed, matching this file's own convention elsewhere (see the
# v13 duplicate-uid story) of writing down reasoning that turned out to be
# wrong instead of erasing it.
#
# v18 -> v19 (launch-readiness Phase 7, "offline UX", stage 7c-ii; docs/
# launch-readiness/phase7-offline-ux.md "Decision 2"; ROADMAP.md's
# 2026-08-28 "retail schema v19 CLAIMED for Phase 7 stage 7c-ii" entry):
# ONE nullable column on the existing v18 single-row `sync_freshness`
# table -- `offline_override_at TEXT`. See _migrate_add_offline_override
# below for the full reasoning. In short: stage 7c-ii blocks NEW SALES once
# this device has been behind for more than 72 hours, behind a manager
# override gated on CAP_CASH_APPROVE. The override has to survive a
# restart for the identical reason v18's `last_*_success_at` clock does --
# a till restarted every morning would otherwise re-prompt a manager on the
# first sale of every day -- so it lands in the same durable, single-row
# device-state table rather than in-memory or in a new table of its own.
#
# v19 -> v20 (launch-readiness Phase 7, "offline UX", stage 7d-i; docs/
# launch-readiness/phase7-offline-ux.md "Decision 4" and its 7d scope note;
# ROADMAP.md's 2026-08-28 "retail schema v20 CLAIMED for Phase 7 stage
# 7d-i" entry): ONE new table, `stock_exceptions` -- the oversell exception
# queue reserved back at v17 alongside `sync_conflicts` and deliberately
# left uncreated through v17, v18 AND v19, because nothing in any of those
# three stages would have written it. See _migrate_add_stock_exceptions
# below for the full reasoning; in short:
#
# A negative balance is already reachable in shipped code with NOTHING
# relaxed to get there: `create_sale` refuses `qty > on_hand` against only
# THIS device's own local balance, so two tills that each hold the last
# unit each pass their own check and each sell it. `_apply_event`'s
# `inventory_movement` branch then merges both movements onto whichever
# device applies the other's event, adding the incoming signed quantity to
# the cached balance with no floor at zero -- both devices land at -1.
# `compute_drift` (Phase 3) already surfaces the resulting discrepancy;
# nothing before this stage records it as a business exception a human can
# act on. Stage 7d-i is DETECTION AND RECORDING ONLY -- the table, a writer
# at the apply site, and a read path. It changes no refusal, relaxes no
# guard, and resolves nothing automatically; see the migration function's
# own docstring for the partial-unique-index shape that keeps one open row
# per (company, product, branch) rather than one row per sync tick.
#
# v20 -> v21 (launch-readiness, "the POS scale fix"; ROADMAP.md's 2026-08-29
# "retail schema v21 CLAIMED for the POS scale fix" entry): five indexes,
# no table or column changes -- see _migrate_add_lookup_indexes below for
# the full reasoning, including why payments(party_type, party_id) needs a
# guard the other four don't. In short: `list_products` (retail_api.py)
# returns every active product for the company with no LIMIT and no search
# parameter, and the frontend/Android both filter that whole array
# CLIENT-SIDE for POS scan, product search and PO scan -- ~2.5 MB at 5,000
# SKUs, ~25 MB at 50,000, re-fetched after every completed sale. This
# version adds the indexes the new server-side lookup endpoint and the
# existing scan/report queries need; it does not itself change any query.
# The companion finding -- every date filter wrapped in `date(...)`, making
# the v13 `idx_*_created_at_utc` indexes unusable -- needs no version at
# all, since rewriting a predicate to a sargable range is a pure query
# change (see recent_sales and core/retail/metrics.py).
#
# v21 -> v22 (launch-readiness, "the case-fold lookup"; ROADMAP.md's
# 2026-08-30 "retail schema v22 CLAIMED for the case-fold lookup" entry):
# two indexes, no table or column changes -- see _migrate_add_nocase_lookup_
# indexes below for the full reasoning. In short: the v21 lookup endpoint
# was made "case-insensitive" by building a fixed set of variants of the
# TYPED code (as-typed, `.upper()`, `.lower()`) and matching with `IN
# (...)`. That cannot match a value STORED mixed-case -- three variants of
# the input never touch a row stored as `AbC-123` when the input is
# `abc-123` -- and Android's `equals(ignoreCase = true)` never had that
# gap, so this was a regression against the behaviour the endpoint was
# explicitly changed to preserve. Measured in ROADMAP.md's 2026-08-29
# "the case-insensitive lookup is only PARTLY case-insensitive" entry.
#
# The correct fix is a NOCASE-collated INDEX, and it IS fully sargable --
# the collation lives in the index, not in a function wrapping the column,
# so `WHERE company_id=? AND sku=? COLLATE NOCASE` against
# `products(company_id, sku COLLATE NOCASE)` plans as a `SEARCH ... USING
# COVERING INDEX`, not a scan. The premise that case-insensitivity costs
# the index -- the reasoning the v21 variant trick relied on -- was wrong.
#
# The v21 plain indexes (`idx_products_company_barcode`, `idx_products_
# company_sku`) are KEPT, not replaced. A NOCASE-collated index cannot
# serve a BINARY equality: SQLite will not use an index whose collation
# differs from the comparison's, so an exact-match caller against the
# plain index and a case-folding caller against the NOCASE index need BOTH
# indexes to exist side by side. This is exactly why the lookup route below
# uses a 4-rung ladder (barcode exact -> barcode NOCASE -> sku exact -> sku
# NOCASE) rather than a single NOCASE-only query: each rung is a single
# indexed point lookup against whichever of the two indexes matches its
# collation.
#
# v22 -> v23 (launch-readiness, "promotions, wave 1"; ROADMAP.md's
# 2026-08-30 "retail schema v23 CLAIMED for promotions, wave 1" entry): two
# new tables, `promotions` and `sale_item_promotions`, and two indexes --
# no change to any existing table, and no change to `pricing.py`. See
# _migrate_add_promotions below for the full reasoning.
#
# A promotion resolves to an effective `discount_pct` on the line and is
# then fed through the EXISTING `pricing.calculate_line` -- that is the
# whole integration. `pricing.py` is documented as the only module allowed
# to compute a persisted financial total, and both tax modes already
# interact with a line discount correctly, so expressing promotions in the
# units that module already speaks makes the tax interaction correct by
# construction rather than by a second implementation kept in agreement by
# hand.
#
# THE RULE MOST LIKELY TO BE GOT WRONG: best price wins, discounts do not
# stack. A line carrying an automatic promotion of P% and a manual discount
# of M% takes `max(P, M)`, never `P + M` -- summing is how a 60% promotion
# plus a 50% manual discount becomes 110%, clamps to 100, and hands the
# item over for nothing. See core/retail/promotions.py's
# `resolve_line_discount_pct`.
#
# THE OTHER RULE MOST LIKELY TO BE GOT WRONG: the existing `CAP_DISCOUNT`
# gate in `create_sale` is judged on the MANUAL component ONLY, before
# promotions are resolved. If that check started seeing the promotion's
# percentage instead, a cashier without `CAP_DISCOUNT` could no longer sell
# a promoted item at all -- the shop's own weekend offer would lock out its
# own till. Configuring a promotion, by contrast, DOES require
# `CAP_DISCOUNT`: deciding to give value away at scale is exactly that
# authority.
#
# `sale_item_promotions` snapshots the promotion's name/pct/amount AS
# APPLIED, because a receipt reprinted next year, and a return processed
# against it, must show what the customer was actually charged -- not what
# that promotion's row says today, and not what it says after someone
# edits it. Same discipline the e-invoicing sequence and the cash-session
# closing figures already follow. `create_return` needs no change as a
# consequence of this design: it already recomputes the refund from the
# original `sale_items` row, and a promoted line's `discount_pct` IS that
# row's `discount_pct`, so a promoted line refunds the price actually paid
# with the returns path never learning promotions exist.
#
# Both new tables carry `company_id` even though `sale_items` itself does
# not -- `sale_items` inherits tenancy through `sale_id`, which predates
# the rule CLAUDE.md now states, and a new table has no reason to repeat
# that; it also lets a promotion-performance report scope itself without a
# three-table join.
#
# NOT in wave 1, deliberately: a fixed-amount promotion ("2 JOD off") --
# only reachable through this design as `amount / gross * 100`, and
# converting amount to percentage and back can land a cent away from the
# amount the shop advertised, which is worse than not having the feature.
# Also deferred: buy-X-get-Y and any other basket-level rule (needs a
# cross-line engine, not a per-line resolver), mix-and-match,
# customer-group pricing, coupon codes, loyalty.
RETAIL_SCHEMA_VERSION = 23

# ── v13: which table gets which group of columns ────────────────────────────
# Kept as module constants rather than inlined into the migration so the
# report/query code that lands on top of these columns can import the same
# lists instead of re-deriving them (and drifting from them).

#: Tables that gain the wire identity `uid` + a partial UNIQUE index.
RETAIL_UID_TABLES = (
    'branches', 'sales', 'sale_items', 'returns', 'return_items',
    'inventory_movements', 'payments',
)

#: Tables that gain actor_user_uid / terminal_id / created_at_utc.
RETAIL_ACTOR_TABLES = (
    'sales', 'returns', 'inventory_movements', 'cash_sessions', 'cash_movements',
)

#: Tables that gain row_version / updated_at_utc / deleted_at_utc.
RETAIL_ROW_VERSION_TABLES = (
    'categories', 'products', 'customers', 'suppliers', 'reorder_requests',
)

#: How many rows the v13 uid backfill materialises in Python at a time. See
#: _migrate_add_identity_and_attribution_columns -- `sale_items` on a shop
#: with years of history is far and away the biggest table here, and this
#: migration runs on a till machine, not a server.
_UID_BACKFILL_CHUNK = 5000


class RetailUidIndexError(Exception):
    """Raised when v13 cannot leave `idx_<table>_uid` in the one shape that
    makes the column mean anything -- a UNIQUE, partial index on `uid`.

    Deliberately NOT caught anywhere. The alternative to raising is advancing
    `user_version` while uid uniqueness is quietly absent, which is the F4
    defect itself: no error, no signal, and the wire identity silently stops
    being an identity. Raising re-runs the whole (idempotent) chain on the
    next launch, repair attempt included, so it is a retry rather than the
    permanent wedge an unguarded `CREATE UNIQUE INDEX` produced -- see
    `_ensure_unique_uid_index` for why reaching this is close to impossible
    once the duplicate repair has run.
    """


class RetailLedgerDriftError(Exception):
    """Raised by v15 when this install's `inventory_balances` cannot be
    reproduced from `inventory_movements` even after the opening counts are
    seeded -- i.e. some (product, branch) whose ledger accounts for MORE
    stock than the balance claims, which no inferred opening count can
    explain because a shop cannot have started with less than nothing.

    Deliberately NOT caught anywhere, and deliberately not softened into a
    warning. `ensure_schema_version` advances `PRAGMA user_version` only when
    `migrate_fn` returns, so raising is the mechanism by which v15 refuses to
    call the cache derivable when it demonstrably is not. A shop that reaches
    this has a real, measurable inconsistency between what its shelves are
    said to hold and what its own recorded history can account for -- the
    exact condition Phase 3 exists to surface, and the one Phase 5 must never
    sync across devices.

    The message names the offending rows and the recovery, because "migration
    failed" is not an actionable report about a shop's stock. Recovery is the
    EXPLICIT, owner-initiated repair (core/retail/stock_reconciliation.py::
    repair_drift), which on these rows RAISES the balance to the total the
    ledger can prove arrived and so destroys no stock. Two things make it
    safe to point an operator at, and the first one alone was not enough:
    v15 commits its seeded opening counts BEFORE this can be raised, AND
    `repair_drift` refuses to lower any balance until this gate has passed.
    See `_migrate_seed_opening_counts_and_gate_drift`.

    THE REFUSAL BLOCKS THE APP FROM STARTING -- `app.py` calls
    `init_retail()` with no handler -- so what reaches it must be worth that.
    After the residual fix it is: the ordinary legacy shop is seeded and
    advances, the two structurally unreconcilable classes are excluded, and
    what is left is a shop whose recorded history and shelves genuinely
    contradict each other in the one direction no inference can resolve.
    """


class RetailCashDrawerBindError(Exception):
    """Raised by v16 when the terminal-bound drawer constraint cannot be put
    in place -- either because two open sessions still share one terminal
    after the resolution pass, or because the index did not come out UNIQUE
    and partial over exactly (company_id, terminal_id).

    THIS IS A BACKSTOP, NOT A PATHWAY, and the difference matters. v16's whole
    design is that the collision is found and resolved BEFORE the index is
    built, because `CREATE UNIQUE INDEX` failing on live data means
    `init_retail()` raises, and `app.py::init_app()` calls it unconditionally
    with no handler -- the exact route by which the v15 defect became a POS
    that would not start. Reaching this exception means the resolution pass
    and the probe that follows it disagree about what a collision is, which is
    a defect in this file rather than a state a shop can be in.

    It is still spelled out rather than left to SQLite because the two
    messages are not comparable. SQLite says "UNIQUE constraint failed:
    cash_sessions.company_id, cash_sessions.terminal_id", which names no shop,
    no shift and no recovery; this one names the terminal, every session id
    involved, and the one action that clears it. An operator who reaches this
    cannot open the app to look, so the message is all they have.

    Nothing v16 WROTE TO A ROW is committed on this path: unlike v15 -- which
    commits its seeded ledger rows before it can refuse, because the recovery
    it recommends has to run against them -- v16 has no recovery that depends
    on its own half-finished work, so letting `ensure_schema_version` discard
    every `UPDATE` this migration issued (blanking a terminal id, force-ending
    a shift, restating `ended_at`/`ended_by`) leaves those rows exactly as
    they were: verified by reading them back after a forced failure --
    `user_version` at 15, every drawer's status/float/terminal_id unchanged,
    zero force-end audit rows, the v10 branch index still the one in force.

    THE THREE `ALTER TABLE ... ADD COLUMN` STATEMENTS IN STEP 1 ARE THE ONE
    EXCEPTION, and this paragraph exists because an earlier version of this
    docstring did not carry it -- in a file whose stated convention is that a
    comment is the durable record, an untrue one is worse than none. `ALTER
    TABLE` is DDL, and Python's `sqlite3` module auto-commits DDL the instant
    it runs, independent of -- and before -- the DML transaction the `UPDATE`s
    above sit inside. Those three columns (`ended_at`, `ended_by`,
    `ended_reason`) run FIRST, ahead of any DML, so by the time this exception
    can even be raised they are already durably on disk regardless of what
    happens next. This is harmless in effect: the columns are nullable and
    additive, every value in them stays NULL until a later pass actually
    force-ends a row, and every future call guards adding them again on
    `PRAGMA table_info` (so a retry is a no-op ALTER, not a duplicate-column
    error). But the honest description of what this path leaves behind is
    "v15's rows, plus three new NULL-filled columns; v16's own writes and its
    index swap not started" -- not, as this docstring previously and
    incorrectly claimed, a database left at exactly v15.
    """


def _get_path(name):
    os.makedirs(SUBSYS_DIR, exist_ok=True)
    return os.path.join(SUBSYS_DIR, f'{name}.db')


def _conn(name):
    c = sqlite3.connect(_get_path(name), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    # Wave 0 (AUDIT-016): every declared FOREIGN KEY in this schema was
    # previously decorative -- SQLite defaults enforcement to OFF, and this
    # was the only connection factory in the file, so it was never turned on
    # anywhere. Set on every connection (SQLite does not persist this setting
    # in the database file itself; it must be set per-connection, every time).
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _is_standalone():
    """Return True when running as a packaged customer build (no seed data)."""
    try:
        import config as _cfg
        return getattr(_cfg, 'IS_STANDALONE', False)
    except ImportError:
        return False


def get_retail_conn():
    return _conn('retail')


def sub_create(conn_fn, table, data):
    conn = conn_fn()
    try:
        keys = ', '.join(data.keys())
        placeholders = ', '.join(['?'] * len(data))
        conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", list(data.values()))
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def _migrate_categories_to_uuid(conn):
    """One-time migration (schema v1 -> v2): categories.id moves from
    INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY (client-generated
    UUIDs), so two offline devices can create categories without ever
    colliding on id. products.category_id is repointed to match, preserving
    every existing row (name/description/company_id/created_at, and every
    product's category link, including NULL).

    Two SQLite behaviours (both confirmed empirically against this exact
    connection factory/pragmas, not assumed) shaped how this is written:

    1. `PRAGMA foreign_keys=ON` is set on every connection by `_conn()`
       above, including the one this runs on. Left on, `DROP TABLE
       categories_old` (once products rows reference it) and `ALTER TABLE
       products DROP COLUMN category_id_old` (SQLite refuses to drop a
       column that is part of a FOREIGN KEY definition) both fail partway
       through a naive rename-based rebuild. So this runs with
       foreign_keys temporarily OFF (it cannot be toggled inside a
       transaction, so it's flipped before BEGIN and restored after COMMIT
       via try/finally).

    2. `ALTER TABLE x RENAME TO y` auto-rewrites any OTHER table's
       FOREIGN KEY clause that points at `x`, to point at `y` instead
       (SQLite's default legacy_alter_table=OFF behaviour). Renaming
       `categories` to `categories_old` would silently rewrite products'
       declared FK to `REFERENCES "categories_old"(id)`; renaming
       `products` away would do the same to inventory_movements/
       sale_items/purchase_order_items/return_items. Both tables are
       instead rebuilt under a `_new` name and swapped in via
       `DROP <original>` + `RENAME <new> TO <original>` -- the original
       name is never renamed away, so no other table's FK text is ever
       touched.

    The whole rebuild runs as one explicit transaction; any failure rolls
    back completely (verified: this Python/SQLite combination honors
    explicit BEGIN across mixed DDL+DML), leaving the database exactly as
    the pre-migration backup captured it for a clean retry on next launch.
    """
    import uuid as _uuid

    # Defensive idempotency: ensure_schema_version's version gate is the
    # normal guard against a second run, but check directly too rather than
    # relying solely on that.
    id_col = next((c for c in conn.execute("PRAGMA table_info(categories)").fetchall() if c["name"] == "id"), None)
    if id_col is not None and id_col["type"].upper() == "TEXT":
        return

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")

        legacy_supplier_links = _legacy_supplier_link(conn)

        cat_rows = conn.execute(
            "SELECT id, company_id, name, description, created_at FROM categories"
        ).fetchall()
        id_map = {row["id"]: str(_uuid.uuid4()) for row in cat_rows}

        conn.execute("""
            CREATE TABLE categories_new (
                id TEXT PRIMARY KEY,
                company_id INTEGER DEFAULT 1,
                name TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for row in cat_rows:
            conn.execute(
                "INSERT INTO categories_new (id, company_id, name, description, created_at) VALUES (?,?,?,?,?)",
                (id_map[row["id"]], row["company_id"], row["name"], row["description"], row["created_at"]),
            )
        conn.execute("DROP TABLE categories")
        conn.execute("ALTER TABLE categories_new RENAME TO categories")

        conn.execute("""
            CREATE TABLE products_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER DEFAULT 1,
                sku TEXT NOT NULL,
                barcode TEXT,
                name TEXT NOT NULL,
                category_id TEXT,
                cost_price REAL DEFAULT 0,
                sell_price REAL DEFAULT 0,
                tax_rate REAL DEFAULT 0,
                unit TEXT DEFAULT 'pcs',
                reorder_level INTEGER DEFAULT 5,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
            )
        """)
        conn.execute("""
            INSERT INTO products_new (id, company_id, sku, barcode, name, category_id,
                                       cost_price, sell_price, tax_rate, unit, reorder_level,
                                       status, created_at)
            SELECT id, company_id, sku, barcode, name, NULL,
                   cost_price, sell_price, tax_rate, unit, reorder_level,
                   status, created_at
            FROM products
        """)
        old_cat_ids = conn.execute(
            "SELECT DISTINCT category_id FROM products WHERE category_id IS NOT NULL"
        ).fetchall()
        for row in old_cat_ids:
            old_cid = row["category_id"]
            new_cid = id_map.get(old_cid)
            if new_cid is not None:
                conn.execute(
                    "UPDATE products_new SET category_id=? WHERE id IN "
                    "(SELECT id FROM products WHERE category_id=?)",
                    (new_cid, old_cid),
                )
        conn.execute("DROP TABLE products")
        conn.execute("ALTER TABLE products_new RENAME TO products")

        _restore_supplier_link(conn, legacy_supplier_links)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")


def _legacy_supplier_link(conn):
    """Snapshot products.supplier_id as {product_id: supplier_id}, or None
    when that column does not exist on the LIVE products table.

    Merge safety (feat/retail-mobile-build-baseline, 2026-08-10): master and
    this sync-foundation lineage each independently invented a
    products.supplier_id column -- master's as
    `INTEGER REFERENCES suppliers(id)` at ITS schema v3 (when suppliers.id was
    still INTEGER), this lineage's as `TEXT REFERENCES suppliers(id)` at v5,
    after _migrate_suppliers_to_uuid made suppliers.id a UUID. See
    ROADMAP.md's supplier_id timing note.

    A database that ran master's v3 therefore reaches this chain holding real,
    user-entered supplier links in a column that EVERY products rebuild below
    recreates from a FIXED column list -- _migrate_categories_to_uuid,
    _migrate_products_category_fk_on_delete_set_null and
    _migrate_products_to_uuid all do `CREATE TABLE products_new (...)` +
    `DROP TABLE products`. Without this helper the column and all of its
    values are silently dropped on the way to v7 and
    _migrate_products_add_supplier_fk then re-adds it empty: verified against
    the real code, every product's supplier link came back NULL, with no
    error and no integrity_check failure. Each rebuild snapshots the column
    here and restores it via _restore_supplier_link below;
    _migrate_suppliers_to_uuid then remaps the surviving values from master's
    integer supplier ids to the new supplier UUIDs, exactly as it already
    does for purchase_orders.supplier_id and payments.party_id.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
    if "supplier_id" not in cols:
        return None
    return {
        row["id"]: row["supplier_id"]
        for row in conn.execute("SELECT id, supplier_id FROM products").fetchall()
    }


def _restore_supplier_link(conn, links, id_map=None):
    """Re-add products.supplier_id after a rebuild and put `links` back.

    `id_map` maps old products.id -> new products.id for the one rebuild that
    also changes the primary key (_migrate_products_to_uuid); pass None when
    the rebuild preserved ids verbatim. The column is declared with exactly
    the DDL _migrate_products_add_supplier_fk uses, so a database that came
    through master's v3 ends with a byte-identical `products` definition to a
    fresh install's. No-ops when `links` is None, i.e. on this lineage's own
    upgrade path where the column does not exist yet at rebuild time.
    """
    if links is None:
        return
    conn.execute("ALTER TABLE products ADD COLUMN supplier_id TEXT REFERENCES suppliers(id)")
    for old_pid, supplier_id in links.items():
        if supplier_id is None:
            continue
        new_pid = old_pid if id_map is None else id_map.get(old_pid)
        if new_pid is None:
            continue
        conn.execute("UPDATE products SET supplier_id=? WHERE id=?", (supplier_id, new_pid))


def _products_category_fk_is_set_null(conn):
    """True when products.category_id's FOREIGN KEY already declares
    ON DELETE SET NULL. Read from SQLite's own `PRAGMA foreign_key_list`
    (the parsed FK definition) rather than by string-matching the stored
    CREATE TABLE text, so formatting/whitespace differences between the base
    schema and a migrated rebuild can never make this misreport."""
    try:
        rows = conn.execute("PRAGMA foreign_key_list(products)").fetchall()
    except sqlite3.DatabaseError:
        return False
    for r in rows:
        if r["table"] == "categories" and r["from"] == "category_id":
            return (r["on_delete"] or "").upper() == "SET NULL"
    return False


def _migrate_products_category_fk_on_delete_set_null(conn):
    """One-time migration (schema v2 -> v3): products.category_id's FOREIGN
    KEY gains ON DELETE SET NULL.

    Why this is a correctness fix and not cosmetics: `_conn()` above sets
    `PRAGMA foreign_keys=ON` on EVERY connection, so a bare
    `REFERENCES categories(id)` is genuinely enforced. Categories are synced
    across devices on one license; products are NOT (this phase). Two devices
    therefore legitimately hold different product -> category assignments.
    Device A (no products in "Electronics") deletes that category and the
    delete is relayed; device B (which does have a product there) applies it
    via `commercial_runtime/sync/sync_service.py::_apply_event`'s
    `DELETE FROM categories WHERE id=?` and gets
    `IntegrityError: FOREIGN KEY constraint failed`. That aborts
    `apply_pull_result` before the cursor is advanced, and `run_once`
    swallows the exception -- so device B silently stops receiving ANY event
    from ANY device, permanently, with no self-healing. ON DELETE SET NULL
    makes the delete unassign B's products instead, which is also exactly
    the desktop UX intent for deleting a category locally.

    Structure mirrors `_migrate_categories_to_uuid` above (read its docstring
    for the full reasoning) and inherits its two safety properties verbatim:

    1. Runs with `PRAGMA foreign_keys` temporarily OFF (it cannot be toggled
       inside a transaction, so it is flipped before BEGIN and restored after
       COMMIT via try/finally) -- otherwise `DROP TABLE products` fails while
       inventory_movements/inventory_balances/purchase_order_items/
       sale_items/return_items rows still reference it.

    2. The original `products` name is never renamed away: the replacement is
       built as `products_new` and swapped in via `DROP products` +
       `RENAME products_new TO products`. Renaming `products` to
       `products_old` would make SQLite silently rewrite all five referencing
       tables' FK clauses to point at `products_old`.

    Idempotent (returns immediately when the FK is already SET NULL, on top
    of ensure_schema_version's user_version gate) and fully transactional:
    any failure rolls the whole rebuild back, leaving the database exactly as
    the pre-migration backup captured it for a clean retry on next launch.
    Every products row and every column value is preserved as-is -- this
    changes only the table's declared FK action, never any data.
    """
    if _products_category_fk_is_set_null(conn):
        return

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        legacy_supplier_links = _legacy_supplier_link(conn)
        conn.execute("""
            CREATE TABLE products_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER DEFAULT 1,
                sku TEXT NOT NULL,
                barcode TEXT,
                name TEXT NOT NULL,
                category_id TEXT,
                cost_price REAL DEFAULT 0,
                sell_price REAL DEFAULT 0,
                tax_rate REAL DEFAULT 0,
                unit TEXT DEFAULT 'pcs',
                reorder_level INTEGER DEFAULT 5,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
            )
        """)
        conn.execute("""
            INSERT INTO products_new (id, company_id, sku, barcode, name, category_id,
                                       cost_price, sell_price, tax_rate, unit, reorder_level,
                                       status, created_at)
            SELECT id, company_id, sku, barcode, name, category_id,
                   cost_price, sell_price, tax_rate, unit, reorder_level,
                   status, created_at
            FROM products
        """)
        conn.execute("DROP TABLE products")
        conn.execute("ALTER TABLE products_new RENAME TO products")
        _restore_supplier_link(conn, legacy_supplier_links)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")


def _migrate_products_to_uuid(conn):
    """One-time migration (schema v3 -> v4): products.id moves from
    INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY (client-generated
    UUIDs), same reason categories got this in v1->v2: two offline devices
    must never collide on an id. Every table with a declared FK to
    products(id) -- inventory_movements, inventory_balances, sale_items,
    purchase_order_items, return_items -- is rebuilt in this SAME
    transaction and its product_id values remapped via an old-id -> new-uuid
    map, mirroring exactly how _migrate_categories_to_uuid remaps
    products.category_id (see that function's docstring for the full
    PRAGMA foreign_keys / rename-hazard reasoning, inherited verbatim here).

    Sales/Inventory/Returns/Payments are NOT synced this phase (see the
    design spec's Scope section) -- only their product_id VALUES are
    remapped here, as a one-time local consequence of products.id changing
    type. This migration runs identically on every device independently;
    it does not require or wait for any other device.
    """
    import uuid as _uuid

    id_col = next((c for c in conn.execute("PRAGMA table_info(products)").fetchall() if c["name"] == "id"), None)
    if id_col is not None and id_col["type"].upper() == "TEXT":
        return

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")

        legacy_supplier_links = _legacy_supplier_link(conn)

        prod_rows = conn.execute(
            "SELECT id, company_id, sku, barcode, name, category_id, cost_price, sell_price, "
            "tax_rate, unit, reorder_level, status, created_at FROM products"
        ).fetchall()
        id_map = {row["id"]: str(_uuid.uuid4()) for row in prod_rows}

        conn.execute("""
            CREATE TABLE products_new (
                id TEXT PRIMARY KEY,
                company_id INTEGER DEFAULT 1,
                sku TEXT NOT NULL,
                barcode TEXT,
                name TEXT NOT NULL,
                category_id TEXT,
                cost_price REAL DEFAULT 0,
                sell_price REAL DEFAULT 0,
                tax_rate REAL DEFAULT 0,
                unit TEXT DEFAULT 'pcs',
                reorder_level INTEGER DEFAULT 5,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
            )
        """)
        for row in prod_rows:
            conn.execute(
                "INSERT INTO products_new (id, company_id, sku, barcode, name, category_id, cost_price, "
                "sell_price, tax_rate, unit, reorder_level, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (id_map[row["id"]], row["company_id"], row["sku"], row["barcode"], row["name"],
                 row["category_id"], row["cost_price"], row["sell_price"], row["tax_rate"],
                 row["unit"], row["reorder_level"], row["status"], row["created_at"]),
            )
        conn.execute("DROP TABLE products")
        conn.execute("ALTER TABLE products_new RENAME TO products")

        referencing = [
            ("inventory_movements", """
                CREATE TABLE inventory_movements_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, product_id TEXT NOT NULL,
                    branch_id INTEGER, movement_type TEXT NOT NULL, quantity REAL NOT NULL, unit_cost REAL DEFAULT 0,
                    reference TEXT, notes TEXT, created_by TEXT DEFAULT 'System', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES products(id)
                )""", "id, company_id, product_id, branch_id, movement_type, quantity, unit_cost, reference, notes, created_by, created_at"),
            ("inventory_balances", """
                CREATE TABLE inventory_balances_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, product_id TEXT NOT NULL,
                    branch_id INTEGER NOT NULL, quantity_on_hand REAL DEFAULT 0, quantity_reserved REAL DEFAULT 0,
                    UNIQUE(company_id, product_id, branch_id), FOREIGN KEY (product_id) REFERENCES products(id)
                )""", "id, company_id, product_id, branch_id, quantity_on_hand, quantity_reserved"),
            ("sale_items", """
                CREATE TABLE sale_items_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, sale_id INTEGER NOT NULL, product_id TEXT NOT NULL,
                    quantity REAL NOT NULL, unit_price REAL NOT NULL, discount_pct REAL DEFAULT 0, tax_rate REAL DEFAULT 0,
                    line_total REAL NOT NULL, FOREIGN KEY (sale_id) REFERENCES sales(id), FOREIGN KEY (product_id) REFERENCES products(id)
                )""", "id, sale_id, product_id, quantity, unit_price, discount_pct, tax_rate, line_total"),
            ("purchase_order_items", """
                CREATE TABLE purchase_order_items_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, po_id INTEGER NOT NULL, product_id TEXT NOT NULL,
                    quantity REAL NOT NULL, unit_cost REAL NOT NULL, total REAL NOT NULL, received_qty REAL DEFAULT 0,
                    FOREIGN KEY (po_id) REFERENCES purchase_orders(id), FOREIGN KEY (product_id) REFERENCES products(id)
                )""", "id, po_id, product_id, quantity, unit_cost, total, received_qty"),
            ("return_items", """
                CREATE TABLE return_items_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, return_id INTEGER NOT NULL, product_id TEXT NOT NULL,
                    quantity REAL NOT NULL, unit_price REAL NOT NULL, line_total REAL NOT NULL,
                    FOREIGN KEY (return_id) REFERENCES returns(id), FOREIGN KEY (product_id) REFERENCES products(id)
                )""", "id, return_id, product_id, quantity, unit_price, line_total"),
        ]
        for table, create_sql, cols in referencing:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists is None:
                # `init_retail()` always creates every one of these tables
                # (via CREATE TABLE IF NOT EXISTS) before this migration ever
                # runs, so a real device is never missing one -- this guard
                # only protects a hand-built/partial database (e.g. a test
                # fixture) from an unconditional rebuild attempt against a
                # table that was never created.
                continue
            conn.execute(create_sql)
            col_list = [c.strip() for c in cols.split(",")]
            select_cols = ", ".join(c if c != "product_id" else "product_id" for c in col_list)
            conn.execute(f"INSERT INTO {table}_new ({cols}) SELECT {select_cols} FROM {table}")
            old_pids = conn.execute(f"SELECT DISTINCT product_id FROM {table} WHERE product_id IS NOT NULL").fetchall()
            for row in old_pids:
                old_pid = row["product_id"]
                new_pid = id_map.get(old_pid)
                if new_pid is not None:
                    conn.execute(
                        f"UPDATE {table}_new SET product_id=? WHERE id IN (SELECT id FROM {table} WHERE product_id=?)",
                        (new_pid, old_pid),
                    )
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")

        _restore_supplier_link(conn, legacy_supplier_links, id_map)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")


def _migrate_customers_to_uuid(conn):
    """One-time migration (still schema v4): customers.id moves from
    INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY, adds a new
    `status` column (customers never had one), and folds the
    credit_mode/credit_limit/credit_balance columns _ensure_credit_schema
    (api/retail_api.py) adds at runtime via ALTER TABLE into the rebuilt
    table as first-class columns -- _ensure_credit_schema's own addcol()
    calls stay in place as a no-op safety net (PRAGMA table_info already
    finding the column is exactly what makes addcol() skip it).

    No table has a declared FK to customers(id) -- sales.customer_id and
    payments.party_id are both loose, undeclared columns (confirmed: grep
    "REFERENCES customers" across schema.py returns nothing) -- so there is
    no SQLite auto-rewrite-on-rename hazard here. This still rebuilds under
    `customers_new` and swaps in, for consistency with every other
    migration in this file, and because relying on "no FK today" staying
    true forever is not a safe long-term assumption to bake into a
    rename-based migration.
    """
    import uuid as _uuid

    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='customers'"
    ).fetchone()
    if exists is None:
        # `init_retail()` always creates `customers` (via CREATE TABLE IF NOT
        # EXISTS) before this migration ever runs, so a real device is never
        # missing it -- this guard only protects a hand-built/partial
        # database (e.g. a test fixture that only sets up categories/
        # products) from an unconditional rebuild attempt against a table
        # that was never created. Mirrors the identical guard in
        # _migrate_products_to_uuid's referencing-table loop above.
        return

    id_col = next((c for c in conn.execute("PRAGMA table_info(customers)").fetchall() if c["name"] == "id"), None)
    if id_col is not None and id_col["type"].upper() == "TEXT":
        return

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")

        existing_cols = {c["name"] for c in conn.execute("PRAGMA table_info(customers)").fetchall()}
        has_credit = "credit_mode" in existing_cols  # _ensure_credit_schema may have already run on this db

        cust_rows = conn.execute("SELECT * FROM customers").fetchall()
        id_map = {row["id"]: str(_uuid.uuid4()) for row in cust_rows}

        conn.execute("""
            CREATE TABLE customers_new (
                id TEXT PRIMARY KEY,
                company_id INTEGER DEFAULT 1,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                address TEXT,
                loyalty_points REAL DEFAULT 0,
                total_spent REAL DEFAULT 0,
                status TEXT DEFAULT 'active',
                credit_mode TEXT DEFAULT 'none',
                credit_limit REAL DEFAULT 0,
                credit_balance REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for row in cust_rows:
            conn.execute(
                "INSERT INTO customers_new (id, company_id, name, phone, email, address, loyalty_points, "
                "total_spent, status, credit_mode, credit_limit, credit_balance, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (id_map[row["id"]], row["company_id"], row["name"], row["phone"], row["email"], row["address"],
                 row["loyalty_points"], row["total_spent"], "active",
                 row["credit_mode"] if has_credit else "none",
                 row["credit_limit"] if has_credit else 0,
                 row["credit_balance"] if has_credit else 0,
                 row["created_at"]),
            )
        conn.execute("DROP TABLE customers")
        conn.execute("ALTER TABLE customers_new RENAME TO customers")

        for row in cust_rows:
            old_id, new_id = row["id"], id_map[row["id"]]
            conn.execute("UPDATE sales SET customer_id=? WHERE customer_id=?", (new_id, old_id))
            conn.execute(
                "UPDATE payments SET party_id=? WHERE party_type='customer' AND party_id=?",
                (new_id, old_id),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")


def _migrate_suppliers_to_uuid(conn):
    """One-time migration (still schema v4): suppliers.id moves from
    INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY, folding
    payment_terms/credit_balance (added at runtime by _ensure_credit_schema)
    into the rebuilt table as first-class columns, same reasoning as
    _migrate_customers_to_uuid.

    Unlike customers, purchase_orders.supplier_id IS a declared FK
    (`FOREIGN KEY (supplier_id) REFERENCES suppliers(id)`, schema.py:428) --
    same rename-away hazard as products/categories: suppliers is rebuilt
    under `suppliers_new` and swapped in, never renamed away directly.
    """
    import uuid as _uuid

    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='suppliers'"
    ).fetchone()
    if exists is None:
        # `init_retail()` always creates `suppliers` (via CREATE TABLE IF NOT
        # EXISTS) before this migration ever runs, so a real device is never
        # missing it -- this guard only protects a hand-built/partial
        # database (e.g. a test fixture that only sets up categories/
        # products) from an unconditional rebuild attempt against a table
        # that was never created. Mirrors the identical guard in
        # _migrate_customers_to_uuid above.
        return

    id_col = next((c for c in conn.execute("PRAGMA table_info(suppliers)").fetchall() if c["name"] == "id"), None)
    if id_col is not None and id_col["type"].upper() == "TEXT":
        return

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")

        existing_cols = {c["name"] for c in conn.execute("PRAGMA table_info(suppliers)").fetchall()}
        has_credit = "payment_terms" in existing_cols

        sup_rows = conn.execute("SELECT * FROM suppliers").fetchall()
        id_map = {row["id"]: str(_uuid.uuid4()) for row in sup_rows}

        conn.execute("""
            CREATE TABLE suppliers_new (
                id TEXT PRIMARY KEY,
                company_id INTEGER DEFAULT 1,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                address TEXT,
                status TEXT DEFAULT 'active',
                payment_terms TEXT DEFAULT 'none',
                credit_balance REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for row in sup_rows:
            conn.execute(
                "INSERT INTO suppliers_new (id, company_id, name, phone, email, address, status, "
                "payment_terms, credit_balance, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (id_map[row["id"]], row["company_id"], row["name"], row["phone"], row["email"], row["address"],
                 row["status"],
                 row["payment_terms"] if has_credit else "none",
                 row["credit_balance"] if has_credit else 0,
                 row["created_at"]),
            )
        conn.execute("DROP TABLE suppliers")
        conn.execute("ALTER TABLE suppliers_new RENAME TO suppliers")

        # Merge safety (2026-08-10): a database that ran master's schema v3
        # carries master's INTEGER products.supplier_id values through this
        # chain (see _legacy_supplier_link above). They point at exactly the
        # old integer supplier ids being replaced right here, so they are
        # remapped like purchase_orders.supplier_id below. Guarded because on
        # this lineage's own upgrade path the column does not exist yet --
        # _migrate_products_add_supplier_fk adds it one step later.
        products_has_supplier_id = any(
            row["name"] == "supplier_id"
            for row in conn.execute("PRAGMA table_info(products)").fetchall()
        )

        for row in sup_rows:
            old_id, new_id = row["id"], id_map[row["id"]]
            conn.execute("UPDATE purchase_orders SET supplier_id=? WHERE supplier_id=?", (new_id, old_id))
            conn.execute(
                "UPDATE payments SET party_id=? WHERE party_type='supplier' AND party_id=?",
                (new_id, old_id),
            )
            if products_has_supplier_id:
                conn.execute("UPDATE products SET supplier_id=? WHERE supplier_id=?", (new_id, old_id))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")


def _migrate_retail_schema(conn):
    """The single `migrate_fn` handed to `ensure_schema_version` -- runs every
    migration this file owns, in version order, on any database behind
    RETAIL_SCHEMA_VERSION. `ensure_schema_version` only tells a migration
    "you are behind", not "you are behind by exactly one version", so a v1
    install upgrading straight to v7 must run ALL steps in one pass. Each
    step is independently idempotent (each inspects the live schema and
    returns immediately when its own change is already present), so running
    them all is correct regardless of which version the database actually
    starts from -- including a database that arrives stamped with master's
    now-superseded v2/v3 numbering (docs/einvoicing/phase1/,
    products.supplier_id as INTEGER; see _legacy_supplier_link/
    _restore_supplier_link above and the RETAIL_SCHEMA_VERSION comment for
    the full merge-reconciliation story, feat/retail-mobile-build-baseline,
    2026-08-10)."""
    _migrate_categories_to_uuid(conn)
    _migrate_products_category_fk_on_delete_set_null(conn)
    _migrate_products_to_uuid(conn)
    _migrate_customers_to_uuid(conn)
    _migrate_suppliers_to_uuid(conn)
    _migrate_products_add_supplier_fk(conn)
    _migrate_add_supplier_contacts_and_po_split(conn)
    # v6 -> v7 (docs/einvoicing/phase1/): adds the einvoice_* tables used by
    # opt-in Jordan JoFotara e-invoicing. CREATE TABLE IF NOT EXISTS only --
    # no existing table is ALTERed, no existing row is read or written, so an
    # install that never enables the feature gains only empty tables.
    # Appended LAST, after every migration above, so this addition cannot
    # change their order or behaviour -- exactly how
    # products/clinic/backend/database/schema.py::_apply_clinic_alters wires
    # the identical call.
    from commercial_runtime.einvoicing.schema import apply_einvoicing_schema
    apply_einvoicing_schema(conn)
    # v7 -> v8 (reorder automation foundation): appended LAST, after every
    # migration above (including einvoicing), for the identical reason
    # einvoicing itself is appended last -- see this function's own
    # docstring and the RETAIL_SCHEMA_VERSION v8 comment above.
    _migrate_add_reorder_automation_foundation(conn)
    # v8 -> v9 (feat/email-outbox-foundation): appended LAST, same reasoning
    # again -- see the RETAIL_SCHEMA_VERSION v9 comment above.
    _migrate_add_notifications_foundation(conn)
    # v9 -> v10 (feat/shift-cash-drawer): appended LAST, same reasoning again
    # -- see the RETAIL_SCHEMA_VERSION v10 comment above.
    _migrate_add_shift_cash_drawer(conn)
    # v10 -> v11 (feat/pos-hold-resume-sale): appended LAST, same reasoning
    # again -- see the RETAIL_SCHEMA_VERSION v11 comment above for the full
    # renumbering/collision story. Runs after shift-cash-drawer since both
    # branches were reconciled together at this merge.
    _migrate_add_held_sales(conn)
    # v11 -> v12 (whatsapp-recipients): appended LAST, same reasoning again --
    # see the RETAIL_SCHEMA_VERSION v12 comment above.
    _migrate_add_whatsapp_recipients(conn)
    # v12 -> v13 (launch-readiness Phase 2): appended LAST, same reasoning
    # again -- see the RETAIL_SCHEMA_VERSION v13 comment above. The first
    # step in this chain that ALTERs the sales-history tables; additive only.
    _migrate_add_identity_and_attribution_columns(conn)
    # v13 -> v14 (launch-readiness Phase 2): appended LAST, and it must stay
    # after v13 specifically -- not merely by the "new steps go last"
    # convention. The rebind rewrites `company_id` on every scoped table,
    # and running it BEFORE v13 would mean the rows it touches do not yet
    # carry the `uid` that identifies them on the wire, so an interrupted
    # rebind could not be reconciled against anything afterwards.
    _migrate_rebind_company_id_to_owner_issued(conn)
    # v14 -> v15 (launch-readiness Phase 3): appended LAST, and it must stay
    # last for a reason of its own on top of the convention. This step is a
    # GATE -- it recomputes drift after seeding and raises rather than let
    # `ensure_schema_version` advance the marker on an install whose ledger
    # cannot reproduce its balances. Anything appended after it would be
    # silently skipped on exactly those installs. It must also stay after
    # v14: the rebind rewrites `company_id` on every scoped table, and this
    # step groups, gates and reports per company, so running it first would
    # measure and record the tenant key the shop is about to stop using.
    _migrate_seed_opening_counts_and_gate_drift(conn)
    # v15 -> v16 (launch-readiness Phase 4): the terminal-bound cash drawer.
    # Appended LAST, by the same convention as every step above -- but this
    # one is also the FIRST NON-ADDITIVE step in the chain (it drops a unique
    # index and creates a different one), so two placement facts are worth
    # stating explicitly rather than leaving to the convention:
    #
    #   - it must stay after `_migrate_add_shift_cash_drawer` (v10), which
    #     creates `idx_cash_sessions_one_open_per_branch`. v16 drops that
    #     index; v10 declines to recreate it once v16's replacement exists
    #     (`_v10_branch_index_is_superseded`). Run in this order the pair
    #     settles on exactly one constraint. Run the other way round, v16
    #     would drop an index that does not exist yet and v10 would then
    #     create it -- because at that moment the terminal index it checks for
    #     is still absent -- leaving the branch constraint in force with the
    #     terminal one beside it, a shop unable to open its second drawer and
    #     a schema version claiming otherwise.
    #   - it runs after v15's gate, which RAISES on an install whose ledger
    #     cannot reproduce its balances. On such an install this step never
    #     runs, and that is correct rather than a silent skip: `user_version`
    #     does not advance either, so there is no state in which the marker
    #     claims 16 while the drawer is still branch-bound. The next launch
    #     re-runs the whole chain from the top.
    _migrate_bind_cash_drawer_to_terminal(conn)
    # v16 -> v17 (launch-readiness Phase 6, stage 6a-i): appended LAST, same
    # convention as every step above. See the RETAIL_SCHEMA_VERSION v17
    # comment for what this does and, just as importantly, what it does NOT
    # do -- the apply side is untouched this stage, on purpose.
    _migrate_add_sync_conflicts_and_drop_quantity_reserved(conn)
    # v17 -> v18 (launch-readiness Phase 7, stage 7a): appended LAST, same
    # convention as every step above. See the RETAIL_SCHEMA_VERSION v18
    # comment for what this does and _migrate_add_sync_freshness's own
    # docstring for the full reasoning.
    _migrate_add_sync_freshness(conn)
    # v18 -> v19 (launch-readiness Phase 7, stage 7c-ii): appended LAST,
    # same convention as every step above, and it must stay AFTER
    # _migrate_add_sync_freshness specifically -- not merely by the
    # "new steps go last" convention -- because it ALTERs the table that
    # step just created. See the RETAIL_SCHEMA_VERSION v19 comment and
    # _migrate_add_offline_override's own docstring for the full reasoning.
    _migrate_add_offline_override(conn)
    # v19 -> v20 (launch-readiness Phase 7, stage 7d-i): appended LAST, same
    # convention as every step above. See the RETAIL_SCHEMA_VERSION v20
    # comment for what this does and, just as importantly, what it does
    # NOT do -- no refusal changes and no guard relaxes this stage, on
    # purpose. _migrate_add_stock_exceptions's own docstring has the full
    # reasoning, including the partial-unique-index shape.
    _migrate_add_stock_exceptions(conn)
    # v20 -> v21 (launch-readiness, "the POS scale fix"): appended LAST, same
    # convention as every step above. Pure index additions, so ordering
    # relative to the steps above it does not matter functionally -- it is
    # placed last only to keep following the chain's own convention. See the
    # RETAIL_SCHEMA_VERSION v21 comment above and _migrate_add_lookup_
    # indexes's own docstring for the full reasoning, including the
    # payments(party_type, party_id) guard.
    _migrate_add_lookup_indexes(conn)
    # v21 -> v22 (launch-readiness, "the case-fold lookup"): appended LAST,
    # same convention as every step above. Pure index additions, so ordering
    # relative to the steps above it does not matter functionally -- it is
    # placed last only to keep following the chain's own convention. See the
    # RETAIL_SCHEMA_VERSION v22 comment above and _migrate_add_nocase_
    # lookup_indexes's own docstring for the full reasoning.
    _migrate_add_nocase_lookup_indexes(conn)
    # v22 -> v23 (launch-readiness, "promotions, wave 1"): appended LAST,
    # same convention as every step above. Two new, self-contained tables
    # and two indexes -- no existing table is ALTERed, read, or written, so
    # ordering relative to the steps above it does not matter functionally.
    # See the RETAIL_SCHEMA_VERSION v23 comment above and
    # _migrate_add_promotions's own docstring for the full reasoning.
    _migrate_add_promotions(conn)


def _migrate_products_add_supplier_fk(conn):
    """One-time migration (schema v4 -> v5): adds products.supplier_id.

    TEXT, not INTEGER -- suppliers.id is already UUID text by the time this
    runs (_migrate_suppliers_to_uuid, just above, runs first). This is a
    brand-new nullable column, not a change to an existing constraint, so
    unlike _migrate_products_category_fk_on_delete_set_null above, no
    DROP+RENAME rebuild is needed: SQLite allows ADD COLUMN with a
    REFERENCES clause directly, and NULL always satisfies a foreign key
    check, so existing rows are left unassigned rather than backfilled.

    Idempotent: skips the ALTER if the column already exists. Index creation
    is deliberately OUTSIDE that guard: an index lives and dies with its
    table, so every products rebuild earlier in this chain drops
    idx_products_supplier along with the old table. Returning early on
    "column already exists" (as this did before the merge-safety fix in
    _legacy_supplier_link/_restore_supplier_link above) would leave a
    database that arrived carrying master's v3 supplier_id with the column
    present but no index. IF NOT EXISTS makes the normal path a no-op.
    """
    cols = {row[1] for row in conn.execute('PRAGMA table_info(products)').fetchall()}
    if 'supplier_id' not in cols:
        conn.execute('ALTER TABLE products ADD COLUMN supplier_id TEXT REFERENCES suppliers(id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_products_supplier ON products(supplier_id)')


def _migrate_add_supplier_contacts_and_po_split(conn):
    """One-time migration (schema v5 -> v6): PO-preview-by-supplier
    foundation (Thursday demo, Stream B -- descoped to preview-only PO
    splitting by supplier; the routes that write these columns land later
    this week, not today).

    Three independent additive pieces, each guarded by its own idempotency
    check (mirrors _migrate_products_add_supplier_fk just above -- brand-new
    nullable columns / IF NOT EXISTS tables, so unlike the UUID migrations
    earlier in this chain, no DROP+RENAME rebuild is needed anywhere here):

    1. `supplier_contacts` -- a supplier can have several named contacts
       (orders/accounts/general), each with its own preferred channel. New
       table entirely, so CREATE TABLE IF NOT EXISTS is itself idempotent.

    2. `purchase_orders` gains split-group/routing/idempotency columns:
       split_group_id/split_index/split_count identify which slice of a
       supplier-split preview a PO belongs to; routing_status/routed_channel/
       routed_at/routed_to record how (and whether) it was sent once routing
       ships; idempotency_key gets the same partial-unique-index treatment as
       `returns.idempotency_key` (see api/retail_api.py's
       `_ensure_credit_schema`, `idx_returns_idempotency`) -- SQLite can't add
       a UNIQUE column via ALTER TABLE, so a partial unique index does the
       job instead, and NULLs (every pre-v6 row) are exempt, matching
       SQLite's own UNIQUE-column NULL semantics.

    3. `suppliers.min_order_value` -- procurement metadata the split
       preview's MOQ warning reads; not touched by anything else yet.

    Idempotent: each ALTER COLUMN is preceded by a PRAGMA table_info check,
    each CREATE TABLE/INDEX already uses IF NOT EXISTS, so a second call
    (or a fresh install that already has this shape via init_retail's base
    executescript) is a clean no-op.
    """
    existing_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    if 'supplier_contacts' not in existing_tables:
        conn.execute("""
            CREATE TABLE supplier_contacts (
                id TEXT PRIMARY KEY,
                company_id TEXT,
                supplier_id TEXT NOT NULL REFERENCES suppliers(id),
                name TEXT NOT NULL,
                role TEXT DEFAULT 'orders',
                email TEXT,
                phone TEXT,
                whatsapp TEXT,
                channel_preference TEXT DEFAULT 'whatsapp',
                is_primary INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_supplier_contacts_supplier "
        "ON supplier_contacts(company_id, supplier_id, status)"
    )

    if 'purchase_orders' in existing_tables:
        po_cols = {row[1] for row in conn.execute('PRAGMA table_info(purchase_orders)').fetchall()}
        for col, decl in [
            ('split_group_id', 'TEXT'),
            ('split_index', 'INTEGER'),
            ('split_count', 'INTEGER'),
            ('routing_status', 'TEXT'),
            ('routed_channel', 'TEXT'),
            ('routed_at', 'TEXT'),
            ('routed_to', 'TEXT'),
            ('idempotency_key', 'TEXT'),
        ]:
            if col not in po_cols:
                conn.execute(f'ALTER TABLE purchase_orders ADD COLUMN {col} {decl}')
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_po_split_group "
            "ON purchase_orders(company_id, split_group_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_po_idempotency "
            "ON purchase_orders(idempotency_key) WHERE idempotency_key IS NOT NULL"
        )

    if 'suppliers' in existing_tables:
        sup_cols = {row[1] for row in conn.execute('PRAGMA table_info(suppliers)').fetchall()}
        if 'min_order_value' not in sup_cols:
            conn.execute('ALTER TABLE suppliers ADD COLUMN min_order_value REAL DEFAULT 0')


def _migrate_add_reorder_automation_foundation(conn):
    """One-time migration (schema v7 -> v8): reorder automation foundation
    (feat/reorder-automation-foundation) -- the SAFE subset of a proposed
    "low-stock auto-reorder via WhatsApp" feature: schema + the post-sale
    trigger + an Admin Center accept/decline page. The WhatsApp send itself
    is explicitly deferred (out of scope for this migration and everything
    it enables).

    Two independent additive pieces, each guarded by its own idempotency
    check (mirrors _migrate_add_supplier_contacts_and_po_split immediately
    above -- brand-new nullable column / IF NOT EXISTS table, so no
    DROP+RENAME rebuild is needed here either):

    1. `products.reorder_method` -- per-product opt-in ('none' by default,
       so an install that never touches this column gets zero behavior
       change from the post-sale hook that reads it). Deliberately reuses
       the EXISTING `products.supplier_id` column (schema v5) for "which
       supplier to draft the reorder against" rather than adding a new
       `default_supplier_id` -- that would have been a redundant column
       duplicating data v5 already carries.

    2. `reorder_requests` -- one row per auto-detected low-stock event
       needing human accept/decline. `id` is a client-generated UUID, NOT
       autoincrement -- deliberately, so this table CAN be relayed through
       Owner's sync (commercial_runtime/sync/sync_service.py's
       `_apply_event` gains a matching `reorder_request` branch). This is
       the opposite choice from `purchase_orders` (still
       INTEGER PRIMARY KEY AUTOINCREMENT): Owner's relay
       (owner/app/sync/routes.py) requires `entity_id` to parse as a UUID
       and 400-rejects the WHOLE PUSH BATCH on the first entity_id that
       doesn't -- syncing purchase_orders directly would permanently jam
       every other entity's sync behind one malformed event the instant a
       PO existed. reorder_requests never has that problem because it was
       never given an autoincrement id in the first place. The PO an Accept
       action creates, by contrast, IS a real purchase_orders row and is
       therefore deliberately never queued to sync_outbox at all -- see
       api/retail_api.py's accept route.

       `status` is 'pending' | 'accepted' | 'declined'. The partial unique
       index below (idx_reorder_requests_open) enforces the idempotency
       invariant the post-sale hook relies on -- at most one OPEN
       ('pending' or 'accepted') request per (company_id, product_id) at a
       time -- as a real database constraint, not just an application-level
       check, mirroring purchase_orders.idempotency_key's partial-unique-
       index treatment (_migrate_add_supplier_contacts_and_po_split above).
       A 'declined' request does NOT hold this slot open, so a later sale
       dropping the same product below its reorder_level again is free to
       open a new request.

    Idempotent: the ALTER COLUMN is preceded by a PRAGMA table_info check,
    the CREATE TABLE/INDEX statements already use IF NOT EXISTS, so a
    second call (or a fresh install migrating 0 -> 8 in one pass, same as
    every step above) is a clean no-op.
    """
    cols = {row[1] for row in conn.execute('PRAGMA table_info(products)').fetchall()}
    if 'reorder_method' not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN reorder_method TEXT DEFAULT 'none'")

    existing_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if 'reorder_requests' not in existing_tables:
        conn.execute("""
            CREATE TABLE reorder_requests (
                id TEXT PRIMARY KEY,
                company_id TEXT,
                branch_id INTEGER,
                product_id TEXT NOT NULL REFERENCES products(id),
                status TEXT NOT NULL DEFAULT 'pending',
                draft_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT
            )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_reorder_requests_company "
        "ON reorder_requests(company_id, status)"
    )
    # Idempotency guard for the post-sale hook (retail_api.py::create_sale
    # via core/retail/reorder_hook.py): at most one pending OR accepted
    # request per product at a time. Declined rows are exempt (status not
    # in the predicate), same NULL/exempt-row shape as idx_po_idempotency.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_reorder_requests_open "
        "ON reorder_requests(company_id, product_id) WHERE status IN ('pending','accepted')"
    )


def _migrate_add_notifications_foundation(conn):
    """One-time migration (schema v8 -> v9): email outbox foundation
    (feat/email-outbox-foundation) -- adds the email_* tables owned by
    commercial_runtime/notifications (settings, outbox, verification
    tokens). CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS only,
    identical shape to v7's einvoice_* addition above -- see
    commercial_runtime/notifications/schema.py's own docstring for the full
    table-by-table reasoning; nothing product-specific lives in that
    module, so this function's only job is to call it inside the same
    ensure_schema_version() transaction every other step in this chain
    already runs inside.

    Idempotent by construction (every statement inside
    apply_notifications_schema() is already IF NOT EXISTS), so a second
    call -- or a fresh install migrating 0 -> 9 in one pass, same as every
    step above -- is a clean no-op.
    """
    from commercial_runtime.notifications.schema import apply_notifications_schema
    apply_notifications_schema(conn)


def _v10_branch_index_is_superseded(conn):
    """True when v16's terminal-bound constraint is already in place, and v10's
    branch-bound one must therefore NOT be put back.

    A named predicate rather than the condition inlined at its one call site,
    so that the guard can be watched failing: restoring the old unconditional
    behaviour is a one-line stub over this function, and
    retail_v16_terminal_cash_drawer_test.py does exactly that and requires the
    boot crash to reappear. A guard nobody has watched fail is a guard nobody
    knows the shape of.

    Reads the index's real SHAPE, not just its name -- see
    `_v16_terminal_index_is_correct` for why a name is not evidence in this
    file. An index called `idx_cash_sessions_one_open_per_terminal` that is not
    actually UNIQUE would mean the drawer is unconstrained, and answering
    "superseded" on the strength of it would drop the branch constraint too and
    leave the table with neither.
    """
    return _v16_terminal_index_is_correct(
        _uid_index_shape(conn, 'cash_sessions', V16_TERMINAL_INDEX))


def _migrate_add_shift_cash_drawer(conn):
    """One-time migration (schema v9 -> v10): shift / cash-drawer management
    (feat/shift-cash-drawer) -- cash float in/out tracking plus X (mid-shift,
    non-destructive) and Z (end-of-shift, locking) reports.

    Three independent additive pieces, each guarded by its own idempotency
    check (same shape as _migrate_add_reorder_automation_foundation above --
    brand-new nullable columns / IF NOT EXISTS tables, so no DROP+RENAME
    rebuild is needed here either):

    1. `cash_sessions` -- one row per opened-to-closed till session. `id` is a
       client-generated UUID, NOT autoincrement, matching reorder_requests'
       reasoning exactly (see this file's own RETAIL_SCHEMA_VERSION v8
       comment): a UUID id means this table COULD be relayed through Owner's
       sync later without hitting the purchase_orders-style "entity_id must
       parse as a UUID" wall, even though it isn't wired into sync_outbox
       yet. idx_cash_sessions_one_open_per_branch is a partial UNIQUE index
       enforcing "at most one OPEN session per (company_id, branch_id)" as a
       real database constraint, not just an application-level check --
       identical technique to idx_reorder_requests_open. SCHEMA v16 REPLACES
       THAT INDEX with a terminal-bound one, and because this function re-runs
       on every pass through the chain it must not put a superseded constraint
       back on top of data that now legitimately violates it -- see
       `_v10_branch_index_is_superseded` and the comment at the CREATE below.

    2. `cash_movements` -- one row per float-in/float-out/paid-in/paid-out
       event against a session. Deliberately has NO company_id/branch_id of
       its own: a movement has no meaning outside the session it belongs to,
       so `session_id` is the only scope it needs (same shape as sale_items
       scoping entirely through sale_id rather than repeating company_id).

    3. `sales.session_id` / `returns.session_id` -- nullable TEXT columns
       referencing cash_sessions(id), stamped at write time by
       retail_api.py's `_open_cash_session_id()` helper so the X/Z report
       math can attribute a sale/return to the EXACT session it happened in,
       via a direct FK rather than a branch+time-range lookup -- see the
       RETAIL_SCHEMA_VERSION v10 comment above for why a time-range lookup
       is the wrong choice here (breaks across midnight / back-to-back
       sessions). NULL for every row written before this migration, and for
       every row written by an install that never opens a cash session --
       zero behavior change to create_sale/create_return in that case (see
       retail_cash_drawer_regression_test.py).

    Idempotent: each ALTER COLUMN is preceded by a PRAGMA table_info check,
    each CREATE TABLE/INDEX already uses IF NOT EXISTS, so a second call (or
    a fresh install migrating 0 -> 10 in one pass, same as every step above)
    is a clean no-op.
    """
    existing_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    if 'cash_sessions' not in existing_tables:
        conn.execute("""
            CREATE TABLE cash_sessions (
                id TEXT PRIMARY KEY,
                company_id TEXT,
                branch_id INTEGER NOT NULL,
                opened_by TEXT,
                opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                opening_float REAL NOT NULL DEFAULT 0,
                closed_by TEXT,
                closed_at TIMESTAMP,
                closing_float_counted REAL,
                closing_float_expected REAL,
                variance REAL,
                status TEXT NOT NULL DEFAULT 'open'
            )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cash_sessions_company "
        "ON cash_sessions(company_id, branch_id, status)"
    )
    # ONE-OPEN-PER-BRANCH IS NOT RECREATED ONCE v16 HAS SUPERSEDED IT, and
    # this guard is load-bearing rather than tidy. Every statement in this
    # function runs on EVERY pass through `_migrate_retail_schema` -- the chain
    # is re-entered in full whenever `user_version` is behind, which is what a
    # future v17 means. On a database v16 has already bound to terminals, two
    # open drawers on ONE branch are legal and expected (a desktop and a phone
    # at the same counter, which is the entire point of that phase). An
    # unconditional `CREATE UNIQUE INDEX IF NOT EXISTS` here would then be
    # evaluated against exactly those rows, raise `UNIQUE constraint failed`,
    # and -- since `app.py::init_app()` calls `init_retail()` with no handler
    # -- turn the next release into a POS that will not start, on the shops
    # that had adopted the feature most.
    #
    # Note the direction of the check: it asks whether the REPLACEMENT
    # constraint is in place, not whether this one is. "The terminal index
    # exists" is the only evidence that v16 has run and that this index is
    # deliberately absent rather than missing; an install that has simply
    # never reached v16 still gets it, which is what keeps a v10-to-v15
    # database correctly constrained.
    if not _v10_branch_index_is_superseded(conn):
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cash_sessions_one_open_per_branch "
            "ON cash_sessions(company_id, branch_id) WHERE status='open'"
        )

    if 'cash_movements' not in existing_tables:
        conn.execute("""
            CREATE TABLE cash_movements (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES cash_sessions(id),
                type TEXT NOT NULL,
                amount REAL NOT NULL,
                reason TEXT,
                created_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cash_movements_session ON cash_movements(session_id)"
    )

    # `sales`/`returns` are guarded by existence the same way
    # _migrate_add_supplier_contacts_and_po_split guards `purchase_orders`
    # above -- every real install has both (init_retail's own executescript
    # creates them), but some test fixtures hand-build a MINIMAL schema
    # (e.g. retail_category_delete_fk_sync_test.py's v1/v2 fixtures, which
    # only contain categories/products/inventory_movements) and call
    # `_migrate_retail_schema` directly against it. An unconditional ALTER
    # here would crash that fixture with "no such table: sales" even though
    # this migration has nothing to do with categories/products at all.
    # `existing_tables` (captured before cash_sessions/cash_movements were
    # created above) is still accurate for sales/returns -- neither of those
    # CREATE TABLE calls could have created or dropped sales/returns.
    if 'sales' in existing_tables:
        sales_cols = {row[1] for row in conn.execute('PRAGMA table_info(sales)').fetchall()}
        if 'session_id' not in sales_cols:
            conn.execute('ALTER TABLE sales ADD COLUMN session_id TEXT REFERENCES cash_sessions(id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_sales_session ON sales(session_id)')

    if 'returns' in existing_tables:
        returns_cols = {row[1] for row in conn.execute('PRAGMA table_info(returns)').fetchall()}
        if 'session_id' not in returns_cols:
            conn.execute('ALTER TABLE returns ADD COLUMN session_id TEXT REFERENCES cash_sessions(id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_returns_session ON returns(session_id)')


def _migrate_add_held_sales(conn):
    """One-time migration (schema v9 -> v11, deliberately skipping v10 --
    see RETAIL_SCHEMA_VERSION's comment for the full collision story with
    feat/shift-cash-drawer, which claimed v10 on the same base): hold/resume
    sale ("park a cart") on the POS screen (feat/pos-hold-resume-sale). Adds
    the held_sales table. CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT
    EXISTS only -- no existing table is ALTERed, read, or written, identical
    shape to v7/v8/v9's additive migrations above.

    A held sale is PRE-completion cart state -- a cashier's in-progress
    draft, never a commercial transaction -- so this is deliberately its own
    table, never a row in `sales`/`sale_items`. `subtotal`/`total` here are
    DISPLAY-ONLY (rendered in the resume picker) -- they are never read by
    create_sale/POST /sales, which always recomputes the real, authoritative
    figures from live product data the same way it does for any freshly-
    built cart (AUDIT-002/AUDIT-003's server-authority guarantee is
    unchanged; this table sits entirely outside that financial-authority
    boundary). `cart_json` carries the full line-item snapshot
    (product_id/quantity/unit_price/tax_rate/line_total/max_stock, mirroring
    RetailSystem._cart's own shape) plus discount_pct/payment_method/
    customer_id so a resume can repopulate the POS screen exactly as it was
    left.

    `customer_id` is TEXT, not INTEGER -- unlike the original commit this
    migration was renumbered from (schema v3 -> v4 on an old base where
    customers.id was still a plain autoincrement integer), customers.id is
    UUID TEXT on this lineage as of _migrate_customers_to_uuid above, so the
    declared column type is fixed here to match the real shape of the value
    it stores. `branch_id` stays INTEGER -- branches.id was never part of
    the UUID migration (see _migrate_categories_to_uuid/_migrate_products_
    to_uuid/_migrate_customers_to_uuid/_migrate_suppliers_to_uuid above;
    branches was never in that list).

    Local-only, NOT part of commercial_runtime/sync/'s outbox -- a mid-edit
    cart isn't a natural sync entity (two devices don't need to see each
    other's in-progress carts, and syncing this table would require solving
    conflict resolution this feature doesn't need to take on); explicit
    scope decision, not an oversight.

    Idempotent: CREATE TABLE/INDEX already use IF NOT EXISTS, so a second
    call -- or a fresh install migrating 0 -> 11 in one pass, same as every
    step above -- is a clean no-op.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS held_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER DEFAULT 1,
            branch_id INTEGER,
            customer_id TEXT,
            hold_number TEXT,
            label TEXT DEFAULT '',
            cart_json TEXT NOT NULL,
            item_count INTEGER DEFAULT 0,
            subtotal REAL DEFAULT 0,
            total REAL DEFAULT 0,
            held_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id),
            FOREIGN KEY (branch_id) REFERENCES branches(id)
        );
        CREATE INDEX IF NOT EXISTS idx_held_sales_company ON held_sales(company_id, created_at);
    """)


def _migrate_add_whatsapp_recipients(conn):
    """One-time migration (schema v11 -> v12): adds the whatsapp_recipients
    table owned by commercial_runtime/notifications (multi-recipient,
    role/branch-scoped WhatsApp report routing) -- identical shape to v9's
    email/whatsapp-outbox addition above: CREATE TABLE IF NOT EXISTS /
    CREATE INDEX IF NOT EXISTS only, nothing product-specific lives in that
    module, so this function's only job is to call it inside the same
    ensure_schema_version() transaction every other step in this chain
    already runs inside.

    Idempotent by construction (every statement inside
    apply_whatsapp_recipients_schema() is already IF NOT EXISTS), so a
    second call -- or a fresh install migrating 0 -> 12 in one pass, same as
    every step above -- is a clean no-op.
    """
    from commercial_runtime.notifications.schema import apply_whatsapp_recipients_schema
    apply_whatsapp_recipients_schema(conn)


def local_terminal_id():
    """This device's terminal id, or None when it has never been established.

    Deliberately NOT a new notion of device identity. It returns
    `commercial_runtime.identity.device_context.peek_local_device_uuid()` --
    the install-stable UUID persisted in
    <AURA_APP_DATA>/device/local_device.json, which `device_registry.devices`
    is already keyed by. Inventing a second "which terminal is this" value
    here would guarantee that `sales.terminal_id` and `devices.id` disagree
    the first time anyone tried to join them.

    `peek_*` rather than `local_device_uuid()` on purpose: the peek variant
    never CREATES the identity file. Stamping a row is a bookkeeping question,
    not a reason to manufacture an install identity as a side effect -- the
    same reasoning `local_device_is_admin()` uses for choosing the peek
    variant (see that function's docstring).

    Returns None instead of raising on LocalDeviceStateCorruptError: a
    corrupt local-device record is a real problem, but the correct place to
    surface it is device resolution, not the middle of a sale. An
    unattributed row is recoverable; a refused sale is not.
    """
    try:
        from commercial_runtime.identity.device_context import (
            LocalDeviceStateCorruptError,
            peek_local_device_uuid,
        )
        return peek_local_device_uuid()
    except Exception:
        # Includes LocalDeviceStateCorruptError and, on a stripped Android
        # build, an ImportError. Both mean "no trustworthy terminal id".
        return None


def _uid_index_shape(conn, table, name):
    """`{'unique': int, 'partial': int, 'columns': [...]}` for the index called
    `name` on `table`, or None if there is no such index.

    Read from `PRAGMA index_list`, which reports the shape SQLite actually
    stored, rather than from `sqlite_master.sql`, which reports the text
    somebody typed. The distinction is the entire point of F4: a
    `CREATE UNIQUE INDEX IF NOT EXISTS` whose name is already taken by a
    plain index leaves the plain index in place and the UNIQUE text nowhere.

    `columns` comes from a second pragma because `index_list` does not carry
    it, and the COLUMN is part of the shape every bit as much as uniqueness
    is. An index named `idx_<t>_uid` that is genuinely UNIQUE and genuinely
    partial but built over `id` satisfies a unique-and-partial check
    completely, while `uid` uniqueness stays absent and `user_version`
    advances anyway -- F4's own consequence, reached past F4's own fix. Not
    hypothetical here: CLAUDE.md warns that databases in this project get
    touched by more than one branch's schema version during testing, and this
    migration re-runs from the top after any failure.
    """
    for _seq, iname, unique, _origin, partial in conn.execute(
        f'PRAGMA index_list("{table}")'
    ).fetchall():
        if iname == name:
            return {
                'unique': unique,
                'partial': partial,
                'columns': [row[2] for row in conn.execute(
                    f'PRAGMA index_info("{name}")'
                ).fetchall()],
            }
    return None


def _uid_index_is_correct(shape):
    """True when `shape` (from `_uid_index_shape`) is the ONE shape that makes
    `uid` mean anything: a UNIQUE, partial index over exactly `uid`.

    A single predicate rather than the condition written out twice, because
    `_ensure_unique_uid_index` asks the question twice -- once to decide
    whether to rebuild, once to verify the postcondition -- and the two must
    not be able to drift apart. If the rebuild test were ever stricter than
    the verify test, the migration would drop and recreate an index on every
    launch forever; if the verify test were stricter, it would rebuild and
    then raise on its own work. Both failures are launch-path failures, and
    neither is visible in a diff that only touches one of the two lines.
    """
    return bool(
        shape is not None
        and shape['unique']
        and shape['partial']
        and shape['columns'] == ['uid']
    )


def _repair_duplicate_uids(conn, table):
    """Reissue `uid` on every row that shares one with an earlier row, keeping
    the lowest rowid's value. Returns how many rows were reissued.

    WHY REPAIR AND NOT RAISE. `CREATE UNIQUE INDEX` on a column holding a
    duplicate raises sqlite3.IntegrityError, and there is nowhere for that
    exception to go that is not fatal: `app.py::init_app()` calls
    `init_retail()` unconditionally, and `ensure_schema_version` advances
    `user_version` only on full success. So one duplicate uid did not fail
    one migration -- it stopped the app from ever starting again AND froze
    the version marker, blocking every future migration behind a condition
    the customer has no way to clear. A till that will not open is not an
    acceptable response to a bookkeeping collision.

    WHY REPAIR IS SAFE HERE, and would not be on most columns: `uid` is a
    WIRE identity minted by this very migration. It is not something a human
    typed, it encodes no business meaning, and at v13 nothing outside this
    database references it yet. Regenerating one loses nothing. (Contrast
    `cashier`/`created_by`, which this migration will not touch under any
    circumstances -- a wrong name on a sale is worse than no name.)

    The LOWEST rowid keeps its value on purpose. If part of this table has
    already been shared with a peer device or Owner, the original row is the
    one likelier to have been seen under that uid; the interloper -- a
    restored row, a merged row, a row copied by support SQL -- is the one
    that should move.

    No shipped writer produces a duplicate today: every one of them uses
    `uuid.uuid4()`, and the `sale_items` uid is correctly generated inside
    the per-line loop rather than once outside it. This exists for the paths
    that are not shipped writers -- a restore, a two-database merge, a
    hand-written support fix, or any future row-copy -- which are exactly the
    moments a customer can least afford "the app will not open".

    Chunked and re-queried each pass for the same reason the backfill above
    is: `sale_items` on a shop with years of history is the biggest table in
    this file by an order of magnitude, this runs on a till machine, and a
    loop that re-reads its own work terminates correctly even if it is
    interrupted and retried.

    THE PREDICATE HAS TO SHRINK ITS OWN WORK QUEUE, and that is the entire
    correctness argument for a loop shaped like this one. A row is selected
    only when a row with a LOWER rowid already holds its uid; the fresh uuid4
    written into it therefore makes it the lowest -- and only -- holder of a
    value nothing else has, so it cannot be selected again. Each pass strictly
    reduces the number of matching rows, and the loop ends.

    That is not a theoretical concern. The first version of this function
    matched on `... > 1 OR (SELECT COUNT(*) ...) = 1`, and SQL's `AND` binding
    tighter than `OR` made that second disjunct a whole-table predicate: every
    row with a distinct uid matched it, was reissued, and matched it again on
    the next pass. The loop never emptied. That is worse than the F1 defect
    this function was written to fix -- F1 raised IntegrityError, which is
    fatal but names its own cause, whereas a spinning repair makes
    `init_retail()` never return at all: a till stuck on a splash screen with
    no error, no log line and no traceback, rewriting every uid in the database
    several times a second, reached again on every subsequent launch because
    the ADD COLUMN above it has already committed.
    `retail_v13_uid_repair_termination_test.py` pins termination and bounded
    work directly, by counting write statements, because a non-terminating loop
    does not fail a test suite -- it hangs one, and `products/run_all_tests.py`
    runs each file with no subprocess timeout at all.

    The `uid IN (SELECT ... HAVING COUNT(*) > 1)` pre-filter is not redundant
    with the `rowid >` test that follows it; it is what keeps this affordable.
    That subquery is uncorrelated, so SQLite evaluates it once per pass into a
    set that is EMPTY on every shipped install, and the correlated MIN(rowid)
    lookup then runs for no rows at all. Without it, the correlated subquery
    would run once per row against a `uid` column that has no index yet (this
    function runs precisely to make creating that index possible), which is a
    full table scan per row -- quadratic on the largest table in the schema, on
    a shop laptop, during boot.
    """
    import uuid as _uuid

    reissued = 0
    while True:
        pending = conn.execute(
            f'SELECT rowid FROM "{table}" AS t'
            f' WHERE t.uid IS NOT NULL'
            f'   AND t.uid IN (SELECT uid FROM "{table}" WHERE uid IS NOT NULL'
            f'                 GROUP BY uid HAVING COUNT(*) > 1)'
            f'   AND t.rowid > (SELECT MIN(x.rowid) FROM "{table}" x WHERE x.uid = t.uid)'
            f' LIMIT {_UID_BACKFILL_CHUNK}'
        ).fetchall()
        if not pending:
            return reissued
        conn.executemany(
            f'UPDATE "{table}" SET uid=? WHERE rowid=?',
            [(str(_uuid.uuid4()), row[0]) for row in pending],
        )
        reissued += len(pending)


def _ensure_unique_uid_index(conn, table):
    """Leave `idx_<table>_uid` existing, UNIQUE and partial -- verified, not
    assumed. Fixes the two ways the original one-liner failed.

        conn.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_uid '
                     f'ON "{table}"(uid) WHERE uid IS NOT NULL')

    F1: unguarded. A duplicate uid raised IntegrityError straight out of
    `init_retail()` -- see `_repair_duplicate_uids` for why that is permanent
    rather than merely annoying. Duplicates are now found and repaired BEFORE
    the index is created, so the statement cannot fail for that reason.

    F4: unverified. `IF NOT EXISTS` is a statement about the NAME, never
    about the SHAPE. A same-named NON-unique index -- left by an interrupted
    earlier attempt of this very migration, or by a support fix -- satisfied
    it completely, so v13 reported success, `user_version` advanced to 14,
    and uid uniqueness was absent forever with no signal anywhere. That is
    the worse of the two failures: F1 at least announces itself.

    ALL THREE PARTS OF THE SHAPE ARE CHECKED, not just uniqueness, and they
    are not all load-bearing for the same reason:

      - COLUMN is the one that silently defeats the whole migration. An index
        of this name that is UNIQUE and partial but built over `id` passes a
        uniqueness check completely and enforces nothing whatsoever about
        `uid`. That is F4's own consequence -- success reported, version
        advanced, constraint absent -- reached past F4's own fix.
      - UNIQUE is F4 as originally found.
      - PARTIAL is NOT a correctness requirement, and it is worth being
        precise about that rather than repeating a plausible-sounding reason.
        A full UNIQUE index over `uid` would NOT reject the un-backfilled
        rows: SQLite considers every NULL distinct from every other NULL for
        the purposes of a unique index, so any number of NULL uids coexist
        under either shape. What the predicate buys is that the index carries
        no entry at all for rows that have no wire identity yet. It is checked
        here because it is the shape this migration DECLARES, and an index
        that does not match what this migration declares was put there by
        something else -- which is precisely the situation worth rebuilding
        out of, whatever the divergence happens to be.

    A mis-shaped index is DROPPED and recreated. That is the one piece of
    non-additive DDL in this migration and it is deliberate: dropping an
    index destroys no row and no column, only a derived structure this
    migration owns and is about to rebuild correctly. The behavioural
    additive-only guard (`retail_v13_additive_only_behavioural_test.py`)
    compares every row of every table across this migration and is
    untroubled by it, which is the check that actually matters.

    The postcondition is then re-read and RAISED on, not logged. See
    RetailUidIndexError: after the repair above there is no remaining
    mechanism for it to fire, and if one is ever found, stopping is right --
    the alternative is advancing the version marker on a database where the
    wire identity is not unique, which is F4 again by a different route.
    """
    name = f'idx_{table}_uid'

    _repair_duplicate_uids(conn, table)

    shape = _uid_index_shape(conn, table, name)
    if shape is not None and not _uid_index_is_correct(shape):
        conn.execute(f'DROP INDEX "{name}"')
        shape = None
    if shape is None:
        conn.execute(
            f'CREATE UNIQUE INDEX "{name}" ON "{table}"(uid) WHERE uid IS NOT NULL'
        )

    shape = _uid_index_shape(conn, table, name)
    if not _uid_index_is_correct(shape):
        raise RetailUidIndexError(
            f'{name} could not be established as a UNIQUE partial index on '
            f'{table}.uid (observed: {shape!r}). user_version is NOT being advanced, '
            f'so this retries on the next launch rather than shipping a wire identity '
            f'that is not unique.'
        )


def _migrate_add_identity_and_attribution_columns(conn):
    """One-time migration (schema v13): identity and attribution columns --
    launch-readiness Phase 2, reserved in ROADMAP.md's 2026-08-21 ledger.

    This is the first migration in this file to ALTER the tables that hold a
    real shop's entire sales history, so it is additive ONLY -- every
    statement below is `ALTER TABLE ... ADD COLUMN`,
    `CREATE [UNIQUE] INDEX IF NOT EXISTS`, or an `UPDATE` guarded on the rows
    that still need it. There is no table rebuild here, and there must never
    be one: the rebuild technique `_migrate_products_to_uuid` uses copies
    every row through a replacement table with `PRAGMA foreign_keys`
    temporarily OFF, and a mistake in one column list loses real financial
    data with no exception and a passing integrity_check (this lineage has
    already lived through exactly that -- see `_legacy_supplier_link`'s
    docstring, where every product's supplier link silently came back NULL).
    `retail_v13_additive_only_behavioural_test.py` pins that constraint
    behaviourally -- it snapshots every row of every table, values included,
    and asserts nothing outside the additive change below differs afterwards.
    (`retail_v13_identity_columns_migration_test.py` also keeps a cheap
    source-level tripwire, but that one is a convenience, not the guarantee:
    it cannot see DML data destruction, nor a statement assembled from a
    variable, and four such mutations shipped past it green.)

    THREE GROUPS OF COLUMNS:

    1. `uid` on RETAIL_UID_TABLES -- the WIRE identity, exactly what registry
       v3 gave `users` (commercial_runtime/identity/account_schema.py). The
       existing autoincrement `id` stays the primary key and stays a private
       detail of THIS install; `uid` is what a peer device, or Owner's relay,
       names the row by. Backfilled with one fresh `uuid.uuid4()` PER ROW,
       generated in Python.

       Generated in Python, and never as `lower(hex(randomblob(16)))`: that
       SQL trick produces a 32-character string which is NOT RFC-4122, and
       Owner's sync ingest gates on `uuid.UUID(entity_id)`. A value that
       merely looks random passes every local eye test and is then rejected
       on the wire, at the point where it is most expensive to discover.
       The registry v3 migration hit precisely this and documents it; the
       same decision is repeated here rather than re-learned.

       `ALTER TABLE ADD COLUMN` cannot carry a UNIQUE constraint (and adding
       one the "proper" way means the table rebuild this migration exists to
       avoid), so uniqueness is a separate partial unique index. The
       `WHERE uid IS NOT NULL` predicate keeps the index from carrying an
       entry for rows that have no wire identity yet -- between this ALTER
       and the backfill, and for any row an older code path inserts before
       the writers learn about the column, "no uid yet" is a legal state.
       It is NOT what makes those rows legal, and the difference is worth
       stating because the opposite is the intuitive guess: SQLite treats
       every NULL as distinct from every other NULL inside a unique index, so
       un-backfilled rows would coexist happily under a full index too.

    2. `actor_user_uid` / `terminal_id` / `created_at_utc` on
       RETAIL_ACTOR_TABLES. The existing free-text `cashier` / `created_by` /
       `opened_by` columns are KEPT and never read, rewritten, or used to
       guess at `actor_user_uid`. A wrong name on a sale is worse than no
       name: the free text is the only surviving evidence of who the shop
       believed rang a transaction, and a fuzzy name match that guessed wrong
       would destroy that evidence while looking like an improvement.

       `created_at_utc` is the one that matters most and looks least
       important. Today's reports bucket on `date(created_at)` and
       `strftime(...)` over a value that is NOT consistently UTC: `create_sale`
       and `create_return` (api/retail_api.py) write LOCAL wall clock
       deliberately -- there is a comment there explaining that the dashboard
       nets returns out of today's revenue by local date -- while
       `inventory_movements` takes SQLite's DEFAULT CURRENT_TIMESTAMP, which
       IS UTC. So the same column name already means two different things in
       two tables, and two devices in two timezones (or one device with a
       wrong clock) file rows on the wrong day, quietly, with every daily
       total wrong and nothing to show for it.

       Existing rows are left NULL rather than converted. The only offset
       available at migration time is THIS machine's CURRENT one, which is
       wrong by an hour for half of every year and wrong by whole hours for a
       row rung on a device somewhere else -- a fabricated instant, which is
       the same category of mistake as a fabricated cashier name. THE
       CONSEQUENCE IS LOAD-BEARING for whoever moves the report predicates
       onto this column: they must read `COALESCE(created_at_utc, created_at)`,
       never `created_at_utc` alone, or every historical row silently drops
       out of every report. `terminal_id` and `actor_user_uid` are left NULL
       on history for the identical reason -- this device cannot prove it is
       the terminal that rang a sale from before the column existed.
       `local_terminal_id()` above is the source write-time code should use.

    3. `row_version` / `updated_at_utc` / `deleted_at_utc` on
       RETAIL_ROW_VERSION_TABLES -- the reject-stale marker and soft
       tombstone, matching the identical triple registry v3 put on `users`.
       `row_version INTEGER NOT NULL DEFAULT 1` is only legal on ADD COLUMN
       because the non-null DEFAULT is supplied, which SQLite writes into
       every existing row in place. `deleted_at_utc` stays NULL everywhere:
       nobody has been deleted yet, and a tombstone stamped by a migration
       would be a lie about when.

    Idempotent: every ADD COLUMN is preceded by a `PRAGMA table_info` check,
    every index uses IF NOT EXISTS, and the uid backfill only touches rows
    that have no uid -- so a retried run (the normal case, since
    `ensure_schema_version` leaves `user_version` un-advanced on any failure
    and the whole chain re-runs on the next launch) is a clean no-op.

    Every table is existence-guarded first. Some test fixtures hand-build a
    MINIMAL schema and call `_migrate_retail_schema` against it directly
    (retail_category_delete_fk_sync_test.py builds only categories/products/
    inventory_movements), and an unconditional `ALTER TABLE sales` would
    crash them with "no such table: sales" -- the same hazard
    `_migrate_add_shift_cash_drawer` already guards for.
    """
    import uuid as _uuid

    live_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    def _columns(table):
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}

    # ── group 1: the wire identity ──────────────────────────────────────
    for table in RETAIL_UID_TABLES:
        if table not in live_tables:
            continue
        if 'uid' not in _columns(table):
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN uid TEXT')
        # Backfilled by rowid, not by `id`: rowid exists on every one of
        # these tables regardless of what their primary key is declared as,
        # so this keeps working if one of them is ever given a TEXT id the
        # way products/customers/suppliers already were.
        #
        # Chunked, not one giant executemany. `sale_items` on a shop with a
        # few years of history is the largest table in this database by an
        # order of magnitude, and materialising every rowid AND every
        # generated uuid as Python objects at once is hundreds of megabytes
        # on a machine that is also running the app. The chunk loop re-runs
        # the same "still needs a uid" query each pass, so it terminates on
        # its own and stays correct if it is interrupted and retried.
        while True:
            pending = conn.execute(
                f'SELECT rowid FROM "{table}" '
                f"WHERE uid IS NULL OR TRIM(uid) = '' LIMIT {_UID_BACKFILL_CHUNK}"
            ).fetchall()
            if not pending:
                break
            conn.executemany(
                f'UPDATE "{table}" SET uid=? WHERE rowid=?',
                [(str(_uuid.uuid4()), row[0]) for row in pending],
            )
        _ensure_unique_uid_index(conn, table)

    # ── group 2: who / where / when-in-real-time ────────────────────────
    for table in RETAIL_ACTOR_TABLES:
        if table not in live_tables:
            continue
        cols = _columns(table)
        for column, decl in (
            ('actor_user_uid', 'TEXT'),
            ('terminal_id', 'TEXT'),
            ('created_at_utc', 'TEXT'),
        ):
            if column not in cols:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {decl}')
        # Reporting reads these by (company_id, day) far more than by row,
        # and a full scan of a year of sales on a shop laptop is the
        # difference between a report that opens and one that appears to
        # hang. Created outside the ADD COLUMN guard, IF NOT EXISTS, for the
        # same reason idx_products_supplier is (see
        # _migrate_products_add_supplier_fk): an index lives and dies with
        # its table, so an early-return on "column already exists" would
        # leave a rebuilt table indexless.
        conn.execute(
            f'CREATE INDEX IF NOT EXISTS idx_{table}_created_at_utc '
            f'ON "{table}"(created_at_utc)'
        )

    # ── group 3: reject-stale marker + soft tombstone ───────────────────
    for table in RETAIL_ROW_VERSION_TABLES:
        if table not in live_tables:
            continue
        cols = _columns(table)
        for column, decl in (
            ('row_version', 'INTEGER NOT NULL DEFAULT 1'),
            ('updated_at_utc', 'TEXT'),
            ('deleted_at_utc', 'TEXT'),
        ):
            if column not in cols:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {decl}')
        # Partial index: the overwhelmingly common query is "the live rows",
        # and a tombstoned row should cost nothing to skip. Guarded on
        # company_id the same way the whole loop is guarded on the table
        # existing -- a hand-built minimal test fixture is under no
        # obligation to carry the tenant key, and an index is not worth
        # crashing a migration over.
        if 'company_id' in _columns(table):
            conn.execute(
                f'CREATE INDEX IF NOT EXISTS idx_{table}_live '
                f'ON "{table}"(company_id) WHERE deleted_at_utc IS NULL'
            )


# ── v14: rebinding the tenant key onto the Owner-issued value ───────────────

class CompanyRebindError(Exception):
    """Raised when `rebind_company_id` cannot identify, unambiguously, which
    tenant's rows it has been asked to move. Deliberately a refusal rather
    than a best guess -- see that function's docstring."""


def company_scoped_tables(conn):
    """Every real table in this database that carries a `company_id` column.

    DISCOVERED from the live schema rather than hardcoded. A hardcoded list
    is a second place to remember, and the failure mode of forgetting is
    silent: the forgotten table keeps the old tenant key, its rows stop
    matching `retail_api._cid()`, and they simply stop appearing. Discovery
    also means every table a later migration adds is covered on the day it
    is added, with no edit here.

    Line tables are correctly absent: `sale_items`, `return_items` and
    `purchase_order_items` carry no `company_id` of their own and scope
    entirely through their parent row -- the same shape `cash_movements`
    uses via `session_id`. If one of them ever shows up in this list, it
    means somebody added a redundant second copy of the tenant key.
    """
    scoped = []
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall():
        name = row[0]
        cols = {c[1] for c in conn.execute(f'PRAGMA table_info("{name}")').fetchall()}
        if 'company_id' in cols:
            scoped.append(name)
    return tuple(scoped)


def rebind_company_id(conn, new_company_id, old_company_id=None):
    """Move every tenant-scoped row in retail.db from one `company_id` to
    another, in ONE transaction, with the row counts verified on both sides.

    Reusable and idempotent by design, NOT inline migration code, because it
    has two callers that fire at completely different moments: the v14
    migration step (for an install that is already licensed when it upgrades)
    and `rebind_company_id_after_activation()` (for the far more common
    install that activates months later -- licensing is OFF by default in
    this product, so most installs migrate long before Owner has issued them
    anything).

    Returns a status dict, never a bare bool: `{'status': ..., 'rows': ...,
    'old_company_id': ..., 'new_company_id': ..., 'tables': (...)}`, where
    status is one of:
      - 'skipped'       -- no `new_company_id` supplied. A no-op, NOT an
                           error: the unlicensed install is the normal case.
      - 'already_bound' -- every scoped row is already on `new_company_id`.
      - 'rebound'       -- rows moved; `rows` says how many.

    IDENTIFYING THE OLD ID. When `old_company_id` is not supplied it is
    derived from the rows themselves -- the distinct set of `company_id`
    values actually present. Deriving it from the rows (rather than from the
    registry, or from config) is what makes an interrupted rebind
    self-healing: a crash that moved `sales` but nothing else leaves the set
    as {old, new}, and the next call finishes the job instead of concluding
    that everything is already done.

    Exactly three cases are safe to act on without being told:
      {}            -> nothing to move.
      {new}         -> already bound.
      {new?, old}   -> one other value; that is the tenant to move.
    Anything else RAISES CompanyRebindError and changes nothing. This is the
    multi-tenant guard, and it is not theoretical: CLAUDE.md states plainly
    that one install can host more than one company. Blanket-moving every row
    onto one licence's id would merge two companies' books into a single
    tenant -- unrecoverable, and it would look exactly like a successful
    migration. A caller that genuinely knows which tenant to move passes
    `old_company_id` explicitly.

    ONE TRANSACTION, NOT THIRTEEN. A database with six tables moved and seven
    not is precisely the silent-invisibility state this whole exercise exists
    to prevent, and it produces no error anywhere -- `_cid()` just stops
    matching. The rewrite therefore runs inside an explicit
    `BEGIN IMMEDIATE`, verifies afterwards (still inside the transaction)
    that every scoped table has zero rows left on the old id and that the
    new id gained exactly the rows the old id lost, and rolls the whole thing
    back on any mismatch or exception. IMMEDIATE rather than a deferred
    BEGIN so the write lock is taken up front: this rewrites every business
    row in the file, and discovering a competing writer halfway through is
    strictly worse than failing before the first UPDATE.

    Any in-flight implicit transaction is committed before that BEGIN --
    Python's sqlite3 opens one automatically on DML, so the v13 step running
    just before this in `_migrate_retail_schema` leaves one open, and
    `BEGIN` inside a transaction is an error. Committing it is safe and
    matches what the rebuild migrations earlier in this file already do:
    `ensure_schema_version` does not hold a transaction across the chain, and
    it advances `user_version` only on full success, so a failure here still
    re-runs every (idempotent) step on the next launch.
    """
    if new_company_id is None or (isinstance(new_company_id, str) and not new_company_id.strip()):
        return {
            'status': 'skipped',
            'reason': 'no Owner-issued company_id available',
            'rows': 0,
            'old_company_id': old_company_id,
            'new_company_id': new_company_id,
            'tables': (),
        }

    tables = company_scoped_tables(conn)
    if not tables:
        return {
            'status': 'skipped',
            'reason': 'no company-scoped tables in this database',
            'rows': 0,
            'old_company_id': old_company_id,
            'new_company_id': new_company_id,
            'tables': (),
        }

    present = set()
    for table in tables:
        for row in conn.execute(
            f'SELECT DISTINCT company_id FROM "{table}" WHERE company_id IS NOT NULL'
        ).fetchall():
            present.add(row[0])

    if old_company_id is None:
        others = {value for value in present if value != new_company_id}
        if not others:
            return {
                'status': 'already_bound',
                'rows': 0,
                'old_company_id': None,
                'new_company_id': new_company_id,
                'tables': tables,
            }
        if len(others) > 1:
            raise CompanyRebindError(
                f'Refusing to rebind company_id: this database holds rows under '
                f'{len(others)} different tenant keys ({sorted(map(repr, others))}). '
                f'Guessing which one the licence belongs to would merge two companies '
                f'books into one, irreversibly. Pass old_company_id explicitly.'
            )
        old_company_id = next(iter(others))

    if conn.in_transaction:
        conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    try:
        # AUDIT (Defect 3, launch-readiness Phase 5 verification, MEDIUM):
        # before_old/before_new/before_total used to be counted BEFORE this
        # BEGIN IMMEDIATE. IMMEDIATE already takes the write lock up front --
        # that is the whole point of using it instead of a deferred BEGIN --
        # but taking the lock and THEN trusting a snapshot counted before the
        # lock existed throws that guarantee away: a single concurrent
        # INSERT landing in the gap between the old count and this lock (a
        # till ringing up a sale, ordinary on a busy store) changes what the
        # UPDATE below actually moves, so the in-transaction verification
        # compares a stale "before" against a real "after" and raises
        # CompanyRebindError on a rebind that was actually fine -- a SAFE
        # failure, but exactly how a busy till reaches the split state its
        # sibling identity-side rebind (commercial_runtime/identity/
        # company_rebind.py) guards against at the activation seam. Counting
        # HERE, after the lock, is what makes the count and the update see
        # the identical snapshot; nothing else can commit a write against
        # this database between this line and the `conn.commit()` below.
        before_old = {
            table: conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE company_id=?', (old_company_id,)
            ).fetchone()[0]
            for table in tables
        }
        before_new = {
            table: conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE company_id=?', (new_company_id,)
            ).fetchone()[0]
            for table in tables
        }
        before_total = {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }
        if sum(before_old.values()) == 0:
            # Nothing to move. The write lock BEGIN IMMEDIATE took is
            # released honestly via rollback (nothing was written, so
            # rollback and commit are equivalent here) rather than held
            # while this function returns.
            conn.rollback()
            return {
                'status': 'already_bound',
                'rows': 0,
                'old_company_id': old_company_id,
                'new_company_id': new_company_id,
                'tables': tables,
            }

        moved = 0
        for table in tables:
            cur = conn.execute(
                f'UPDATE "{table}" SET company_id=? WHERE company_id=?',
                (new_company_id, old_company_id),
            )
            moved += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

        for table in tables:
            stranded = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE company_id=?', (old_company_id,)
            ).fetchone()[0]
            landed = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE company_id=?', (new_company_id,)
            ).fetchone()[0]
            total = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if stranded or landed != before_old[table] + before_new[table] or total != before_total[table]:
                raise CompanyRebindError(
                    f'company_id rebind failed its own count check on {table!r}: '
                    f'{stranded} row(s) left on the old key, {landed} on the new '
                    f'(expected {before_old[table] + before_new[table]}), '
                    f'{total} rows total (expected {before_total[table]}). '
                    f'Rolled back -- the tenant key is unchanged.'
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        'status': 'rebound',
        'rows': moved,
        'old_company_id': old_company_id,
        'new_company_id': new_company_id,
        'tables': tables,
    }


def owner_issued_company_id(app_data_dir=None):
    """The Owner-issued tenant key for this install, or None.

    That value is `license_public_id` from the last assertion this install
    verified and persisted. Established by reading Owner, not by assumption:
    `owner/app/sync/routes.py` scopes the entire event stream by
    `SyncEvent.license_id`, resolved server-side from the VERIFIED
    installation and never from body input, and
    `owner/app/licensing_service/assertions.py` signs that same value into
    every assertion as `"license_public_id": str(license_row.id)`. So the
    licence -- not the installation -- is Owner's tenant.

    `installation_public_id` is deliberately NOT used even though it is also
    Owner-issued and sits in the same payload: it identifies one DEVICE's
    installation, so adopting it as `company_id` would give every terminal in
    a shop a different tenant key. That is the tenant-fragmentation failure
    this rebind exists to end, not a way to implement it.

    Read with plain sqlite3 + json, with NO import of
    `commercial_runtime.licensing_contracts`. That package imports
    `cryptography` at module scope, and an eager import of it crashed the
    whole Android backend with ModuleNotFoundError -- the same reason
    `device_context.local_device_fingerprint()` reads the licensing system's
    `device_key_meta.json` as plain JSON rather than importing the module
    that writes it. This function is a read-only consumer of somebody else's
    file, never its authority, so every failure (missing file, unreadable
    file, no assertion yet, malformed JSON) is reported the same honest way:
    None.

    The persisted state's `current_state` is deliberately NOT consulted. A
    lapsed subscription or an expired assertion does not make a shop's rows
    belong to a different tenant, and refusing to name the tenant while the
    licence is in a warning state would mean the rebind could never converge
    for exactly the installs most likely to need support.
    """
    if app_data_dir:
        path = os.path.join(app_data_dir, 'database', 'subsystems', 'licensing.db')
    else:
        path = os.path.join(SUBSYS_DIR, 'licensing.db')
    # Existence-checked before connecting: sqlite3.connect CREATES the file,
    # and an empty licensing.db conjured up by a read is exactly the kind of
    # side effect that makes "is this install licensed?" ambiguous later.
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path, timeout=5)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            'SELECT assertion_envelope_json FROM licensing_state WHERE id=1'
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    # ONE try, covering the parse AND both lookups, and typed at every step
    # rather than trusted.
    #
    # This guard used to stop one line short: `payload = ...get('payload')`
    # was inside it and `value = payload.get('license_public_id')` was
    # outside. An envelope whose `payload` is a list, a string or a number is
    # a perfectly legal JSON document -- `_json.loads(...).get('payload')`
    # returns it happily, it is truthy, and `.get` then raised AttributeError
    # one line later with nothing to catch it. `_json.loads` itself raises
    # TypeError, never listed here at all, when the column does not hold text
    # or bytes; the shipped table declares that column TEXT, whose affinity
    # converts an INTEGER to a string on the way in and hides the case, but
    # affinity is a property of one particular CREATE TABLE and this function
    # does not own this file.
    #
    # Either exception propagates through `_migrate_rebind_company_id_to_
    # owner_issued` -> `_migrate_retail_schema` -> `init_retail()` ->
    # `app.py::init_app()`, all unconditional, and `ensure_schema_version`
    # advances `user_version` only on full success. So the cost of a
    # malformed envelope was not "the tenant key could not be resolved" -- it
    # was the app never starting again with every future migration blocked
    # behind a frozen version marker. Exactly the same permanent wedge the
    # v13 duplicate-uid defect produced, reached from a different direction.
    #
    # `isinstance` on the payload instead of a bare `.get`, for the same
    # reason the return value is type-checked below: this is a read-only
    # consumer of somebody else's file, so a shape it cannot verify is a
    # shape it declines to use. The docstring above promises every failure is
    # reported the same honest way -- None -- and this is what makes that
    # true rather than aspirational.
    try:
        import json as _json
        envelope = _json.loads(row[0])
        if not isinstance(envelope, dict):
            return None
        payload = envelope.get('payload')
        if not isinstance(payload, dict):
            return None
        value = payload.get('license_public_id')
    except (ValueError, TypeError, AttributeError):
        return None
    if not value or not isinstance(value, str):
        return None
    return value


def local_authoritative_company_id():
    """The `company_id` this install's IDENTITY layer currently considers
    authoritative -- the value `mt_auth.create_session` puts into
    `session['company_id']` and therefore the one every
    `retail_api._cid()`-filtered query in the product compares against.

    Same source and same precedence order `sync_service.
    local_company_id_from_registry()` already uses (company_settings first,
    the admin `users` row as a fallback, None rather than a fabricated
    default), re-implemented here as a small read instead of imported,
    for two reasons: `commercial_runtime/sync/` is out of scope this phase
    and must not gain a new dependant, and `registry_db` caches its DB_PATH
    at import time -- fine for a real process, which imports it once, but
    wrong for anything that has to resolve AURA_APP_DATA freshly (the
    anti-pattern `device_context._resolve_app_data()` documents and
    products/run_all_tests.py's docstring explains at length).
    """
    app_data = os.environ.get('AURA_APP_DATA')
    path = (
        os.path.join(app_data, 'database', 'registry.db') if app_data
        else os.path.join(BASE_DIR, 'registry.db')
    )
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path, timeout=5)
    except sqlite3.Error:
        return None
    try:
        for sql in (
            'SELECT company_id FROM company_settings LIMIT 1',
            "SELECT company_id FROM users WHERE role='admin' LIMIT 1",
        ):
            try:
                row = conn.execute(sql).fetchone()
            except sqlite3.Error:
                continue
            if row and row[0]:
                return row[0]
        return None
    finally:
        conn.close()


def _rebind_to_owner_issued(conn, new_company_id):
    """Shared body of the v14 migration step and the activation-time hook.

    THE GUARD THIS FUNCTION EXISTS FOR, and the reason neither caller is a
    bare `rebind_company_id(conn, owner_issued_company_id())`:

    `session['company_id']` is populated from registry.db's `users` row
    (`commercial_runtime/identity/mt_auth.py::create_session`), and
    `retail_api._cid()` filters essentially every query in the product on it.
    Moving retail.db's rows onto the Owner-issued key while the identity
    layer still says `md5(admin_email)` therefore makes every
    `WHERE company_id=?` match zero rows: a real shop's entire history
    disappears from the UI, with no exception, no failed integrity_check and
    no log line. That is strictly worse than not rebinding at all.

    So the retail side only ever CONVERGES onto a tenant key the identity
    layer has ALREADY adopted. This is the same direction of travel
    `sync_service._apply_event` already takes when it ignores a pulled
    payload's `company_id` and stamps the RECEIVING device's own value
    instead -- and it is what makes an interrupted two-database rebind
    self-healing rather than fatal: if identity moved and retail did not,
    the next call (next launch, or the next activation) finishes the job,
    because `rebind_company_id` derives the old id from retail's own rows.

    Until the identity-side rebind exists, this returns 'deferred' and
    changes nothing. That is an honest no rather than a half-applied yes.
    """
    authoritative = local_authoritative_company_id()
    if authoritative is None or str(authoritative) != str(new_company_id):
        return {
            'status': 'deferred',
            'reason': (
                'the identity layer still reports company_id='
                f'{authoritative!r}, not the Owner-issued {new_company_id!r}; '
                'rebinding retail.db first would hide every row from _cid()'
            ),
            'rows': 0,
            'old_company_id': authoritative,
            'new_company_id': new_company_id,
            'tables': (),
        }
    return rebind_company_id(conn, new_company_id)


def _migrate_rebind_company_id_to_owner_issued(conn):
    """One-time migration (schema v14): rebind `company_id` onto the
    Owner-issued tenant key -- launch-readiness Phase 2, reserved in
    ROADMAP.md's 2026-08-21 ledger.

    v14 is about being ABLE to rebind, not about having done it. Licensing is
    OFF by default in this product (config.py: with OWNER_LICENSING_BASE_URL
    unset the app runs fully unlocked and there IS no Owner-issued id), so
    the overwhelmingly common install reaches this step with nothing to
    rebind to. That is a no-op, not an error, and `user_version` still
    advances -- otherwise every later migration would sit behind a version
    marker that never moves on the majority of installs.

    An install that activates a licence LATER does not get missed, because
    the same work is reachable from `rebind_company_id_after_activation()`
    (wired into the activation route via
    `make_licensing_blueprint(on_activation_success=...)`).

    `CompanyRebindError` is caught HERE and only here. Raising it out of a
    migration would leave a multi-tenant install unable to advance its schema
    version ever again -- every future migration blocked by a refusal that is
    itself correct. The direct `rebind_company_id()` API still raises, so a
    caller that asked for the rebind explicitly still hears about it.
    """
    new_company_id = owner_issued_company_id()
    if not new_company_id:
        return {
            'status': 'skipped',
            'reason': 'this install has no Owner-issued licence assertion',
            'rows': 0,
            'old_company_id': None,
            'new_company_id': None,
            'tables': (),
        }
    try:
        return _rebind_to_owner_issued(conn, new_company_id)
    except CompanyRebindError as exc:
        return {
            'status': 'deferred',
            'reason': str(exc),
            'rows': 0,
            'old_company_id': None,
            'new_company_id': new_company_id,
            'tables': (),
        }


def rebind_company_id_after_activation():
    """Activation-time entry point: called after a licence activation
    succeeds, so an install that activates months after it migrated gets
    rebound at that moment instead of waiting for a next migration that may
    never come.

    Opens its own connection -- the activation request has none, and it is
    handled by `commercial_runtime/licensing_contracts/routes.py`, which
    never imports anything product-specific. Retail's `app.py` passes this
    function in as `make_licensing_blueprint(on_activation_success=...)`,
    which keeps that package's product-agnostic boundary intact.

    NEVER RAISES. A licence activation that genuinely succeeded at Owner must
    not be reported back to the customer as a failure because a local
    bookkeeping rewrite hit a locked database -- the customer would retry the
    activation, which is the one action that cannot fix it. Every outcome is
    a status dict; the work is idempotent and reachable again from the next
    activation, check-in-triggered call, or migration.
    """
    try:
        new_company_id = owner_issued_company_id()
        if not new_company_id:
            return {
                'status': 'skipped',
                'reason': 'activation left no assertion carrying a license_public_id',
                'rows': 0,
                'old_company_id': None,
                'new_company_id': None,
                'tables': (),
            }
        conn = get_retail_conn()
        try:
            result = _rebind_to_owner_issued(conn, new_company_id)
            conn.commit()
            return result
        finally:
            conn.close()
    except Exception as exc:
        return {
            'status': 'failed',
            'reason': f'{type(exc).__name__}: {exc}',
            'rows': 0,
            'old_company_id': None,
            'new_company_id': None,
            'tables': (),
        }


# ── v15: making the cache provably derivable from the ledger ────────────────

#: The movement type v15 writes for a balance that had no ledger history.
#:
#: Deliberately NOT `opening_stock`. That type means "an operator declared
#: this opening figure", and api/import_api.py sums exactly those rows (plus
#: its own import reference) to work out how much of a product's stock has
#: already been declared, so it can post a DELTA instead of re-adding the
#: whole figure on every re-import. These rows were declared by nobody: the
#: migration inferred them from the cached balance. Filing an inference under
#: the type reserved for a human declaration would make the importer treat a
#: guess as evidence, which is the same category of mistake as stamping a
#: real user on the row.
V15_OPENING_COUNT_TYPE = 'opening_count'

#: Reference marker on the seeded rows, so they are one indexed query away
#: from any screen, report or later migration that needs to tell an inferred
#: opening count apart from a counted one.
V15_OPENING_COUNT_REFERENCE = 'V15-OPENING-COUNT'

#: The reason, stored on the row itself rather than only in this file. A
#: number in a goods ledger with no explanation attached is exactly how the
#: next person concludes somebody counted this stock.
V15_OPENING_COUNT_REASON = (
    'Opening count inferred by schema migration v15: the cached balance minus '
    'everything the ledger already accounts for at this product/branch. This '
    'is what must have been on hand before the recorded history for that '
    'history to end where the balance says it does. Nobody counted it.'
)

#: How many seeded movements are handed to one executemany. The candidate
#: list is bounded by products x branches (not by transaction history, which
#: is orders of magnitude larger), so it is materialised in one query -- but
#: the INSERT is still sliced, because this runs on a till machine.
_V15_SEED_CHUNK = 2000

#: Rows named individually in the gate's failure message. The full list is a
#: whole-catalogue dump; the first few are what makes the message actionable.
_V15_REPORTED_OFFENDERS = 5

#: How many product ids go into one `IN (...)` when the seeder re-checks that
#: its candidates still have a product row. Well under SQLite's 999-variable
#: floor (SQLITE_MAX_VARIABLE_NUMBER was raised to 32766 only in 3.32), so
#: this holds on whatever SQLite the packaged interpreter happens to carry.
_V15_PRODUCT_PROBE_CHUNK = 500


def _v15_company_ids(conn):
    """Every company_id that has stock state at all, from BOTH sides.

    UNION rather than a read of `inventory_balances` alone: a company whose
    balances were all deleted still has movements, and it is exactly that
    company whose drift nobody would otherwise look at.
    """
    return [row[0] for row in conn.execute(
        "SELECT company_id FROM inventory_balances "
        "UNION "
        "SELECT company_id FROM inventory_movements"
    ).fetchall()]


def _v15_resolve_unambiguous_null_branches(conn, company_id, live_tables):
    """Give legacy NULL-branch movements a branch ONLY where there is exactly
    one branch they could belong to. Returns how many rows moved.

    `inventory_balances.branch_id` is NOT NULL, so a movement with a NULL
    branch has no balance row that could ever hold it and `compute_drift`
    reports it as `repairable: False` forever. A company with exactly ONE
    branch has one answer and it is not a guess. A company with two has two,
    and picking either puts real stock in the wrong shop -- worse than
    leaving it visibly unassigned, because a wrong number looks right.

    Counts ALL branches, not just active ones: an archived branch is still
    somewhere the stock could have been, so its existence makes the answer
    ambiguous even though nobody sells there any more.

    Runs BEFORE the opening-count seeding, and the order is load-bearing.
    Resolving a NULL-branch movement onto branch B gives (product, B) ledger
    history; seeding first would have already written an opening count for
    that key on the grounds that it had none, and the two would then sum to
    double the stock and fail the gate.
    """
    if 'branches' not in live_tables:
        return 0
    pending = conn.execute(
        "SELECT COUNT(*) FROM inventory_movements "
        "WHERE company_id=? AND branch_id IS NULL", (company_id,)
    ).fetchone()[0]
    if not pending:
        return 0
    branch_ids = [row[0] for row in conn.execute(
        "SELECT id FROM branches WHERE company_id=?", (company_id,)
    ).fetchall()]
    if len(branch_ids) != 1:
        return 0        # zero branches: nowhere to put them. Two or more: a guess.
    conn.execute(
        "UPDATE inventory_movements SET branch_id=? "
        "WHERE company_id=? AND branch_id IS NULL",
        (branch_ids[0], company_id),
    )
    return pending


def _v15_seed_opening_counts(conn, company_id, movement_columns, drifted):
    """One `opening_count` movement carrying the RESIDUAL for every key the
    ledger cannot yet explain. `drifted` is `compute_drift`'s output AFTER
    the NULL-branch resolve step.

    THE RESIDUAL, NOT THE ABSENCE OF HISTORY. This function originally seeded
    only keys with ZERO movements, and that was the single most dangerous
    defect this phase produced. The importer used to write an absolute
    `SET quantity_on_hand=?` with NO movement row (git 13c1c92,
    api/import_api.py:1146), and every sale since has written one -- so the
    COMMON legacy key has sales and no opening. It was skipped, the gate
    refused, `app.py`'s unconditional `init_retail()` meant the POS would not
    boot, and the refusal's own recovery advice then completed the disaster:
    `repair_drift` saw real ledger history (the sales), so
    SKIP_NO_LEDGER_HISTORY never fired, and it wrote the sales-only sum as
    the balance. Measured end to end: shelves [112, 57, 7] -> refused ->
    "repaired 3, skipped 0" -> shelves [-8, -3, -1] -> relaunch advances
    reporting zero drift. A shop's stock, destroyed by following the
    instructions.

    An opening count is "what must have been on the shelf before the recorded
    history, for that history to end where the balance says it does". That is
    the residual -- `quantity_on_hand` minus the ledger total at the key --
    by definition, and the zero-movement case is just its special case where
    the ledger total is zero. `_seed_retail` at the bottom of this file had
    the correct expression all along; this is the same one.

    The residual is `compute_drift`'s `drift`, so the candidates come FROM
    `compute_drift` rather than from a second copy of its key-set SQL. That
    is not merely tidy: the gate is evaluated on `compute_drift`'s output, so
    seeding from anything else would let the two disagree about which keys
    exist -- which is exactly how the zero-movement version got it wrong.

    ONLY NON-NEGATIVE RESIDUALS ARE SEEDED, and this is what keeps the gate
    real rather than softening it into decoration. A NEGATIVE residual says
    the ledger accounts for MORE goods than the shelf shows, and no opening
    count explains that: "the shop began with minus eight units" is not a
    fact about a shop. It is shrinkage, theft, or a writer that decremented a
    balance without recording why -- a human decision, not a migration's
    inference. Those rows fall through to the gate and are refused, and
    `repair_drift` can resolve them because doing so RAISES the balance to a
    total the ledger can prove arrived.

    Idempotent by construction, still: after this run the residual at every
    seeded key is zero, so a second run's `compute_drift` yields nothing to
    seed. Nothing is keyed off the seeded rows' own marker, so a shop that
    later posts a real movement is unaffected either way.

    Zero-quantity balances with NO history at all are seeded too, at zero.
    A zero balance with no movements behind it is not "counted, found
    nothing" -- it is "never counted", and after v15 the difference has to be
    visible in the ledger rather than inferred from an absence. It is also
    what stops `repair_drift` refusing the key forever for
    SKIP_NO_LEDGER_HISTORY. These keys have no drift, so `compute_drift`
    never reports them and they need the second query below.

    WHAT THE ROW DOES NOT CLAIM: `actor_user_uid` and `terminal_id` stay
    NULL, and `created_by` is the schema's existing non-person sentinel.
    Nobody performed this count and this device cannot prove which terminal
    would have. `created_at_utc` IS stamped, and the contrast with v13 --
    which left `created_at_utc` NULL on every history row it touched -- is
    deliberate rather than inconsistent: v13's rows already existed and their
    real instant was unknowable, while these rows are being created right
    now, so their creation time is a fact about them. `unit_cost` is left at
    its default: the shop's cost_price today is not evidence of what this
    stock cost when it arrived, and valuation reading a made-up figure is
    worse than valuation reading a zero it can see.
    """
    import uuid as _uuid
    from core.retail.stock_reconciliation import DEFAULT_TOLERANCE

    # `repairable` already excludes both structural impossibilities: a
    # NULL branch (no balance row could hold it) and a product row that is
    # gone (`inventory_movements.product_id` has a FOREIGN KEY to
    # products(id) and `_conn()` turns enforcement ON, so the INSERT below
    # would raise IntegrityError and kill the migration on "FOREIGN KEY
    # constraint failed" -- a message that says nothing about stock and
    # leaves the operator with a crashing app). See compute_drift.
    pending = {
        (row['product_id'], row['branch_id']): float(row['drift'])
        for row in drifted
        if row['repairable'] and row['drift'] > DEFAULT_TOLERANCE
    }

    # The never-counted keys `compute_drift` cannot report, because they do
    # not drift: a balance of zero with no ledger history at all.
    #
    # `branch_id IS b.branch_id` rather than `=`: movements' branch_id is
    # nullable, and `=` against NULL is NULL (never true), which would make
    # an unresolved NULL-branch movement look like "no history" and earn the
    # balance a duplicate opening count on every single run.
    for product_id, branch_id, quantity in conn.execute(
        "SELECT b.product_id, b.branch_id, b.quantity_on_hand "
        "FROM inventory_balances b "
        "WHERE b.company_id=? "
        "  AND NOT EXISTS ("
        "      SELECT 1 FROM inventory_movements m "
        "      WHERE m.company_id=b.company_id "
        "        AND m.product_id=b.product_id "
        "        AND m.branch_id IS b.branch_id)"
        "  AND EXISTS (SELECT 1 FROM products p WHERE p.id=b.product_id)",
        (company_id,),
    ).fetchall():
        key = (product_id, branch_id)
        if key in pending:
            continue        # already carrying its residual from the drift set
        value = float(quantity or 0)
        if value < 0:
            continue        # a negative opening count is not an opening count
        pending[key] = value

    if not pending:
        return 0

    # ONE LAST PROBE, and it is not redundant with `repairable` above.
    #
    # `inventory_movements.product_id` has a FOREIGN KEY to products(id) and
    # `_conn()` turns enforcement ON, so an INSERT for a product that is gone
    # raises IntegrityError and kills the whole migration on "FOREIGN KEY
    # constraint failed" -- a message that says nothing about stock and
    # leaves the operator with an app that will not start and no idea why.
    # The `repairable` filter already excludes those keys, so this can only
    # fire if `compute_drift` and this function ever disagree about what a
    # missing product is. That is exactly the kind of coupling that goes
    # quietly wrong later, and a migration is the worst possible place to
    # find out: the cost of being wrong here is a crash loop, the cost of the
    # probe is one indexed lookup per candidate product. A test that
    # deliberately mis-classifies an orphan as repairable found this.
    candidates = sorted({key[0] for key in pending}, key=str)
    live = set()
    for start in range(0, len(candidates), _V15_PRODUCT_PROBE_CHUNK):
        chunk = candidates[start:start + _V15_PRODUCT_PROBE_CHUNK]
        live.update(row[0] for row in conn.execute(
            f"SELECT id FROM products WHERE id IN ({','.join('?' * len(chunk))})",
            tuple(chunk)).fetchall())
    pending = {key: value for key, value in pending.items() if key[0] in live}

    if not pending:
        return 0

    columns = ['company_id', 'product_id', 'branch_id', 'movement_type',
               'quantity', 'reference', 'notes', 'created_by']
    # v13 columns, present on every real install by the time this runs (v13 is
    # earlier in the same chain) but absent from hand-built minimal fixtures,
    # so they are added only when the live table actually has them.
    optional = [c for c in ('uid', 'created_at_utc') if c in movement_columns]
    columns.extend(optional)
    sql = (f"INSERT INTO inventory_movements ({','.join(columns)}) "
           f"VALUES ({','.join('?' * len(columns))})")

    stamp = datetime.now(timezone.utc).isoformat()
    rows = []
    # Sorted so a run is reproducible and two installs with the same data
    # seed in the same order -- dict iteration order is insertion order here,
    # which is compute_drift's ORDER BY followed by the second query's, and
    # depending on that would be depending on an implementation detail.
    #
    # `str(product_id)` because v13 moved products off autoincrement ids onto
    # uuids and a database mid-migration can hold both, which sort against
    # each other with a TypeError rather than an ordering. `branch_id` cannot
    # be NULL among these candidates (both sources exclude it) but is
    # defended anyway: a sort key is a terrible place to learn otherwise.
    for (product_id, branch_id), quantity in sorted(
            pending.items(),
            key=lambda item: (str(item[0][0]),
                              -1 if item[0][1] is None else item[0][1])):
        values = [company_id, product_id, branch_id, V15_OPENING_COUNT_TYPE,
                  quantity, V15_OPENING_COUNT_REFERENCE,
                  V15_OPENING_COUNT_REASON, 'System']
        for column in optional:
            # A real RFC-4122 uuid4 per row, generated in Python -- never
            # SQLite's lower(hex(randomblob(16))), which is 32 characters of
            # something that is not a UUID and that Owner's sync ingest
            # rejects at the point it is most expensive to discover. Same
            # decision, same reason, as v13's uid backfill.
            values.append(str(_uuid.uuid4()) if column == 'uid' else stamp)
        rows.append(tuple(values))

    for start in range(0, len(rows), _V15_SEED_CHUNK):
        conn.executemany(sql, rows[start:start + _V15_SEED_CHUNK])
    return len(rows)


def _v15_record_outcome(conn, company_id, summary, live_tables):
    """Write what v15 did, and what it deliberately did not do, to audit_log.

    `user_id` is NULL on purpose: no person seeded these counts or declined
    to assign those branches, and audit_log's `user_id` is the one place a
    reader looks to find out who did. The unassigned and orphaned counts are
    recorded here because "the gate passed" and "the gate passed while
    excluding four rows it could not place and two whose products no longer
    exist" are different facts, and only one of them is true.

    An observation, not state: re-running the step (which only happens while
    the version marker is still behind) appends a second observation rather
    than editing the first. The ledger, the balances and the branch
    assignments are what must be idempotent, and they are.
    """
    if 'audit_log' not in live_tables:
        return
    if not (summary['seeded'] or summary['branches_resolved']
            or summary['unassigned'] or summary['orphaned']):
        return
    import json as _json
    conn.execute(
        "INSERT INTO audit_log (company_id,user_id,action,entity,entity_id,details) "
        "VALUES (?,?,?,?,?,?)",
        (company_id, None, 'STOCK_LEDGER_SEEDED_V15', 'inventory_movements', None,
         _json.dumps(summary, sort_keys=True, default=str)),
    )


def _v15_gate_message(refusals):
    """The refusal, written to be acted on.

    "Migration failed" is not a report about a shop's stock. Every company
    that could not be reconciled is named, with the rows that could not be,
    and with the one recovery that is supported -- because an operator who
    reaches this cannot open the app to look, and the message is all they
    have.
    """
    parts = []
    for company_id, blocking, summary in refusals:
        net = round(sum(row['drift'] for row in blocking), 4)
        offenders = '; '.join(
            f"{row['sku'] or row['product_id']} @ branch {row['branch_id']}: "
            f"balance {row['stored_balance']} vs ledger {row['ledger_balance']} "
            f"(drift {round(row['drift'], 4)})"
            for row in blocking[:_V15_REPORTED_OFFENDERS]
        )
        more = ('' if len(blocking) <= _V15_REPORTED_OFFENDERS
                else f" ...and {len(blocking) - _V15_REPORTED_OFFENDERS} more")
        parts.append(
            f"company {company_id}: {len(blocking)} (product, branch) balance(s) "
            f"account for LESS stock than inventory_movements records arriving, "
            f"net drift {net} [{offenders}{more}]; "
            f"{summary['seeded']} opening count(s) seeded and committed; "
            f"recover with core.retail.stock_reconciliation.repair_drift(conn, "
            f"{company_id!r})"
        )
    return (
        "Retail schema v15 refused to advance PRAGMA user_version. "
        + ' | '.join(parts) + ". "
        "This is NOT a broken migration, and it is not the ordinary legacy shape "
        "either -- v15 already inferred and seeded an opening count for every key "
        "whose balance merely exceeded what the ledger knew about. What is left is "
        "the opposite: the ledger records MORE goods arriving than the shelf claims "
        "to hold, and no opening count explains that, because a shop cannot have "
        "started with less than nothing. It is shrinkage, loss, or a writer that "
        "decremented a balance without recording why -- the inconsistency Phase 3 "
        "exists to surface and the one multi-device sync must never spread. "
        "The repair above RAISES each of these balances to the total the ledger can "
        "prove arrived (it takes its own transaction and writes its own audit row) "
        "and destroys no stock; if the goods really are gone, post an adjustment "
        "movement for the loss instead, which is the honest record. Either way, "
        "relaunch afterwards and v15 will re-run and pass. Do not relax this check: "
        "it converts a detected problem into an undetected one."
    )


def _migrate_seed_opening_counts_and_gate_drift(conn):
    """One-time migration (schema v15): make `inventory_balances` provably
    derivable from `inventory_movements` -- launch-readiness Phase 3
    (docs/launch-readiness/phase3-ledger-truth.md), reserved in ROADMAP.md's
    2026-08-21 ledger.

    Additive only, like v13 and v14: one INSERT per unbacked balance, one
    guarded UPDATE for unambiguous NULL branches, one CREATE INDEX IF NOT
    EXISTS. No table is rebuilt, no row is deleted, no existing quantity is
    rewritten -- in particular this step never touches
    `inventory_balances.quantity_on_hand`. It moves the LEDGER to explain the
    cache, never the cache to match the ledger: overwriting a balance is a
    repair, repair destroys the evidence that a writer is broken, and repair
    is owner-initiated by design (see core/retail/stock_reconciliation.py's
    module docstring).

    THE ORDER OF THE FIVE STEPS IS THE DESIGN:

    1. measure BEFORE, so the run can say what it found rather than only what
       it left;
    2. resolve NULL branches where one branch makes it unambiguous;
    3. RE-measure, because step 2 moved ledger history onto a branch and so
       changed the residual there;
    4. seed one opening count carrying that residual per unexplained key;
    5. measure AFTER and GATE.

    STEPS 2 AND 3 ARE NOT INTERCHANGEABLE WITH 4. Resolving a NULL-branch
    movement onto branch B gives (product, B) ledger history it did not have;
    seeding first would have written an opening count computed without it,
    and the two would sum to the wrong stock and fail the gate. The
    re-measure between them exists for the same reason at the level of the
    number rather than the key.

    STEP 2 IS THE ONLY NON-ADDITIVE WRITE IN THE MIGRATION, and it earns its
    place. A verifier found that on a single-branch shop with pre-branch NULL
    history it was the step that caused the refusal -- but that was true only
    while step 4 seeded the ABSENCE of history rather than the residual: the
    resolve gave the key history, the old seeder therefore skipped it, and
    the gate refused a shop whose answer was never in doubt. With the
    residual it is the resolve that makes the shop CORRECT rather than merely
    unblocked: the sales land on the one branch they can have happened at,
    the opening count is computed against them, and the stock is intact.
    Deleting the step would also have worked -- every NULL row to the queue,
    still advancing, still simpler -- and it was rejected because it is worse
    for the common install: a single-branch shop is the overwhelmingly likely
    legacy shape, and it would be handed a permanent "needs assignment" queue
    whose every entry has exactly one possible answer. A queue nobody can
    resolve wrongly is not a safety feature, it is a chore.

    THE GATE IS ZERO DRIFT AMONG REPAIRABLE ROWS, NOT ZERO DRIFT.
    `compute_drift` reports `repairable: False` for the two STRUCTURAL
    impossibilities -- a movement whose branch_id is still NULL after step 2
    (`inventory_balances.branch_id` is NOT NULL, so no balance row could ever
    hold it) and a balance whose product row is gone
    (`inventory_movements.product_id` has a FOREIGN KEY to products(id), so
    no movement row can ever be written for it). A gate demanding global zero
    would mean an install carrying one such row could never advance its
    schema version AGAIN -- not for v15, not for anything after it. That is
    the permanent wedge the v13 duplicate-uid defect produced, and the orphan
    balance reached it from a third direction: the seeder skipped the row,
    the gate refused over it, and `repair_drift` refused it too, so three
    measured launch/repair/launch cycles left `user_version` at 14, 14, 14
    with nothing an operator could do. Such an install advances here carrying
    counted, recorded queues (`stock_reconciliation.unassigned_movements` and
    `orphan_balances`) instead of a guess or a brick.

    WHAT THE GATE STILL REFUSES, and it must: a key whose residual is
    NEGATIVE -- the ledger accounts for more goods than the shelf shows. No
    opening count explains that (a shop cannot begin with less than nothing),
    so `_v15_seed_opening_counts` deliberately leaves it alone and it reaches
    here. Softening this into "seed whatever closes the gap" would make the
    gate incapable of failing, which is the opposite failure and just as bad.

    WHY THE COMMIT IS WHERE IT IS. The seeding is committed BEFORE the gate
    can raise, which looks like a violation of "leave the database as the
    backup found it" and is the opposite. `ensure_schema_version` does not
    commit on the failure path, so without this the seeded opening counts
    would be discarded, and the recovery this failure names would run against
    a ledger that had been rolled back to silence. Seeding first, durably, is
    half of what makes `repair_drift` safe to point an operator at; the other
    half is `SKIP_LEDGER_NOT_ESTABLISHED`, which refuses to LOWER a balance
    at all until this gate has passed, so that even an operator who runs the
    repair on an install v15 has never touched cannot lose stock by it.
    Because every write here is idempotent and additive, committing it costs
    nothing if the next launch retries.

    Existence-guarded: some fixtures hand-build a minimal schema and call
    `_migrate_retail_schema` directly (see
    retail_category_delete_fk_sync_test.py, which builds no
    `inventory_balances` at all), and a migration that assumed the full
    schema would crash them.

    Returns {company_id: summary} for the caller that wants to report what
    happened; `_migrate_retail_schema` ignores it, and the durable record is
    the audit_log row (`_v15_record_outcome`).
    """
    live_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    # All four, not just the two this step writes: `compute_drift` LEFT JOINs
    # `products` and `branches` to name the rows it reports, so the gate
    # cannot run without them either. Every real install has all four from
    # `_init_retail`'s base executescript; what this guard is for is the
    # hand-built minimal fixtures that call `_migrate_retail_schema`
    # directly (retail_category_delete_fk_sync_test.py builds only
    # categories/products/inventory_movements), where skipping is the
    # correct answer rather than a crash.
    if not {'inventory_balances', 'inventory_movements',
            'products', 'branches'} <= live_tables:
        return {}

    # `compute_drift` reads its rows by NAME. Every real caller comes through
    # `get_retail_conn()` (row_factory = sqlite3.Row), but a migration that
    # only works on a connection configured a particular way is a trap for
    # the next person calling it directly, so the factory is set here and
    # restored afterwards rather than assumed.
    from core.retail.stock_reconciliation import (
        SKIP_NO_BRANCH, SKIP_NO_PRODUCT, compute_drift)
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        # Both sides of `compute_drift`'s UNION, plus this step's own
        # NOT EXISTS probe and the importer's already-declared sum, look
        # rows up by exactly this triple and there was no index on it.
        # Created here rather than in the base executescript so a pre-v15
        # install gets it too.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_inventory_movements_key "
            "ON inventory_movements(company_id, product_id, branch_id)"
        )
        movement_columns = {
            row[1] for row in conn.execute('PRAGMA table_info("inventory_movements")').fetchall()
        }

        outcomes, refusals = {}, []
        for company_id in _v15_company_ids(conn):
            before = compute_drift(conn, company_id)
            resolved = _v15_resolve_unambiguous_null_branches(conn, company_id, live_tables)
            # Re-measured AFTER the resolve, because the resolve MOVES ledger
            # history onto a branch and therefore changes the residual at
            # that key. Seeding from `before` would seed the pre-resolve
            # figure and double-count by exactly the resolved rows.
            resolvable = compute_drift(conn, company_id)
            seeded = _v15_seed_opening_counts(
                conn, company_id, movement_columns, resolvable)
            after = compute_drift(conn, company_id)

            blocking = [row for row in after if row['repairable']]
            unassigned = [row for row in after
                          if row['blocked_reason'] == SKIP_NO_BRANCH]
            orphaned = [row for row in after
                        if row['blocked_reason'] == SKIP_NO_PRODUCT]
            summary = {
                'company_id': company_id,
                'drift_rows_before': len(before),
                'net_drift_before': round(sum(row['drift'] for row in before), 4),
                'branches_resolved': resolved,
                'seeded': seeded,
                'unassigned': len(unassigned),
                'net_drift_unassigned': round(sum(row['drift'] for row in unassigned), 4),
                'orphaned': len(orphaned),
                'net_drift_orphaned': round(sum(row['drift'] for row in orphaned), 4),
                'drift_rows_after': len(blocking),
            }
            _v15_record_outcome(conn, company_id, summary, live_tables)
            outcomes[company_id] = summary

            if blocking:
                # Collected, not raised here. One install CAN host more than
                # one company, and stopping at the first refusal would leave
                # every later company's seeding undone and its drift
                # undiscovered until the operator had fixed this one and
                # relaunched -- learning about the next shop one relaunch at
                # a time. Every company's honest work lands; the refusal at
                # the end names all of them.
                refusals.append((company_id, blocking, summary))

        # Durable BEFORE any refusal -- see this function's docstring. This
        # commit is what makes the recovery the message recommends safe.
        conn.commit()
        if refusals:
            raise RetailLedgerDriftError(_v15_gate_message(refusals))
        return outcomes
    finally:
        conn.row_factory = previous_factory


# ── v16: the terminal-bound cash drawer ─────────────────────────────────────
#
# The status vocabulary, as module constants rather than string literals
# scattered through this file and api/retail_api.py. `open` and `closed` are
# what schema v10 already wrote; `ended` is new here and is the half of the
# split that did not previously exist.
#
#   open    -- trading. At most one per (company_id, terminal_id), enforced by
#              a real partial UNIQUE index rather than an application check.
#   ended   -- the drawer has stopped trading. Either somebody counted it, or
#              v16 ended it because it had run past its business date or was
#              standing between another shift and the terminal it is bound to.
#              An ENDED session is NOT a claim that anybody accepted what the
#              count found.
#   closed  -- the variance has been ACCEPTED, which is a separate authority
#              (`retail.cash.approve`, CAP_CASH_APPROVE) deliberately withheld
#              from every role that can close a drawer.
#
# Pre-v16 rows keep the `closed` they were written with. Under that model
# closing WAS counting, there was no approval step to have skipped, and
# rewriting years of a shop's closed shifts to record the absence of an
# authority nobody was ever asked for would be a fabrication in the opposite
# direction from the one v13 refused. See _migrate_bind_cash_drawer_to_terminal.
CASH_SESSION_STATUS_OPEN = 'open'
CASH_SESSION_STATUS_ENDED = 'ended'
CASH_SESSION_STATUS_CLOSED = 'closed'

#: The constraint v16 exists to install: at most one OPEN drawer per terminal.
V16_TERMINAL_INDEX = 'idx_cash_sessions_one_open_per_terminal'

#: The v10 constraint it replaces: at most one OPEN drawer per BRANCH.
#:
#: Two steps in this file now reference it and they have to stay in step.
#: `_migrate_add_shift_cash_drawer` creates it, but only while
#: `_v10_branch_index_is_superseded` says no terminal-bound index exists yet;
#: v16 drops it, but only after that terminal-bound index is in place. Written
#: that way round because the chain is re-entered from the TOP on every future
#: migration, so v10 runs again on a v16 database -- and by then two open
#: drawers on one branch are legal, which an unconditional recreate would
#: refuse at boot. Either half alone is not idempotent; the pair is.
V16_SUPERSEDED_BRANCH_INDEX = 'idx_cash_sessions_one_open_per_branch'

#: `ended_by` on a shift the migration ended. The same non-person sentinel
#: v15 stamps on the opening counts it seeds (`created_by='System'`) and for
#: the same reason: no human performed this act, and the column a reader looks
#: at to find out who did must not name one.
V16_ENDED_BY_SYSTEM = 'System'

#: The drawer was still open on a business date that has since passed. The
#: value is stored on the row, not merely logged, because a number in a cash
#: ledger with no explanation attached is exactly how the next person
#: concludes somebody counted this drawer.
V16_ENDED_REASON_STALE = 'unverified_stale_business_date'

#: The drawer was one of two or more open on a single terminal, and was not
#: the most recently opened of them. See _migrate_bind_cash_drawer_to_terminal
#: for why the newest is the one that keeps the terminal.
V16_ENDED_REASON_COLLISION = 'unverified_terminal_collision'

#: Both force-end reasons, for consumers that need to ask "was this shift
#: ended by a person or by the migration?" without matching on strings.
#: Every value starts `unverified_` on purpose: an ENDED row carrying one of
#: these has NULL `closing_float_counted` and NULL `variance`, and the two
#: statements -- the reason text and the absent figures -- have to agree.
V16_UNVERIFIED_END_REASONS = frozenset({
    V16_ENDED_REASON_STALE, V16_ENDED_REASON_COLLISION,
})

#: audit_log action for a shift v16 ended. One row per session, matching
#: api/retail_api.py's own CASH_SESSION_OPENED / CASH_SESSION_CLOSED trail,
#: because "where did my open drawer go?" is a question asked about ONE
#: drawer and answered from the audit screen.
V16_FORCE_END_AUDIT_ACTION = 'CASH_SESSION_FORCE_ENDED_V16'

#: Accepted spellings for a stored timestamp, tried after `fromisoformat`.
#: `opened_at` is written by api/retail_api.py::_now() as
#: '%Y-%m-%d %H:%M:%S', but the column also carries SQLite's own
#: DEFAULT CURRENT_TIMESTAMP for any row inserted without one, and a database
#: restored from an export can carry either. A migration is the wrong place to
#: be strict about a format nobody promised.
_V16_TIMESTAMP_FORMATS = (
    '%Y-%m-%d %H:%M:%S.%f',
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%d %H:%M',
    '%Y-%m-%d',
)


def _v16_parse_timestamp(raw):
    """A stored timestamp as a NAIVE local `datetime`, or None if it cannot be
    read as one.

    Always naive, never aware, even when the stored text carries an offset:
    an aware value is converted onto this machine's own zone and then
    stripped. Two reasons, and the second is the one that bites. First, every
    other timestamp in this table is device-local wall clock (`_now()`), so a
    naive local value is the shape the rest of the code already means.
    Second, `datetime` refuses to order an aware value against a naive one
    with a TypeError, and the one place these get compared is the sort that
    decides which of two open drawers keeps a terminal -- a migration failing
    on `can't compare offset-naive and offset-aware datetimes` would be an
    app that will not start, over a tie-break.

    None rather than a raise on unreadable input. The caller treats "cannot
    determine" as "do not force-end this shift", which is the conservative
    direction: ending a drawer is the act that needs justification, so an
    unreadable timestamp must not be able to cause one.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    parsed = None
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        for fmt in _V16_TIMESTAMP_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _v16_business_date(conn, company_id, moment):
    """`moment` as this company's BUSINESS date, or None if it cannot be
    placed on that clock.

    Delegates to core/retail/metrics.py::business_now rather than reimplementing
    the shop-clock rule. That function already handles the whole of it -- the
    declared IANA `business_timezone`, the `business_day_start_hour` a shop
    trading past midnight sets, an undeclared shop falling back to device
    local time, and a `retail_settings` table that does not exist yet -- and a
    second copy here would be a second thing to keep in step with a rule this
    project has already had to correct once (see that function's docstring for
    the off-by-one it shipped).

    `business_now` names its argument `now`, but nothing in it is specific to
    the present: it takes any instant and moves it onto the business clock.
    Passing a historical `opened_at` through the SAME transform as the current
    time is the entire point -- comparing a raw stored timestamp against a
    business-clock "today" is how a comparison ends up half on one clock and
    half on the other.

    The one inexactness, stated rather than hidden: attaching a device zone to
    a naive historical timestamp uses the offset in force NOW, not the offset
    in force on the day it was written, so a row from the other side of a
    daylight-saving change can be off by an hour. The caller compares whole
    DATES with a strict `<`, so an hour cannot move a shift opened minutes ago
    across a day boundary, and a shift open since last year is over that
    boundary by hundreds of hours.
    """
    if moment is None:
        return None
    try:
        from core.retail import metrics
        return metrics.business_now(conn, company_id, moment).date()
    except Exception:
        # A shop whose clock cannot be resolved is not a shop whose drawers
        # should be force-ended on a guess. `business_day` already degrades to
        # "unconfigured" for the ordinary causes (no `retail_settings` table,
        # an unparseable zone name, a nonsense start hour), so what reaches
        # here is unexpected -- and the safe answer to an unexpected clock is
        # to leave the shift alone and let the collision pass below, which
        # needs no clock at all, do whatever is still necessary.
        return None


def _v16_force_end(conn, session, reason, ended_at, live_tables):
    """End one shift the shop never ended, and say so on the row.

    WHAT IS WRITTEN: `status`, `ended_at`, `ended_by`, `ended_reason`. That is
    all. `opening_float` is untouched, `closing_float_counted`,
    `closing_float_expected` and `variance` stay NULL, `closed_at`/`closed_by`
    stay NULL. Every one of those absences is load-bearing:

      - a `variance` figure would be a claim that somebody counted this
        drawer and found it short or over. Nobody counted it. The reason text
        says `unverified_*` and the empty figures say the same thing
        structurally, so a consumer that reads either one gets the same
        answer.
      - `closed_at` staying NULL is what keeps ENDED distinguishable from
        CLOSED on a row this migration touched. A force-ended shift has not
        been through the approval question at all; filling in the close trail
        would launder it into a shift that had.

    `ended_at` IS stamped, with now, and the contrast with v13 -- which left
    `created_at_utc` NULL on every history row it touched -- is deliberate
    rather than inconsistent. v13's rows already existed and their real
    instant was unknowable. This END is happening right now: the migration is
    what is ending the shift, so the instant it happened at is a fact about
    it. It is not, and does not claim to be, the instant trading stopped --
    `ended_by='System'` and the reason on the row say who ended it and why.

    The audit row is per session, not per company, matching the
    CASH_SESSION_OPENED / CASH_SESSION_CLOSED trail api/retail_api.py already
    writes: "where did my open drawer go?" is asked about one drawer.
    `user_id` is NULL because no person did this, and `user_id` is the column
    a reader looks at to find out who did.
    """
    conn.execute(
        "UPDATE cash_sessions SET status=?, ended_at=?, ended_by=?, ended_reason=? "
        "WHERE id=?",
        (CASH_SESSION_STATUS_ENDED, ended_at, V16_ENDED_BY_SYSTEM, reason,
         session['id']),
    )
    if 'audit_log' not in live_tables:
        return
    import json as _json
    conn.execute(
        "INSERT INTO audit_log (company_id,user_id,action,entity,entity_id,details) "
        "VALUES (?,?,?,?,?,?)",
        (session['company_id'], None, V16_FORCE_END_AUDIT_ACTION, 'cash_session',
         session['id'],
         _json.dumps({
             'reason': reason,
             'branch_id': session['branch_id'],
             'terminal_id': session['terminal_id'],
             'opened_at': session['opened_at'],
             'opened_by': session['opened_by'],
             'opening_float': session['opening_float'],
             'ended_at': ended_at,
             'counted': False,
             'note': (
                 'Schema migration v16 ended this shift so the drawer could be '
                 'bound to a terminal. Nobody counted it, so no closing float '
                 'and no variance were recorded -- the money figures on the row '
                 'are exactly what they were before the migration ran.'
             ),
         }, sort_keys=True, default=str)),
    )


def _v16_terminal_id_is_blank(value):
    """True when `value` is not a usable terminal name: SQL NULL, or a string
    that is nothing but whitespace once Python's `str.strip()` is done with
    it.

    THE ONE DEFINITION OF "BLANK" in this migration -- used by step 4's
    normalisation and step 5's skip check alike. It used to be two.
    REPRODUCED: step 4 blanked with SQL `TRIM(terminal_id) = ''`, and
    SQLite's `TRIM` with no second argument strips only 0x20; step 5 skipped
    with `not str(value).strip()`, and Python's `str.strip()` strips every
    code point `str.isspace()` calls whitespace -- ASCII tab and newline
    among them, and U+00A0 NO-BREAK SPACE too. Measured directly: '' and
    '   ' agreed under both tests; '\t', '\t\t', '\xa0' and '\n' did not.

    Two open sessions both holding terminal_id='\t' are a GENUINE duplicate
    as far as SQLite's own unique index is concerned -- it compares two
    non-NULL strings byte for byte, and '\t' equals '\t' -- but the old step
    5 called them "unnameable" and left them unresolved, so
    `CREATE UNIQUE INDEX` a few lines below raised `UNIQUE constraint failed`
    out of `init_retail()`, which `app.py::init_app()` calls with no handler:
    a POS that will not start, `user_version` stuck at 15, every relaunch
    repeating the exact same failure forever.

    Deliberately Python's `str.strip()`, not SQL `TRIM`, and not a
    hand-rolled character class: it is the test step 5 already used, so
    making step 4 match it (rather than inventing a third definition) is
    what makes the probe, the resolution and SQLite agree without changing
    what "genuinely different terminals" means. '  X  ' and 'X' stay
    different terminals under this test exactly as before -- over-grouping
    those would end a live till to tidy up a difference SQLite was never
    going to object to.
    """
    return value is None or not str(value).strip()


def _v16_open_sessions(conn):
    """Every session still `status='open'`, newest first.

    Ordered here so the two passes below (stale sweep, collision resolution)
    both see the same order and neither has to re-derive it.
    """
    return conn.execute(
        "SELECT id, company_id, branch_id, opened_by, opened_at, opening_float, "
        "       terminal_id "
        "FROM cash_sessions WHERE status=? ORDER BY opened_at DESC, id DESC",
        (CASH_SESSION_STATUS_OPEN,),
    ).fetchall()


def _v16_terminal_index_is_correct(shape):
    """True when `shape` (from `_uid_index_shape`) is the ONE shape that makes
    the terminal binding a real constraint: UNIQUE, partial, over exactly
    (company_id, terminal_id).

    Read from `PRAGMA index_list`/`index_info` -- the shape SQLite actually
    stored -- rather than from `sqlite_master.sql`, which is the text somebody
    typed. That distinction is v13's F4 defect exactly: a
    `CREATE UNIQUE INDEX IF NOT EXISTS` whose name is already taken by a plain
    index leaves the plain index in place, the UNIQUE text nowhere, and the
    migration reporting success. The name is new in this file, so the only way
    to reach that state is a database touched by a branch this one has not
    seen -- which CLAUDE.md names as a routine condition of testing here, not
    a hypothetical.
    """
    return bool(
        shape is not None
        and shape['unique']
        and shape['partial']
        and shape['columns'] == ['company_id', 'terminal_id']
    )


def _v16_collision_probe(conn):
    """(company_id, terminal_id, count) for every terminal that still holds
    more than one OPEN session -- i.e. every row-group that would make
    `CREATE UNIQUE INDEX` raise.

    WRITTEN TO BE THE SAME QUESTION THE INDEX ASKS, not a tidier one, and the
    two ways of being different both cost something real.

    ASKING LESS than the index does means the create raises on a row this
    never reported. An earlier draft excluded `TRIM(terminal_id) = ''` on the
    reasoning that the migration normalises blanks to NULL before this runs,
    so they cannot reach here. True today, and still wrong: it made the probe
    blind to exactly the case -- two identical blank terminal ids -- that
    SQLite treats as a duplicate, so deleting the normalisation would have
    moved the failure from this function's actionable refusal to a raw
    `UNIQUE constraint failed` at boot. A backstop that cannot see the thing
    it backstops is decoration. So values group by their EXACT stored text,
    because that is how SQLite compares them: two rows holding '' collide, and
    a row holding '' and a row holding '   ' do not.

    ASKING MORE than the index does costs a shop a live till, because the
    caller resolves what this reports by ENDING a drawer. Hence BOTH key
    columns are required non-NULL, not just the terminal. SQL uniqueness
    treats a NULL as distinct from everything including another NULL, and that
    rule is per COLUMN OF THE KEY, not per table: two open sessions with a
    NULL `company_id` and the identical terminal id are NOT a duplicate as far
    as the index is concerned, while `GROUP BY company_id, terminal_id` groups
    them together and would have reported one. `cash_sessions.company_id` is
    nullable and always has been, so that is a row a legacy install can be
    holding -- and ending somebody's open drawer to satisfy a constraint that
    was never going to object to it is the worse of the two errors by a wide
    margin.
    """
    return conn.execute(
        "SELECT company_id, terminal_id, COUNT(*) AS sessions "
        "FROM cash_sessions "
        "WHERE status=? AND terminal_id IS NOT NULL AND company_id IS NOT NULL "
        "GROUP BY company_id, terminal_id HAVING COUNT(*) > 1",
        (CASH_SESSION_STATUS_OPEN,),
    ).fetchall()


def _v16_bind_message(collisions):
    """The refusal, written to be acted on rather than merely logged.

    An operator who reaches this cannot open the app to look at anything, so
    every terminal, every count and the one supported action have to be in the
    string itself.
    """
    parts = [
        f"company {row['company_id']} terminal {row['terminal_id']}: "
        f"{row['sessions']} sessions still open"
        for row in collisions
    ]
    return (
        "Retail schema v16 refused to bind the cash drawer to a terminal: "
        + '; '.join(parts) + ". "
        "v16 resolves this before it builds the index -- reaching it means the "
        "resolution pass and the check that follows it disagree about what a "
        "collision is, which is a defect in database/schema.py rather than a "
        "state your shop can be in. Nothing was changed: PRAGMA user_version "
        "was not advanced and this migration's writes were not committed, so "
        "the next launch retries from the same point. Recovery, if this is "
        "ever seen in the field, is to end all but one of the sessions named "
        "above (UPDATE cash_sessions SET status='ended' -- do NOT delete them, "
        "they hold a float somebody put in a drawer) and relaunch."
    )


def _index_owning_table(conn, index_name):
    """The table `sqlite_master` says owns an index called `index_name`,
    database-WIDE, or None if no such index exists at all.

    Index names are a single global namespace in SQLite -- unlike a column or
    a table name, an index does not belong to the table it indexes as far as
    naming goes. `PRAGMA index_list(<table>)`, which `_uid_index_shape`
    reads, can only ever answer "is there an index by this name ON THIS
    TABLE"; it is blind to the identical name sitting on some OTHER table,
    because that pragma only lists the one table's indexes. `CREATE INDEX` is
    not blind to it: SQLite raises `index ... already exists` regardless of
    which table's CREATE statement asks.

    REPRODUCED: a plain `CREATE INDEX idx_cash_sessions_one_open_per_terminal
    ON sales(company_id)` on a real v15 database. `_uid_index_shape(conn,
    'cash_sessions', V16_TERMINAL_INDEX)` correctly returns None (the name is
    not on `cash_sessions`), so the drop-and-rebuild branch below is skipped
    and the bare `CREATE UNIQUE INDEX` raises `OperationalError` straight out
    of `init_retail()` -- naming no shop, no shift and no recovery, on a path
    `RetailCashDrawerBindError` exists to cover and did not. Checking here,
    by NAME, database-wide, before that CREATE runs, is what lets this
    migration raise its own named, actionable refusal instead.
    """
    row = conn.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchone()
    return row[0] if row else None


def _v16_run_index_ddl(conn, sql):
    """Runs exactly one DDL statement for step 6 -- the CREATE of the
    terminal-bound index, or the DROP of the branch-bound one it replaces.

    Broken out into its own function purely so a test can interrupt EXACTLY
    between the two statements without needing to fake a raw SQL substring
    match, and without caring which of the two is coded first in
    `_migrate_bind_cash_drawer_to_terminal` -- see that function's step 6 for
    why the order between them is not decorative.

    THE ORDER MATTERS BECAUSE OF WHAT THIS FUNCTION DOES NOT DO: it does not
    wrap `conn.execute` in any transaction control of its own. `CREATE INDEX`
    and `DROP INDEX` are DDL, and Python's `sqlite3` module auto-commits DDL
    the instant it runs, independent of -- and before -- the DML transaction
    the rest of this migration's `UPDATE`s sit inside (see
    `RetailCashDrawerBindError`'s docstring for what that already means for
    the `ALTER TABLE` columns in step 1). So whichever of the two statements
    executes first is durably on disk before the second one gets a chance to
    run at all, and an interruption between them leaves the table with
    whichever ONE of the two constraints ran first -- never neither, and only
    if the safer one (CREATE the new index) is the one coded first. Swap the
    two calls in `_migrate_bind_cash_drawer_to_terminal` and an interruption
    here instead leaves NEITHER constraint standing, because the DROP (now
    first) durably removes the old one before the CREATE (now second, and the
    one that got interrupted) ever adds the new one.
    """
    conn.execute(sql)


def _migrate_bind_cash_drawer_to_terminal(conn):
    """One-time migration (schema v16): the cash drawer stops belonging to a
    BRANCH and starts belonging to a TERMINAL -- launch-readiness Phase 4,
    reserved in ROADMAP.md's 2026-08-21 ledger.

    THE DEFECT THIS FIXES IS PRESENT TODAY, WITH NO SYNC INVOLVED. schema v10
    put a partial UNIQUE index on (company_id, branch_id) WHERE status='open',
    so one branch gets one drawer. A shop with a desktop till and a phone at
    the same counter cannot open a second drawer at all, and every sale the
    phone rings is stamped with the session that IS open on that branch --
    the desktop's (api/retail_api.py::_open_cash_session_id looks the session
    up by company+branch+status, nothing else). The Z report at close then
    reconciles one physical cash drawer against takings that were never in it,
    and the variance it reports is arithmetic on two different tills.

    THIS IS THE FIRST NON-ADDITIVE STEP IN THIS FILE'S CHAIN, and the whole
    shape of the function follows from that. `CREATE UNIQUE INDEX` is
    evaluated against the rows that are already there. On an install that
    already holds two sessions that would collide, it raises -- and
    `app.py::init_app()` calls `init_retail()` unconditionally with no
    handler, so a raise there is a POS that will not start. That is not a
    hypothetical failure mode in this repository; it is what the v15 gate did
    to a real shop. So THE CONFLICT IS FOUND AND RESOLVED BEFORE THE INDEX IS
    BUILT. The index never discovers anything.

    THE SIX STEPS, AND WHY THEY ARE IN THIS ORDER:

    1. ADD the three columns -- `ended_at`, `ended_by`, `ended_reason`.
       Guarded on `PRAGMA table_info`, so a retry is a no-op. `terminal_id`
       itself is NOT added here: it is v13's column, and its absence means
       something this step must decline rather than repair -- see the comment
       at the early return above the ALTERs.

    2. RESTATE the counting instant on rows that are already `closed`:
       `ended_at` := `closed_at`, `ended_by` := `closed_by`. Under the pre-v16
       model, closing WAS counting; that instant and that person are facts the
       row already carries, moved into the columns the ENDED/CLOSED split
       reads. Nothing is invented and nothing is destroyed -- `closed_at` and
       `closed_by` stay exactly where they were. `status` is NOT rewritten to
       'ended': see the note below on why.

    3. FORCE-END every open session whose business date has passed. This step
       is FIRST among the three that touch open rows, and that ordering does
       real work: on the ordinary legacy install -- a shop with a shift
       somebody forgot to close months ago, or one per branch -- this alone
       removes the collision, so the terminal backfill lands on a single
       genuinely-live drawer and step 5 finds nothing to resolve.

    4. BIND the remaining open sessions to this device. Blank terminal ids are
       normalised to NULL first (SQLite's unique index treats two NULLs as
       distinct and two empty strings as equal, so a blank is a collision
       where a NULL is not), then `local_terminal_id()` fills every open row
       that has none. That function is `peek_local_device_uuid()` -- the same
       install-stable identity `device_registry.devices` is keyed by, the same
       one `_stamp()` already writes at open time, and deliberately the `peek`
       variant, which never CREATES the identity file. A migration is not a
       reason to manufacture an install identity as a side effect.

       CLOSED HISTORY IS NOT BACKFILLED, for v13's reason exactly: this device
       cannot prove it is the till that held a drawer from before the column
       existed, and a wrong terminal on a closed shift is worse than no
       terminal, because it is the only surviving evidence about where that
       money was. An OPEN drawer is a different claim -- it is open HERE, NOW,
       on this install, with its float on this counter.

       WHEN `local_terminal_id()` RETURNS None -- a build with no local device
       record, or a stripped Android build with no `device_context` at all --
       the open rows keep their NULL and STAY OPEN. The index is still created
       and still binds every drawer that does have a terminal; NULLs simply do
       not participate. Ending a shop's live drawer because this build cannot
       name its own terminal would be destroying real bookkeeping to enforce a
       constraint that has nothing to say about it.

    5. RESOLVE any collision that survives: a terminal holding two or more
       open sessions keeps the MOST RECENTLY OPENED and the rest are
       force-ended. Newest-wins because the drawer a cashier is standing at is
       the one they opened last; the older ones are shifts nobody ended, which
       is the same fact step 3 acts on, reached from a different direction. A
       session whose `opened_at` cannot be parsed sorts oldest -- a drawer
       whose open instant is unreadable is the worst candidate for "the one
       currently in use" -- and the id breaks ties so two installs with the
       same data resolve the same way.

    6. CREATE the new index, then DROP the old one -- in that order, so the
       table is never left with neither. An interruption between them leaves
       the v10 branch constraint standing, which is the wrong constraint but
       not the absence of one, and the next launch finishes the job.

       THE DROP IS NOT THE END OF IT. `ensure_schema_version` re-enters this
       chain from the TOP whenever `user_version` is behind, so v10 runs again
       on every future migration -- on a database where two open drawers on
       one branch are now legal and expected. Recreating a branch-bound unique
       index over them raises, out of `init_retail()`, on precisely the shops
       that adopted this feature. `_v10_branch_index_is_superseded` is what
       stops that, and it lives beside the CREATE it guards rather than here.

    WHY FORCE-ENDING RATHER THAN ANY OF THE ALTERNATIVES. A session that must
    be moved aside holds a float somebody physically put in a drawer, so:

      - it is never DELETED. The row, its float, its `opened_by` and its
        `cash_movements` all survive untouched.
      - it is never CLOSED. `status='closed'` after v16 means a variance was
        accepted, and nothing about this shift was ever counted, let alone
        accepted.
      - it is not LEFT OPEN WITH A NULL TERMINAL to dodge the constraint. That
        looks like the gentlest option and is the worst: NULLs are distinct in
        a unique index, so any number of such rows could accumulate, and
        `_open_cash_session_id` -- which matches on company+branch+status and
        never looks at `terminal_id` -- would keep folding sales into them.
        The pass condition would BE the bug signature.

      It is ENDED, with `ended_reason` naming why, `ended_by='System'` naming
      that no person did it, and `closing_float_counted`/`variance` left NULL
      because nobody counted it. A shift nobody ended is a fact about the
      shop; recording it as a clean close would be hiding it.

    WHY PRE-v16 `closed` ROWS KEEP `status='closed'`. Under the new split,
    CLOSED means the variance was accepted. Those historical rows were never
    put to an approver, because no approval step existed when they were
    written. Both available stories about them are therefore imperfect, and
    they are not equally so: leaving them CLOSED overstates an approval nobody
    was asked for, while rewriting thousands of a shop's settled shifts to
    'ended' is a non-additive DML pass over years of financial history that
    changes what every existing report and screen shows, to record the absence
    of an authority that did not exist -- and it would leave a permanent queue
    of shifts nobody will ever approve. The restatement in step 2 is what
    makes the difference legible without rewriting anything: those rows carry
    `ended_at == closed_at`, which is precisely "counted and closed in one
    act", the pre-v16 model stated honestly.

    IDEMPOTENT AND RESUMABLE. Every ADD COLUMN is guarded on
    `PRAGMA table_info`; step 2 only touches rows whose `ended_at` is still
    NULL; steps 3 and 5 only look at rows that are still `open`, and a row
    they have already ended is not; step 6 uses IF NOT EXISTS / IF EXISTS and
    verifies the shape it left rather than the name. `ensure_schema_version`
    leaves `user_version` un-advanced on any failure and the whole chain
    re-runs on the next launch, so a run interrupted anywhere above resumes
    from wherever it stopped and reaches the same end state.

    APPENDED LAST in `_migrate_retail_schema`, after v15's gate. On an install
    v15 refuses, this never runs -- which is correct rather than a silent
    skip: `user_version` does not advance either, the app does not start, and
    the next launch re-runs the entire chain from the top. There is no state
    in which the marker claims 16 while this step has not run.

    EXISTENCE-GUARDED. Some fixtures hand-build a minimal schema and call
    `_migrate_retail_schema` directly (retail_category_delete_fk_sync_test.py
    builds only categories/products/inventory_movements), where skipping is
    the right answer rather than a crash -- the same guard
    `_migrate_add_shift_cash_drawer` and v15 already carry.

    Returns a summary of what it did, for callers that want to report it;
    `_migrate_retail_schema` ignores it, and the durable record is the
    per-session audit_log rows.
    """
    live_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if 'cash_sessions' not in live_tables:
        return {}

    summary = {
        'terminal_id': None,
        'restated_from_close': 0,
        'blanked_terminal_ids': 0,
        'bound_to_terminal': 0,
        'force_ended_stale': [],
        'force_ended_collision': [],
    }

    columns = {
        row[1] for row in conn.execute('PRAGMA table_info("cash_sessions")').fetchall()
    }
    # ── 0b. the column this step binds to ───────────────────────────────
    # `terminal_id` belongs to v13, which runs earlier in this same chain and
    # adds it unconditionally to every table in RETAIL_ACTOR_TABLES that
    # exists -- so on every real install it is already here, and this branch is
    # unreachable in production.
    #
    # THE ANSWER TO ITS ABSENCE IS TO DO NOTHING, NOT TO ADD IT. A first draft
    # of this function self-healed with an `ALTER TABLE ... ADD COLUMN
    # terminal_id`, on the reasoning that a one-line ALTER beats a crash. Two
    # things were wrong with that. The visible one: it stole a column v13 is
    # supposed to add, so a test calling v13 in isolation against such a
    # database found it had nothing left to do
    # (retail_v13_additive_only_behavioural_test.py catches exactly this). The
    # one that actually matters: a `cash_sessions` with no `terminal_id` is a
    # table whose WRITERS do not know about terminals either, so a
    # terminal-bound unique index over it would be a constraint on a column
    # nothing ever populates -- binding nothing -- while the branch-bound
    # constraint it replaced got dropped. Replacing a working constraint with
    # a decorative one is strictly worse than leaving the working one alone.
    #
    # Returning early leaves v10's branch index in force (nothing drops it,
    # and `_v10_branch_index_is_superseded` keeps answering False because no
    # terminal index exists), so the table is never left unconstrained.
    if 'terminal_id' not in columns:
        return {'skipped': 'cash_sessions has no terminal_id column, so v13 has '
                           'not run against this database and there is no device '
                           'identity to bind a drawer to'}

    # ── 1. the columns ──────────────────────────────────────────────────
    for column, decl in (
        ('ended_at', 'TIMESTAMP'),
        ('ended_by', 'TEXT'),
        ('ended_reason', 'TEXT'),
    ):
        if column not in columns:
            conn.execute(f'ALTER TABLE cash_sessions ADD COLUMN {column} {decl}')
            columns.add(column)

    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        # ── 2. restate the counting instant on settled shifts ────────────
        summary['restated_from_close'] = conn.execute(
            "UPDATE cash_sessions SET ended_at=closed_at, ended_by=closed_by "
            "WHERE status=? AND closed_at IS NOT NULL AND ended_at IS NULL",
            (CASH_SESSION_STATUS_CLOSED,),
        ).rowcount

        stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # ── 3. shifts that ran past their business date ──────────────────
        # `today` is resolved once per COMPANY, not once per row: one install
        # can host more than one, each with its own declared timezone and
        # trading-day start, and each pays one settings lookup.
        #
        # THE BAR IS "before the PREVIOUS business date", NOT "before today's"
        # -- i.e. `opened < today - 1 day`, not `opened < today`. A POS is
        # exactly the software that trades past midnight, and the difference
        # between those two comparisons is a real shift a cashier is standing
        # at right now. REPRODUCED: a genuine v15 install with no
        # `retail_settings` row (so the shop clock falls back to plain local
        # midnight), one drawer opened 2026-08-23T23:00, still open at
        # 2026-08-24T01:00, opening_float 420.00, cashier still serving --
        # `opened < today` is true the instant the calendar date ticks over,
        # so the OLD comparison force-ended a two-hour-old drawer with real
        # money in it, permanently marked `unverified_stale_business_date`
        # (never counted), the moment `init_retail()` next ran. `opened` here
        # is at most one business day behind `today`; only a drawer at least
        # TWO business days behind is stale enough that nobody is plausibly
        # still standing at it.
        #
        # This is safe for the partial-unique-index build a few lines below:
        # the collision pass (step 5) is what actually clears same-terminal
        # contention -- newest wins, the rest get force-ended by THAT pass,
        # on whatever business date they were opened -- so nothing about
        # making step 3 less eager leaves `CREATE UNIQUE INDEX` unable to
        # run. Step 3 only needs to catch the shift nobody is coming back
        # for; step 5 is what makes the index creatable.
        today_by_company = {}
        for session in _v16_open_sessions(conn):
            company_id = session['company_id']
            if company_id not in today_by_company:
                today_by_company[company_id] = _v16_business_date(
                    conn, company_id, datetime.now())
            today = today_by_company[company_id]
            opened = _v16_business_date(
                conn, company_id, _v16_parse_timestamp(session['opened_at']))
            if today is None or opened is None:
                # Either the shop clock or this row's own timestamp could not
                # be read. Ending a drawer on a date comparison that could not
                # be made is exactly the guess this file does not make; the
                # collision pass below needs no clock and still runs.
                continue
            if opened < today - timedelta(days=1):
                _v16_force_end(conn, session, V16_ENDED_REASON_STALE,
                               stamp, live_tables)
                summary['force_ended_stale'].append(session['id'])

        # ── 4. bind what is left to this terminal ────────────────────────
        # Blanks first. An empty string -- or any string that is nothing but
        # whitespace once trimmed -- is not a terminal name, and unlike a
        # NULL it makes two open drawers collide: SQLite's unique index
        # treats every NULL as distinct from every other NULL, but compares
        # two non-NULL strings byte for byte, so two rows holding the same
        # "blank" text are a genuine duplicate to it. Normalised in PYTHON
        # via `_v16_terminal_id_is_blank` -- the SAME predicate step 5 below
        # uses to decide what it may skip -- rather than SQL TRIM. See that
        # function's docstring for the disagreement this closes: SQL TRIM
        # only strips 0x20, so a value like '\t' used to sail past this
        # UPDATE unnormalised, reach step 5 still non-NULL, get called
        # "unnameable" there and left unresolved, and then meet SQLite's own
        # unique index as the genuine duplicate it always was. Scoped to
        # OPEN rows because the index is partial on exactly those; a blank
        # on a settled shift is meaningless too, but rewriting closed
        # history to tidy it up would be a non-additive pass for no gain.
        open_terminal_rows = conn.execute(
            "SELECT id, terminal_id FROM cash_sessions "
            "WHERE status=? AND terminal_id IS NOT NULL",
            (CASH_SESSION_STATUS_OPEN,),
        ).fetchall()
        blank_ids = [row['id'] for row in open_terminal_rows
                     if _v16_terminal_id_is_blank(row['terminal_id'])]
        if blank_ids:
            placeholders = ','.join('?' for _ in blank_ids)
            conn.execute(
                f"UPDATE cash_sessions SET terminal_id=NULL "
                f"WHERE id IN ({placeholders})",
                blank_ids,
            )
        summary['blanked_terminal_ids'] = len(blank_ids)

        terminal = local_terminal_id()
        summary['terminal_id'] = terminal
        if terminal:
            summary['bound_to_terminal'] = conn.execute(
                "UPDATE cash_sessions SET terminal_id=? "
                "WHERE status=? AND terminal_id IS NULL",
                (terminal, CASH_SESSION_STATUS_OPEN),
            ).rowcount

        # ── 5. two drawers, one terminal ─────────────────────────────────
        contested = {}
        for session in _v16_open_sessions(conn):
            value = session['terminal_id']
            if value is None:
                # Step 4 above already normalised every BLANK value -- by
                # the exact same predicate, `_v16_terminal_id_is_blank` --
                # to NULL, so None is the only thing left to skip here.
                # Checking for None alone, rather than re-testing blankness
                # with a second hand-written condition, is deliberate: it is
                # what keeps this step and step 4 unable to drift apart the
                # way SQL TRIM and Python str.strip() once did, because
                # there is now exactly one blank test, used once, upstream.
                continue        # unnameable terminal: cannot collide, stays open
            if session['company_id'] is None:
                # A NULL anywhere in a unique index key makes the whole row
                # distinct from every other row, so this one cannot be what
                # makes the create fail either -- and ending it would be
                # taking a live drawer off a shop to satisfy a constraint that
                # was never going to object. Same rule, same reasoning, as
                # `_v16_collision_probe`'s two IS NOT NULL predicates; the two
                # must agree or the resolution and the check that follows it
                # would disagree about what a collision is.
                continue
            # Keyed on the EXACT stored value, never a trimmed or normalised
            # one. The index compares strings byte for byte, so grouping ' A '
            # with 'A' here would end a drawer SQLite was never going to
            # object to -- over-grouping costs a shop a live till, which is a
            # far worse error than the tidiness it buys.
            contested.setdefault((session['company_id'], value), []).append(session)

        for key in sorted(contested, key=lambda item: (str(item[0]), str(item[1]))):
            rivals = contested[key]
            if len(rivals) < 2:
                continue
            # Newest-opened keeps the terminal. `_v16_open_sessions` already
            # returns newest-first by the stored text, which sorts
            # lexicographically and therefore chronologically for the ISO-like
            # shapes this column holds -- but "therefore" is doing too much
            # work there for a decision about somebody's till, so the order is
            # re-derived from parsed datetimes. An unparseable `opened_at`
            # sorts oldest (datetime.min); the id breaks ties so that two
            # installs holding the same data resolve it the same way.
            rivals.sort(key=lambda row: (
                _v16_parse_timestamp(row['opened_at']) or datetime.min,
                str(row['id']),
            ), reverse=True)
            for loser in rivals[1:]:
                _v16_force_end(conn, loser, V16_ENDED_REASON_COLLISION,
                               stamp, live_tables)
                summary['force_ended_collision'].append(loser['id'])

        # ── 6. the constraint ────────────────────────────────────────────
        # Probed, not attempted. See RetailCashDrawerBindError: the difference
        # between this refusal and SQLite's own is that this one names the
        # shop, the terminal and the recovery.
        collisions = _v16_collision_probe(conn)
        if collisions:
            raise RetailCashDrawerBindError(_v16_bind_message(collisions))

        shape = _uid_index_shape(conn, 'cash_sessions', V16_TERMINAL_INDEX)
        if shape is not None and not _v16_terminal_index_is_correct(shape):
            # The name exists carrying the wrong shape -- v13's F4 defect,
            # where CREATE UNIQUE INDEX IF NOT EXISTS silently accepts a plain
            # index that already holds the name and advances the version with
            # no constraint in place. Dropped and rebuilt rather than trusted.
            conn.execute(f'DROP INDEX IF EXISTS {V16_TERMINAL_INDEX}')
            shape = None
        if shape is None:
            # Checked by NAME, database-wide, before the CREATE is even
            # attempted -- see `_index_owning_table`. `_uid_index_shape`
            # above only asked "is this name on cash_sessions", which is a
            # different question from "does this name exist at all", and
            # SQLite's CREATE INDEX answers the second one, not the first.
            owner = _index_owning_table(conn, V16_TERMINAL_INDEX)
            if owner is not None and owner != 'cash_sessions':
                raise RetailCashDrawerBindError(
                    f"Retail schema v16 cannot create index {V16_TERMINAL_INDEX}: "
                    f"that name already exists on table '{owner}', not on "
                    f"cash_sessions. SQLite index names are a single namespace "
                    f"for the whole database, not scoped per table, so this is a "
                    f"name collision from something else in this install -- not "
                    f"a defect in the cash-drawer migration itself. Nothing was "
                    f"changed: PRAGMA user_version was not advanced. Recovery: "
                    f"rename or drop the conflicting index on '{owner}' (it does "
                    f"not belong to this migration) and relaunch."
                )
            _v16_run_index_ddl(
                conn,
                f"CREATE UNIQUE INDEX {V16_TERMINAL_INDEX} "
                f"ON cash_sessions(company_id, terminal_id) "
                f"WHERE status='{CASH_SESSION_STATUS_OPEN}'"
            )
        if not _v16_terminal_index_is_correct(
                _uid_index_shape(conn, 'cash_sessions', V16_TERMINAL_INDEX)):
            raise RetailCashDrawerBindError(
                f"Retail schema v16 created {V16_TERMINAL_INDEX} on cash_sessions "
                f"but it did not come out UNIQUE and partial over exactly "
                f"(company_id, terminal_id). Advancing PRAGMA user_version now "
                f"would record a terminal-bound drawer that is not actually "
                f"constrained -- two tills could share one open session and "
                f"nothing would say so. user_version was not advanced; the next "
                f"launch retries."
            )

        # Only now is the branch constraint dropped: creating first means the
        # table is never left with neither, so an interruption between these
        # two statements leaves the v10 guarantee standing rather than none.
        # Both statements run through `_v16_run_index_ddl` -- see that
        # function's docstring for why the ORDER of these two specific calls,
        # not just their presence, is what keeps an interrupted migration
        # safe.
        _v16_run_index_ddl(conn, f'DROP INDEX IF EXISTS {V16_SUPERSEDED_BRANCH_INDEX}')
        return summary
    finally:
        conn.row_factory = previous_factory


def _migrate_add_sync_conflicts_and_drop_quantity_reserved(conn):
    """One-time migration (schema v16 -> v17): launch-readiness Phase 6,
    "catalogue correctness", stage 6a-i. See the RETAIL_SCHEMA_VERSION v17
    comment above for the full reasoning; this function is deliberately
    small because the actual weight of stage 6a-i is in application code
    (every catalogue write site bumping `row_version`), not here.

    1. `sync_conflicts` -- CREATE TABLE IF NOT EXISTS, empty. The visible
       landing spot for a row stage 6a-ii's reject-stale gate refuses to
       apply, instead of the silent drop design §6 explicitly forbids.
       Columns record exactly what a human needs to act on a conflict:
       which row (`entity_type`/`entity_id`), which company (multi-tenant
       scoping, like every other business table in this database), what
       kind of write was rejected (`event_type`), the version comparison
       that caused the rejection (`local_row_version`/
       `incoming_row_version`), when (`detected_at_utc`), and the full
       incoming payload so the conflict is actually actionable rather than
       just a number. None of the five catalogue types' payloads carry a
       credential (that is only ever true of `user`/`user_permission`
       events, a different sync stream entirely -- see
       commercial_runtime/identity/user_accounts.py's own payload
       allowlist), so storing the payload verbatim here is safe.
       Deliberately no writer yet: stage 6a-ii is the first thing that ever
       inserts a row.
    2. `inventory_balances.quantity_reserved` -- DROPPED. See the
       RETAIL_SCHEMA_VERSION v17 comment for why this is judged the
       lowest-risk destructive migration available in this chain.

    Idempotent: `sync_conflicts` uses CREATE TABLE/INDEX IF NOT EXISTS; the
    DROP COLUMN is preceded by a PRAGMA table_info existence check, exactly
    like every ADD COLUMN elsewhere in this chain -- a retried run (the
    normal case after any failure, since `ensure_schema_version` leaves
    `user_version` un-advanced) is a clean no-op both times.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_conflicts (
            id TEXT PRIMARY KEY,
            company_id INTEGER,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            local_row_version INTEGER,
            incoming_row_version INTEGER,
            incoming_payload TEXT NOT NULL,
            detected_at_utc TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sync_conflicts_entity "
        "ON sync_conflicts(company_id, entity_type, entity_id)"
    )

    live_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if 'inventory_balances' in live_tables:
        cols = {row[1] for row in conn.execute('PRAGMA table_info(inventory_balances)').fetchall()}
        if 'quantity_reserved' in cols:
            conn.execute('ALTER TABLE inventory_balances DROP COLUMN quantity_reserved')


def _migrate_add_sync_freshness(conn):
    """One-time migration (schema v17 -> v18): launch-readiness Phase 7,
    stage 7a (persisted sync freshness). See the RETAIL_SCHEMA_VERSION v18
    comment above and docs/launch-readiness/phase7-offline-ux.md "FINDING
    1" for the full reasoning; this function is deliberately small because
    the actual weight of stage 7a is in SyncService itself (loading this
    table at construction and writing through to it on every recorded
    success -- commercial_runtime/sync/sync_service.py), not here.

    `sync_freshness` -- ONE row (id=1), two nullable columns,
    `last_push_success_at` / `last_pull_success_at`. Both start NULL: a
    fresh install has never synced successfully, and NULL is what lets a
    reader (SyncFreshnessStore.load / SyncService.get_health) tell "never
    synced" apart from "synced a long time ago" -- collapsing the former
    into a huge elapsed number would trip the 72-hour hard stop stage 7c
    builds on top of this on a shop's very first day, before it has done
    anything wrong.

    A SEPARATE table, deliberately NOT two new columns on `sync_cursor`,
    even though the two live in the same database and are conceptually
    "sync device state": `sync_cursor` means one specific thing -- how far
    THIS device has pulled -- and it has wedge history (see
    retail_category_delete_fk_sync_test.py's module docstring for the
    FK-vs-cursor-advance story this codebase already lived through once,
    where a bug in what ran on the apply path silently stopped a device's
    cursor from ever advancing again). Widening `sync_cursor`'s meaning
    with unrelated columns risks that logic for a change that doesn't need
    to touch it at all; a same-shaped, separate single-row table costs
    nothing extra to migrate or query and cannot interact with that history.

    Single-row shape mirrors `sync_cursor`'s own convention exactly --
    `id INTEGER PRIMARY KEY CHECK (id = 1)` -- for the same reason:
    exactly one freshness record per device, not company-scoped (this is
    local device state, not tenant data, like `sync_cursor` itself).

    Idempotent: CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE, exactly like
    `sync_cursor`'s own bootstrap in `_init_retail` -- a retried run (the
    normal case after any interrupted migration, since
    `ensure_schema_version` leaves `user_version` un-advanced on failure)
    is a clean no-op both times.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_freshness (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_push_success_at TEXT,
            last_pull_success_at TEXT
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO sync_freshness (id, last_push_success_at, last_pull_success_at) "
        "VALUES (1, NULL, NULL)"
    )


def _migrate_add_offline_override(conn):
    """One-time migration (schema v18 -> v19): launch-readiness Phase 7,
    stage 7c-ii (72-hour hard stop on new sales, behind a manager
    override). See the RETAIL_SCHEMA_VERSION v19 comment above and docs/
    launch-readiness/phase7-offline-ux.md "Decision 2" for the full
    reasoning.

    ONE nullable column, `offline_override_at TEXT`, added to the EXISTING
    v18 `sync_freshness` table -- not a new table, and not two new columns
    the way `sync_freshness` itself carries `last_push_success_at` /
    `last_pull_success_at` as a pair. This is deliberate: the override is
    validated by COMPARISON against the most recent successful-sync
    instant (`offline_override_at IS NOT NULL AND offline_override_at >
    last_<x>_success_at`), never by expiry, so the two facts have to live
    in the same row a single query can read together. Splitting the
    override into its own table would invite a reader to fetch the two
    halves separately and compare them out of sync with each other -- the
    exact hazard ROADMAP.md's v19 claim entry names as the reason NOT to
    split it out.

    NULL means "no standing override" -- the ordinary state for every
    install that has never been behind by 72 hours, and the state a
    successful sync silently returns to (no expiry job, no cleanup path,
    nothing writes NULL back in explicitly; the comparison rule alone is
    what makes a stale override stop validating once a fresher sync
    succeeds).

    Guarded by `PRAGMA table_info` so a second run (the normal retry shape
    after any interrupted migration -- `ensure_schema_version` leaves
    `user_version` un-advanced on failure) is a clean no-op, matching
    every other single-column ALTER in this file (e.g. the `session_id`
    additions in `_migrate_add_shift_cash_drawer` above). `sync_freshness`
    itself is guaranteed to already exist by the time this function runs:
    `_migrate_add_sync_freshness` (v17 -> v18) is called immediately
    before this step, unconditionally, in `_migrate_retail_schema`'s own
    chain, every pass -- so no existence check is needed for the TABLE,
    only for the column.
    """
    cols = {row[1] for row in conn.execute('PRAGMA table_info(sync_freshness)').fetchall()}
    if 'offline_override_at' not in cols:
        conn.execute('ALTER TABLE sync_freshness ADD COLUMN offline_override_at TEXT')


def _migrate_add_stock_exceptions(conn):
    """One-time migration (schema v19 -> v20): launch-readiness Phase 7,
    stage 7d-i (the oversell exception queue). See the RETAIL_SCHEMA_
    VERSION v20 comment above and docs/launch-readiness/phase7-offline-ux.md
    "Decision 4" for the full reasoning; this function is deliberately
    small because the actual weight of stage 7d-i is in the WRITER
    (commercial_runtime/sync/sync_service.py's `inventory_movement` apply
    branch), not here -- identical division of labour to v17's
    `sync_conflicts` (empty at creation, filled in by stage 6a-ii) and
    v18's `sync_freshness` (seeded with NULLs, written by SyncService).

    `stock_exceptions` -- one row per OPEN oversell, closest sibling in
    shape and purpose to `sync_conflicts` (v17): both are "a visible queue
    instead of a silent drop" for something a human has to see, both are
    company-scoped like every other business table in this database (see
    CLAUDE.md), and both store the full context a human needs to act
    rather than a bare count. Columns: `company_id` / `product_id` (a real
    FOREIGN KEY to `products(id)`, matching `inventory_movements`' own --
    the apply site only ever calls this after `_row_exists(conn, "products",
    ...)` has already confirmed the row exists, so the FK can never be the
    reason an exception fails to record) / `branch_id` (NOT NULL, no FK --
    matching `inventory_movements.branch_id`'s own "no FOREIGN KEY at all"
    shape, since this table is only ever written with an already-resolved
    local branch id, never a raw payload value) / `observed_quantity_on_
    hand` (the negative balance AT DETECTION TIME, REAL like `inventory_
    balances.quantity_on_hand` itself) / `detected_at_utc` (when THIS
    device's apply site noticed, not the originating movement's own
    timestamp -- identical convention to `sync_conflicts.detected_at_utc`)
    / `resolved_at_utc` (nullable; NULL means open). Deliberately no writer
    for `resolved_at_utc` anywhere in this stage -- Decision 4 is explicit
    that resolving one is a stock-movement-writing act for a later stage
    (7d-ii), gated `CAP_STOCK_ADJUST`, and that nothing in 7d-i may
    auto-resolve or silently drop a row.

    ONE OPEN ROW PER (company_id, product_id, branch_id) -- enforced by a
    REAL constraint, `idx_stock_exceptions_open`, not application logic
    alone: a shop with one genuinely oversold product that keeps merging
    further negative on every later sync tick must UPDATE the same open
    row (refreshed quantity, refreshed detected_at_utc), never append a
    second one, or the queue becomes unusable noise indistinguishable from
    one new exception per product. The index is PARTIAL --
    `WHERE resolved_at_utc IS NULL` -- exactly the `idx_reorder_requests_
    open` / `idx_po_idempotency` shape already in this file (grep this
    module for `WHERE uid IS NOT NULL` and its siblings): a RESOLVED
    exception for the same key must never block a fresh one from opening
    later, which a non-partial UNIQUE(company_id, product_id, branch_id)
    would do the moment the first oversell on that product was ever
    resolved. The writer's `INSERT ... ON CONFLICT(company_id, product_id,
    branch_id) WHERE resolved_at_utc IS NULL DO UPDATE ...` MUST repeat
    this exact WHERE clause verbatim to target this index at all --
    omitting it does not fall back to matching the index, it raises
    `OperationalError` on every apply, exactly as sync_service.py's own
    module docstring already warns for `ON CONFLICT(uid) WHERE uid IS NOT
    NULL` -- this project has been bitten by precisely that mismatch
    before.

    Idempotent: CREATE TABLE/INDEX IF NOT EXISTS only, no ALTER -- a
    retried run (the normal case after any interrupted migration, since
    `ensure_schema_version` leaves `user_version` un-advanced on failure)
    is a clean no-op.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_exceptions (
            id TEXT PRIMARY KEY,
            company_id INTEGER,
            product_id INTEGER NOT NULL REFERENCES products(id),
            branch_id INTEGER NOT NULL,
            observed_quantity_on_hand REAL NOT NULL,
            detected_at_utc TEXT NOT NULL,
            resolved_at_utc TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_stock_exceptions_company "
        "ON stock_exceptions(company_id, resolved_at_utc)"
    )
    # The uniqueness guarantee itself -- see the docstring above for why it
    # must be PARTIAL (open rows only) and why the writer's ON CONFLICT
    # must repeat this WHERE clause verbatim.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_exceptions_open "
        "ON stock_exceptions(company_id, product_id, branch_id) "
        "WHERE resolved_at_utc IS NULL"
    )


def _migrate_add_lookup_indexes(conn):
    """One-time migration (schema v20 -> v21): launch-readiness "the POS
    scale fix" (ROADMAP.md's 2026-08-29 "retail schema v21 CLAIMED" entry).
    Five indexes, no table or column changes -- see the RETAIL_SCHEMA_
    VERSION v21 comment above for the full measurement (`list_products`
    with no LIMIT and no search parameter, ~2.5 MB at 5,000 SKUs / ~25 MB at
    50,000, re-fetched after every completed sale, filtered client-side for
    POS scan / product search / PO scan by both the web frontend and
    Android's `ProductLookup.kt`).

    Confirmed genuinely absent by reading this file (grep `CREATE INDEX`)
    before adding any of them: `idx_products_supplier` is the only existing
    index on `products` (on `supplier_id`, from `_migrate_products_add_
    supplier_fk`), and nothing at all pre-existed on `sale_items`, `sales`,
    or `payments` beyond the row-version/actor-tables loop's single-column
    `idx_{table}_created_at_utc` / `idx_{table}_live` indexes above (neither
    of which covers any of the five columns below):

      * `products(company_id, barcode)` -- the scan path (the new `GET
        /products/lookup` this version backs, and the client-side scan it
        is meant to replace) has NO index today.
      * `products(company_id, sku)` -- same scan path, the SKU branch.
      * `sale_items(sale_id)` -- `recent_sales` LEFT JOINs this per request.
      * `sales(customer_id)` -- `customer_statement` scans by it.
      * `payments(party_type, party_id)` -- see the guard below.

    Deliberately NOT a UNIQUE index on `products(company_id, barcode)`:
    uniqueness there is enforced only at the application layer today
    (`create_product`'s 409 check, see that route's own AUDIT comment).
    Blank barcodes are explicitly exempt from that check, and a
    sync-applied row could already violate uniqueness on a real install --
    a UNIQUE index would fail THIS migration on exactly the installs it is
    meant to help. Whether to tighten that is a separate product decision,
    not a side effect of an index migration; recorded, not silently taken.

    `payments(party_type, party_id)` is guarded on column existence,
    unlike the four indexes above -- those two columns are NOT part of
    `payments`' base `CREATE TABLE` (see `_init_retail`'s DDL: no
    `party_type`/`party_id` there at all). They are added lazily, at
    request time, by `api/retail_api.py`'s `_ensure_credit_schema()` --
    which every install that has ever touched a credit/payment route has
    long since run, but a BRAND NEW install has NOT: `_init_retail` runs
    this entire migration chain, including this function, before any
    request has ever reached `_ensure_credit_schema`. Creating this index
    unconditionally would raise `OperationalError: no such column:
    party_type` on every fresh install, every time, since the column
    genuinely does not exist yet at this point in that install's timeline.
    So this function only creates the index when the columns already
    exist (an existing install upgrading to v21, where `_ensure_credit_
    schema` fired long ago); `_ensure_credit_schema` itself carries the
    identical `CREATE INDEX IF NOT EXISTS`, placed immediately after the
    loop that adds those two columns, for the brand-new-install path this
    migration cannot reach. Both are `IF NOT EXISTS`, so whichever runs
    first wins and the other is a clean no-op -- the same shape
    `idx_products_supplier` already uses by living outside its own ADD
    COLUMN guard (see that migration's own docstring).

    All five: `CREATE INDEX IF NOT EXISTS` only, no `ALTER`, no data
    touched -- a retried run (the normal case after any interrupted
    migration, since `ensure_schema_version` leaves `user_version`
    un-advanced on failure) is a clean no-op.

    EACH TABLE IS ALSO GUARDED ON EXISTENCE, the same `live_tables` check
    `_migrate_add_identity_and_attribution_columns`'s own RETAIL_ACTOR_
    TABLES/RETAIL_ROW_VERSION_TABLES loops already use above -- found the
    hard way, by running this suite rather than by inspection alone:
    retail_category_delete_fk_sync_test.py's `_build_v2_database` hand-
    builds a genuinely old, minimal schema (`categories`/`products`/
    `inventory_movements` ONLY, `PRAGMA user_version = 2`) to prove the
    full migration chain still behaves on a device this old --
    `ensure_schema_version` runs every step in one pass on such a device
    (it only knows "behind", never "behind by exactly one version", see
    `_migrate_retail_schema`'s own docstring), so this function reached
    `sale_items`/`sales` on a database that has neither and raised
    `OperationalError: no such table: main.sale_items` unconditionally,
    failing three tests that have nothing to do with this change. Every
    REAL install always has all four base tables (`products`, `sale_
    items`, `sales`, `payments` are all in `_init_retail`'s own DDL, which
    runs before ANY migration), so this guard is a no-op there; it only
    matters for a synthetic fixture proving this exact chain-robustness
    property, which is precisely the case it exists to keep passing.
    """
    live_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if 'products' in live_tables:
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_products_company_barcode '
            'ON products(company_id, barcode)'
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_products_company_sku '
            'ON products(company_id, sku)'
        )
    if 'sale_items' in live_tables:
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_sale_items_sale_id '
            'ON sale_items(sale_id)'
        )
    if 'sales' in live_tables:
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_sales_customer_id '
            'ON sales(customer_id)'
        )
        # `created_at`, NOT `created_at_utc`. Schema v13 already created
        # `idx_sales_created_at_utc`, and it is useless to `recent_sales`
        # because that route filters the OTHER column -- a distinction found
        # only by reading `EXPLAIN QUERY PLAN`, which reported `SCAN s` both
        # before and after the date predicate was made sargable. Rewriting the
        # predicate was necessary and not sufficient: a sargable filter with no
        # index on its column still scans.
        #
        # This is the Sales History / returns-lookup hot path. On a hypermarket
        # doing ~500k sales a year, `_findSaleForReturn` fetching `?limit=200`
        # through an unindexed date scan is a multi-second wait at the returns
        # counter with a customer standing there.
        #
        # Deliberately NOT a replacement for `created_at_utc`. Both columns are
        # real and both are read: `created_at` is the local wall clock this
        # route filters on, `created_at_utc` is the device-independent instant
        # Phase 2 added for cross-device ordering. Indexing one says nothing
        # about the other.
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_sales_created_at '
            'ON sales(created_at)'
        )
    # Guarded on TABLE existence (this block) AND on the two columns' own
    # existence (below) -- see the docstring above for why the columns
    # cannot be assumed present at schema-migration time the way the other
    # four indexes' columns can.
    if 'payments' in live_tables:
        payments_cols = {row[1] for row in conn.execute('PRAGMA table_info(payments)').fetchall()}
        if 'party_type' in payments_cols and 'party_id' in payments_cols:
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_payments_party '
                'ON payments(party_type, party_id)'
            )


def _migrate_add_nocase_lookup_indexes(conn):
    """One-time migration (schema v21 -> v22): launch-readiness "the
    case-fold lookup" (ROADMAP.md's 2026-08-30 "retail schema v22 CLAIMED"
    entry). Two indexes, no table or column changes -- see the RETAIL_
    SCHEMA_VERSION v22 comment above for the full measurement and the
    2026-08-29 "the case-insensitive lookup is only PARTLY case-insensitive"
    ROADMAP.md entry this migration closes.

    `GET /products/lookup` (retail_api.py's `lookup_product`) was made
    "case-insensitive" at v21 by building a fixed set of variants of the
    TYPED code -- as-typed, `.upper()`, `.lower()` -- and matching with
    `IN (...)`. Three variants of the INPUT cannot match a value STORED
    mixed-case: a row with `sku = 'AbC-123'` and a typed `abc-123` produces
    none of `['ABC-123', 'abc-123']` and the lookup misses a product that is
    genuinely there. Measured, not assumed -- see the ROADMAP.md entry above.

    A NOCASE-collated INDEX is the correct fix, and it IS fully sargable.
    The reasoning that ruled it out (COLLATE NOCASE / LOWER() on the COLUMN
    is non-sargable and would destroy the v21 indexes) is true of wrapping
    the COLUMN in a function or collation at query time, but the collation
    here lives in the INDEX definition instead:

        CREATE INDEX idx_x ON products(company_id, sku COLLATE NOCASE)
        SELECT ... WHERE company_id=? AND sku=? COLLATE NOCASE

    plans as `SEARCH products USING COVERING INDEX idx_x (company_id=? AND
    sku=?)`, not a scan. Measured in the same probe as the ROADMAP.md entry.

    NON-UNIQUE, deliberately, same reasoning as the v21 plain indexes'
    docstring above: a UNIQUE nocase index would FAIL this migration on any
    install that already holds case-duplicates (e.g. both `abc` and `ABC`
    already stored) -- which is exactly the install this change exists to
    help, not one it can afford to break at migration time. Whether to
    tighten unique-ness is a separate product decision, not a side effect
    of an index migration; the three write-side dup checks this same change
    makes case-insensitive (retail_api.py `create_product`/`update_product`,
    import_api.py's CSV upsert) narrow how much NEW duplication can form
    going forward, without touching what a legacy install already has on
    disk.

    These are ADDITIONAL to the v21 plain indexes on the same two columns,
    which are KEPT, not replaced: a NOCASE-collated index cannot serve a
    BINARY equality (SQLite will not use an index whose collation differs
    from the comparison's), so an exact-match lookup still needs the plain
    index and a case-folding lookup needs this one. Together they back the
    4-rung ladder in `lookup_product` (barcode exact -> barcode NOCASE ->
    sku exact -> sku NOCASE) -- every rung a single indexed point lookup.

    `live_tables` guard copied verbatim from `_migrate_add_lookup_indexes`
    immediately above: a synthetic old-database fixture
    (retail_category_delete_fk_sync_test.py's `_build_v2_database`) runs the
    ENTIRE migration chain in one pass against a hand-built schema that
    predates `products` existing in its final shape, and `ensure_schema_
    version` cannot tell "behind by one version" from "behind by many" --
    it just runs every step. Without this guard this function would raise
    `OperationalError: no such table: main.products` on that fixture, same
    failure mode `_migrate_add_lookup_indexes` already documents.
    """
    live_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if 'products' in live_tables:
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_products_company_barcode_nocase '
            'ON products(company_id, barcode COLLATE NOCASE)'
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_products_company_sku_nocase '
            'ON products(company_id, sku COLLATE NOCASE)'
        )


def _migrate_add_promotions(conn):
    """One-time migration (schema v22 -> v23): launch-readiness "promotions,
    wave 1" (ROADMAP.md's 2026-08-30 "retail schema v23 CLAIMED for
    promotions, wave 1" entry). Two new tables, two indexes -- see the
    RETAIL_SCHEMA_VERSION v23 comment above for the full reasoning; this
    docstring covers the migration shape.

    `promotions` -- one row per rule. `product_id`/`category_id` are both
    nullable and API-validated (create_promotion/update_promotion,
    api/retail_api.py) to hold EXACTLY ONE of the two; `branch_id` NULL
    means every branch. Neither column carries a literal FOREIGN KEY --
    deliberately, matching this table's own dynamic values: `product_id`/
    `category_id` hold the SAME TEXT UUIDs `products.id`/`categories.id`
    have held since the v13 identity migration, stored into a column
    declared with INTEGER affinity for exactly the same reason
    `company_id INTEGER DEFAULT 1` already does on `branches`/`categories`/
    `products` themselves -- SQLite affinity is a storage hint, not an
    enforced type, and a value that cannot be losslessly converted (a UUID
    is never a well-formed integer literal) is stored and compared as TEXT
    regardless of the column's declared affinity. A real FOREIGN KEY would
    additionally be enforced (`PRAGMA foreign_keys=ON` on every connection,
    Wave 0 / AUDIT-016, `_conn()` above) and is deliberately left off both
    new tables here, so that a promotion whose target product/category was
    later deleted cannot raise mid-sale; ownership is instead validated at
    write time by the API layer (company-scoped SELECT against
    products/categories/branches), which is where the cross-tenant leak
    this guards against actually needs catching -- see the create/update
    routes' own comments for the supplier_id precedent this repeats.

    `sale_item_promotions` -- one row per sale LINE a promotion actually
    won, snapshotting the promotion's name/pct/resulting amount AS APPLIED
    (never re-read from `promotions` later -- see the RETAIL_SCHEMA_VERSION
    v23 comment above for why). `sale_item_id`/`promotion_id` are real
    INTEGER autoincrement ids on both sides (`sale_items.id`, this
    migration's own `promotions.id`), so no affinity mismatch applies to
    them the way it does to `promotions.product_id`/`category_id` above --
    still no literal FOREIGN KEY, for the identical "never block a sale on
    a referential-integrity technicality" reasoning.

    Both tables carry `company_id` directly rather than inheriting tenancy
    through a join, unlike `sale_items` (which inherits through `sale_id`)
    -- see the RETAIL_SCHEMA_VERSION v23 comment above for why that is a
    deliberate difference, not an inconsistency.

    `live_tables` guard copied verbatim from `_migrate_add_nocase_lookup_
    indexes` immediately above, for the SAME synthetic old-database fixture
    (retail_category_delete_fk_sync_test.py's `_build_v2_database`, which
    runs the ENTIRE migration chain in one pass against a hand-built schema
    holding only categories/products/inventory_movements plus a few sync
    tables -- no `branches`, no `sales`, no `sale_items`). Honestly:
    UNLIKE that function, this migration's `CREATE TABLE IF NOT EXISTS`
    statements cannot actually fail against that fixture or any other --
    `promotions` and `sale_item_promotions` are brand-new, self-contained
    tables with no FOREIGN KEY on any column (see above), so there is no
    pre-existing table either CREATE TABLE depends on to succeed. The guard
    is kept anyway, gating the two CREATE INDEX statements below, for one
    reason: it costs nothing, and it keeps this step visually consistent
    with every neighbour in this chain that checks before it creates,
    rather than being the one step a future reader has to double back to
    and ask why it skips the check everything around it makes. If either
    table ever grows a real FOREIGN KEY in a later migration, this guard is
    already sitting here ready to earn its keep instead of needing to be
    invented from scratch under time pressure.
    """
    live_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.execute("""
        CREATE TABLE IF NOT EXISTS promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            discount_pct REAL NOT NULL,
            product_id INTEGER,
            category_id INTEGER,
            branch_id INTEGER,
            starts_at TEXT,
            ends_at TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sale_item_promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            sale_item_id INTEGER NOT NULL,
            promotion_id INTEGER NOT NULL,
            name_snapshot TEXT NOT NULL,
            discount_pct_snapshot REAL NOT NULL,
            discount_amount_snapshot REAL NOT NULL
        )
    """)
    live_tables = live_tables | {'promotions', 'sale_item_promotions'}
    if 'promotions' in live_tables:
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_promotions_company_live '
            'ON promotions(company_id, status)'
        )
    if 'sale_item_promotions' in live_tables:
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_sale_item_promotions_item '
            'ON sale_item_promotions(sale_item_id)'
        )


def load_sync_freshness(conn) -> dict:
    """Reads the single `sync_freshness` row -- the concrete `load` half of
    the `SyncFreshnessStore` collaborator `SyncService` is constructed with
    (see that NamedTuple's docstring in commercial_runtime/sync/
    sync_service.py). Called once, at `SyncService.__init__`, to seed
    `self._health`'s in-memory cache with whatever THIS device last durably
    recorded, so a restarted process reports the same `last_success_at` the
    previous one did instead of resetting to None.

    Returns `{'push': None, 'pull': None, 'override_at': None}` both when
    the row genuinely holds NULLs (a fresh install that has never synced
    successfully and has no standing offline-sales override -- the normal
    post-migration state `_migrate_add_sync_freshness` /
    `_migrate_add_offline_override` seed) and, defensively, when no row
    exists at all (should not happen on any v18+ database, since the
    migration always seeds it, but "no row" and "a row of NULLs" mean the
    identical thing to every caller of this function, so there is no
    reason to raise here instead of returning the same shape either way).

    `override_at` (schema v19, stage 7c-ii) added as a THIRD key on the
    SAME dict this function has always returned, not a second lookup --
    `SyncService._ensure_freshness_loaded` reads all three off one call,
    which is what keeps the offline-sales-stop comparison (Decision 2)
    from ever observing the override and the success clocks from two
    different moments in time.
    """
    row = conn.execute(
        'SELECT last_push_success_at, last_pull_success_at, offline_override_at '
        'FROM sync_freshness WHERE id = 1'
    ).fetchone()
    if row is None:
        return {'push': None, 'pull': None, 'override_at': None}
    return {
        'push': row['last_push_success_at'],
        'pull': row['last_pull_success_at'],
        'override_at': row['offline_override_at'],
    }


def record_offline_override(conn, when: str) -> None:
    """Writes the manager's offline-sales-stop override timestamp -- the
    concrete `record_override` half of the extended `SyncFreshnessStore`
    collaborator (commercial_runtime/sync/sync_service.py), called from
    `SyncService.record_offline_override` when the /sync/offline-override
    route (retail_api.py) accepts a manager's approval to keep selling past
    the 72-hour hard stop.

    Mirrors `record_sync_freshness` exactly: does not commit (the caller
    owns the transaction boundary on the connection it opened, and holds
    the same `_health_lock` this write-through happens inside of, for the
    identical reason `record_sync_freshness` is called from inside that
    lock in `_record_sync_success`), and writes a single column on the
    same single-row (`id = 1`) table rather than opening a connection of
    its own.
    """
    conn.execute('UPDATE sync_freshness SET offline_override_at = ? WHERE id = 1', (when,))


def record_sync_freshness(conn, half: str, when: str) -> None:
    """Writes one half's last-success timestamp -- the concrete `record`
    half of the `SyncFreshnessStore` collaborator, called from
    `SyncService._record_sync_success` on every successful push or pull,
    inside the same `_health_lock` that updates the in-memory cache (see
    that method's own comment for why this write-through belongs inside
    that lock rather than after it).

    `half` is always exactly `'push'` or `'pull'` -- `SyncService`'s own
    vocabulary (`run_once`'s two try/except blocks) -- so the column name
    is derived from it rather than accepting an arbitrary column name from
    the caller. Does not commit; the caller (`_record_sync_success`) owns
    the transaction boundary on the connection it opened, exactly like
    `_ensure_credit_schema`'s callers own theirs for `local_ensure_schema`.
    """
    column = 'last_push_success_at' if half == 'push' else 'last_pull_success_at'
    conn.execute(f'UPDATE sync_freshness SET {column} = ? WHERE id = 1', (when,))


def record_or_refresh_stock_exception(conn, *, company_id, product_id, branch_id,
                                       observed_quantity_on_hand) -> None:
    """Writes or refreshes ONE visible `stock_exceptions` row for a
    (company, product, branch) balance a caller just found negative --
    launch-readiness Phase 7, `stock_exceptions` (schema v20, stage 7d-i;
    docs/launch-readiness/phase7-offline-ux.md "Decision 4"). THE canonical
    implementation of this upsert -- do not add a second copy anywhere. See
    `_migrate_add_stock_exceptions` above for the full table-shape
    reasoning (why `idx_stock_exceptions_open` is PARTIAL, why the ON
    CONFLICT below must repeat its WHERE clause verbatim).

    TWO callers, as of launch-readiness Phase 7 stage 7d-iii (ROADMAP.md's
    2026-08-29 "Correction to the v20 claim" entry):

      * commercial_runtime/sync/sync_service.py's `_apply_event`
        `inventory_movement` branch, when a MERGED balance lands negative
        -- the original 7d-i writer, reached through the OPTIONAL
        `stock_exception_recorder` collaborator threaded through
        `SyncService.__init__` (mirrors `SyncFreshnessStore`: `None` by
        default, checked explicitly, wired at the retail/Android
        construction sites in products/retail/backend/app.py). Reached
        this way, rather than a direct import, because `sync_service.py`
        is shared with Clinic and importing anything under
        `products/retail` from `commercial_runtime` would be a layering
        violation -- absence of the collaborator must never be inferred
        from a caught exception, the identical rule `SyncFreshnessStore`'s
        own docstring states for the identical reason.
      * `create_sale` (products/retail/backend/api/retail_api.py), when a
        LOCAL sale is allowed past a stale-LOW on-hand figure -- stage
        7d-iii's relaxation of the `qty > on_hand` refusal, gated on
        `_is_device_behind_on_sync()`. Called directly; retail_api.py is
        already this module's own product, so there is no layering
        concern for this caller the way there is for sync_service.py.

    7d-i's original writer docstring said a local sale could never drive
    its own balance negative, "because its own `qty > on_hand` refusal
    still stands" -- true before 7d-iii, false after: that refusal is now
    conditional on the device being behind on sync, so a local sale is a
    SECOND genuine route to a negative balance, not just the cross-device
    merge 7d-i was written for. This function existing as ONE shared
    implementation, rather than two copies of the same upsert, is exactly
    why that change needed no change to the SQL itself, only a second
    caller -- see sync_service.py's `_record_or_refresh_stock_exception`
    for that correction recorded at its own original location.

    `INSERT ... ON CONFLICT(company_id, product_id, branch_id) WHERE
    resolved_at_utc IS NULL DO UPDATE SET observed_quantity_on_hand=...,
    detected_at_utc=...` -- the WHERE clause repeats `idx_stock_exceptions_
    open`'s own partial-index predicate VERBATIM, which SQLite's UPSERT
    syntax requires to match a partial index at all (omitting it does not
    fall back to matching the index; it raises `OperationalError` on every
    call -- this project has been bitten by exactly that mismatch before,
    see `_migrate_add_stock_exceptions`'s own docstring). An OPEN row for
    the same key is refreshed in place (same id, updated quantity, updated
    `detected_at_utc`) so a product that keeps going further negative --
    on repeated merges, or repeated behind-sales -- accumulates ONE row,
    not one per call. A RESOLVED row does not satisfy the partial index's
    predicate, so it is invisible to this ON CONFLICT and a fresh open row
    is inserted instead -- a past resolution must never block a new
    oversell on the same product from being recorded.

    Never resolves, never deletes, and takes no position on WHETHER to
    record -- that decision (is the balance actually negative right now?
    has this exact write already been accounted for?) belongs entirely to
    the caller, which is why this function has no guard of its own beyond
    the upsert's own idempotency. Both current callers only call this
    AFTER their own balance write, re-reading the resulting balance rather
    than deriving it, and only when that reread is negative -- landing
    exactly on zero is not an oversell (see each caller's own comment).
    Does not commit -- matches `record_sync_freshness`/`record_offline_
    override` immediately above: the caller owns the transaction boundary
    on the connection it opened.
    """
    import uuid as _uuid
    conn.execute(
        "INSERT INTO stock_exceptions (id, company_id, product_id, branch_id, "
        "observed_quantity_on_hand, detected_at_utc, resolved_at_utc) "
        "VALUES (?,?,?,?,?,?,NULL) "
        "ON CONFLICT(company_id, product_id, branch_id) WHERE resolved_at_utc IS NULL "
        "DO UPDATE SET observed_quantity_on_hand=excluded.observed_quantity_on_hand, "
        "detected_at_utc=excluded.detected_at_utc",
        (str(_uuid.uuid4()), company_id, product_id, branch_id,
         observed_quantity_on_hand, datetime.now(timezone.utc).isoformat()),
    )


def _v16_rebind_orphaned_open_drawers(conn):
    """Boot-time, NOT version-gated: bind any OPEN drawer still carrying a
    NULL `terminal_id` to this device, the moment this device is able to name
    itself. Runs on EVERY launch, from the same place `_init_retail` already
    runs the schema work -- unlike `_migrate_bind_cash_drawer_to_terminal`
    above, this is not a one-time migration step and must never be folded
    into one, for the reason this docstring exists to record.

    THE GAP THIS CLOSES. `_migrate_bind_cash_drawer_to_terminal`'s own
    backfill (step 4) binds every open, NULL-terminal drawer to
    `local_terminal_id()` -- but ONLY ONCE, the moment schema v16 itself
    runs, and only using whatever `local_terminal_id()` returns AT THAT
    INSTANT. `local_terminal_id()` is `peek_local_device_uuid()`, which BY
    DESIGN never creates `local_device.json` -- the only thing that writes it
    is `device_context.resolve_local_device()`, reached in production only
    via `GET /api/devices/me`, which the frontend calls from
    `app-shell.js::init()` -- AFTER LOGIN. `init_retail()` runs at PROCESS
    BOOT, before any login has happened. So on the FIRST boot after an
    install upgrades to v16, the migration runs with no device identity yet,
    every open drawer's `terminal_id` stays NULL, and `PRAGMA user_version`
    advances to 16 regardless -- correctly, because the migration did
    everything it could with the identity it had. The device identity then
    appears moments later, at login, and `_migrate_bind_cash_drawer_to_terminal`
    NEVER RUNS AGAIN: `ensure_schema_version` only re-enters the chain when
    `user_version` is behind, and it is not, not ever again for this
    install. Without this function, that shop's live, counted-into drawer is
    stranded with a NULL terminal forever: no device can find it through
    `_open_cash_session_id` (which will bind THIS device to nothing, since
    there is no open row with THIS terminal_id), no device can close it,
    and the float somebody physically counted into it is unreachable by any
    code path this product ships. This function is the reconciliation that
    catches up once the identity that step 4 needed finally exists.

    WHY NOT VERSION-GATED. A version-gated step runs once, ever, at the
    moment the marker crosses its threshold -- which is fine for a migration
    reshaping the SCHEMA, but wrong for reconciling against a device identity
    that this function has no control over the timing of. The identity can
    appear on boot 1, boot 50, or never (a stripped Android build with no
    `device_context` at all, or a machine that has genuinely never logged
    in). Tying this to a version bump would mean "did the boot happen to
    also be the one that changed a version" decides whether a shop's drawer
    ever gets bound -- unrelated facts, coupled by accident. Running
    unconditionally, every boot, makes the answer "as soon as the identity
    exists, the very next launch" instead, with no dependency on which
    launch that happens to be.

    THE FOUR RULES, IN ORDER:

      1. `local_terminal_id()` returns None -> do nothing at all. Still no
         device identity: nothing here can be bound to anything, and the
         correct action is to try again next boot, not to invent an
         identity or leave a stale one behind. This is the SAME reasoning
         `_migrate_bind_cash_drawer_to_terminal` step 4 already uses for the
         identical return value, applied on a schedule that keeps checking
         instead of checking exactly once.

      2. An open session with a NON-NULL `terminal_id` is never touched.
         This function only ever fills an absence; it does not reassign, and
         it does not adjudicate between two drawers that both already claim
         a terminal (`_migrate_bind_cash_drawer_to_terminal` step 5's
         newest-wins collision resolution owns that question, and it owns it
         exactly once, during the migration, not on every boot).

      3. Binding a NULL-terminal drawer to THIS terminal is SKIPPED, leaving
         the NULL in place, whenever this terminal already holds an open
         drawer for that same `company_id`. Not attempted-and-caught: BINDING
         WOULD VIOLATE `idx_cash_sessions_one_open_per_terminal`, the
         partial UNIQUE index over exactly (company_id, terminal_id), and a
         boot-time reconciliation raising `IntegrityError` out of
         `init_retail()` -- which `app.py::init_app()` calls unconditionally,
         no handler -- would turn "help this drawer find its terminal" into
         "the POS will not start", the exact failure category this whole
         file exists to keep off the boot path. The check is a real
         same-transaction SELECT immediately before each UPDATE, not a
         stale snapshot: binding one orphan for a company makes it visible
         to the check run for the NEXT orphan of that same company, so two
         or more NULL-terminal drawers stacked on one company correctly
         bind at most one of them and leave the rest NULL rather than
         raising on the second.

         A `company_id IS NULL` row is the one exception to "check first":
         SQL uniqueness treats a NULL as distinct from every other value
         including another NULL, and that rule applies to EVERY column of a
         composite key -- so two open sessions with a NULL company_id can
         never collide in this index no matter what terminal_id either one
         carries. Running the same collision check against a NULL company
         would ask a question the index itself is never going to ask, and
         answering it "yes, this collides" would leave a live drawer
         unbound for a constraint that was never going to object to it --
         the identical reasoning `_v16_collision_probe`'s two `IS NOT NULL`
         predicates already state for the migration proper.

      4. Never write, alter or fabricate a money column. Only `terminal_id`
         is written, only on rows this function selected by `status='open'
         AND terminal_id IS NULL`, so no float, count, or variance already
         on a row is at any risk of being touched here.

      CHEAP ON THE COMMON PATH, WHICH IS EVERY LAUNCH AFTER THE FIRST WHERE
      IDENTITY ALREADY EXISTED AT MIGRATION TIME. `local_terminal_id()` is
      checked before any query touches `cash_sessions` at all, so a build
      with no device identity yet costs one file-system check per boot, not
      one. When an identity DOES exist, the very next thing this function
      does is a single indexed COUNT against
      `idx_cash_sessions_one_open_per_terminal` (partial on `status='open'`,
      covering `terminal_id`) -- on the overwhelmingly common case, where
      step 4 already bound everything at migration time, that COUNT is zero
      and this function returns immediately having issued exactly one query.

      Commits its own work: unlike the steps inside
      `_migrate_bind_cash_drawer_to_terminal`, which all sit inside
      `ensure_schema_version`'s own transaction, this function runs AFTER
      that call returns, so nothing else in `_init_retail` commits on its
      behalf.
    """
    live_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if 'cash_sessions' not in live_tables:
        return

    columns = {
        row[1] for row in conn.execute('PRAGMA table_info("cash_sessions")').fetchall()
    }
    if 'terminal_id' not in columns:
        # v13 has not run against this database (see the identical guard and
        # reasoning in _migrate_bind_cash_drawer_to_terminal above) -- there
        # is no column to bind and no device identity is meaningful yet.
        return

    # Rule 1, checked BEFORE any query touches cash_sessions: a build that
    # cannot yet name its own terminal has nothing this function can do, and
    # the cost of finding that out is one file read, not one database query.
    terminal = local_terminal_id()
    if not terminal:
        return

    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        # The cheap common path: one indexed COUNT, and out. On every launch
        # after the one where identity already existed at migration time,
        # step 4 above has already bound everything there is to bind, and
        # this returns having done a single query.
        orphan_count = conn.execute(
            "SELECT COUNT(*) FROM cash_sessions WHERE status=? AND terminal_id IS NULL",
            (CASH_SESSION_STATUS_OPEN,),
        ).fetchone()[0]
        if not orphan_count:
            return

        orphans = conn.execute(
            "SELECT id, company_id FROM cash_sessions "
            "WHERE status=? AND terminal_id IS NULL "
            "ORDER BY opened_at ASC, id ASC",
            (CASH_SESSION_STATUS_OPEN,),
        ).fetchall()
        bound = 0
        for row in orphans:
            company_id = row['company_id']
            if company_id is not None:
                # Rule 3: re-checked fresh for every orphan, not from a
                # snapshot taken before this loop started, so binding one
                # orphan for a company is visible to the check for the next
                # one -- see this function's own docstring for why that is
                # what keeps two stacked orphans from raising on the second.
                collision = conn.execute(
                    "SELECT 1 FROM cash_sessions "
                    "WHERE status=? AND terminal_id=? AND company_id=? LIMIT 1",
                    (CASH_SESSION_STATUS_OPEN, terminal, company_id),
                ).fetchone()
                if collision:
                    continue
            conn.execute(
                "UPDATE cash_sessions SET terminal_id=? WHERE id=?",
                (terminal, row['id']),
            )
            bound += 1
        if bound:
            conn.commit()
    finally:
        conn.row_factory = previous_factory


def init_retail():
    """Create/upgrade retail.db and seed it on first boot.

    A thin wrapper around `_init_retail` so the connection is closed on EVERY
    exit, including the exceptional one. It previously was not: the single
    `conn.close()` lived at the end of the body, so a migration that raised
    skipped it entirely and left the connection alive inside the propagating
    traceback -- frames keep their locals, and the caller
    (`app.py::init_app()`, which calls this unconditionally) lets the
    exception through to be logged, which keeps the traceback alive too.

    The consequence was a misdiagnosis, not just a leak. That connection is
    holding the WAL write lock it took when the migration's first DML opened
    Python's implicit transaction, so the NEXT launch failed with "database
    is locked" -- a message that points at concurrency and says nothing at
    all about the migration that actually broke. A support engineer reading
    it goes looking for a second copy of the app.

    The rollback is conditional on `in_transaction` rather than
    unconditional: on the success path everything downstream has already
    committed (`ensure_schema_version` commits after the migrate step and
    again after advancing `user_version`; `_seed_retail` commits its own
    work), so an unconditional rollback would be a no-op that reads like a
    discard. On the failure path it releases the write lock explicitly
    instead of relying on close() to do it.
    """
    conn = get_retail_conn()
    try:
        _init_retail(conn)
    finally:
        try:
            if conn.in_transaction:
                conn.rollback()
        except sqlite3.Error:
            # Never let cleanup replace the real exception with a worse one:
            # whatever went wrong in the migration is the thing the operator
            # needs to read, and close() below releases the lock regardless.
            pass
        conn.close()


def _init_retail(conn):
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS branches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        address TEXT,
        phone TEXT,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        sku TEXT NOT NULL,
        barcode TEXT,
        name TEXT NOT NULL,
        category_id INTEGER,
        cost_price REAL DEFAULT 0,
        sell_price REAL DEFAULT 0,
        tax_rate REAL DEFAULT 0,
        unit TEXT DEFAULT 'pcs',
        reorder_level INTEGER DEFAULT 5,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        -- ON DELETE SET NULL, not a bare REFERENCES: a category delete
        -- relayed in from ANOTHER device must never be able to fail against
        -- this device's own product links (products are not synced, so two
        -- devices on one license legitimately hold different product ->
        -- category assignments). See
        -- _migrate_products_category_fk_on_delete_set_null above.
        FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS inventory_movements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        product_id INTEGER NOT NULL,
        branch_id INTEGER,
        movement_type TEXT NOT NULL,
        quantity REAL NOT NULL,
        unit_cost REAL DEFAULT 0,
        reference TEXT,
        notes TEXT,
        created_by TEXT DEFAULT 'System',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS inventory_balances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        product_id INTEGER NOT NULL,
        branch_id INTEGER NOT NULL,
        quantity_on_hand REAL DEFAULT 0,
        quantity_reserved REAL DEFAULT 0,
        UNIQUE(company_id, product_id, branch_id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        address TEXT,
        loyalty_points REAL DEFAULT 0,
        total_spent REAL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        address TEXT,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        -- PO-preview-by-supplier foundation (schema v6): procurement
        -- metadata the split preview's MOQ warning reads. See
        -- _migrate_add_supplier_contacts_and_po_split below -- included
        -- here too so a brand-new install gets v6 shape directly without
        -- ever running that migration, same as sync_outbox/sync_cursor.
        min_order_value REAL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS purchase_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        po_number TEXT UNIQUE,
        supplier_id INTEGER,
        branch_id INTEGER,
        status TEXT DEFAULT 'pending',
        subtotal REAL DEFAULT 0,
        tax_total REAL DEFAULT 0,
        total REAL DEFAULT 0,
        notes TEXT,
        ordered_at TEXT,
        received_at TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        -- PO-preview-by-supplier foundation (schema v6): split-group/
        -- routing/idempotency columns -- the writers that populate them
        -- land later this week, this only adds the columns. See
        -- _migrate_add_supplier_contacts_and_po_split below -- included
        -- here too so a brand-new install gets v6 shape directly without
        -- ever running that migration, same as sync_outbox/sync_cursor.
        split_group_id TEXT,
        split_index INTEGER,
        split_count INTEGER,
        routing_status TEXT,
        routed_channel TEXT,
        routed_at TEXT,
        routed_to TEXT,
        idempotency_key TEXT,
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    );
    CREATE TABLE IF NOT EXISTS purchase_order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        po_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity REAL NOT NULL,
        unit_cost REAL NOT NULL,
        total REAL NOT NULL,
        received_qty REAL DEFAULT 0,
        FOREIGN KEY (po_id) REFERENCES purchase_orders(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        sale_number TEXT UNIQUE,
        branch_id INTEGER,
        customer_id INTEGER,
        cashier TEXT DEFAULT 'POS',
        subtotal REAL DEFAULT 0,
        discount_amount REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        total REAL DEFAULT 0,
        amount_paid REAL DEFAULT 0,
        change_amount REAL DEFAULT 0,
        payment_method TEXT DEFAULT 'cash',
        status TEXT DEFAULT 'completed',
        idempotency_key TEXT UNIQUE,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sale_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity REAL NOT NULL,
        unit_price REAL NOT NULL,
        discount_pct REAL DEFAULT 0,
        tax_rate REAL DEFAULT 0,
        line_total REAL NOT NULL,
        FOREIGN KEY (sale_id) REFERENCES sales(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS returns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        return_number TEXT UNIQUE,
        sale_id INTEGER,
        branch_id INTEGER,
        cashier TEXT DEFAULT 'POS',
        reason TEXT,
        refund_method TEXT DEFAULT 'cash',
        refund_amount REAL DEFAULT 0,
        status TEXT DEFAULT 'completed',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (sale_id) REFERENCES sales(id)
    );
    CREATE TABLE IF NOT EXISTS return_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        return_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity REAL NOT NULL,
        unit_price REAL NOT NULL,
        line_total REAL NOT NULL,
        FOREIGN KEY (return_id) REFERENCES returns(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        sale_id INTEGER,
        method TEXT NOT NULL,
        amount REAL NOT NULL,
        reference TEXT,
        status TEXT DEFAULT 'success',
        idempotency_key TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (sale_id) REFERENCES sales(id)
    );
    CREATE TABLE IF NOT EXISTS tax_rates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        rate REAL NOT NULL,
        is_default INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS journal_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        reference TEXT,
        description TEXT,
        debit_account TEXT,
        credit_account TEXT,
        amount REAL NOT NULL,
        entry_type TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        user_id TEXT,
        action TEXT NOT NULL,
        entity TEXT,
        entity_id INTEGER,
        details TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    -- Multi-device sync foundation (2026-08-06): sync_outbox holds locally
    -- committed writes not yet relayed to the Owner; sync_cursor is a
    -- single-row (id=1) high-water-mark of the last Owner-side seq this
    -- device has pulled. Consumed by Task 4 (routes write to sync_outbox)
    -- and Task 5 (sync client reads/writes both).
    CREATE TABLE IF NOT EXISTS sync_outbox (
        id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sync_cursor (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        last_seq INTEGER NOT NULL DEFAULT 0
    );
    INSERT OR IGNORE INTO sync_cursor (id, last_seq) VALUES (1, 0);
    -- Phase 5 (money-moving sync, launch-readiness): sync_apply_quarantine is
    -- the APPLY-side twin of Owner's own push-side quarantine (681b0fa,
    -- owner/app/sync/quarantine_routes.py) -- same philosophy, opposite end
    -- of the wire. A pulled sale_item/return_item/return/payment event can
    -- legitimately arrive with its parent unresolvable on THIS device even
    -- though nothing here is malformed: Owner's own quarantine can skip a
    -- PARENT event (a malformed sale) while still relaying its already-valid
    -- CHILDREN (its sale_items), or a device's cursor range can genuinely
    -- split parent and child across two pulls. Letting the INSERT raise a
    -- real FK violation would abort apply_pull_result() entirely -- the
    -- cursor never advances, and EVERY event after the failing one (from
    -- every device on the license, not just the one that caused it) is
    -- retried and re-fails forever. Silently dropping the child is worse:
    -- money vanishes with no trace. This table is the third option Owner's
    -- own commit message names: "written verbatim, skipped so the batch
    -- applies and the cursor advances, and stays visible and replayable."
    -- SyncService._quarantine_apply_event / _retry_quarantined_events (Phase
    -- 5) are the only writers/readers. `(entity_id, event_type)` -- NOT
    -- `entity_id` alone -- is the PRIMARY KEY, so re-parking the same
    -- still-blocked event on every retry tick is a no-op (INSERT OR IGNORE)
    -- rather than an ever-growing pile of duplicate rows for one stuck
    -- event. `entity_id` alone would be too coarse: a `payment`'s `create`
    -- and its later `void` share one entity_id (the payment's own wire
    -- `uid`) but are DIFFERENT events that can each independently need
    -- quarantining -- keying on entity_id alone would let a quarantined
    -- `void` silently overwrite (INSERT OR IGNORE no-ops, so it would
    -- instead silently DROP) a still-pending `create` row for the same id,
    -- or vice versa, depending on which arrived first. Currently
    -- unreachable in practice (void refuses a sale-tied payment outright,
    -- the only kind of payment event this table would ever see -- see
    -- sync_service.py's `_apply_event` "payment" branch), but cheap
    -- insurance against a later wave that relaxes that refusal.
    CREATE TABLE IF NOT EXISTS sync_apply_quarantine (
        entity_id TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        reason TEXT NOT NULL,
        detail TEXT,
        quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (entity_id, event_type)
    );
    -- PO-preview-by-supplier foundation (schema v6): a supplier can have
    -- several named contacts (orders/accounts/general), each with its own
    -- preferred channel -- read by core/retail/po_split.py's contact
    -- resolution ladder. See _migrate_add_supplier_contacts_and_po_split
    -- below -- included here too so a brand-new install gets v6 shape
    -- directly without ever running that migration, same as sync_outbox/
    -- sync_cursor just above.
    CREATE TABLE IF NOT EXISTS supplier_contacts (
        id TEXT PRIMARY KEY,
        company_id TEXT,
        supplier_id TEXT NOT NULL REFERENCES suppliers(id),
        name TEXT NOT NULL,
        role TEXT DEFAULT 'orders',
        email TEXT,
        phone TEXT,
        whatsapp TEXT,
        channel_preference TEXT DEFAULT 'whatsapp',
        is_primary INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_supplier_contacts_supplier
        ON supplier_contacts(company_id, supplier_id, status);
    """)
    # idx_po_split_group / idx_po_idempotency are deliberately NOT created
    # here even though a brand-new install already has the columns (line
    # ~1020 above): this executescript runs BEFORE ensure_schema_version()
    # below, so on any pre-v6 install whose purchase_orders table predates
    # split_group_id/idempotency_key, an unconditional CREATE INDEX on those
    # columns crashes init_retail() before the version-gated migration ever
    # gets a chance to ALTER them in. _migrate_add_supplier_contacts_and_po_split
    # already creates both indexes safely (column-existence-checked ALTER,
    # then CREATE INDEX IF NOT EXISTS) for both fresh and legacy installs --
    # see ensure_schema_version() call below. Found via a real desktop .exe
    # launch against a genuine pre-v6 AppData database, not a synthetic test.
    conn.commit()

    # Wave 1B (Part K) / multi-device sync foundation (2026-08-06): first
    # real Retail schema changes -- see _migrate_retail_schema above, which
    # chains the categories/products/customers/suppliers UUID migrations, the
    # products.supplier_id and PO-split additions, and finally the einvoice_*
    # tables (docs/einvoicing/phase1/) that reached this file from master's
    # own, superseded v2/v3 numbering (see the RETAIL_SCHEMA_VERSION comment
    # above for the full merge-reconciliation story).
    # Mirrors products/clinic/backend/database/schema.py's identical
    # ensure_schema_version pattern; see
    # commercial_runtime/security/migration_safety.py.
    from commercial_runtime.security.migration_safety import ensure_schema_version
    ensure_schema_version(
        conn, _get_path('retail'), RETAIL_SCHEMA_VERSION, _migrate_retail_schema,
        backup_dir=os.path.join(BASE_DIR, 'migration_backups'),
    )

    # NOT version-gated, and deliberately outside the block above: this is a
    # boot-time reconciliation, not a one-time migration step, and it must
    # run every launch regardless of whether `user_version` moved this time.
    # See _v16_rebind_orphaned_open_drawers's own docstring for the gap this
    # closes -- schema v16's own backfill can only bind an open drawer to a
    # device identity that exists AT THE MOMENT v16 RUNS, and that identity
    # is not created until the first post-login /api/devices/me call, which
    # is always later than the process-boot init_retail() that just ran
    # above. Placed here, right after the migration this reconciles against,
    # by the same "run every launch" reasoning as everything below it in this
    # function.
    _v16_rebind_orphaned_open_drawers(conn)

    if cur.execute("SELECT COUNT(*) FROM branches").fetchone()[0] == 0 and not _is_standalone():
        _seed_retail(conn, cur)
    # No conn.close() here -- init_retail()'s finally owns that now, so it
    # runs on the exceptional path too. See its docstring.


def _seed_retail(conn, cur, company_id=1):
    """Seed demo data for ONE company. `company_id` defaults to 1 to preserve
    the existing dev-mode first-boot behaviour. Every INSERT is parameterized
    on company_id -- callers that need to reset a specific company's demo
    data (see api/retail_api.py::demo_seed) must pass company_id explicitly."""
    now = datetime.now()
    cid = company_id

    cur.executemany("INSERT INTO branches (company_id,name,address,phone) VALUES (?,?,?,?)", [
        (cid, 'Main Branch', '100 Market Street, Downtown', '+1-555-1001'),
        (cid, 'North Branch', '45 Commerce Ave, Northside', '+1-555-1002'),
        (cid, 'West Mall', '200 Shopping Blvd, Westgate', '+1-555-1003'),
    ])

    # categories.id is TEXT (client-generated UUID) since the multi-device
    # sync foundation migration (see _migrate_categories_to_uuid) -- there is
    # no autoincrement to fall back on, so seed data must generate its own
    # ids explicitly, same as any real device would.
    import uuid as _uuid
    category_names = ['Electronics', 'Clothing', 'Food & Beverages', 'Home & Living', 'Health & Beauty']
    category_ids = [str(_uuid.uuid4()) for _ in category_names]
    cur.executemany("INSERT INTO categories (id,company_id,name) VALUES (?,?,?)", [
        (category_ids[i], cid, category_names[i]) for i in range(len(category_names))
    ])

    # suppliers.id is TEXT (client-generated UUID) since the multi-device
    # sync foundation migration (see _migrate_suppliers_to_uuid) -- there is
    # no autoincrement to fall back on, so seed data must generate its own
    # ids explicitly, same as categories/products/customers above.
    supplier_ids = [str(_uuid.uuid4()) for _ in range(3)]
    cur.executemany("INSERT INTO suppliers (id,company_id,name,phone,email) VALUES (?,?,?,?,?)", [
        (supplier_ids[0], cid, 'TechDistrib Ltd', '+1-555-9001', 'orders@techdistrib.com'),
        (supplier_ids[1], cid, 'FashionWholesale Co', '+1-555-9002', 'supply@fashionwholesale.com'),
        (supplier_ids[2], cid, 'GroceryDirect', '+1-555-9003', 'bulk@grocerydirect.com'),
    ])

    cur.execute("INSERT INTO tax_rates (company_id,name,rate,is_default) VALUES (?,'Standard VAT',15,1)", (cid,))
    cur.execute("INSERT INTO tax_rates (company_id,name,rate,is_default) VALUES (?,'Zero Rated',0,0)", (cid,))
    cur.execute("INSERT INTO tax_rates (company_id,name,rate,is_default) VALUES (?,'Reduced Rate',5,0)", (cid,))

    # 4th element is a 1-based index into category_names/category_ids above
    # (1=Electronics .. 5=Health & Beauty), preserved from the source data's
    # positional convention -- resolved to the real generated UUID below,
    # since category_id is no longer a small autoincrement int.
    products = [
        ('SKU-R001', '6001001', 'Laptop 15" Pro', 1, 750.0, 1199.99, 15, 'pcs', 3),
        ('SKU-R002', '6001002', 'Wireless Earbuds', 1, 25.0, 59.99, 15, 'pcs', 10),
        ('SKU-R003', '6001003', 'Smart Watch', 1, 85.0, 199.99, 15, 'pcs', 5),
        ('SKU-R004', '6001004', 'USB-C Charger', 1, 8.0, 24.99, 15, 'pcs', 15),
        ('SKU-R005', '6001005', 'Classic T-Shirt', 2, 4.5, 14.99, 0, 'pcs', 20),
        ('SKU-R006', '6001006', 'Denim Jeans', 2, 18.0, 49.99, 0, 'pcs', 10),
        ('SKU-R007', '6001007', 'Running Shoes', 2, 35.0, 89.99, 0, 'pcs', 8),
        ('SKU-R008', '6001008', 'Mineral Water 1L', 3, 0.3, 1.49, 0, 'pcs', 50),
        ('SKU-R009', '6001009', 'Orange Juice 1L', 3, 0.8, 2.99, 0, 'pcs', 40),
        ('SKU-R010', '6001010', 'Coffee Blend 500g', 3, 5.0, 12.99, 0, 'pcs', 25),
        ('SKU-R011', '6001011', 'LED Desk Lamp', 4, 12.0, 34.99, 15, 'pcs', 8),
        ('SKU-R012', '6001012', 'Shampoo 400ml', 5, 2.5, 7.99, 5, 'pcs', 20),
    ]
    # products.id is TEXT (client-generated UUID) since the multi-device sync
    # foundation migration (see _migrate_products_to_uuid) -- there is no
    # autoincrement to fall back on, so seed data must generate its own ids
    # explicitly, same as any real device would (mirrors the categories fix
    # above; `_uuid` is already imported there, reused here).
    product_ids = [str(_uuid.uuid4()) for _ in products]
    for i, p in enumerate(products):
        cat_id = category_ids[p[3] - 1]
        row = (p[0], p[1], p[2], cat_id) + p[4:]
        cur.execute(
            "INSERT INTO products (id,company_id,sku,barcode,name,category_id,cost_price,sell_price,tax_rate,unit,reorder_level) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (product_ids[i], cid) + row,
        )

    cur.execute("SELECT id FROM products WHERE company_id=?", (cid,))
    prod_ids = [r[0] for r in cur.fetchall()]
    for pid in prod_ids:
        qty = random.randint(10, 150)
        cur.execute(
            "INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,1,?)",
            (cid, pid, qty)
        )
        cur.execute(
            "INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,2,?)",
            (cid, pid, random.randint(5, 60))
        )

    customers = [
        ('Ahmed Al-Mansouri', '+1-555-2001', 'ahmed@email.com', 450, 3200.0),
        ('Sara Johnson', '+1-555-2002', 'sara@email.com', 120, 890.0),
        ('Michael Chen', '+1-555-2003', 'mchen@email.com', 800, 6500.0),
        ('Fatima Al-Hassan', '+1-555-2004', 'fatima@email.com', 60, 340.0),
        ('Carlos Rivera', '+1-555-2005', 'carlos@email.com', 290, 2100.0),
    ]
    # customers.id is TEXT (client-generated UUID) since the multi-device
    # sync foundation migration (see _migrate_customers_to_uuid) -- there is
    # no autoincrement to fall back on, so seed data must generate its own
    # ids explicitly, same as categories/products above.
    customer_ids = [str(_uuid.uuid4()) for _ in customers]
    for i, c in enumerate(customers):
        cur.execute(
            "INSERT INTO customers (id,company_id,name,phone,email,loyalty_points,total_spent) VALUES (?,?,?,?,?,?,?)",
            (customer_ids[i], cid) + c,
        )

    from core.retail import pricing as _tax_engine
    try:
        _row = cur.execute(
            "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='tax_calculation_mode'", (cid,)
        ).fetchone()
        tax_mode = _tax_engine.normalize_mode(_row[0] if _row else None)
    except Exception:
        tax_mode = _tax_engine.DEFAULT_MODE  # retail_settings not created yet (fresh install) -- use the default
    sale_num = 1000
    methods = ['cash', 'cash', 'card', 'card', 'digital_wallet']
    for i in range(40):
        d = (now - timedelta(days=random.randint(0, 30))).strftime('%Y-%m-%d %H:%M:%S')
        branch_id = random.choice([1, 2])
        # customer_ids holds real generated UUIDs (customers.id is TEXT now,
        # see _migrate_customers_to_uuid) -- picking from it, not a
        # hardcoded 1-5 range, mirrors the cat_id fix above for the same
        # reason: a stale small-int id would never match any real customer
        # row via `s.customer_id=c.id`, silently showing every demo sale as
        # "Walk-in".
        cust_id = random.choice(customer_ids + [None])
        method = random.choice(methods)
        num_items = random.randint(1, 4)
        subtotal = 0
        tax_total = 0
        sale_num += 1
        sn = f'S-{sale_num}'
        cur.execute(
            "INSERT INTO sales (company_id,sale_number,branch_id,customer_id,cashier,payment_method,status,created_at) VALUES (?,?,?,?,?,?,'completed',?)",
            (cid, sn, branch_id, cust_id, 'Cashier', method, d)
        )
        sid = cur.lastrowid
        for _ in range(num_items):
            pid = random.choice(prod_ids)
            row = cur.execute("SELECT sell_price,tax_rate FROM products WHERE id=?", (pid,)).fetchone()
            price, trate = row[0], row[1]
            qty = random.randint(1, 3)
            disc = random.choice([0, 0, 0, 5, 10])
            _calc = _tax_engine.calculate_line(price, qty, discount_pct=disc, tax_rate=trate, mode=tax_mode)
            line = round(_calc['gross'] - _calc['discount_amount'], 2)
            tax = _calc['tax']
            subtotal += line
            tax_total += tax
            cur.execute(
                "INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,discount_pct,tax_rate,line_total) VALUES (?,?,?,?,?,?,?)",
                (sid, pid, qty, price, disc, trate, line)
            )
            cur.execute(
                "INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,unit_cost,reference,created_by) VALUES (?,?,?,?,?,?,?,?)",
                (cid, pid, branch_id, 'sale_out', -qty, price * 0.6, sn, 'System')
            )
        total = round(subtotal + tax_total, 2)
        cur.execute(
            "UPDATE sales SET subtotal=?,tax_amount=?,total=?,amount_paid=?,change_amount=0 WHERE id=?",
            (round(subtotal, 2), round(tax_total, 2), total, total, sid)
        )

    # ── the demo shop's opening declaration (Phase 3, schema v15) ───────────
    #
    # Everything above writes `inventory_balances` rows out of thin air and
    # then posts `sale_out` movements against them, so the demo database
    # arrived with a cache its own ledger flatly contradicted: 120 on hand
    # for a product whose entire recorded history is "-8". That was a real
    # inconsistency in seed data, not a rounding artefact, and until v15 it
    # merely made `stock_reconciliation.compute_drift` report every seeded
    # product forever. It stopped being harmless at v15 for two reasons:
    #
    #   * v15's gate refuses to advance `PRAGMA user_version` while a
    #     REPAIRABLE balance cannot be reproduced from the ledger. This
    #     seeder runs AFTER the migration on a fresh install, so such a
    #     database is born past the gate and fails it the next time ANY
    #     migration runs -- v16 is already reserved in ROADMAP.md. Every
    #     demo and dev install would have refused to start, on a defect
    #     planted months earlier by seed data.
    #   * the Repair action rewrites a drifted balance from its ledger total.
    #     On these rows that total is NEGATIVE (sales with nothing to sell
    #     from), so repairing a demo database drove its whole catalogue below
    #     zero -- while reporting success.
    #
    # The fix is the honest one and it changes no balance: the demo shop
    # DECLARES the opening stock it must have had, exactly as
    # api/import_api.py records a real operator's opening declaration
    # ('opening_stock' / 'OPENING'). Declared quantity is what is on hand now
    # plus everything sold since, so the ledger sums to the cached balance
    # and the demo database can pass the same gate a customer's does.
    #
    # Computed per (product, branch) from what was actually written rather
    # than from the quantities chosen above, so it stays correct if the sales
    # loop, the branch ids or the balance rows ever change shape.
    residuals = cur.execute(
        "SELECT b.product_id, b.branch_id, "
        "       b.quantity_on_hand - COALESCE(("
        "           SELECT SUM(m.quantity) FROM inventory_movements m "
        "           WHERE m.company_id=b.company_id AND m.product_id=b.product_id "
        "             AND m.branch_id IS b.branch_id), 0) AS opening "
        "FROM inventory_balances b WHERE b.company_id=?",
        (cid,)
    ).fetchall()
    for product_id, branch_id, opening in residuals:
        if not opening:
            continue
        cur.execute(
            "INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,reference,notes,created_by) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (cid, product_id, branch_id, 'opening_stock', float(opening), 'OPENING',
             'Demo opening stock', 'System')
        )

    conn.commit()
