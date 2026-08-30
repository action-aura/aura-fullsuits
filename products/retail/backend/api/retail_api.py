"""
Aura Retail — Retail & POS API
Full-suite retail management: products, POS, customers, suppliers,
purchase orders, returns, reports.

Extracted verbatim (business logic unchanged) from Action Aura Enterprise's
api/subsystems/retail_api.py -- see docs/migration/retail-extraction-report.md.
"""
import os
import re
import csv
import io
import time
import json
import logging
import sqlite3
import uuid as _uuid
import requests
from decimal import Decimal, ROUND_HALF_UP
from flask import Blueprint, request, jsonify, session, current_app, Response, stream_with_context
from commercial_runtime.identity.mt_auth import (
    mt_login_required, mt_require_subsystem, mt_require_capability, session_has_capability,
    CAPABILITY_DENIED_MESSAGE,
)
from commercial_runtime.identity.user_accounts import (
    CAP_SELL, CAP_REFUND, CAP_DISCOUNT, CAP_STOCK_ADJUST, CAP_REPORTS,
    CAP_CASH_CLOSE, CAP_CASH_APPROVE, CAP_EMPLOYEES,
    # The ONE format every *_at_utc column in this suite is written in --
    # timezone-aware '...+00:00', never `datetime.utcnow()`'s naive string.
    # Imported rather than re-spelled inline so retail's `created_at_utc` and
    # registry's `users.updated_at_utc` can never drift into two forms that
    # sort wrong against each other; see that function's docstring for the
    # concrete failure ('2026-08-20T10:00:00' compares as LATER than
    # '2026-08-20T09:00:00+00:00' as text).
    now_utc_iso,
)
from commercial_runtime.identity import device_context
from commercial_runtime.identity.registry_db import get_conn as _registry_conn
from commercial_runtime.licensing_contracts.flask_guard import make_capability_guard
from commercial_runtime.sync.sync_service import nudge as _sync_nudge
from commercial_runtime.sync.sync_service import get_active_health as _sync_get_active_health
from commercial_runtime.sync.sync_service import SYNC_STALE_THRESHOLD_SECONDS as _SYNC_STALE_THRESHOLD_SECONDS
from commercial_runtime.sync.sync_service import (
    SYNC_SALES_STOP_THRESHOLD_SECONDS as _SYNC_SALES_STOP_THRESHOLD_SECONDS,
)
from commercial_runtime.sync.sync_service import (
    record_offline_override as _sync_record_offline_override,
)
# Launch-readiness chain wave C1 (ROADMAP.md's 2026-08-30 "the multi-branch
# capture defect" entry; docs/launch-readiness/seats-and-chain-design.md
# §5.2 gap 1). `SyncService._resolve_branch_id`/`_log_branch_fallback_summary`
# are reused rather than duplicated -- see `_resolve_working_branch` below
# (right after `_default_branch`) for why a static method on this class is
# still "cleanly importable": Python does not restrict attribute access by a
# leading underscore, and this module already imports several other names
# out of sync_service.py above.
from commercial_runtime.sync.sync_service import SyncService as _SyncService
from commercial_runtime.identity.onboarding_routes import (
    get_device_branch_uid as _onboarding_get_device_branch_uid,
    set_device_branch_uid as _onboarding_set_device_branch_uid,
)
from commercial_runtime.notifications import settings as _notification_settings
from commercial_runtime.notifications.outbox import EmailOutboxRepository as _EmailOutboxRepository
from commercial_runtime.notifications import whatsapp_settings as _whatsapp_settings
from database.schema import (
    get_retail_conn, sub_create, local_terminal_id,
    # The drawer's status vocabulary and v16's force-end markers. IMPORTED,
    # never re-spelled: schema.py's migration writes these exact values onto
    # real rows, and a second copy of the word 'ended' living in this file is
    # how a route comes to disagree with the migration that produced the row
    # it is reading. Same discipline as `now_utc_iso` above.
    CASH_SESSION_STATUS_OPEN, CASH_SESSION_STATUS_ENDED, CASH_SESSION_STATUS_CLOSED,
    V16_UNVERIFIED_END_REASONS,
    # Launch-readiness Phase 7 stage 7d-iii: THE canonical `stock_exceptions`
    # upsert -- see its own docstring in schema.py. Imported and called
    # directly (unlike commercial_runtime/sync/sync_service.py's own use of
    # the SAME function, which reaches it through an injected constructor
    # collaborator instead): retail_api.py already lives in this module's
    # own product, so there is no layering concern importing it here.
    record_or_refresh_stock_exception,
)
from datetime import datetime, timedelta, timezone
from core.retail import pricing as tax_engine
from core.retail import po_split
# The single definition of revenue / gross sales / returns / average ticket /
# COGS, plus the period and branch predicates. Read that module's docstring
# before adding any new sales figure to this file -- it exists because those
# figures were previously hand-written about seven times here and disagreed
# with each other on screen.
from core.retail import metrics
# The single definition of "what SHOULD this product's balance be?" --
# recomputed from the inventory_movements ledger. See its module docstring:
# inventory_balances is a cache, the ledger is the truth.
from core.retail import stock_reconciliation
from core.retail import whatsapp_hook as _whatsapp_hook
# Promotions resolver (schema v23, launch-readiness "promotions, wave 1").
# Aliased, not `from ... import promotions`, so it cannot collide with the
# `promotions` local variable name inside create_sale/list routes below --
# same discipline `tax_engine` (core.retail.pricing) already uses on this
# import block for the identical reason.
from core.retail import promotions as promo_engine
from config import (
    DATABASE_DIR, AURA_AI_ENDPOINT_URL, AURA_AI_BEARER_TOKEN, AURA_AI_TIMEOUT_SECONDS,
    AURA_AI_MODEL_NAME,
)

retail_bp = Blueprint('retail_api', __name__, url_prefix='/api/sub/retail')

#: Module logger. Deliberately at module scope rather than the `import
#: logging` inside a handler this file used to do in one place: a swallowed
#: failure that is only reported when somebody remembers to import logging at
#: the call site is a failure nobody hears about. See ACTOR_LOOKUP_FAILURES
#: below for the specific silence this closes.
log = logging.getLogger(__name__)

# Phase 7 Part T -- the one enforcement choke point every mutation route
# below is guarded with. See docs/licensing/phase7/
# retail-restriction-capability-matrix.md for the full route-to-capability
# mapping and the reasoning behind each allow/block decision (grounded in
# reading these handlers, not assumed).
require_license_capability = make_capability_guard(os.path.dirname(DATABASE_DIR))

# Capabilities that remain available once licensing enters a restricted
# state. Read access plus returns (server-authoritative reversal of an
# existing sale -- a lawful refund obligation) plus customer payments
# (paying down existing debt, cash-flow positive, goodwill-critical) --
# see the matrix doc's "Decision" section for the reasoning behind each.
# Everything else -- new sales, new products/suppliers/customers, stock
# adjustment, purchasing, outbound supplier/PO payments, settings/staff
# changes -- is blocked.
RETAIL_RESTRICTED_ALLOWLIST = frozenset({
    "retail.records.read",
    "retail.report.view",
    "retail.return.create",
    "retail.customer.payment.record",
    "retail.backup.create",
    "retail.backup.restore",
    "retail.data.export",
})

# ══════════════════════════════════════════════════════════════════════════════
#  CAPABILITY GATING (Phase 1, design §3 "Permissions")
#
#  Until this pass, every route below carried one literal
#  @mt_require_subsystem('retail') and nothing finer. That decorator answers
#  "may this account touch Retail at all?", so a shop that answered yes for a
#  cashier answered yes for adjusting stock, reading the whole debtor book,
#  changing the tax mode and wiping the company. @mt_require_capability now
#  answers the second question -- "may THIS PERSON do THIS?" -- against the
#  eight codes seeded into user_permissions
#  (commercial_runtime/identity/user_accounts.py).
#
#  BOTH still run. mt_require_subsystem is the LICENCE/module check and stays
#  exactly as it was; @require_license_capability is the licensing-STATE check
#  and stays where it was too. @mt_require_capability is written LAST, closest
#  to the view, so it runs last: an install whose licence is restricted should
#  be told that, not told the user lacks permission, which would send them to
#  their owner instead of to billing.
#
#  THE EIGHT CODES ARE AUTHORITIES, NOT ROUTE NAMES. There are eight of them
#  and ~80 routes, so several are broader than their name suggests. That is
#  deliberate: the design fixes the vocabulary at eight and every existing
#  account has already been migrated with a row per code, so inventing a ninth
#  would leave every account un-provisioned for it -- a code with no row is a
#  code nobody holds. Where a route's code is wider than its literal name, the
#  reason is stated at that route or in the section header above it.
#
#    retail.sell          ring a sale, park/recall a cart, and the customer
#                         record the till needs to ring a NAMED sale
#    retail.refund        reverse money already taken
#    retail.discount      give value away with no matching payment. Has no
#                         route of its own -- a discount is a FIELD on a sale
#                         and credit terms are fields on a customer -- so it is
#                         checked inside those two handlers with
#                         session_has_capability()
#    retail.stock.adjust  change what the shop's records say it has, and the
#                         procurement that changes it. Also the gate on
#                         master-data writes (products, categories, suppliers,
#                         contacts): no code in the fixed eight names "edit
#                         master data", and this is the one that governs
#                         changing the shop's own records with no customer
#                         transaction behind it -- and, more to the point, it
#                         is the right TIER, which is the decision that
#                         actually matters
#    retail.reports       see, send or generate the shop's numbers
#    retail.cash.close    operate THIS terminal's drawer: open, movements,
#                         close
#    retail.cash.approve  accept a cash variance. It now has exactly ONE route
#                         (POST /cash-sessions/<id>/approve), added by Phase 4
#                         together with the ENDED/CLOSED split this comment
#                         used to say it was waiting for. It is still NOT
#                         required to CLOSE, for the reason this comment gave
#                         when the route did not exist and which has not
#                         changed: a cashier who counted short could never end
#                         their shift. Ending needs retail.cash.close; only
#                         ACCEPTING the resulting variance needs this. Kept out
#                         of the manager default so that no role can both close
#                         a drawer and sign off its own shortfall
#    retail.employees     owner-level administration of the shop itself:
#                         settings, branches, payment methods, demo reset, and
#                         money paid OUT to suppliers. Read it as "owner
#                         authority" -- it is the code whose default grant is
#                         owner-only. Putting supplier and PO payments here
#                         also separates duties the way a shop should: the
#                         person who receives the delivery
#                         (retail.stock.adjust) is not the person who pays for
#                         it
#
#  Reads are NOT uniformly gated, on purpose. A till has to be able to list
#  products, find a customer and look up a sale, and none of the eight codes
#  names "browse the catalogue"; those stay on the subsystem gate. The reads
#  that disclose the shop's FINANCIAL POSITION -- every report, the debtor and
#  creditor books, the audit log, the stock-drift dump -- carry
#  retail.reports, because the gate has to match what an endpoint DISCLOSES,
#  not how little it writes (the same reasoning inventory_reconciliation's own
#  docstring already applies to itself).
#
#  The full route-to-code map is frozen as data in
#  products/retail/tests/retail_route_capability_matrix_test.py, which also
#  fails if any mutating route is added without one.
# ══════════════════════════════════════════════════════════════════════════════

#: Refusals for the two capability checks that are NOT a whole route (see
#: retail.discount above). Fixed literals, and keys in BOTH
#: products/retail/frontend/locales/en.json and ar.json -- this product ships
#: Arabic and is RTL, so a message the catalog does not know renders as raw
#: English inside an RTL layout. mt_auth.CAPABILITY_DENIED_MESSAGE is the
#: third and covers every route-level refusal.
DISCOUNT_DENIED_MESSAGE = 'You do not have permission to apply a discount.'
CREDIT_TERMS_DENIED_MESSAGE = 'You do not have permission to change credit terms.'
#: create_purchase_order's own amount_paid field (~1283 below) -- a THIRD
#: field-level check, same shape as the two above. Not yet a catalog key as
#: of this pass; report the exact English to whoever owns
#: products/retail/frontend/locales/{en,ar}.json before this ships.
SUPPLIER_PAYMENT_DENIED_MESSAGE = 'You do not have permission to record a payment to a supplier.'
#: launch-readiness Phase 6 stage 6b-iii-b -- a FOURTH field-level check, same
#: shape as the three above. `update_customer`'s route decorator floor is
#: retail.sell (a cashier-level default, so the till can still correct a
#: mistyped phone/address), but `delete_customer` -- the action a `status`
#: PATCH can undo -- is gated retail.stock.adjust (manager-level, "master-
#: data retirement", see that route's own comment). Without this separate
#: check, adding `status` to `update_customer`'s allowed fields would let any
#: cashier reverse a manager's deletion, a weaker path to the exact effect
#: deleting already requires the stronger capability for. Not yet a catalog
#: key as of this pass; report the exact English to whoever owns
#: products/retail/frontend/locales/{en,ar}.json before this ships.
CUSTOMER_RESTORE_DENIED_MESSAGE = 'You do not have permission to restore a deleted customer.'

#: Largest page GET /sales/recent will serve a caller who does NOT hold
#: retail.reports. 200 is not arbitrary: it is exactly what the returns flow
#: asks for (frontend/subsystem-retail.js::_findSaleForReturn fetches
#: `?limit=200` and matches the receipt client-side), so the till's own
#: request is unaffected while `?limit=100000` -- which used to return the
#: company's entire sales history in one response -- no longer is.
TILL_SALES_LOOKUP_MAX_LIMIT = 200

#: Absolute ceiling on GET /sales/recent for EVERY caller, including one that
#: holds retail.reports. TILL_SALES_LOOKUP_MAX_LIMIT above is the tighter
#: additional cap applied to callers who do not.
#:
#: This exists because the till cap was the ONLY cap: a reports-holding caller
#: had no upper bound at all, and -- worse -- `min(limit, N)` is not a ceiling
#: in the first place, since SQLite reads a negative LIMIT as unbounded. Both
#: halves are now clamped at parse time, before any branch reads the value.
#:
#: 500 rather than 200: the Sales History screen is a genuine reporting surface
#: and paging it at the till cap would be a regression for the people entitled
#: to use it. It is a viewer either way, not a bulk export.
SALES_HISTORY_MAX_LIMIT = 500


def clamp_page_limit(raw, default, ceiling):
    """Turn an untrusted `?limit=` query value into a safe page size.

    Exists as a named function, rather than inline at each call site, because
    the property is what needs testing and an inline expression can only be
    tested one input at a time. Three tests sat green over the bug this
    replaces, each asserting an outcome for the single input `limit=100000`.

    Two failure modes, both real and both shipped:

    * `min(raw, ceiling)` IS NOT A CEILING. SQLite reads a negative LIMIT as
      UNBOUNDED, so `?limit=-1` returned a shop's entire sales book -- every
      row, every total -- to a cashier holding no reports capability, through
      a route meant to answer a narrow till question.
    * `int(...)` straight off the query string turns `?limit=abc` into a 500.

    Anything unparseable falls back to `default` rather than raising: a
    malformed page size is a caller mistake, not a server error, and a 500
    tells an operator something is broken when nothing is.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, ceiling))

#: Columns `SELECT s.*` drags into the till's payload that name a PERSON
#: rather than a sale. `actor_user_uid` is a registry `users.uid` (another
#: database entirely) and `uid` is the sync identity; the desktop history
#: table renders neither, and Android resolves actors through the
#: retail.reports-gated by-employee report. Dropped for callers without that
#: capability rather than left lying in a response a cashier can read.
SALES_ROW_IDENTITY_COLUMNS = ('actor_user_uid', 'uid')

#: Default and hard ceiling for GET /products' new, OPTIONAL `?limit=`
#: (launch-readiness "the POS scale fix", ROADMAP.md's 2026-08-29 v21
#: entry). Unlike SALES_HISTORY_MAX_LIMIT/TILL_SALES_LOOKUP_MAX_LIMIT above,
#: these are never applied unless the caller's query string literally
#: contains `limit=` -- list_products' no-parameter path must stay
#: byte-compatible with today (the whole catalogue, no LIMIT clause at
#: all), since the frontend still calls it that way until the client-side
#: rewrite lands. DEFAULT only matters for an unparseable value
#: (`?limit=abc`); MAX is the ceiling `clamp_page_limit` enforces against
#: `?limit=-1`/`?limit=100000` the same way it already does for sales.
PRODUCTS_LIST_DEFAULT_LIMIT = 200
PRODUCTS_LIST_MAX_LIMIT = 1000

# ── Session helpers ────────────────────────────────────────────────────────────
def _cid():
    return session.get('company_id') or session.get('mt_company_id', 1)

def _uid():
    return session.get('mt_user_id') or session.get('user_id', 'system')


# ══════════════════════════════════════════════════════════════════════════════
#  THE v13 ATTRIBUTION STAMP  (WHO / WHERE / WHEN-in-real-time / WIRE IDENTITY)
#
#  Schema v13 (database/schema.py::_migrate_add_identity_and_attribution_
#  columns) added the columns; these three helpers are what actually puts a
#  value in them. Read that migration's docstring first -- it explains why each
#  column exists and, just as importantly, why it deliberately left every
#  HISTORICAL row NULL rather than guessing. That decision is only defensible
#  if every write from here on fills them in, which is what this block is for.
#
#  WHY THIS IS WORTH THIS MUCH COMMENT. An INSERT that omits these columns
#  does not fail. There is no NOT NULL, no constraint, no error; the sale
#  completes, the receipt prints, the totals are right, and integrity_check
#  says 'ok'. The damage is only visible much later, when someone asks who
#  authorised a refund and the honest answer is that the database cannot say
#  and can never be made to say -- exactly the state v13 refused to fabricate
#  its way out of for pre-v13 rows. Silent, deferred and irreversible is the
#  worst combination a defect can have, so both halves of the guard against it
#  are structural: `retail_attribution_stamp_structural_test.py` reads THIS
#  FILE'S SOURCE and fails on any INSERT into a v13 table that does not name
#  these columns, driven off schema.RETAIL_UID_TABLES/RETAIL_ACTOR_TABLES
#  rather than a list anyone has to maintain, and
#  `retail_attribution_stamp_test.py` drives the real routes and reads the
#  rows back.
#
#  RESOLVE ONCE PER REQUEST, NOT PER ROW. `_stamp()` costs one small registry
#  read plus one small file read, and a sale with twelve lines writes thirteen
#  stamped rows. Calling it per row would multiply that by twelve for no gain
#  AND would let the rows of one transaction disagree about when they happened,
#  which is worse than the cost: a sale and its own stock movements are one
#  event and should carry one instant.
# ══════════════════════════════════════════════════════════════════════════════

#: Every way an actor-identity lookup can decline to answer, counted.
#:
#: WHY A COUNTER AND NOT JUST A LOG LINE. The policy below -- write NULL
#: rather than a fallback identity -- is right and is not changing: a local
#: `users.id` parked in a wire column is undetectable and unrepairable,
#: whereas a NULL is honest and `account_schema._backfill_uids` can still fix
#: the row it came from. But the policy was implemented as a BARE
#: `except Exception: return None`, and that combination is the problem: a
#: transient `database is locked` on registry.db writes permanently NULL
#: `actor_user_uid` onto real financial rows -- sales, returns, cash
#: movements, stock adjustments -- and NOTHING anywhere records that it
#: happened. The rows look exactly like legitimate pre-v13 history. Nobody
#: can tell afterwards whether a shop's unattributed bucket is old data or a
#: five-minute outage last Tuesday.
#:
#: So: still NULL, still never raises, but now loud (ERROR, with the
#: exception) and countable. A caller that wants to expose this (a health
#: endpoint, a support bundle) reads this mapping; a test asserts the number
#: moved, which a log line alone cannot support.
#:
#: NOT an audit row. `record()` would have to write to a database on the
#: request path of a sale, and the overwhelmingly likely reason this lookup
#: failed in the first place is that a database was unavailable -- so the
#: audit write would fail too, at best, and compound a lock storm at worst.
#: The counter costs nothing and cannot fail.
ACTOR_LOOKUP_FAILURES = {
    # The registry read itself raised -- lock, missing file, corrupt page.
    'write_lookup_error': 0,
    # The read succeeded and there is no `users` row for this session's id.
    'write_no_user_row': 0,
    # The row exists but carries no uid yet (pre-v3 account, not yet
    # backfilled). Transient by design, but still a row written unattributed.
    'write_blank_uid': 0,
    # The REPORT-side bulk lookup failed; the figures still render, nameless.
    'read_lookup_error': 0,
}


def _note_actor_lookup_failure(reason, detail, exc=None):
    """Count it, then say so. Never raises -- see ACTOR_LOOKUP_FAILURES."""
    try:
        ACTOR_LOOKUP_FAILURES[reason] = ACTOR_LOOKUP_FAILURES.get(reason, 0) + 1
        log.error(
            "retail attribution: %s (%s). A financial row is being written or "
            "read with NO actor identity; this is the honest answer but it is "
            "NOT normal -- occurrence #%d for this reason since process start.",
            reason, detail, ACTOR_LOOKUP_FAILURES[reason], exc_info=exc is not None,
        )
    except Exception:  # pragma: no cover - a logger that throws must not lose a sale
        pass


def _actor_user_uid():
    """The signed-in user's WIRE identity -- registry `users.uid` -- or None.

    NOT `_uid()`. This is the single most likely wrong answer in this whole
    change, so it is worth being explicit about: `_uid()` returns
    `session['mt_user_id']`, which `mt_auth.create_session` sets to
    `user['id']` -- the LOCAL primary key, which registry v3's account_schema
    docstring describes as "a private detail of THIS install". `users.uid` is
    the separate value "a peer device names a user by". BOTH ARE uuid4
    STRINGS. Putting the id in this column would pass every local check, every
    `uuid.UUID()` gate, and every eye test, and would only surface when a
    second device tried to resolve the actor of a row and found nobody -- by
    which point months of rows carry it.

    Returns None rather than falling back to `_uid()` when the row has no uid.
    A NULL is honest and repairable (account_schema's `_backfill_uids` stamps
    any uid-less row on the next launch, and both account-creation routes
    write one inline, so in production this is transient at worst); a local id
    silently parked in a wire column is neither, because nothing downstream
    can tell it apart from a real one. Nothing is lost by declining: the
    pre-existing free-text `cashier`/`created_by`/`opened_by` columns still
    carry `_uid()` exactly as they always have, which is the evidence trail
    v13 was careful never to disturb.

    Never raises. Attribution is bookkeeping and a sale is the business --
    same posture as `_open_cash_session_id` above and `local_terminal_id`
    below, both of which document the same choice.
    """
    local_user_id = session.get('mt_user_id')
    if not local_user_id:
        # An unauthenticated write should not be reachable (every mutating
        # route carries @mt_login_required), but `_uid()`'s 'system' fallback
        # proves this file has seen sessionless calls. 'system' is a label,
        # not an identity, and has no uid to resolve.
        return None
    try:
        conn = _registry_conn()
        try:
            row = conn.execute(
                "SELECT uid FROM users WHERE id=?", (local_user_id,)
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        # Still None -- the policy is unchanged and deliberate. What changed is
        # that this no longer happens in silence; see ACTOR_LOOKUP_FAILURES.
        _note_actor_lookup_failure(
            'write_lookup_error', f"registry read for mt_user_id={local_user_id!r} failed: {exc}",
            exc=exc)
        return None
    if not row:
        _note_actor_lookup_failure(
            'write_no_user_row', f"no registry users row for mt_user_id={local_user_id!r}")
        return None
    value = row['uid']
    # TRIM-equivalent: account_schema treats '' and NULL identically when it
    # decides which rows still need a uid, so this must too, or a blank string
    # would be stamped as though it were an identity.
    if value and str(value).strip():
        return value
    _note_actor_lookup_failure(
        'write_blank_uid', f"registry users row {local_user_id!r} has no uid yet")
    return None


def _stamp():
    """`(actor_user_uid, terminal_id, created_at_utc)` for one write.

    Bound as a tuple so a write site cannot accidentally supply two of the
    three -- the columns are only useful together, and a row that knows WHEN
    but not WHO is barely better than a row that knows nothing.
    """
    return _actor_user_uid(), local_terminal_id(), now_utc_iso()


# ── The READ side of the v13 stamp: a uid back into a person ─────────────────
#
# `_actor_user_uid()` above turns a session into a wire identity at WRITE
# time. What follows is the inverse at READ time, and it is a genuinely
# awkward lookup rather than a SQL join: `sales.actor_user_uid` holds
# registry.db's `users.uid`, and registry.db is a DIFFERENT SQLITE FILE from
# retail.db. No `JOIN` can span them on the connection a report holds, which
# is exactly why core/retail/metrics.py returns bare uids and says so:
# "NO NAMES -- actor_user_uid resolves through registry.db's users table, a
# different database on a connection this module does not hold. The route
# joins it."
#
# Until this existed, NO backend code anywhere produced an employee name, so
# the desktop's `sale.employee_name` and Android's `sale.actor_email` /
# `actor_employee_id` were fields both clients read and nobody wrote. Both
# fell through to their "no resolved name" branch and printed a truncated
# uuid4 to a manager who asked who rang a sale.
#
# THREE RULES, each with a concrete failure behind it:
#
#   * ONE query per request, never one per row -- the same rule `_stamp()`
#     states for the write side ("resolve once per request, not per row"). A
#     shop with forty staff would otherwise open a second SQLite file forty
#     times inside one report.
#   * FAIL SOFT, LOUDLY. Any failure yields identity-less rows and a logged,
#     counted incident -- never a 500. A report of raw uids is degraded; a
#     report that is not there is absent.
#   * NEVER INVENT A NAME. A uid that does not resolve -- deleted account,
#     another company's user, or a lookup that just failed -- gets JSON null
#     in every identity field. And the bucket with no uid at all stays
#     something no client can render as a person.

#: registry `users` has NO `full_name`/`name` column at all (registry_db.py's
#: CREATE TABLE plus account_schema's v3 ALTERs). The only human-readable
#: identifiers a user row carries are `email` and `employee_id` ('ADMIN-0001').
#: Any design that assumes a person's name exists is designing against a
#: schema that is not there.
_ACTOR_IDENTITY_SELECT = "SELECT uid, employee_id, email FROM users WHERE company_id=? AND uid IN ({})"

#: Comfortably under SQLite's default SQLITE_MAX_VARIABLE_NUMBER (999), with
#: room for the company_id bind. Chunked rather than assumed safe: an
#: all-time report on a shop with staff turnover really can exceed it, and
#: the failure mode would be a hard OperationalError on the biggest report.
_ACTOR_UID_CHUNK = 900


def _identity_or_none(value):
    """'' and '   ' are not identities.

    Load-bearing on BOTH clients and in opposite directions if we get it
    wrong: Android's `attributedName` does `takeIf { it.isNotEmpty() }` (its
    docstring names `"email": ""` as a server bug it defends against) and the
    desktop's `_attribution()` treats a blank as absent too. Sending '' would
    read as "no identity" on one path and as a name on another.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve_actor_identities(cid, uids):
    """`{users.uid: {'employee_id', 'email', 'employee_name'}}` for `uids`.

    ONE batched query (chunked only for the parameter limit), scoped to this
    company. The scoping is free defence-in-depth rather than a correctness
    requirement -- `schema._rebind_to_owner_issued` guarantees retail.db only
    ever converges ONTO a tenant key the identity layer already adopted, so
    the retail cid and registry `users.company_id` are the same value by
    construction -- but a multi-company install should not be able to name
    another tenant's staff in this one's payroll report.

    `employee_name` is the ONE pre-formatted display string the desktop
    renders, derived HERE from the same two raw columns Android receives, so
    the two clients cannot disagree about who a row is. Email first,
    employee_id second: that is Android's already-shipped, already-reasoned
    `attributedName` ordering, and the desktop has no established preference,
    so Android's wins by default.

    Never raises.
    """
    wanted = sorted({u for u in uids if _identity_or_none(u)})
    if not wanted:
        return {}
    resolved = {}
    try:
        conn = _registry_conn()
        try:
            for i in range(0, len(wanted), _ACTOR_UID_CHUNK):
                chunk = wanted[i:i + _ACTOR_UID_CHUNK]
                sql = _ACTOR_IDENTITY_SELECT.format(','.join('?' * len(chunk)))
                for row in conn.execute(sql, [cid, *chunk]).fetchall():
                    employee_id = _identity_or_none(row['employee_id'])
                    email = _identity_or_none(row['email'])
                    resolved[row['uid']] = {
                        'employee_id': employee_id,
                        'email': email,
                        'employee_name': email or employee_id,
                    }
        finally:
            conn.close()
    except Exception as exc:
        # Degrade to "nobody resolved" rather than 500 the whole report, and
        # count it -- a page of raw uids with no trace of WHY is how a
        # permanent registry problem gets mistaken for old unattributed data.
        _note_actor_lookup_failure(
            'read_lookup_error',
            f"bulk identity lookup for {len(wanted)} uid(s) in company {cid!r} failed: {exc}",
            exc=exc)
        return {}
    return resolved


#: What a row that resolved to nobody carries. All four identity fields are
#: null TOGETHER and from ONE expression, so a client cannot receive a
#: half-identity, and no fourth signal exists that could disagree with the
#: other three.
_UNRESOLVED_IDENTITY = {'employee_id': None, 'email': None, 'employee_name': None}


def _new_uid():
    """A fresh wire identity for one row of a RETAIL_UID_TABLES table.

    `uuid.uuid4()`, in Python, and never SQLite's `lower(hex(randomblob(16)))`
    -- that trick yields a 32-character string which is NOT RFC-4122, and
    Owner's sync ingest gates on `uuid.UUID(entity_id)`. The trap is that
    `uuid.UUID()` ACCEPTS the 32-char form, so a value that merely looks random
    survives every local check and is rejected only on the wire, where it is
    most expensive to discover. The registry v3 migration and the v13 backfill
    both hit this and both wrote it down; this is the third place that has to
    know it, hence a named helper rather than a bare `str(_uuid.uuid4())`
    repeated at fourteen call sites.
    """
    return str(_uuid.uuid4())


# Multi-device sync foundation (2026-08-06): queues a row into sync_outbox
# (Task 3) so the push loop (Task 5) can relay it to the Owner. Must be
# called with the SAME cur/conn as the row write it describes, before that
# transaction's commit() -- the outbox row and the row it describes land or
# roll back together.
def _queue_sync_event(cur, entity_type, entity_id, event_type, payload):
    cur.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) VALUES (?,?,?,?,?,?)",
        (str(_uuid.uuid4()), entity_type, str(entity_id), event_type,
         json.dumps(payload), datetime.now(timezone.utc).isoformat()),
    )

def _default_branch(conn, cid):
    """Resolve this company's working branch the SAME way create_product files stock —
    the company's first branch, self-healing one if none exists. Inventory rows are
    keyed by (company_id, product_id, branch_id); a hardcoded branch_id=1 silently
    misses when a tenant's first branch isn't id 1 (multi-company / standalone installs),
    leaving sales that never decrement stock. Always derive the branch from the company."""
    row = conn.execute("SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (cid,)).fetchone()
    if row:
        return row['id']
    cur = conn.cursor()
    # v13 `uid`: a branch invented HERE is a real branch -- sales, stock and
    # cash sessions all key off it -- so it needs the same wire identity a
    # branch created through /branches gets. Easy to miss precisely because
    # this is a four-line helper and nothing about the calling route mentions
    # branches; pinned by retail_attribution_stamp_test.py's self-heal case.
    # No actor stamp: `branches` is in RETAIL_UID_TABLES only. It is a place,
    # not an event, and v13 gave the actor triple to the five tables that
    # record events.
    branch_uid = _new_uid()
    cur.execute("INSERT INTO branches (company_id,name,address,phone,uid) VALUES (?,?,?,?,?)",
                (cid, 'Main Branch', '', '', branch_uid))
    # `cur.lastrowid` is captured immediately after the INSERT it describes.
    # Nothing sits between the two lines today, and nothing may be added
    # there: sqlite3's `Cursor.lastrowid` reflects whichever INSERT that
    # cursor executed MOST RECENTLY, not "the one this function cares about",
    # so any statement slipped in between silently sends every caller of
    # `_default_branch` off to file stock and sales against the wrong branch.
    # That is not hypothetical -- it was reproduced while a sync-event INSERT
    # briefly did sit here: a second company's product immediately reported
    # "have 0.0" on a sale, because its stock landed under one branch_id and
    # the sale resolved a different one.
    new_branch_id = cur.lastrowid
    # Wave B, CORRECTED -- this self-heal deliberately queues NO sync event,
    # which is the opposite of what wave B1 first shipped.
    #
    # The first version queued a `branch`/`create` here, arguing that "a
    # self-healed branch is as real as one created through POST /branches".
    # The premise is true -- it really does carry stock, sales and cash
    # sessions -- and the conclusion was still wrong, because a self-heal is
    # not an OPERATOR ACT. It is a placeholder invented locally by whichever
    # route happened to need a branch first. Two devices each invent their
    # own, with different `uid`s, and `_apply_event`'s `branch` upsert dedupes
    # on `uid` alone -- NOTHING dedupes by name.
    #
    # Reproduced end to end: a second till that writes ANYTHING before its
    # first successful pull (creating its first product is the normal way an
    # operator provisions one, well inside the sync tick) leaves BOTH devices
    # holding two permanently-unmerged rows called 'Main Branch', with the
    # same product reading `branch_id=1: 0.0` and `branch_id=2: 1000.0` --
    # two entries no UI can tell apart, and no merge path anywhere.
    #
    # Worth recording why the wave's own acceptance test could not see it:
    # `compute_drift` returns ZERO throughout. The per-branch ledger/balance
    # invariant genuinely holds. The stock is simply filed under the wrong one
    # of two identical-looking branches -- the defect is in IDENTITY, not
    # arithmetic. Totals right, place wrong.
    #
    # `_resolve_branch_id`'s tier-2 self-heal on the APPLY side had already
    # made this exact call and stayed local-only; this now agrees with it, so
    # every self-heal site in the product behaves the same way. Branches an
    # operator really created -- POST /branches, and the bulk branch import --
    # still sync, which is what multi-branch shops actually need.
    #
    # Known limitation, written down rather than left to be discovered:
    # renaming a SELF-HEALED default branch does not propagate, because each
    # device holds its own. Renaming an operator-created branch does. A
    # movement naming an unresolvable branch_uid falls back to the receiver's
    # own default (`_resolve_branch_id` tier 2) -- exactly the pre-wave-B
    # behaviour this restores.
    return new_branch_id


def _branch_uid(conn, branch_id):
    """The wire identity (v13 `uid`) for a local `branches.id` -- the SAME
    lookup create_sale/create_return already perform inline for their own
    sale/return sync events, factored out so every inventory_movement
    emission site (wave B) can resolve `branch_uid` for its payload without
    re-deriving this SELECT six times over. Returns None when the branch row
    somehow carries no uid yet (a pre-v13 row that has not been backfilled --
    see the v13 migration's own docstring) or does not exist at all; both are
    handled identically by the apply side's `_resolve_branch_id` fallback."""
    row = conn.execute("SELECT uid FROM branches WHERE id=?", (branch_id,)).fetchone()
    return row['uid'] if row else None


def _resolve_working_branch(conn, cid, explicit_branch_id=None):
    """Resolves which branch a WRITE files under -- launch-readiness chain
    wave C1 (ROADMAP.md's 2026-08-30 "the multi-branch capture defect"
    entry; docs/launch-readiness/seats-and-chain-design.md §5.2 gap 1).

    Three tiers, in order:

      1. `explicit_branch_id`, IF the caller passed one -- validated
         against THIS company first. An unvalidated branch id is the same
         cross-tenant shape as the supplier_id leak this codebase already
         shipped once (see create_purchase_order's own "Cross-tenant leak
         fix" comment) -- a caller in company A could otherwise silently
         address company B's branch.
      2. This DEVICE's pinned `branch_uid` (onboarding_routes.py's
         get_device_branch_uid / config.json, deliberately device-local
         and never synced -- see that function's own docstring for why a
         DB row would be wrong here), resolved to THIS device's own local
         `branches.id`. Deliberately a UID lookup, never an integer one:
         `branches.id` is a plain per-device autoincrement, so the SAME
         physical branch carries a DIFFERENT id on every device that has
         ever self-healed or re-seeded one (see `_default_branch`'s own
         docstring) -- pinning the integer would break on exactly the
         device this fix exists for.
      3. `_default_branch(conn, cid)` -- UNCHANGED, the company's first
         branch, self-healing one if none exists. Called directly (not
         through any pin-aware path) when this device carries no pin at
         all, so an install that never sets one -- nearly every install
         today -- behaves IDENTICALLY to before this fix landed, not just
         equivalently: same function, same code path it always was.

    Returns `(branch_id, error_response)`. `error_response` is `None` on
    success; otherwise it is a ready-to-return Flask response
    (`(jsonify(...), 400)`) naming the invalid `explicit_branch_id` --
    callers `return` it directly, rolling back/closing their own
    connection first exactly as they already do for every other
    validation refusal in their own function.

    Tier 2's resolution and its self-heal (tier 3 duplicated as tier 2's
    own fallback) are NOT re-derived here: `SyncService._resolve_branch_id`
    (commercial_runtime/sync/sync_service.py) already does precisely
    this -- uid lookup, then this device's own default/self-heal -- for
    the identical problem on the sync-apply side, and reuses cleanly as a
    static method import (see the module-level import comment above). A
    PIN THAT DOES NOT RESOLVE (the branch row has not synced to this
    device yet) must not silently fall through to branch 1 -- that
    reproduces the exact defect this fix closes -- so the fallback is
    made VISIBLE: `_resolve_branch_id`'s own `fallback_sink` mechanism
    (built for exactly this) is reused to detect the fallback, and both
    established, schema-free surfacing mechanisms this codebase already
    has for "an anomaly happened, a human should be able to see it" are
    used together -- `_log_branch_fallback_summary` (the same WARNING-log
    format the sync-apply path already produces for this exact condition)
    and `_audit` (the same generic, already-visible-in-the-audit-log
    mechanism `sync_offline_override` uses for its own "manager approved
    an anomaly" record). Neither needs a new table or a schema version:
    `stock_exceptions`/`sync_conflicts` were considered and rejected --
    both are dedicated tables for a DIFFERENT kind of anomaly (an open
    business problem a human resolves), and inventing a third such table,
    or repurposing either of theirs, for a device-configuration condition
    would be exactly the new schema version this fix does not need.
    """
    if explicit_branch_id not in (None, ''):
        row = conn.execute(
            "SELECT id FROM branches WHERE id=? AND company_id=?",
            (explicit_branch_id, cid),
        ).fetchone()
        if not row:
            return None, (jsonify({
                'status': 'error', 'message': f'Unknown branch_id: {explicit_branch_id}',
            }), 400)
        return row['id'], None

    pinned_uid = _onboarding_get_device_branch_uid()
    if not pinned_uid:
        return _default_branch(conn, cid), None

    fallback_sink = []
    bid = _SyncService._resolve_branch_id(conn, cid, pinned_uid, fallback_sink=fallback_sink)
    if fallback_sink:
        _SyncService._log_branch_fallback_summary(fallback_sink)
        _audit(conn, 'BRANCH_PIN_UNRESOLVED', 'branch', bid,
               f"this device's pinned branch_uid={pinned_uid!r} did not resolve to a local "
               f"branch; filed under this device's default branch id={bid} instead.")
    return bid, None


#: "the caller did not pass this argument", distinct from "the caller passed
#: None". None is a MEANINGFUL terminal value here (an unidentified till), so
#: it cannot double as the default -- a `terminal=None` default would make
#: "use this device's identity" and "scope to the unidentified till"
#: indistinguishable at the call site.
_UNSET = object()


def _terminal(value):
    """The one spelling of a terminal id used by every drawer comparison here.

    `''` and `NULL` both mean "this row does not know which till it belongs
    to", and they have to compare EQUAL or the same physical device would be
    treated as two different tills depending on which code path wrote the row.
    Normalising to None once, here, is the same discipline account_schema
    applies to a blank `uid` (see `_actor_user_uid` above).
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _this_terminal():
    """THIS device's terminal id -- the scope every drawer read and write below
    is bound to.

    Identical value to the one `_stamp()` writes into `terminal_id`
    (`local_terminal_id()` -> `peek_local_device_uuid()`), and that identity is
    the whole point: the column a session is SCOPED by must be the same column
    the row was STAMPED with, or a drawer would be owned by one notion of
    "this terminal" and found by another.

    MAY BE None, and the drawer code below must keep working when it is.
    `peek_*` never creates the local-device file, so an install that has not
    established a device identity yet (and a stripped Android build, where the
    import fails) has no terminal id at all. That case is handled by treating
    "no terminal id" as its own scope -- see `_TERMINAL_IS` -- which means two
    unidentified devices still share one drawer, exactly as they do today.
    That is a KNOWN, NARROWED residual, not a fix: it is strictly better than
    today's branch-wide fold (which merges identified devices too), it is
    visible on the screen rather than silent (the drawer bar says the till is
    unidentified), and it disappears the moment device identity exists.
    """
    return _terminal(local_terminal_id())


#: `IS`, not `=`, and this is load-bearing rather than a style preference.
#:
#: SQL `=` against NULL is NULL, which is not TRUE, so `terminal_id = ?` bound
#: to None matches NOTHING -- including the rows this device itself wrote when
#: it had no terminal id. An unidentified till would open a drawer, fail to
#: find it one line later, and open a second. SQLite's `IS` is null-safe
#: equality (IS NOT DISTINCT FROM), so one predicate serves both cases.
_TERMINAL_IS = 'terminal_id IS ?'


def _open_cash_session_id(conn, cid, bid, terminal=_UNSET):
    """Best-effort lookup of the currently OPEN cash_sessions row for this
    company+branch+TERMINAL (feat/shift-cash-drawer, schema v10; terminal
    scoping is Phase 4 / retail v16), used to stamp sales.session_id /
    returns.session_id at write time so the X/Z report math can attribute a
    sale/return to the EXACT session it happened in.

    ── THE TERMINAL PREDICATE IS THE PHASE-4 FIX, AND IT IS HERE ────────────
    This lookup used to read "the open session for this company and branch",
    and THAT is why a phone's takings landed in the desktop's Z report. Not
    `_cash_session_report`, which has always summed strictly by `session_id`
    and has always been right about the rows it was given: the contamination
    was upstream, at the stamp. With one open session per BRANCH, every device
    selling into that branch -- desktop, phone, the second till by the door --
    resolved to the SAME session id here and wrote it onto its sales. By the
    time the desktop cashier pressed "Close Shift", the phone's cash was
    already, permanently, part of the desktop drawer's `cash_sales`, and no
    report-side filter could separate them again because nothing recorded that
    they had ever been different.

    So the fix has to be at write time, and it has to be the terminal: this
    device stamps sales with the session THIS device opened, or with nothing.
    `None` when this terminal has no open drawer is the correct answer and
    always was -- a sale rung on a till with no drawer open is unattributed,
    not somebody else's.

    A direct FK stamp, not a branch+time-range lookup at report time --
    see database/schema.py's RETAIL_SCHEMA_VERSION v10 comment for why a
    time-range query is the wrong choice here (silently wrong the instant a
    session spans midnight, or whenever two sessions on the same branch sit
    back-to-back). Deliberately NEVER raises: returns None on any failure
    (table not migrated yet, DB error, whatever) rather than let a cash-
    session lookup ever touch a sale or return's success -- see
    retail_cash_drawer_regression_test.py, which asserts create_sale's
    response is byte-for-byte identical whether or not a session is open.
    That posture is unchanged and matters MORE now, not less: the terminal
    predicate is one more thing that can decline, and declining must still
    cost the sale nothing.

    `terminal` is injectable for tests that need to act as a second device
    without a second process; it defaults to this device's real identity so no
    production call site can accidentally supply the wrong one."""
    try:
        term = _this_terminal() if terminal is _UNSET else _terminal(terminal)
        row = conn.execute(
            "SELECT id FROM cash_sessions "
            f"WHERE company_id=? AND branch_id=? AND {_TERMINAL_IS} AND status='open' "
            "ORDER BY opened_at DESC LIMIT 1",
            (cid, bid, term)
        ).fetchone()
        return row['id'] if row else None
    except Exception:
        return None

def _audit(conn, action, entity, entity_id, details=''):
    try:
        conn.execute(
            'INSERT INTO audit_log (company_id,user_id,action,entity,entity_id,details) VALUES (?,?,?,?,?,?)',
            (_cid(), _uid(), action, entity, entity_id, str(details))
        )
    except Exception:
        pass

# feat/audit-log-viewer: server-side half of the same admin-device gate
# app-shell.js already uses to hide the Admin Center nav entry (GET
# /api/devices/me -- see commercial_runtime/identity/device_routes.py).
# list_reorder_requests() above documents that THAT route relies on
# client-side hiding alone, with no is_admin_device check of its own,
# because a stale reorder draft isn't sensitive if someone types the URL.
# audit_log rows include refund/void trails with real user_id attribution
# for every user in the company, which is a materially more sensitive
# surface -- worth the extra real enforcement here rather than trusting
# nav-hiding alone.
#
# SECURITY (2026-08-20): this used to call
# device_context.resolve_local_device(), which is a WRITE -- it upserts the
# device row, and it also auto-promoted the first device a company ever
# resolved to admin. So this authorization check GRANTED the privilege it
# was checking for: the first device to request GET /api/sub/retail/audit-log
# on a fresh company was made that company's admin device by the act of
# asking, and then correctly told "yes, you're the admin device" -- 200,
# full audit trail, every user's refunds and voids. It now goes through
# device_context.local_device_is_admin(), which is a strict read (no INSERT,
# no UPDATE, no local_device.json creation) and additionally requires the
# stored row to match this company and still be 'active'.
#
# Fail-closed by design: local_device_is_admin() can raise
# LocalDeviceStateCorruptError (see device_context.py), and ANY failure to
# affirmatively establish "this is the admin device" is treated as NOT
# admin -- never fails open.
def _is_admin_device(cid):
    try:
        conn = _registry_conn()
        try:
            return device_context.local_device_is_admin(conn, cid)
        finally:
            conn.close()
    except Exception:
        return False

def _emit(event_type, payload):
    """Best-effort local event emission. In the source monolith this posted
    to an in-process event bus on the same Flask app; Aura Retail runs
    standalone here so there is no bus listening by default -- this is a
    no-op unless AURA_EVENT_BUS_URL is explicitly set (e.g. a future
    integration wiring Retail to something else on the same machine)."""
    bus_url = os.environ.get('AURA_EVENT_BUS_URL')
    if not bus_url:
        return
    try:
        requests.post(bus_url, json={
            'event_type': event_type, 'source_system': 'Retail',
            'company_id': _cid(), 'payload': payload
        }, timeout=1)
    except Exception:
        pass

# ── Dashboard ─────────────────────────────────────────────────────────────────

@retail_bp.route('/dashboard/stats', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
# retail.reports, even though this is the app's landing screen. What it
# returns is today's and this month's revenue, gross figures, the hourly
# takings chart and the last eight sales -- the "Retail Overview" analytics
# page, which is exactly the "reads reports" authority. A cashier who lands
# here now gets a 403 and the tile shows an error until the shell learns to
# hide what the user cannot fetch; that is a frontend follow-up (this pass
# does not touch products/retail/frontend/), and it is the right way round.
# The alternative is every cashier in every shop seeing the day's takings on
# the till, which is precisely what an owner does not want on the shop floor.
@mt_require_capability(CAP_REPORTS)
def dashboard_stats():
    """Every money figure below comes from core/retail/metrics.py -- the KPI
    cards, the hourly chart and the payment-method doughnut are three views
    of the SAME period and must not be allowed to disagree.

    They used to. today_sales netted refunds out; the hourly and
    payment-method queries right beside it were gross SUM(total). One refund
    today and this single screen printed two different revenues. Now all
    three are metrics.revenue()/revenue_by_hour()/revenue_by_payment_method()
    over one Period, so `sum(hourly_data) == sum(payment_methods) ==
    today_sales` holds by construction (see
    products/retail/tests/retail_metrics_consistency_test.py).

    `?branch_id=` is accepted here for the same reason the Reports page
    accepts it -- no caller sends it today, but the metrics module applies
    the branch predicate to sales AND returns uniformly, so supporting it is
    a passthrough rather than a seventh hand-written variant waiting to
    happen."""
    cid = _cid()
    branch_id = request.args.get('branch_id')
    conn = get_retail_conn()

    def q(sql, *params):
        return conn.execute(sql, params).fetchone()[0] or 0

    # ONE `now` for the whole response. Every period below plus `current_hour`
    # further down derive from this single instant instead of each calling
    # datetime.now() again: a request that happens to straddle midnight would
    # otherwise mix two different "today"s into one payload (KPI card on
    # yesterday, hourly chart on today). It also keeps this route's clock on
    # THIS module's `datetime`, which is the seam tests freeze -- metrics has
    # its own import, so a metrics function calling datetime.now() itself
    # would silently ignore a frozen clock here.
    now = datetime.now()
    # ...and then ONTO THE SHOP'S CLOCK, once, before anything derives a
    # window from it.
    #
    # `datetime.now()` is the REPORTING DEVICE's wall clock. metrics buckets
    # every row on the SHOP's -- `revenue_by_hour` keys on
    # strftime('%H', <the instant converted through the shop's zone>) -- so
    # the moment those two clocks differ, a window built from the device's
    # reading asks about a different day, and an axis cut at the device's hour
    # cuts off shop-hours that have already happened. That is not
    # hypothetical: shop on +03:00, device left on UTC, and this screen
    # printed a Revenue KPI of 150.00 above a chart whose bars summed to
    # 50.00, with the missing 100.00 reported nowhere. Before business dates
    # existed both sides read this one naive clock and COULD NOT disagree;
    # the bucketing fix opened the seam and this closes it.
    #
    # `business_now` is the whole conversion, applied once here rather than
    # inside the period constructors -- they take `now` and only `now` on
    # purpose, so that one response cannot straddle midnight and so a test
    # that freezes THIS module's `datetime` still governs the answer.
    boundary = metrics.business_day(conn, cid)
    shop_now = metrics.business_now(conn, cid, now)
    today_p = metrics.period_today(shop_now)
    yest_p  = metrics.period_yesterday(shop_now)
    month_p = metrics.period_month_to_date(shop_now)

    today_sales    = metrics.revenue(conn, cid, today_p, branch_id)
    today_txns     = metrics.transactions(conn, cid, today_p, branch_id)
    today_returns  = metrics.refunds(conn, cid, today_p, branch_id)
    yest_sales     = metrics.revenue(conn, cid, yest_p, branch_id)
    month_sales    = metrics.revenue(conn, cid, month_p, branch_id)
    month_txns     = metrics.transactions(conn, cid, month_p, branch_id)
    month_returns  = metrics.refunds(conn, cid, month_p, branch_id)

    # launch-readiness Phase 6 stage 6b-iii-a: `deleted_at_utc IS NULL` added
    # to all three counts below. `total_customers` carried no `status` filter
    # at all before this stage either -- left that way, matching the
    # customer count's pre-existing (unfiltered-by-status) behaviour rather
    # than introducing an unrelated second change here.
    total_customers = q("SELECT COUNT(*) FROM customers WHERE company_id=? AND deleted_at_utc IS NULL", cid)
    total_products  = q("SELECT COUNT(*) FROM products WHERE company_id=? AND status='active' AND deleted_at_utc IS NULL", cid)

    low_stock = conn.execute("""
        SELECT COUNT(p.id) FROM products p
        LEFT JOIN (SELECT product_id, SUM(quantity_on_hand) as qty
                   FROM inventory_balances WHERE company_id=? GROUP BY product_id) b ON p.id=b.product_id
        WHERE p.company_id=? AND COALESCE(b.qty,0) <= p.reorder_level AND p.status='active' AND p.deleted_at_utc IS NULL
    """, (cid, cid)).fetchone()[0] or 0

    # Hourly breakdown today -- zero-fill every hour from midnight through the
    # current hour instead of only emitting hours that had a sale. GROUP BY hr
    # alone drops quiet hours entirely, so e.g. sales at 09:00 and then not
    # again until 15:00 used to produce two ADJACENT array entries; Chart.js
    # renders a plain category axis (one bar per entry, subsystem-retail.js),
    # so the gap silently disappeared and the chart looked like a continuous
    # business day instead of showing the real slow period. The zero-filling
    # stays here (only the route knows how far into the day "now" is); the
    # revenue per hour is metrics'.
    # TWO THINGS THE AXIS HAS TO GET RIGHT, and it used to get both wrong:
    #
    #   * WHICH CLOCK. `int(now.strftime('%H'))` was the reporting device's
    #     hour against keys on the shop's -- see the `shop_now` comment above
    #     for the 100.00 that vanished. `shop_now` is already on the shop's
    #     business clock, so the two cannot drift apart again.
    #   * WHICH HOURS. "Midnight through now" is only the trading day when
    #     the trading day starts at midnight. A shop opening at 04:00 and
    #     currently at 01:00 has been trading for twenty-one hours, and its
    #     22:00 sales are INSIDE this period (business date is the day it
    #     opened) but were outside a 00:00-01:00 axis -- the same money-off-
    #     the-chart failure by a different route. `shop_now` is the shop's
    #     clock MINUS the day-start shift, so `shop_now.hour` is exactly
    #     "hours elapsed in the current trading day", and walking that many
    #     hours forward from the opening hour gives the trading day in the
    #     wall-clock labels a shopkeeper recognises (`_hour_key` deliberately
    #     applies no day-start shift, so the keys are wall-clock hours too).
    #
    # Unconfigured shops -- the overwhelming majority, since nothing wrote
    # these settings until this release -- have zone None and day_start 0, so
    # this collapses to the identical `range(device_hour + 1)` list as before.
    hourly_by_hr = metrics.revenue_by_hour(conn, cid, today_p, branch_id)

    def _trading_position(hour_key):
        """Where a wall-clock hour sits in THIS shop's trading day: 0 is the
        hour it opened. Returns None for a key SQLite could not produce an
        hour for (a malformed timestamp makes strftime yield NULL rather than
        raise), so one bad row cannot take the whole chart down with it."""
        try:
            return (int(hour_key) - boundary.day_start_hour) % 24
        except (TypeError, ValueError):
            return None

    # The axis spans the trading day so far -- and never stops short of an
    # hour that actually holds money.
    #
    # "So far" alone is the honest length, but it assumes no row is stamped
    # ahead of the shop's own clock, and two terminals with a few minutes of
    # skew between them break that assumption for a few minutes at a time.
    # When it breaks, the failure mode is the exact one this whole change is
    # about: the KPI counts the sale (it is inside the period) and the chart
    # silently has nowhere to draw it. Extending the axis to reach any hour
    # metrics produced a bucket for makes `sum(hourly_data) == today_sales` a
    # PROPERTY rather than a hope, and on the overwhelmingly normal day it
    # adds nothing at all, because every bucket is already behind us.
    positions = [p for p in (_trading_position(k) for k in hourly_by_hr) if p is not None]
    span = min(max([shop_now.hour] + positions), 23)
    trading_hours = [(boundary.day_start_hour + i) % 24 for i in range(span + 1)]
    hourly_labels = [f"{h:02d}:00" for h in trading_hours]
    hourly_data   = [hourly_by_hr.get(f"{h:02d}", 0.0) for h in trading_hours]

    # Payment method breakdown today -- net of refunds, keyed by the tender
    # the refund was paid back in (see metrics.revenue_by_payment_method).
    pay_methods = {r['payment_method']: {'count': r['count'], 'revenue': r['revenue']}
                   for r in metrics.revenue_by_payment_method(conn, cid, today_p, branch_id)}

    # Recent sales -- a list of documents, not a revenue figure, so it is
    # deliberately not period-scoped; it does honour branch_id so the whole
    # response describes one branch when one is asked for.
    recent_sql = """
        SELECT s.id, s.sale_number, s.total, s.payment_method, s.created_at,
               COALESCE(c.name,'Walk-in') as customer_name,
               COUNT(si.id) as item_count
        FROM sales s
        LEFT JOIN customers c ON s.customer_id=c.id
        LEFT JOIN sale_items si ON s.id=si.sale_id
        WHERE s.company_id=?
    """
    recent_params = [cid]
    if branch_id:
        recent_sql += " AND s.branch_id=?"
        recent_params.append(branch_id)
    recent_sql += " GROUP BY s.id ORDER BY s.created_at DESC LIMIT 8"
    recent = conn.execute(recent_sql, recent_params).fetchall()

    conn.close()
    sales_change = round(((today_sales - yest_sales) / yest_sales * 100) if yest_sales > 0 else 0, 1)

    return jsonify({'status': 'success', 'data': {
        'today_sales':      round(today_sales, 2),
        'today_transactions': today_txns,
        'today_returns':    round(today_returns, 2),
        'month_returns':    round(month_returns, 2),
        'yesterday_sales':  round(yest_sales, 2),
        'sales_change_pct': sales_change,
        'month_sales':      round(month_sales, 2),
        'month_transactions': month_txns,
        'low_stock_alerts': low_stock,
        'total_customers':  total_customers,
        'total_products':   total_products,
        'hourly_labels':    hourly_labels,
        'hourly_data':      hourly_data,
        'payment_methods':  pay_methods,
        'recent_sales':     [dict(r) for r in recent],
    }})

# ── Categories ────────────────────────────────────────────────────────────────

@retail_bp.route('/categories', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def list_categories():
    cid = _cid()
    conn = get_retail_conn()
    # launch-readiness Phase 6 stage 6b-ii (tombstones): `c.deleted_at_utc IS
    # NULL` added to the WHERE -- `c` is the main FROM table here (not the
    # LEFT JOIN side), so this is a plain "only live categories" filter, not
    # the LEFT-JOIN-in-WHERE trap that would silently drop unrelated rows.
    # launch-readiness Phase 6 stage 6b-iii-a: `AND p.deleted_at_utc IS NULL`
    # added to the product-count JOIN condition, alongside the existing
    # `p.status='active'` -- a tombstoned product must not inflate a live
    # category's product_count, and `status` alone stopped being sufficient
    # to hide it the moment `delete_product` stopped writing it.
    rows = conn.execute(
        "SELECT c.*, COUNT(p.id) as product_count FROM categories c "
        "LEFT JOIN products p ON p.category_id=c.id AND p.status='active' AND p.deleted_at_utc IS NULL "
        "WHERE c.company_id=? AND c.deleted_at_utc IS NULL GROUP BY c.id ORDER BY c.name", (cid,)
    ).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@retail_bp.route('/categories', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.product.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def create_category():
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Category name required'}), 400
    cid = _cid()
    conn = get_retail_conn()
    cur = conn.cursor()
    new_id = str(_uuid.uuid4())
    # launch-readiness Phase 6 stage 6a-i: row_version/updated_at_utc stamped
    # explicitly at creation (1/now) rather than left to the column's own
    # `DEFAULT 1` -- the emission payload below needs the real value, and a
    # future device applying this row needs a non-NULL updated_at_utc from
    # the moment the row exists, not only from its first edit. See
    # docs/launch-readiness/phase6-catalogue-correctness.md.
    now = now_utc_iso()
    cur.execute(
        "INSERT INTO categories (id, company_id, name, description, row_version, updated_at_utc) "
        "VALUES (?,?,?,?,?,?)",
        (new_id, cid, data['name'], data.get('description', ''), 1, now))
    # No `company_id` in the wire payload -- it is meaningless cross-device
    # (each device derives its own `company_id` locally at onboarding; see
    # commercial_runtime/sync/sync_service.py's module docstring). The
    # receiving device stamps ITS OWN company_id on apply.
    _queue_sync_event(cur, 'category', new_id, 'create', {
        'id': new_id, 'name': data['name'], 'description': data.get('description', ''),
        'row_version': 1, 'updated_at_utc': now,
    })
    conn.commit(); conn.close()
    _sync_nudge()  # best-effort immediate push -- see commercial_runtime/sync/sync_service.py
    return jsonify({'status': 'success', 'data': {'id': new_id}})

@retail_bp.route('/categories/<string:category_id>', methods=['PUT'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.product.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def update_category(category_id):
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Category name required'}), 400
    cid = _cid()
    conn = get_retail_conn()
    cur = conn.cursor()
    # launch-readiness Phase 6 stage 6b-i (changed-field deltas -- a LOCAL
    # fix, reached before sync is even involved): this route used to write
    # `data.get('description', '')` unconditionally, so a caller that sent
    # ONLY `name` silently blanked `description` on THIS device, before any
    # other device or the sync layer ever saw the write. That is the exact
    # clobber this whole stage exists to close, reached by a different route
    # -- so `fields` is built the same way every sibling update route in
    # this file already does (update_product/update_customer/
    # update_supplier above): only a key actually present in the request
    # goes in the SET clause. `name` stays required (unchanged 400 contract
    # below) because every sibling route's `allowed`-field gate still needs
    # SOMETHING to update; a description-only PUT here still 400s, exactly
    # as before.
    fields = {'name': data['name']}
    if 'description' in data:
        fields['description'] = data.get('description') or ''
    # row_version/updated_at_utc bumped in the SAME UPDATE statement as the
    # field change -- launch-readiness Phase 6 stage 6a-i, unchanged by this
    # stage; `fields` is never empty (name is required above), so every
    # successful call here always bumps.
    now = now_utc_iso()
    sets = ', '.join(f'{k}=?' for k in fields)
    cur.execute(
        f"UPDATE categories SET {sets}, row_version=row_version+1, updated_at_utc=? "
        "WHERE id=? AND company_id=?",
        list(fields.values()) + [now, category_id, cid])
    if cur.rowcount == 0:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Category not found'}), 404
    # Read `name`/`description` back from the row rather than from `data` --
    # the wire payload is still a FULL snapshot (the apply-side INSERT half
    # needs it for a device that has never seen this id), and reading the
    # row after the UPDATE gives the current value of a field this request
    # did not touch, instead of inventing one.
    row = conn.execute(
        "SELECT name, description, row_version FROM categories WHERE id=?", (category_id,)
    ).fetchone()
    # No `company_id` in the wire payload -- see create_category's comment above.
    _queue_sync_event(cur, 'category', category_id, 'update', {
        'id': category_id, 'name': row['name'], 'description': row['description'],
        'row_version': row['row_version'], 'updated_at_utc': now,
        '_changed_fields': sorted(fields),
    })
    conn.commit(); conn.close()
    _sync_nudge()  # best-effort immediate push -- see commercial_runtime/sync/sync_service.py
    return jsonify({'status': 'success'})

@retail_bp.route('/categories/<string:category_id>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.product.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def delete_category(category_id):
    cid = _cid()
    conn = get_retail_conn()
    # Final-review Fix 4 (2026-08-07): defense-in-depth error containment.
    # Deleting a category is the one destructive write here that can fail on
    # a database-integrity rule rather than on validation. That specific
    # failure (a product still referencing the category) can no longer happen
    # since schema v3 made products.category_id ON DELETE SET NULL -- but
    # before, this raised an uncaught sqlite3.IntegrityError straight into
    # Flask's default 500 handler, WITH the connection never closed on that
    # path (leaked for the duration of the process, holding a WAL read/write
    # lock). Any future FK/constraint added anywhere near this table would
    # reintroduce exactly that, so the containment stays: a clean 409 with a
    # real message the UI can show, and a connection that is always closed.
    try:
        cur = conn.cursor()
        # launch-readiness Phase 6 stage 6b-ii (tombstones,
        # phase6b-decisions.md "Decision A"): a gated soft-delete replaces
        # the old hard DELETE. The old `DELETE FROM categories WHERE id=?`
        # carried no `row_version` at all on its sync event -- stage 6a-ii
        # flagged this as the one catalogue destructive path it could not
        # gate, because there was no version to compare. This UPDATE is a
        # real gated write like every other catalogue delete in this file:
        # `AND deleted_at_utc IS NULL` makes a repeat delete of an
        # already-tombstoned category a no-op 404 (below), not a re-stamp.
        now = now_utc_iso()
        cur.execute(
            "UPDATE categories SET deleted_at_utc=?, row_version=row_version+1, updated_at_utc=? "
            "WHERE id=? AND company_id=? AND deleted_at_utc IS NULL",
            (now, now, category_id, cid))
        if cur.rowcount == 0:
            return jsonify({'status': 'error', 'message': 'Category not found'}), 404
        # THE CASCADE. Schema v3 made `products.category_id`
        # `ON DELETE SET NULL` because a hard delete arriving at a device
        # that still held products raised `IntegrityError` inside
        # `_apply_event`, which aborted `apply_pull_result` BEFORE the
        # cursor advanced -- `run_once` swallowed it, and that device
        # silently stopped receiving every event from every device, forever
        # (see retail_category_delete_fk_sync_test.py's module docstring
        # for the full history). Tombstoning removes the `DELETE`, so the FK
        # cascade never fires and that self-healing is gone; this explicit
        # walk reproduces the same outcome by hand.
        #
        # Deliberately does NOT bump `row_version` on the products it
        # touches, and does NOT queue a product sync event for any of them.
        # `SyncService._apply_event`'s category branch (sync_service.py)
        # performs this IDENTICAL walk locally when it applies the category
        # tombstone, so every device reaches the same end state from the
        # SAME single category event. Emitting per-product events here would
        # turn one category delete into N product updates AND would advance
        # those products' `row_version` on THIS device only -- a genuine
        # concurrent product edit made on another till would then arrive
        # with a LOWER version than this phantom bump and be rejected as
        # stale. That is stage 6a-i's loyalty-accumulator trap, reached by a
        # third route. See phase6b-decisions.md "Decision A" for the one
        # residual divergence this cannot close (a product concurrently
        # re-pointed AT the doomed category survives with a dangling id on
        # the device that applied that edit) and why it is an accepted,
        # cosmetically-invisible trade -- every categories join in this
        # codebase is a LEFT JOIN.
        cur.execute(
            "UPDATE products SET category_id=NULL WHERE category_id=? AND company_id=?",
            (category_id, cid))
        row = conn.execute("SELECT row_version FROM categories WHERE id=?", (category_id,)).fetchone()
        _queue_sync_event(cur, 'category', category_id, 'delete', {
            'id': category_id, 'deleted_at_utc': now, 'row_version': row['row_version'],
            'updated_at_utc': now, '_changed_fields': ['deleted_at_utc'],
        })
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        current_app.logger.warning("delete_category(%s) failed on a database constraint: %s", category_id, exc)
        return jsonify({
            'status': 'error',
            'message': 'This category could not be deleted because other records still depend on it.',
        }), 409
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        current_app.logger.exception("delete_category(%s) failed: %s", category_id, exc)
        return jsonify({'status': 'error', 'message': 'Could not delete this category.'}), 400
    finally:
        conn.close()
    _sync_nudge()  # best-effort immediate push -- see commercial_runtime/sync/sync_service.py
    return jsonify({'status': 'success'})

# ── Products ──────────────────────────────────────────────────────────────────

@retail_bp.route('/products', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def list_products():
    cid = _cid()
    conn = get_retail_conn()
    # launch-readiness Phase 6 stage 6b-ii (tombstones): `AND c.deleted_at_utc
    # IS NULL` added to the categories JOIN CONDITION, deliberately NOT to
    # the WHERE clause -- this is a LEFT JOIN, and a product with no category
    # at all (category_id IS NULL) must still be returned. Putting the
    # filter in WHERE would silently drop every uncategorised product; in
    # the JOIN condition it only blanks a dangling/tombstoned category's
    # name, exactly like this codebase's existing behaviour for category_id
    # NULL (this is also the residual divergence phase6b-decisions.md
    # "Decision A" names: cosmetically invisible either way).
    #
    # launch-readiness Phase 6 stage 6b-iii-a (deletion stops overloading
    # `status`): `p.deleted_at_utc IS NULL` added to the WHERE. Until this
    # stage `p.status='active'` alone was enough to hide a tombstoned
    # PRODUCT, because `delete_product` wrote `status='inactive'`
    # unconditionally alongside the tombstone. That write is now gone, so
    # `status` no longer says anything about deletion for a NEW delete --
    # only `deleted_at_utc` does. Both conditions stay: a LEGACY row deleted
    # before 6b-ii has `status='inactive'`/`deleted_at_utc IS NULL` and is
    # hidden only by the `status` half; a row deleted after this stage has
    # `status='active'`/`deleted_at_utc` set and is hidden only by the
    # `deleted_at_utc` half. This is THE highest-priority read path in the
    # codebase for this filter -- `_findByCode` (subsystem-retail.js) filters
    # CLIENT-SIDE over the exact array this query returns, backing POS scan,
    # product search and PO scan alike.
    #
    # launch-readiness "the POS scale fix" (ROADMAP.md's 2026-08-29 v21
    # entry): `?q=` and `?limit=` added, BOTH OPTIONAL and BOTH OFF BY
    # DEFAULT, because the frontend still calls this route with no
    # parameters at all and expects the full array back -- it keeps doing
    # that until the client-side rewrite (a separate, later change) lands.
    # So the no-parameter path below is untouched byte-for-byte: same three
    # WHERE conditions in the same order, same GROUP BY/ORDER BY, and
    # critically NO `LIMIT` clause is ever appended unless the caller's
    # query string literally contains `limit=` -- `request.args.get('limit')`
    # returns None rather than a default when the key is absent, and that is
    # the branch this route relies on to stay byte-compatible. A caller that
    # explicitly opts in gets a search filter and a capped, indexed page
    # instead of the whole catalogue -- see GET /products/lookup just below
    # for the single-row counterpart the scan path is meant to move onto.
    q = request.args.get('q', '').strip()
    raw_limit = request.args.get('limit')
    conditions = ["p.company_id=?", "p.status='active'", "p.deleted_at_utc IS NULL"]
    params = [cid]
    if q:
        conditions.append("(p.name LIKE ? OR p.sku LIKE ? OR p.barcode LIKE ?)")
        like = f'%{q}%'
        params.extend([like, like, like])
    sql = (
        "SELECT p.*, c.name as category_name, "
        "COALESCE(SUM(b.quantity_on_hand), 0) as total_stock "
        "FROM products p "
        "LEFT JOIN categories c ON p.category_id=c.id AND c.deleted_at_utc IS NULL "
        "LEFT JOIN inventory_balances b ON p.id=b.product_id AND b.company_id=p.company_id "
        f"WHERE {' AND '.join(conditions)} "
        "GROUP BY p.id ORDER BY p.name"
    )
    if raw_limit is not None:
        limit = clamp_page_limit(raw_limit, PRODUCTS_LIST_DEFAULT_LIMIT, PRODUCTS_LIST_MAX_LIMIT)
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

# Launch-readiness "the POS scale fix" (ROADMAP.md's 2026-08-29 v21 entry).
# The server-side counterpart to the client-side scan `_findByCode`
# (subsystem-retail.js) performs today over list_products' entire payload --
# POS scan, product search and PO scan all resolve one code against the
# whole array; Android's ProductLookup.kt repeats the identical client-side
# scan. Wiring either client onto this route is a separate, later change
# (backend only here); this route exists so that change has something to
# call.
#
# No @mt_require_capability, matching list_products immediately above: a
# till has to be able to resolve a scanned barcode to ring a sale, and that
# is exactly the authority list_products itself already grants any
# authenticated retail user with no capability decorator at all.
@retail_bp.route('/products/lookup', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def lookup_product():
    """Resolve ONE product by barcode or SKU, company-scoped, returning the
    same row shape a POS tile needs -- category_name and total_stock
    included, identical to list_products' own SELECT.

    SAME visibility rules as list_products, matched exactly rather than
    approximated: `status='active'` AND `deleted_at_utc IS NULL`. Both
    conditions exist because of real, previously-shipped bugs (launch-
    readiness Phase 6/7 -- see list_products' own comment for the legacy-
    vs-new-delete story); a new query is exactly how a fixed bug comes
    back, so this route re-derives its WHERE clause from that one rather
    than writing a fresh one from memory.

    `code` is checked against `barcode` first, then `sku` -- separate,
    fully-indexed point lookups (see the four-rung ladder below) rather
    than one query with an OR, so each rung's plan is unambiguous and
    provably indexed. Barcode is checked first because this route's
    primary caller is the scan path, where barcode is the natural key; a
    code that happens to collide with both a barcode on one product and a
    different product's SKU resolves to the barcode match.

    CASE-INSENSITIVE ON BOTH COLUMNS, matching Android's ProductLookup.kt
    (`it.barcode?.equals(code, ignoreCase = true)`) -- Android is about to
    be switched onto this endpoint, and an exact-match server would
    silently regress scanning for any shop whose SKUs are mixed-case.

    CORRECTED (schema v22, ROADMAP.md's 2026-08-30 "retail schema v22
    CLAIMED for the case-fold lookup" entry): this route used to build a
    small FIXED set of case variants of the INPUT (`code`, `code.upper()`,
    `code.lower()`) and match them with `IN (...)`, on the reasoning that
    `COLLATE NOCASE` / `LOWER(p.barcode)` wraps the COLUMN in a function
    and is non-sargable. That reasoning is TRUE of wrapping the column at
    query time, but the conclusion drawn from it was wrong: three variants
    of the INPUT cannot match a value STORED mixed-case. A row with
    `sku = 'AbC-123'` and a typed `abc-123` produces none of `['ABC-123',
    'abc-123']`, so the lookup silently missed a product that was
    genuinely there -- measured, not assumed (see ROADMAP.md's 2026-08-29
    "the case-insensitive lookup is only PARTLY case-insensitive" entry).
    Left here so the next reader does not re-derive the same wrong
    conclusion from the same plausible-looking argument.

    The fix is a NOCASE-collated INDEX, not a NOCASE-collated column
    expression: the collation lives in the index (`products(company_id,
    barcode COLLATE NOCASE)` / `..., sku COLLATE NOCASE)`, schema v22,
    `_migrate_add_nocase_lookup_indexes`), and `WHERE company_id=? AND
    barcode=? COLLATE NOCASE` against that index plans as `SEARCH ...
    USING COVERING INDEX idx_products_company_barcode_nocase`, never a
    scan. Fully sargable -- proven with real `EXPLAIN QUERY PLAN` output
    in retail_product_lookup_test.py.

    FOUR-RUNG LADDER, first hit wins, rather than one NOCASE-only query:

        1. barcode exact   (idx_products_company_barcode, v21)
        2. barcode NOCASE  (idx_products_company_barcode_nocase, v22)
        3. sku exact       (idx_products_company_sku, v21)
        4. sku NOCASE      (idx_products_company_sku_nocase, v22)

    A legacy install may ALREADY hold case-duplicates created before this
    change (e.g. both `abc` and `ABC` stored as two different products'
    barcodes) -- a bare NOCASE match against such an install would resolve
    to an arbitrary one of the two, which is exactly the "scanning
    silently resolves to the wrong item" failure AUDIT (2026-08-14) closed,
    through a different door. Trying the exact match FIRST makes
    resolution deterministic: an exact hit always wins over a same-code
    case-fold, and only when no exact match exists does the NOCASE rung
    run. Column precedence (barcode before sku) is unchanged from the
    shipped route; exactness wins within a column before falling through
    to the next column. Every rung is a single indexed point lookup, and
    the common case -- an exact barcode scan -- returns on the first.

    404 (not 200 with a null payload) when nothing matches -- a scan that
    finds nothing is a normal, expected outcome for a till, and the
    frontend needs to tell "no product" apart from a transport error.
    """
    cid = _cid()
    code = request.args.get('code', '').strip()
    if not code:
        return jsonify({'status': 'error', 'message': 'code is required'}), 400
    select = (
        "SELECT p.*, c.name as category_name, "
        "COALESCE(SUM(b.quantity_on_hand), 0) as total_stock "
        "FROM products p "
        "LEFT JOIN categories c ON p.category_id=c.id AND c.deleted_at_utc IS NULL "
        "LEFT JOIN inventory_balances b ON p.id=b.product_id AND b.company_id=p.company_id "
        "WHERE p.company_id=? AND p.status='active' AND p.deleted_at_utc IS NULL "
        "AND p.{col}=?{collate} "
        "GROUP BY p.id"
    )
    conn = get_retail_conn()
    row = None
    for col, collate in (
        ('barcode', ''),
        ('barcode', ' COLLATE NOCASE'),
        ('sku', ''),
        ('sku', ' COLLATE NOCASE'),
    ):
        row = conn.execute(select.format(col=col, collate=collate), (cid, code)).fetchone()
        if row is not None:
            break
    conn.close()
    if row is None:
        return jsonify({'status': 'error', 'message': 'Product not found'}), 404
    return jsonify({'status': 'success', 'data': dict(row)})

@retail_bp.route('/products', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.product.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def create_product():
    data = request.json or {}
    cid  = _cid()
    if not data.get('name') or not data.get('sku'):
        return jsonify({'status': 'error', 'message': 'Name and SKU are required'}), 400
    conn = get_retail_conn()
    try:
        # COLLATE NOCASE (schema v22): lookup_product now resolves a scanned
        # code case-insensitively (idx_products_company_sku_nocase), so this
        # guard has to agree on what "the same SKU" means or two products
        # ('abc' and 'ABC') could both be created and the case-folding scan
        # would resolve to an arbitrary one of them -- the read and the
        # write must use the same definition of "duplicate", or the write
        # guard is decorative.
        existing = conn.execute("SELECT id FROM products WHERE company_id=? AND sku=? COLLATE NOCASE",
                                (cid, data['sku'])).fetchone()
        if existing:
            conn.close()
            return jsonify({'status': 'error', 'message': 'SKU already exists'}), 409
        # AUDIT (2026-08-14): barcode had no uniqueness check here, unlike SKU above --
        # two products could end up sharing one barcode (typo, or the same physical
        # label scanned twice). Both the desktop scanner lookup (_findByCode) and the
        # Android ProductLookup.findProductByCode resolve a scanned code with a
        # first-match lookup, so a duplicate barcode makes scanning silently resolve
        # to whichever product sorts first -- wrong item, wrong price, no warning to
        # the cashier. Blank barcodes are exempt (most products never get one) and
        # deactivated products still count, matching the SKU check's own behavior.
        if data.get('barcode'):
            # COLLATE NOCASE (schema v22): same reasoning as the SKU check
            # above -- lookup_product resolves a scanned barcode case-
            # insensitively (idx_products_company_barcode_nocase), so this
            # guard must fold case the same way or a case-folding scan can
            # resolve to an arbitrary one of two "different" stored codes,
            # which is exactly the AUDIT (2026-08-14) failure this check
            # exists to prevent, reopened through a different door.
            existing_barcode = conn.execute(
                "SELECT id FROM products WHERE company_id=? AND barcode=? COLLATE NOCASE",
                (cid, data['barcode'])
            ).fetchone()
            if existing_barcode:
                conn.close()
                return jsonify({'status': 'error', 'message': 'Barcode already exists'}), 409
        # `supplier_id` arrived straight from the request body with no check of
        # any kind -- not existence, not tenancy, not tombstone -- so a caller
        # could file a product against ANOTHER company's supplier. Same
        # unvalidated-foreign-key shape `create_purchase_order` carried until
        # `6b79b5a`, on a different table, and validated here the same way.
        #
        # Severity stated honestly, because it is lower than it first looks and
        # the next reader deserves the real reason this exists: nothing in this
        # codebase currently reads supplier DETAILS through a product. Every
        # join to `suppliers` was enumerated (there are exactly two, both in the
        # purchase-order routes, both company-scoped since `6b79b5a`), and
        # `list_products` returns `p.*` -- the raw `supplier_id`, never a
        # supplier name. So a poisoned value is a dangling cross-tenant
        # reference today, not a live data leak.
        #
        # It is fixed anyway because it is ONE JOIN away from being one. The
        # moment anyone puts a supplier name on the products list -- an obvious
        # feature -- that id starts rendering another tenant's data, and whoever
        # adds the join has no reason to suspect the id is untrusted. CLAUDE.md
        # treats an unscoped business query as a bug rather than a
        # simplification; an unvalidated FK write is the same class.
        #
        # `supplier_id` stays OPTIONAL: a product with no supplier is a real,
        # exercised state, so only a supplied value is checked.
        supplier_id = data.get('supplier_id')
        if supplier_id:
            valid_supplier = conn.execute(
                "SELECT id FROM suppliers WHERE id=? AND company_id=? AND status='active' AND deleted_at_utc IS NULL",
                (supplier_id, cid)
            ).fetchone()
            if not valid_supplier:
                conn.close()
                return jsonify({'status': 'error',
                                 'message': f'Unknown supplier_id: {supplier_id}'}), 400
        cur = conn.cursor()
        pid = str(_uuid.uuid4())
        # launch-readiness Phase 6 stage 6a-i: row_version/updated_at_utc
        # stamped explicitly at creation, same reasoning as
        # create_category's identical comment above.
        now = now_utc_iso()
        cur.execute("""
            INSERT INTO products (id,company_id,sku,barcode,name,category_id,supplier_id,cost_price,
                                  sell_price,tax_rate,unit,reorder_level,reorder_method,status,
                                  row_version,updated_at_utc)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?)
        """, (pid, cid, data['sku'], data.get('barcode',''), data['name'],
              data.get('category_id'), data.get('supplier_id'), data.get('cost_price',0), data.get('sell_price',0),
              data.get('tax_rate',0), data.get('unit','pcs'), data.get('reorder_level',5),
              data.get('reorder_method','none'), 1, now))
        # File opening stock under the company's working branch — the SAME branch that
        # sales/returns/adjustments resolve to (via _resolve_working_branch), so a sale
        # always decrements the row this created. Self-heals a branch on fresh/standalone
        # installs. Launch-readiness chain wave C1: consults this device's pinned
        # branch_uid before the self-heal -- create_product takes no branch_id from the
        # client, so there is only the pin/default tiers here, never the explicit one.
        bid, _ = _resolve_working_branch(conn, cid)
        conn.execute("""
            INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand)
            VALUES (?,?,?,?)
        """, (cid, pid, bid, data.get('initial_stock', 0)))
        if data.get('initial_stock', 0) > 0:
            # v13 stamp. `created_by` keeps the local id it has always held;
            # actor_user_uid/terminal_id/created_at_utc are the second,
            # structured channel beside it, never a replacement for it.
            actor, terminal, utc_now = _stamp()
            movement_uid = _new_uid()
            movement_qty = data.get('initial_stock', 0)
            conn.execute("""
                INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,reference,created_by,
                                                 uid,actor_user_uid,terminal_id,created_at_utc)
                VALUES (?,?,?,'opening_stock',?,?,?,?,?,?,?)
            """, (cid, pid, bid, movement_qty, 'OPENING', _uid(),
                  movement_uid, actor, terminal, utc_now))
            # Wave B: every writer into inventory_movements queues the
            # matching sync event, this one included -- see the module-wide
            # note at create_sale's own inventory_movement emission below for
            # the shape every one of these six sites shares.
            _queue_sync_event(cur, 'inventory_movement', movement_uid, 'create', {
                'uid': movement_uid, 'product_id': pid, 'branch_id': bid, 'branch_uid': _branch_uid(conn, bid),
                'movement_type': 'opening_stock', 'quantity': movement_qty, 'unit_cost': 0,
                'reference': 'OPENING', 'notes': None, 'created_by': _uid(),
                'actor_user_uid': actor, 'terminal_id': terminal, 'created_at_utc': utc_now,
            })
        _audit(conn, 'PRODUCT_CREATED', 'product', pid, data['name'])
        _queue_sync_event(cur, 'product', pid, 'create', {
            'id': pid, 'sku': data['sku'], 'barcode': data.get('barcode', ''), 'name': data['name'],
            'category_id': data.get('category_id'), 'supplier_id': data.get('supplier_id'),
            'cost_price': data.get('cost_price', 0),
            'sell_price': data.get('sell_price', 0), 'tax_rate': data.get('tax_rate', 0),
            'unit': data.get('unit', 'pcs'), 'reorder_level': data.get('reorder_level', 5),
            # reorder automation foundation: propagated through the outbox so
            # a second device's SyncService._apply_event product upsert
            # picks it up too -- see that function's product branch.
            'reorder_method': data.get('reorder_method', 'none'),
            'row_version': 1, 'updated_at_utc': now,
        })
        conn.commit(); conn.close()
        _emit('ProductCreated', {'product_id': pid})
        _sync_nudge()
        return jsonify({'status': 'success', 'data': {'id': pid}})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@retail_bp.route('/products/<string:pid>', methods=['PATCH'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.product.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def update_product(pid):
    data = request.json or {}
    cid  = _cid()
    allowed = ['name','barcode','category_id','supplier_id','cost_price','sell_price','tax_rate','unit','reorder_level','reorder_method','status']
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({'status': 'error', 'message': 'No valid fields'}), 400
    conn = get_retail_conn()
    # AUDIT (2026-08-14): PATCH let barcode be set to any value with no uniqueness
    # check at all -- see the matching create_product fix above for why a shared
    # barcode is a real scanning-safety bug, not just a data-quality nit. Same
    # blank-exempt rule as create; excludes this product's own row so re-saving
    # an unchanged barcode doesn't false-positive against itself.
    if fields.get('barcode'):
        # COLLATE NOCASE (schema v22): same reasoning as create_product's
        # matching check -- lookup_product resolves case-insensitively now,
        # so this guard must agree or a PATCH can (re)introduce the exact
        # case-duplicate the read side was fixed to disambiguate. `id<>?`
        # self-exclusion is untouched: re-saving this product's own
        # unchanged barcode must still succeed.
        existing_barcode = conn.execute(
            "SELECT id FROM products WHERE company_id=? AND barcode=? COLLATE NOCASE AND id<>?",
            (cid, fields['barcode'], pid)
        ).fetchone()
        if existing_barcode:
            conn.close()
            return jsonify({'status': 'error', 'message': 'Barcode already exists'}), 409
    # Same unvalidated-foreign-key fix as create_product above -- `supplier_id`
    # is in this route's `allowed` list, so a PATCH could re-point an existing
    # product at ANOTHER company's supplier even though creating it that way
    # was blocked. Fixing only the create path would leave the identical hole
    # one request later, which is the "present on one path, absent on another"
    # shape this codebase has shipped before.
    #
    # `'supplier_id' in fields` rather than a truthiness check: clearing the
    # supplier by PATCHing an explicit null is legitimate and must stay
    # allowed, so only a non-empty supplied value is validated. See
    # create_product's comment for why this is worth fixing at all when
    # nothing currently reads supplier details through a product.
    if fields.get('supplier_id'):
        valid_supplier = conn.execute(
            "SELECT id FROM suppliers WHERE id=? AND company_id=? AND status='active' AND deleted_at_utc IS NULL",
            (fields['supplier_id'], cid)
        ).fetchone()
        if not valid_supplier:
            conn.close()
            return jsonify({'status': 'error',
                             'message': f'Unknown supplier_id: {fields["supplier_id"]}'}), 400
    cur = conn.cursor()
    sets = ', '.join(f'{k}=?' for k in fields)
    values = list(fields.values())
    # launch-readiness Phase 6 stage 6b-iii-a (Step 3, restore paths must
    # clear the tombstone): `status` no longer marks deletion as of this
    # stage, so a PATCH setting it back to 'active' no longer un-deletes a
    # soft-deleted product on its own. When this happens, `deleted_at_utc=
    # NULL` is added to the SAME UPDATE (never a second write -- see
    # delete_product's own gated-write pattern), and `deleted_at_utc` is
    # added to the changed-field set below so the restore actually reaches
    # the wire. `changed_fields` is a `set`, not the `fields` dict itself --
    # `deleted_at_utc` is not a column `allowed` lists or the SET clause
    # above touches, so it has to be tracked separately from `sets`/`values`.
    changed_fields = set(fields)
    restoring = fields.get('status') == 'active'
    if restoring:
        sets += ', deleted_at_utc=NULL'
        changed_fields.add('deleted_at_utc')
    # launch-readiness Phase 6 stage 6a-i: row_version/updated_at_utc bumped
    # in the SAME UPDATE statement as the field change. `allowed` above is
    # EXACTLY the set of columns products syncs (sync_service.py's product
    # upsert), so `fields` non-empty (checked above) always means a synced
    # field changed -- unlike update_customer/update_supplier below, this
    # route needs no conditional: every successful call bumps.
    now = now_utc_iso()
    cur.execute(f'UPDATE products SET {sets}, row_version=row_version+1, updated_at_utc=? WHERE id=? AND company_id=?',
                values + [now, pid, cid])
    if cur.rowcount == 0:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Product not found'}), 404
    _audit(conn, 'PRODUCT_UPDATED', 'product', pid)
    # `status` included here (AUDIT-follow-up, 2026-08-10): a PATCH restoring a
    # soft-deleted product (`allowed` above includes 'status') is an "update"
    # event, not a "delete" one -- without status in this SELECT, the outbox
    # payload could never carry the restore, so the other device stayed
    # stuck showing the product inactive forever. See sync_service.py's
    # product upsert for the matching apply-side fix.
    #
    # `deleted_at_utc` added to this SELECT (stage 6b-iii-a): the payload
    # must carry the CURRENT (now NULL, if `restoring`) value, not the stale
    # one `data` never even carried -- exactly the same "re-read after the
    # UPDATE rather than trust the request" pattern update_category already
    # uses for its own restore-adjacent fields.
    row = conn.execute("SELECT sku,barcode,name,category_id,supplier_id,cost_price,sell_price,tax_rate,unit,reorder_level,reorder_method,status,deleted_at_utc,row_version,updated_at_utc FROM products WHERE id=?", (pid,)).fetchone()
    # launch-readiness Phase 6 stage 6b-i (changed-field deltas): the payload
    # still carries the full row (the apply-side INSERT half needs it for a
    # device that has never seen this id), but `_changed_fields` now names
    # exactly the columns THIS PATCH touched -- `fields` above is already
    # that set. Without it, a PATCH that only edited `name` would re-send
    # this device's own possibly-stale `sku`/`cost_price`/etc, and the other
    # device's DO UPDATE used to apply all of them, clobbering anything it
    # had changed on those other columns in the meantime. See
    # sync_service.py's `_delta_set_clause` for the apply-side half of this.
    #
    # `sorted(changed_fields)`, not `sorted(fields)` (stage 6b-iii-a): the two
    # agree on every ordinary PATCH; they diverge only on a restore, where
    # `changed_fields` additionally names `deleted_at_utc` so the receiving
    # device's apply branch (which now delta-gates that column too) actually
    # clears its own tombstone instead of leaving it alone.
    _queue_sync_event(cur, 'product', pid, 'update', dict(row) | {'id': pid, '_changed_fields': sorted(changed_fields)})
    conn.commit(); conn.close()
    _sync_nudge()
    return jsonify({'status': 'success'})

@retail_bp.route('/products/<string:pid>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.product.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def delete_product(pid):
    cid = _cid()
    conn = get_retail_conn()
    try:
        cur = conn.cursor()
        # launch-readiness Phase 6 stage 6a-i: row_version bumps in the SAME
        # UPDATE, same as every other site.
        #
        # launch-readiness Phase 6 stage 6b-iii-a (deletion stops overloading
        # `status`): `status='inactive'` is REMOVED from this UPDATE.
        # `deleted_at_utc` is now the ENTIRE visibility gate for a deletion --
        # every read path that could sell/list/count a product (list_products,
        # create_sale's line-item read, adjust_stock, dashboard/AI-context
        # counts, metrics.inventory_value, the PO-preview/reorder-accept
        # write gates) was given a `deleted_at_utc IS NULL` filter in this
        # same stage BEFORE this write changed, and both halves land in the
        # same commit -- see phase6b-deltas-and-tombstones.md / phase6b-
        # decisions.md for why the two must not be split. `status` now means
        # only what it says: a genuinely deactivated-but-not-deleted row
        # (there is no deactivate UI today, but the column is free to mean
        # that again). A LEGACY row soft-deleted before 6b-ii still has
        # `status='inactive'`/`deleted_at_utc IS NULL` and stays hidden by
        # the `status` half of every read filter -- it is deliberately left
        # exactly as it is (phase6b-decisions.md's backfill posture).
        # `AND deleted_at_utc IS NULL` makes deleting an already-tombstoned
        # row a no-op that still returns the existing 404 below, rather than
        # re-stamping a new deletion time over the original one.
        now = now_utc_iso()
        cur.execute(
            "UPDATE products SET deleted_at_utc=?, "
            "row_version=row_version+1, updated_at_utc=? "
            "WHERE id=? AND company_id=? AND deleted_at_utc IS NULL",
            (now, now, pid, cid))
        if cur.rowcount == 0:
            return jsonify({'status': 'error', 'message': 'Product not found'}), 404
        _audit(conn, 'PRODUCT_DELETED', 'product', pid, 'Product deactivated')
        row = conn.execute("SELECT row_version FROM products WHERE id=?", (pid,)).fetchone()
        # `_changed_fields` names only `deleted_at_utc` now -- `status` no
        # longer changes on delete, so naming it would be a lie the apply
        # side would act on (it would still be membership-tested against the
        # code-owned column list, so today it would be harmless, but stating
        # a column changed when it did not is exactly the kind of payload
        # this stage's delta-gating exists to make trustworthy).
        _queue_sync_event(cur, 'product', pid, 'delete', {
            'id': pid, 'row_version': row['row_version'], 'updated_at_utc': now,
            'deleted_at_utc': now, '_changed_fields': ['deleted_at_utc'],
        })
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        current_app.logger.warning("delete_product(%s) failed on a database constraint: %s", pid, exc)
        return jsonify({'status': 'error', 'message': 'This product could not be deleted.'}), 409
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        current_app.logger.exception("delete_product(%s) failed: %s", pid, exc)
        return jsonify({'status': 'error', 'message': 'Could not delete this product.'}), 400
    finally:
        conn.close()
    _sync_nudge()
    return jsonify({'status': 'success', 'message': 'Product deactivated'})

@retail_bp.route('/products/<string:pid>/stock-adjust', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.stock.adjust", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def adjust_stock(pid):
    """Manual stock adjustment, filed against ONE branch's balance row.

    Stock-accuracy sweep -- three real defects fixed together here, because
    they only ever bite the same install at the same time (a multi-branch
    one), and each on its own would leave the "the stock is not accurate"
    report only partly explained:

    1. BRANCH. This route called _default_branch() unconditionally, so
       every adjustment landed on the company's FIRST branch by id no
       matter which branch the operator was actually looking at. In a
       two-branch install, topping up branch 2 silently credited Main
       while the POS on branch 2 kept refusing the sale with "Insufficient
       stock" -- stock present in the totals column, unusable at the till.
       branch_id is now accepted from the caller exactly the way
       create_sale/create_return already accept it, validated to belong to
       THIS company (an unknown or foreign branch is rejected, never
       silently redirected to the default -- a redirect is how the bug
       looked in the first place), and _default_branch is only the
       fallback when the caller names no branch.
    2. PRODUCT EXISTENCE. The balance row was upserted straight from the
       URL's pid with no check that the product exists at all. A stale or
       mistyped id created an orphan inventory_balances +
       inventory_movements pair for a product that is not in this company
       -- stock no screen ever shows, which the reconciliation maintenance
       route below would then report as permanent, unexplainable drift.
    3. NEGATIVE STOCK. Nothing floored the result, so -100 against a
       balance of 5 wrote -95 on hand. create_sale refuses to oversell;
       an adjustment that can drive the same balance negative behind its
       back makes that guarantee worthless, and a negative balance then
       poisons every valuation and reorder calculation downstream.

    Wrapped in BEGIN IMMEDIATE for the same reason create_sale is: the
    "is there enough on hand to remove?" read and the UPDATE that acts on
    it must not straddle another writer's commit.
    """
    data = request.json or {}
    cid  = _cid()
    try:
        qty = float(data.get('quantity', 0))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Invalid quantity'}), 400
    reason = data.get('reason', 'Manual adjustment')
    if qty == 0:
        return jsonify({'status': 'error', 'message': 'Quantity cannot be zero'}), 400

    conn = get_retail_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # launch-readiness Phase 6 stage 6b-iii-a: `AND deleted_at_utc IS
        # NULL` added -- this route had NO filter at all before this stage,
        # not even for `status`, so a tombstoned product's id (still
        # perfectly valid as a foreign key) could have stock adjusted
        # against it forever, invisibly to every screen that lists products.
        # Deliberately does not add a `status='active'` check alongside it --
        # that would be a second, unrelated behaviour change (this route
        # already allowed adjusting a merely-deactivated product) and is out
        # of this stage's scope.
        product = conn.execute(
            "SELECT id, name FROM products WHERE id=? AND company_id=? AND deleted_at_utc IS NULL",
            (pid, cid)
        ).fetchone()
        if not product:
            conn.rollback()
            return jsonify({'status': 'error', 'message': 'Product not found'}), 404

        requested_bid = data.get('branch_id')
        if requested_bid in (None, ''):
            # Launch-readiness chain wave C1: pinned-branch-aware fallback,
            # in place of the bare self-heal -- see _resolve_working_branch's
            # own docstring. The explicit-branch validation just below is
            # untouched; this route already had it before this fix.
            bid, _ = _resolve_working_branch(conn, cid)
        else:
            branch = conn.execute(
                "SELECT id FROM branches WHERE id=? AND company_id=?", (requested_bid, cid)
            ).fetchone()
            if not branch:
                conn.rollback()
                return jsonify({'status': 'error', 'message': 'Branch not found'}), 404
            bid = branch['id']

        conn.execute("""
            INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand)
            VALUES (?,?,?,0)
        """, (cid, pid, bid))
        current = conn.execute(
            "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
            (cid, pid, bid)
        ).fetchone()
        on_hand = float(current['quantity_on_hand'] or 0) if current else 0.0
        # Epsilon, not a bare `< 0`: quantity_on_hand is REAL and is
        # ACCUMULATED by repeated `quantity_on_hand + ?` UPDATEs, so a
        # fractional-unit product legitimately sits at 0.7999999999999999
        # after 0.7 + 0.1. An exact comparison rejects "remove the 0.8 that
        # is there" as if it were an oversell. Same constant the reconciler
        # and the bulk importer compare with (stock_reconciliation.py), so
        # all three agree on what "zero" means.
        if on_hand + qty < -stock_reconciliation.DEFAULT_TOLERANCE:
            conn.rollback()
            return jsonify({'status': 'error',
                            'message': f'Cannot remove {abs(qty)} of "{product["name"]}" -- '
                                       f'only {on_hand} on hand at this branch.'}), 400

        conn.execute("""
            UPDATE inventory_balances SET quantity_on_hand = quantity_on_hand + ?
            WHERE company_id=? AND product_id=? AND branch_id=?
        """, (qty, cid, pid, bid))
        # v13 stamp. Of every movement type this file writes, THIS is the one
        # that most needs it: an 'ADJ' row has no sale, no return and no PO
        # behind it -- somebody simply declared that the shop has a different
        # amount of something than it thought. "Who?" is the entire question,
        # and until v13 the only answer was a free-text `created_by`.
        actor, terminal, utc_now = _stamp()
        movement_type = 'stock_in' if qty > 0 else 'stock_out'
        movement_uid = _new_uid()
        conn.execute("""
            INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,reference,notes,created_by,
                                             uid,actor_user_uid,terminal_id,created_at_utc)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (cid, pid, bid,
              movement_type,
              qty, 'ADJ', reason, _uid(),
              movement_uid, actor, terminal, utc_now))
        # Wave B: queue the matching sync event -- see create_sale's own
        # inventory_movement emission for the shape every writer shares.
        _queue_sync_event(conn.cursor(), 'inventory_movement', movement_uid, 'create', {
            'uid': movement_uid, 'product_id': pid, 'branch_id': bid, 'branch_uid': _branch_uid(conn, bid),
            'movement_type': movement_type, 'quantity': qty, 'unit_cost': 0,
            'reference': 'ADJ', 'notes': reason, 'created_by': _uid(),
            'actor_user_uid': actor, 'terminal_id': terminal, 'created_at_utc': utc_now,
        })
        # branch is now in the audit detail: without it, an audit row for a
        # multi-branch company could never answer "which balance moved?".
        _audit(conn, 'STOCK_ADJUSTED', 'product', pid, f'qty={qty}, branch={bid}, reason={reason}')
        new_row = conn.execute(
            "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
            (cid, pid, bid)
        ).fetchone()
        new_qty = float(new_row['quantity_on_hand']) if new_row else 0.0
        conn.commit()
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        current_app.logger.exception("adjust_stock(%s) failed: %s", pid, exc)
        return jsonify({'status': 'error', 'message': 'Could not adjust stock.'}), 400
    finally:
        conn.close()
    return jsonify({'status': 'success', 'new_stock': new_qty, 'branch_id': bid})

# ── Customers ─────────────────────────────────────────────────────────────────

# launch-readiness Phase 6 stage 6a-i: the exact set of `customers` columns
# sync_service.py's customer upsert applies (name/phone/email/address/status
# -- see that module's `entity_type == "customer"` branch). update_customer's
# own `allowed` fields list below is WIDER than this (it also accepts
# credit_mode/credit_limit, which are local-only credit-terms fields with no
# apply-side column at all), so this set is what update_customer checks a
# given PATCH's fields against before deciding whether to bump row_version
# and emit a sync event at all -- see that route for why "bump only when a
# SYNCED field actually changes" applies here exactly as it does to the
# loyalty-accumulator trap this phase is built around.
#
# `status` ADDED (stage 6b-iii-b): `update_customer` gains a restore path
# this stage (mirroring update_product/update_supplier), and the apply-side
# customer branch's DELTA-GATED column list (sync_service.py) has carried
# `status` since stage 6a-i and `deleted_at_utc` since 6b-iii-a -- both were
# already there, ready to receive this, but INERT until something actually
# emitted a customer `_changed_fields` naming either (that branch's own
# comment says so verbatim). Leaving `status` out of THIS set here would
# have meant a status-only restore PATCH computes `touches_synced=False`
# below and bumps/emits NOTHING -- the restore would apply locally and never
# reach the wire, exactly the way an update to a purely local-only field
# (credit_mode/credit_limit) correctly does today. `status` is not
# local-only, so it cannot be treated the same way.
SYNCED_CUSTOMER_FIELDS = frozenset({'name', 'phone', 'email', 'address', 'status'})

@retail_bp.route('/customers', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def list_customers():
    cid = _cid()
    q   = request.args.get('q', '')
    conn = get_retail_conn()
    _ensure_credit_schema(conn)
    # launch-readiness Phase 6 stage 6b-iii-a: `AND c.deleted_at_utc IS NULL`
    # added to both branches below, alongside the existing `c.status='active'`
    # -- see list_products's identical comment for why both conditions stay
    # (a legacy pre-6b-ii delete is hidden only by `status`, a post-6b-iii-a
    # delete only by `deleted_at_utc`).
    if q:
        rows = conn.execute("""
            SELECT c.*, COUNT(s.id) as order_count
            FROM customers c LEFT JOIN sales s ON s.customer_id=c.id AND s.company_id=c.company_id
            WHERE c.company_id=? AND c.status='active' AND c.deleted_at_utc IS NULL
                  AND (c.name LIKE ? OR c.phone LIKE ? OR c.email LIKE ?)
            GROUP BY c.id ORDER BY c.name
        """, (cid, f'%{q}%', f'%{q}%', f'%{q}%')).fetchall()
    else:
        rows = conn.execute("""
            SELECT c.*, COUNT(s.id) as order_count
            FROM customers c LEFT JOIN sales s ON s.customer_id=c.id AND s.company_id=c.company_id
            WHERE c.company_id=? AND c.status='active' AND c.deleted_at_utc IS NULL
            GROUP BY c.id ORDER BY c.total_spent DESC LIMIT 200
        """, (cid,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@retail_bp.route('/customers', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.customer.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
# retail.sell, not a manager code: create_sale REFUSES a credit sale to a
# walk-in ("Credit sales require a customer"), so a till that cannot name a
# new customer is a till that cannot ring a credit sale at all. This handler
# writes name/phone/email/address and nothing else -- it cannot grant credit,
# which is why it is safe at the till while update_customer's credit fields
# are not (see that route).
@mt_require_capability(CAP_SELL)
def create_customer():
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Customer name required'}), 400
    cid  = _cid()
    conn = get_retail_conn()
    cur  = conn.cursor()
    nid = str(_uuid.uuid4())
    # launch-readiness Phase 6 stage 6a-i: stamped at creation, same
    # reasoning as create_category's identical comment above.
    now = now_utc_iso()
    cur.execute(
        "INSERT INTO customers (id,company_id,name,phone,email,address,row_version,updated_at_utc) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (nid, cid, data['name'], data.get('phone',''), data.get('email',''), data.get('address',''), 1, now))
    _audit(conn, 'CUSTOMER_CREATED', 'customer', nid, data['name'])
    _queue_sync_event(cur, 'customer', nid, 'create', {
        'id': nid, 'name': data['name'], 'phone': data.get('phone', ''),
        'email': data.get('email', ''), 'address': data.get('address', ''),
        'row_version': 1, 'updated_at_utc': now,
    })
    conn.commit(); conn.close()
    _sync_nudge()
    return jsonify({'status': 'success', 'data': {'id': nid}})

@retail_bp.route('/customers/<string:cust_id>', methods=['PATCH'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.customer.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_SELL)
def update_customer(cust_id):
    data = request.json or {}
    cid  = _cid()
    # `status` ADDED (stage 6b-iii-b, Part 3 of the tombstone rule's
    # completion): the read-path pass found `update_customer` had NO restore
    # path at all -- product/supplier PATCH both accept `status` and clearing
    # it back to 'active' also clears `deleted_at_utc` (stage 6b-iii-a); this
    # route omitted `status` entirely, so a deleted customer had no way back.
    # See the capability gate immediately below for why this field alone,
    # unlike the other five, needs its OWN check.
    fields = {k: v for k, v in data.items() if k in ['name','phone','email','address','credit_mode','credit_limit','status']}
    if not fields:
        return jsonify({'status': 'error', 'message': 'No valid fields'}), 400

    # ── retail.discount, on two of the seven writable fields ─────────────────
    # The route is gated on retail.sell because the till legitimately edits a
    # customer: correcting a mistyped phone number is part of ringing a named
    # sale. But two of the fields this same handler accepts are not contact
    # details at all -- credit_mode and credit_limit decide whether this
    # customer may walk out with goods they have not paid for, and how much.
    # create_sale reads exactly these two to accept or refuse a credit sale,
    # so a cashier who could set them could authorise their own unlimited
    # credit sale in two requests. That is the same authority as a discount --
    # value handed over with no matching payment, only spread over time -- so
    # it takes the same code, and only that half of the payload is refused.
    #
    # `status` is NOT in that class -- widening this existing check to cover
    # it too would be wrong, it authorises nothing about money -- so it gets
    # its OWN separate check just below, not folded into this one.
    if ('credit_mode' in fields or 'credit_limit' in fields) and not session_has_capability(CAP_DISCOUNT):
        return jsonify({'status': 'error', 'message': CREDIT_TERMS_DENIED_MESSAGE}), 403
    # ── retail.stock.adjust, on `status` alone ────────────────────────────────
    # `delete_customer` (this file) is gated retail.stock.adjust -- explicitly
    # NOT retail.sell, on purpose: "master-data retirement, not selling" (see
    # that route's own comment). This route's floor is retail.sell, so
    # without a check here, adding `status` to `allowed` above would let any
    # cashier PATCH `status: 'active'` and undo a manager's deletion -- a
    # strictly WEAKER path to the same effect delete_customer already
    # requires the stronger capability for, i.e. a privilege escalation.
    # Gated at the SAME capability the deletion it reverses requires, the
    # same "field-level check on top of a broader route floor" shape as the
    # credit_mode/credit_limit check above.
    if 'status' in fields and not session_has_capability(CAP_STOCK_ADJUST):
        return jsonify({'status': 'error', 'message': CUSTOMER_RESTORE_DENIED_MESSAGE}), 403
    conn = get_retail_conn()
    cur = conn.cursor()
    sets = ', '.join(f'{k}=?' for k in fields)
    params = list(fields.values())
    # launch-readiness Phase 6 stage 6b-iii-a (Step 3, restore paths must
    # clear the tombstone): same rule as update_product's/update_supplier's
    # identical comment -- `status` no longer marks deletion, so setting it
    # to 'active' must ALSO clear `deleted_at_utc`, in the SAME UPDATE.
    # `restoring` implies `touches_synced` below (`status` is now a member of
    # SYNCED_CUSTOMER_FIELDS), so this can never fire on a credit-only PATCH
    # that bumps nothing.
    restoring = fields.get('status') == 'active'
    if restoring:
        sets += ', deleted_at_utc=NULL'
    # launch-readiness Phase 6 stage 6a-i, THE loyalty-accumulator rule
    # applied to a second site: `fields` here can be entirely credit_mode/
    # credit_limit, which are local-only credit-terms columns with no
    # apply-side column at all (SYNCED_CUSTOMER_FIELDS above). Bumping
    # row_version on a credit-only edit would mean a genuine name/phone
    # correction made on another till later would arrive with a LOWER
    # version than this device's own credit-only bump and be rejected as
    # stale once stage 6a-ii's gate is live -- the exact shape
    # phase6-catalogue-correctness.md's "THE TRAP" describes for the
    # loyalty accumulator, reproduced here by a different route. So this
    # bumps -- and emits -- ONLY when a field in THIS request is actually
    # synced; a credit-only PATCH bumps nothing and queues nothing.
    touches_synced = bool(SYNCED_CUSTOMER_FIELDS & fields.keys())
    now = None
    if touches_synced:
        now = now_utc_iso()
        sets += ', row_version=row_version+1, updated_at_utc=?'
        params.append(now)
    cur.execute(f'UPDATE customers SET {sets} WHERE id=? AND company_id=?',
                params + [cust_id, cid])
    if cur.rowcount == 0:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Customer not found'}), 404
    _audit(conn, 'CUSTOMER_UPDATED', 'customer', cust_id)
    if touches_synced:
        # `deleted_at_utc` added to this SELECT (stage 6b-iii-b): the payload
        # must carry the CURRENT (now NULL, if `restoring`) value -- same
        # "re-read after the UPDATE rather than trust the request" pattern
        # update_product/update_supplier already use for their own restore.
        row = conn.execute(
            "SELECT name,phone,email,address,deleted_at_utc,row_version,updated_at_utc FROM customers WHERE id=?",
            (cust_id,)).fetchone()
        # launch-readiness Phase 6 stage 6b-i (changed-field deltas):
        # `_changed_fields` is `SYNCED_CUSTOMER_FIELDS & fields.keys()`, NOT
        # `sorted(fields)` -- `fields` can also carry credit_mode/
        # credit_limit, which are local-only credit-terms columns with no
        # apply-side column at all (see `touches_synced`'s own comment
        # above). Naming them would be harmless -- the apply side only ever
        # membership-tests `_changed_fields` against its own code-owned
        # column list (`_delta_set_clause`'s docstring) -- but misleading:
        # a field that has no synced column is not a "changed field" in any
        # sense the receiving device understands.
        #
        # stage 6b-iii-b: `deleted_at_utc` added to the changed set ONLY when
        # `restoring` -- an ordinary status-untouched (or contact-detail-only)
        # PATCH must not claim `deleted_at_utc` changed, or the apply side's
        # delta gate on that column (sync_service.py's customer branch) would
        # clear a tombstone this PATCH never touched -- same rule as
        # update_supplier's identical guard.
        changed = (SYNCED_CUSTOMER_FIELDS & fields.keys()) | ({'deleted_at_utc'} if restoring else set())
        _queue_sync_event(cur, 'customer', cust_id, 'update',
                           dict(row) | {'id': cust_id, '_changed_fields': sorted(changed)})
    conn.commit(); conn.close()
    _sync_nudge()
    return jsonify({'status': 'success'})

@retail_bp.route('/customers/<string:cust_id>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.customer.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
# Deliberately NOT retail.sell, unlike its create/update siblings above. The
# till writes customer rows as part of ringing a named sale; it has no reason
# to retire one. Deactivating a customer takes them out of list_customers
# (which filters status='active') while customers_receivables still counts
# their balance, so a cashier could make a debtor invisible at the till
# without clearing the debt -- master-data retirement, not selling.
@mt_require_capability(CAP_STOCK_ADJUST)
def delete_customer(cust_id):
    cid = _cid()
    conn = get_retail_conn()
    try:
        cur = conn.cursor()
        # launch-readiness Phase 6 stage 6a-i: row_version bumps in the SAME
        # UPDATE.
        #
        # launch-readiness Phase 6 stage 6b-iii-a (deletion stops overloading
        # `status`): `status='inactive'` is REMOVED -- see delete_product's
        # identical comment above for the full reasoning (both halves of
        # this stage, the read filters and this write, land in the same
        # commit). `AND deleted_at_utc IS NULL` makes a repeat delete of an
        # already-tombstoned row a no-op 404, not a re-stamp.
        now = now_utc_iso()
        cur.execute(
            "UPDATE customers SET deleted_at_utc=?, "
            "row_version=row_version+1, updated_at_utc=? "
            "WHERE id=? AND company_id=? AND deleted_at_utc IS NULL",
            (now, now, cust_id, cid))
        if cur.rowcount == 0:
            return jsonify({'status': 'error', 'message': 'Customer not found'}), 404
        _audit(conn, 'CUSTOMER_DELETED', 'customer', cust_id, 'Customer deactivated')
        row = conn.execute("SELECT row_version FROM customers WHERE id=?", (cust_id,)).fetchone()
        # `_changed_fields` names only `deleted_at_utc` -- see delete_product's
        # identical comment above.
        _queue_sync_event(cur, 'customer', cust_id, 'delete', {
            'id': cust_id, 'row_version': row['row_version'], 'updated_at_utc': now,
            'deleted_at_utc': now, '_changed_fields': ['deleted_at_utc'],
        })
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        current_app.logger.warning("delete_customer(%s) failed on a database constraint: %s", cust_id, exc)
        return jsonify({'status': 'error', 'message': 'This customer could not be deleted.'}), 409
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        current_app.logger.exception("delete_customer(%s) failed: %s", cust_id, exc)
        return jsonify({'status': 'error', 'message': 'Could not delete this customer.'}), 400
    finally:
        conn.close()
    _sync_nudge()
    return jsonify({'status': 'success', 'message': 'Customer deactivated'})

@retail_bp.route('/customers/<string:cust_id>/sales', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def customer_sales(cust_id):
    cid = _cid()
    conn = get_retail_conn()
    rows = conn.execute("""
        SELECT s.id, s.sale_number, s.total, s.payment_method, s.created_at,
               COUNT(si.id) as items
        FROM sales s LEFT JOIN sale_items si ON si.sale_id=s.id
        WHERE s.company_id=? AND s.customer_id=?
        GROUP BY s.id ORDER BY s.created_at DESC LIMIT 50
    """, (cid, cust_id)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

# ── Suppliers ─────────────────────────────────────────────────────────────────

# launch-readiness Phase 6 stage 6a-i: the exact set of `suppliers` columns
# sync_service.py's supplier upsert applies (name/phone/email/address/status
# -- see that module's `entity_type == "supplier"` branch). update_supplier's
# own `allowed` fields list below also accepts `payment_terms`, which is
# NOT yet a first-class synced column (see that route's own comment) -- same
# "bump only when a SYNCED field actually changes" gate as
# SYNCED_CUSTOMER_FIELDS above.
SYNCED_SUPPLIER_FIELDS = frozenset({'name', 'phone', 'email', 'address', 'status'})

@retail_bp.route('/suppliers', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def list_suppliers():
    cid = _cid()
    conn = get_retail_conn()
    _ensure_credit_schema(conn)
    # launch-readiness Phase 6 stage 6b-iii-a: `AND s.deleted_at_utc IS NULL`
    # added alongside the existing `s.status='active'` -- see list_customers's
    # identical comment above for why both conditions stay.
    rows = conn.execute("""
        SELECT s.*, COUNT(po.id) as order_count
        FROM suppliers s LEFT JOIN purchase_orders po ON po.supplier_id=s.id AND po.company_id=s.company_id
        WHERE s.company_id=? AND s.status='active' AND s.deleted_at_utc IS NULL
        GROUP BY s.id ORDER BY s.name
    """, (cid,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@retail_bp.route('/suppliers', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.supplier.manage", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def create_supplier():
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Supplier name required'}), 400
    cid = _cid()
    conn = get_retail_conn()
    cur = conn.cursor()
    nid = str(_uuid.uuid4())
    # launch-readiness Phase 6 stage 6a-i: stamped at creation, same
    # reasoning as create_category's identical comment above.
    now = now_utc_iso()
    cur.execute(
        "INSERT INTO suppliers (id,company_id,name,phone,email,address,row_version,updated_at_utc) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (nid, cid, data['name'], data.get('phone',''), data.get('email',''), data.get('address',''), 1, now))
    _audit(conn, 'SUPPLIER_CREATED', 'supplier', nid, data['name'])
    _queue_sync_event(cur, 'supplier', nid, 'create', {
        'id': nid, 'name': data['name'], 'phone': data.get('phone', ''),
        'email': data.get('email', ''), 'address': data.get('address', ''),
        'row_version': 1, 'updated_at_utc': now,
    })
    conn.commit(); conn.close()
    _sync_nudge()
    return jsonify({'status': 'success', 'data': {'id': nid}})

@retail_bp.route('/suppliers/<string:sid>', methods=['PATCH'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.supplier.manage", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def update_supplier(sid):
    data = request.json or {}
    cid  = _cid()
    fields = {k: v for k, v in data.items() if k in ['name','phone','email','address','status','payment_terms']}
    if not fields:
        return jsonify({'status': 'error', 'message': 'No valid fields'}), 400
    conn = get_retail_conn()
    cur = conn.cursor()
    sets = ', '.join(f'{k}=?' for k in fields)
    params = list(fields.values())
    # launch-readiness Phase 6 stage 6a-i: same "bump only when a SYNCED
    # field actually changes" gate as update_customer above -- `fields` here
    # can be entirely `payment_terms`, which is not yet a first-class synced
    # column (SYNCED_SUPPLIER_FIELDS above / the comment on this route's own
    # SELECT below), so a payment_terms-only PATCH must bump nothing and
    # emit nothing, for the identical reason a credit-only customer PATCH
    # must not.
    touches_synced = bool(SYNCED_SUPPLIER_FIELDS & fields.keys())
    # launch-readiness Phase 6 stage 6b-iii-a (Step 3, restore paths must
    # clear the tombstone): same rule as update_product's identical comment
    # above -- `status` no longer marks deletion, so setting it to 'active'
    # must ALSO clear `deleted_at_utc`, in the SAME UPDATE. `restoring`
    # implies `touches_synced` (`status` is a member of
    # SYNCED_SUPPLIER_FIELDS, checked just above), so this can never fire on
    # a `payment_terms`-only PATCH that bumps nothing.
    restoring = fields.get('status') == 'active'
    if restoring:
        sets += ', deleted_at_utc=NULL'
    now = None
    if touches_synced:
        now = now_utc_iso()
        sets += ', row_version=row_version+1, updated_at_utc=?'
        params.append(now)
    cur.execute(f'UPDATE suppliers SET {sets} WHERE id=? AND company_id=?',
                params + [sid, cid])
    if cur.rowcount == 0:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Supplier not found'}), 404
    _audit(conn, 'SUPPLIER_UPDATED', 'supplier', sid)
    # `status` and `payment_terms` included here (AUDIT-follow-up, 2026-08-10):
    # both are in this route's own `allowed` fields list above (payment_terms
    # can be PATCHed even though it's never set at creation -- create_supplier
    # doesn't accept it at all), so both were silently patchable locally but
    # never carried in the outbox payload. `status` completes the same
    # soft-delete/restore round-trip fix as update_product's SELECT above --
    # see sync_service.py's supplier upsert. `payment_terms` is included here
    # for forward-compatible payload completeness, matching this codebase's
    # existing "unknown field/entity type is silently ignored, not an error"
    # pattern (see sync_service.py's module docstring) -- sync_service.py's
    # supplier upsert does not yet apply it (payment_terms/credit_balance are
    # not first-class synced columns in this phase; see
    # docs/superpowers/specs/2026-08-07-retail-catalog-party-sync-expansion-design.md),
    # so it does not cross-device propagate yet even after this change.
    if touches_synced:
        # `deleted_at_utc` added to this SELECT (stage 6b-iii-a): the payload
        # must carry the CURRENT value -- NULL when `restoring` just cleared
        # it in the same UPDATE above, unchanged otherwise.
        row = conn.execute(
            "SELECT name,phone,email,address,status,payment_terms,deleted_at_utc,row_version,updated_at_utc "
            "FROM suppliers WHERE id=?", (sid,)).fetchone()
        # launch-readiness Phase 6 stage 6b-i (changed-field deltas):
        # `_changed_fields` is `SYNCED_SUPPLIER_FIELDS & fields.keys()`, NOT
        # `sorted(fields)` -- `fields` can also carry `payment_terms`, which
        # is in this route's `allowed` list but is not a first-class synced
        # column (see `touches_synced`'s own comment above). Same reasoning
        # as update_customer's identical guard.
        #
        # stage 6b-iii-a: `deleted_at_utc` added to the changed set ONLY when
        # `restoring` -- an ordinary status-untouched PATCH must not claim
        # `deleted_at_utc` changed, or the apply side's delta gate on that
        # column (sync_service.py's supplier branch) would clear a tombstone
        # this PATCH never touched.
        changed = (SYNCED_SUPPLIER_FIELDS & fields.keys()) | ({'deleted_at_utc'} if restoring else set())
        _queue_sync_event(cur, 'supplier', sid, 'update',
                           dict(row) | {'id': sid, '_changed_fields': sorted(changed)})
    conn.commit(); conn.close()
    _sync_nudge()
    return jsonify({'status': 'success'})

@retail_bp.route('/suppliers/<string:sid>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.supplier.manage", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def delete_supplier(sid):
    cid = _cid()
    conn = get_retail_conn()
    try:
        cur = conn.cursor()
        # launch-readiness Phase 6 stage 6a-i: row_version bumps in the SAME
        # UPDATE.
        #
        # launch-readiness Phase 6 stage 6b-iii-a (deletion stops overloading
        # `status`): `status='inactive'` is REMOVED -- see delete_product's
        # identical comment above for the full reasoning. `AND deleted_at_utc
        # IS NULL` makes a repeat delete of an already-tombstoned row a no-op
        # 404, not a re-stamp.
        now = now_utc_iso()
        cur.execute(
            "UPDATE suppliers SET deleted_at_utc=?, "
            "row_version=row_version+1, updated_at_utc=? "
            "WHERE id=? AND company_id=? AND deleted_at_utc IS NULL",
            (now, now, sid, cid))
        if cur.rowcount == 0:
            return jsonify({'status': 'error', 'message': 'Supplier not found'}), 404
        _audit(conn, 'SUPPLIER_DELETED', 'supplier', sid, 'Supplier deactivated')
        row = conn.execute("SELECT row_version FROM suppliers WHERE id=?", (sid,)).fetchone()
        # `_changed_fields` names only `deleted_at_utc` -- see delete_product's
        # identical comment above.
        _queue_sync_event(cur, 'supplier', sid, 'delete', {
            'id': sid, 'row_version': row['row_version'], 'updated_at_utc': now,
            'deleted_at_utc': now, '_changed_fields': ['deleted_at_utc'],
        })
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        current_app.logger.warning("delete_supplier(%s) failed on a database constraint: %s", sid, exc)
        return jsonify({'status': 'error', 'message': 'This supplier could not be deleted.'}), 409
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        current_app.logger.exception("delete_supplier(%s) failed: %s", sid, exc)
        return jsonify({'status': 'error', 'message': 'Could not delete this supplier.'}), 400
    finally:
        conn.close()
    _sync_nudge()
    return jsonify({'status': 'success', 'message': 'Supplier deactivated'})

# ── Supplier Contacts (PO-preview-by-supplier foundation, schema v6) ───────────
# A supplier can have several named contacts (orders/accounts/general), each
# with its own preferred channel -- core/retail/po_split.py's contact
# resolution ladder (resolve_contact) reads these to decide who a split
# group's slice would be routed to. CRUD only here: nothing dispatches
# anything yet (that lands with the routing routes later this week).

@retail_bp.route('/suppliers/<string:sid>/contacts', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def list_supplier_contacts(sid):
    cid = _cid()
    conn = get_retail_conn()
    sup = conn.execute("SELECT id FROM suppliers WHERE id=? AND company_id=?", (sid, cid)).fetchone()
    if not sup:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Supplier not found'}), 404
    rows = conn.execute(
        "SELECT * FROM supplier_contacts WHERE company_id=? AND supplier_id=? ORDER BY is_primary DESC, name",
        (cid, sid)
    ).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@retail_bp.route('/suppliers/<string:sid>/contacts', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.supplier.manage", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def create_supplier_contact(sid):
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Contact name required'}), 400
    cid = _cid()
    conn = get_retail_conn()
    try:
        # launch-readiness Phase 6 stage 6b-iii-b: `AND deleted_at_utc IS
        # NULL` added -- no NEW contact may be attached to a deleted
        # supplier. `list_supplier_contacts` just above deliberately does
        # NOT gain this filter (reading EXISTING contacts of a since-deleted
        # supplier must not break), the same "write gates refuse a
        # tombstoned row, reads of historical/in-flight records do not
        # filter" rule this stage applies everywhere else.
        #
        # `AND status='active'` added alongside it (ROADMAP.md's "2026-08-28
        # -- Gaps surfaced by Phase 6b" entry): two deleted populations exist
        # here. A row deleted BEFORE tombstones existed carries
        # `status='inactive'` with `deleted_at_utc` still NULL -- the
        # deliberate no-backfill posture pinned by
        # `test_a_legacy_row_deleted_before_tombstones_is_still_hidden` --
        # while one deleted AFTER carries `status='active'` with the
        # tombstone set instead. Every catalogue READ already filters on
        # BOTH conditions for exactly this reason (drop either and the OTHER
        # population reappears); this WRITE gate now matches that rule
        # rather than catching only the newer half of it. Same pairing, same
        # reason, on `create_purchase_order`, `preview_po_split`,
        # `accept_reorder_request`, create_sale's credit-customer lookup,
        # and reorder_hook.py's product re-fetch -- not repeated in full at
        # each of those.
        sup = conn.execute(
            "SELECT id FROM suppliers WHERE id=? AND company_id=? AND status='active' AND deleted_at_utc IS NULL",
            (sid, cid)).fetchone()
        if not sup:
            return jsonify({'status': 'error', 'message': 'Supplier not found'}), 404
        role = data.get('role') or 'orders'
        is_primary = 1 if data.get('is_primary') else 0
        nid = str(_uuid.uuid4())
        # BEGIN IMMEDIATE: the primary-contact invariant below (clear, then
        # set) must not interleave with a concurrent request doing the same
        # thing for the same (supplier, role) -- same reasoning as
        # create_sale's stock-check BEGIN IMMEDIATE above.
        conn.execute("BEGIN IMMEDIATE")
        if is_primary:
            # Enforced invariant: at most one primary contact per (supplier, role).
            conn.execute(
                "UPDATE supplier_contacts SET is_primary=0 WHERE company_id=? AND supplier_id=? AND role=?",
                (cid, sid, role)
            )
        conn.execute("""
            INSERT INTO supplier_contacts (id,company_id,supplier_id,name,role,email,phone,whatsapp,channel_preference,is_primary,status)
            VALUES (?,?,?,?,?,?,?,?,?,?,'active')
        """, (nid, cid, sid, data['name'], role, data.get('email'), data.get('phone'), data.get('whatsapp'),
              data.get('channel_preference', 'whatsapp'), is_primary))
        _audit(conn, 'SUPPLIER_CONTACT_CREATED', 'supplier_contact', nid, data['name'])
        conn.commit()
        return jsonify({'status': 'success', 'data': {'id': nid}})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()

@retail_bp.route('/suppliers/<string:sid>/contacts/<string:contact_id>', methods=['PATCH'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.supplier.manage", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def update_supplier_contact(sid, contact_id):
    data = request.json or {}
    cid = _cid()
    allowed = ['name', 'role', 'email', 'phone', 'whatsapp', 'channel_preference', 'is_primary', 'status']
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({'status': 'error', 'message': 'No valid fields'}), 400
    conn = get_retail_conn()
    try:
        sup = conn.execute("SELECT id FROM suppliers WHERE id=? AND company_id=?", (sid, cid)).fetchone()
        if not sup:
            return jsonify({'status': 'error', 'message': 'Supplier not found'}), 404
        existing = conn.execute(
            "SELECT role FROM supplier_contacts WHERE id=? AND company_id=? AND supplier_id=?",
            (contact_id, cid, sid)
        ).fetchone()
        if not existing:
            return jsonify({'status': 'error', 'message': 'Contact not found'}), 404

        # Route to whichever role this contact will hold AFTER this PATCH
        # (its new role if role is being changed in the same request, else
        # its current one) -- the invariant is scoped per (supplier, role).
        role_for_invariant = fields.get('role', existing['role'])
        if 'is_primary' in fields:
            fields['is_primary'] = 1 if fields['is_primary'] else 0

        conn.execute("BEGIN IMMEDIATE")
        if fields.get('is_primary'):
            # Enforced invariant: at most one primary contact per (supplier, role).
            conn.execute(
                "UPDATE supplier_contacts SET is_primary=0 WHERE company_id=? AND supplier_id=? AND role=?",
                (cid, sid, role_for_invariant)
            )
        sets = ', '.join(f'{k}=?' for k in fields)
        conn.execute(f'UPDATE supplier_contacts SET {sets} WHERE id=? AND company_id=? AND supplier_id=?',
                     list(fields.values()) + [contact_id, cid, sid])
        _audit(conn, 'SUPPLIER_CONTACT_UPDATED', 'supplier_contact', contact_id)
        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()

@retail_bp.route('/suppliers/<string:sid>/contacts/<string:contact_id>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.supplier.manage", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def delete_supplier_contact(sid, contact_id):
    """Soft-delete: sets status='inactive', never removes the row -- matches
    delete_supplier's own soft-delete convention just above, and keeps the
    contact's history (it may still be referenced by past split-preview
    audit trails once routing lands)."""
    cid = _cid()
    conn = get_retail_conn()
    sup = conn.execute("SELECT id FROM suppliers WHERE id=? AND company_id=?", (sid, cid)).fetchone()
    if not sup:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Supplier not found'}), 404
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE supplier_contacts SET status='inactive' WHERE id=? AND company_id=? AND supplier_id=?",
            (contact_id, cid, sid)
        )
        if cur.rowcount == 0:
            return jsonify({'status': 'error', 'message': 'Contact not found'}), 404
        _audit(conn, 'SUPPLIER_CONTACT_DELETED', 'supplier_contact', contact_id, 'Contact deactivated')
        conn.commit()
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        current_app.logger.exception("delete_supplier_contact(%s,%s) failed: %s", sid, contact_id, exc)
        return jsonify({'status': 'error', 'message': 'Could not delete this contact.'}), 400
    finally:
        conn.close()
    return jsonify({'status': 'success', 'message': 'Contact deactivated'})

# ── Purchase Orders ───────────────────────────────────────────────────────────

@retail_bp.route('/purchase-orders', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def list_purchase_orders():
    cid = _cid()
    conn = get_retail_conn()
    # Cross-tenant leak fix, 2026-08-29: `AND s.company_id=po.company_id`
    # added to the JOIN CONDITION, deliberately NOT to the WHERE clause --
    # same reasoning as list_products's categories-join comment (above,
    # this file). This is a LEFT JOIN and a PO with no supplier at all
    # (supplier_id IS NULL, a legitimate PO) must still be returned; a
    # condition in WHERE would silently drop every supplier-less PO from
    # this list instead of just blanking its supplier_name. Before this
    # fix, a purchase_orders row carrying another company's supplier_id
    # (create_purchase_order accepted one with no validation until this
    # same fix) joined straight across the tenant boundary and handed this
    # company that OTHER company's supplier NAME. Mirrors the existing
    # reverse-direction join in `list_suppliers` (above, this file):
    # `ON po.supplier_id=s.id AND po.company_id=s.company_id`.
    rows = conn.execute("""
        SELECT po.*, s.name as supplier_name
        FROM purchase_orders po LEFT JOIN suppliers s ON po.supplier_id=s.id AND s.company_id=po.company_id
        WHERE po.company_id=? ORDER BY po.created_at DESC LIMIT 100
    """, (cid,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@retail_bp.route('/purchase-orders', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.purchase.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def create_purchase_order():
    data = request.json or {}
    cid  = _cid()
    items = data.get('items', [])
    if not items:
        return jsonify({'status': 'error', 'message': 'At least one item required'}), 400

    # AUDIT: same asymmetry class as void_payment's B1 fix above -- ordering
    # a PO is retail.stock.adjust (a manager-level action, and this route's
    # own decorator floor stays exactly that), but this route's OWN
    # amount_paid field posts a real money-out payment to the supplier via
    # _record_payment below (~1310ish; the identical action supplier_payment
    # and pay_purchase_order both require CAP_EMPLOYEES for, with an
    # explicit comment on supplier_payment that receiving a delivery and
    # paying for it are different authorities). Without this check that
    # CAP_EMPLOYEES gate was decoration only: a manager correctly refused
    # by both dedicated payment routes could still pay a supplier by typing
    # a number into amount_paid on a brand-new PO instead.
    #
    # Refuses the WHOLE request rather than creating the PO and silently
    # dropping amount_paid, on purpose. This route has no partial-success
    # shape in its response (unlike execute_import's `warnings` list) to
    # hang a "payment not recorded" notice on, and silently dropping a
    # figure someone typed into a money field is how a shop ends up
    # believing a supplier was paid when they were not -- the PO would show
    # payment_status='unpaid' with no error, which reads as this route
    # working, not as it refusing part of the request. Refusing outright
    # gives the caller nothing rather than a wrong thing; they can retry
    # with amount_paid omitted and have an owner record the down-payment
    # through pay_purchase_order once CAP_EMPLOYEES is available.
    #
    # amount_paid is computed HERE, ONCE, via the same _money() coercion and
    # the same > 0.005 threshold the real payment write uses further down --
    # that single local is reused rather than recomputed, so no numeric
    # spelling (absent, 0, negative, a numeric string, a float that rounds
    # up under ROUND_HALF_UP) can reach _record_payment without first
    # passing through this exact check. Negative values fall out of scope
    # for the same reason they already fell out of the pre-existing
    # `if amount_paid > 0.005:` guard below: they were never going to post a
    # payment either way.
    #
    # Checked BEFORE get_retail_conn()/_next_ref() -- this route holds no
    # explicit BEGIN IMMEDIATE, but _next_ref() is the first statement that
    # writes (it increments doc_sequences), so the refusal has to run even
    # earlier than that to have no side effect at all, same rule
    # void_payment's check documents relative to its own write.
    amount_paid = _money(data.get('amount_paid', 0))
    if amount_paid > 0.005 and not session_has_capability(CAP_EMPLOYEES):
        return jsonify({'status': 'error', 'message': SUPPLIER_PAYMENT_DENIED_MESSAGE}), 403

    conn = get_retail_conn()
    _ensure_credit_schema(conn)
    # 2026-08-28: defense-in-depth error containment, same shape and same
    # reasoning as delete_category's own Fix 4 comment (above, this file).
    # `purchase_orders.po_number` carries a bare (not per-company, not
    # per-device) UNIQUE constraint (database/schema.py), and before the
    # company+device fragments added to po_number below, two companies on
    # one install minting their FIRST purchase order both generated
    # "PO-000001" and collided on it. This route had NO try/except around
    # that INSERT at all: the resulting sqlite3.IntegrityError escaped
    # straight into Flask's default 500 handler, WITH the connection never
    # closed on that path -- leaked for the rest of the process, holding
    # the WAL write lock it took on its first write, so every OTHER write
    # anywhere in this database then failed with "database is locked". One
    # colliding PO took the whole install down, not just that request (see
    # ROADMAP.md's 2026-08-28 "po_number collides across companies" entry).
    #
    # The fragments below make that SPECIFIC collision unreachable in
    # practice, but the containment stays regardless and is not redundant
    # with it: this is what stops the NEXT constraint added anywhere near
    # this table from reintroducing the exact same install-wide outage,
    # exactly as delete_category's own comment already argues for that
    # route.
    try:
        cur = conn.cursor()
        # launch-readiness Phase 6 stage 6b-iii-b: this route had NO product
        # existence check at all before this stage -- `purchase_order_items` was
        # inserted straight from the request's `product_id`s with no read of
        # `products` in between. `preview_po_split` just below in this file
        # already gained a `company_id=? AND deleted_at_utc IS NULL` product read
        # at stage 6b-iii-a for the identical reason (it is a WRITE gate that
        # plans a PO a caller may go on to actually create, not a display list);
        # this is that same read, added here so a NEW PO cannot be raised for a
        # deleted product either. `list_purchase_orders`/`get_purchase_order`/
        # `receive_purchase_order` deliberately do NOT gain this filter -- a PO
        # already raised for a since-deleted product must stay receivable, or
        # stock already in transit becomes permanently unreceivable (same
        # "existing pending/in-flight documents do not filter" rule stage
        # 6b-iii-a established for a pending reorder request against a deleted
        # product; see reorder_hook.py's own comment for the write-side half of
        # that same rule).
        #
        # `status='active'` joins the tombstone check for the two-population
        # reason `create_supplier_contact`'s comment (above, this file) spells
        # out in full -- a product deleted before tombstones existed never got a
        # `deleted_at_utc` stamp, so the tombstone filter alone would miss it and
        # let it straight back onto a brand-new PO.
        po_product_ids = {item['product_id'] for item in items}
        if po_product_ids:
            placeholders = ','.join('?' * len(po_product_ids))
            valid_rows = conn.execute(
                f"SELECT id FROM products WHERE company_id=? AND status='active' AND deleted_at_utc IS NULL AND id IN ({placeholders})",
                [cid, *po_product_ids]
            ).fetchall()
            missing_ids = po_product_ids - {r['id'] for r in valid_rows}
            if missing_ids:
                return jsonify({'status': 'error',
                                 'message': f'Unknown product_id: {sorted(missing_ids)[0]}'}), 400

        # Cross-tenant leak fix, 2026-08-29 (ROADMAP.md's "create_purchase_
        # order still has no cross-tenant validation on supplier_id" entry):
        # `supplier_id` carried NO validation of any kind before this block
        # -- not existence, not company ownership, not tombstone. A caller
        # in company A could pass company B's supplier_id straight through;
        # the INSERT below has no FK enforcing a company_id match, so the
        # row landed cleanly, and `list_purchase_orders`/`get_purchase_order`
        # (below in this file) joined suppliers with no company condition on
        # the join either, so the very next read handed company A company
        # B's supplier NAME. This closes the WRITE half; the join fix on
        # those two read routes closes the READ half -- neither is
        # sufficient alone, since validation here does nothing for rows
        # already written, and the join fix alone would still let a bad row
        # be written (and would silently blank its supplier name on read
        # rather than refuse the write).
        #
        # `supplier_id` stays OPTIONAL: `if supplier_id:` guards this
        # exactly like every other `if supplier_id:` branch already in this
        # function (the AP-ledger credit adjustment below) -- a PO with no
        # supplier at all is legitimate and must not be refused. Same
        # shape, same error style, same status code as the product check
        # immediately above: 400, with the offending id named rather than a
        # generic "invalid" message. `status='active' AND deleted_at_utc IS
        # NULL` is the same tombstone/legacy-delete pairing
        # `create_supplier_contact`'s comment (above, this file) explains in
        # full.
        supplier_id = data.get('supplier_id')
        if supplier_id:
            valid_supplier = conn.execute(
                "SELECT id FROM suppliers WHERE id=? AND company_id=? AND status='active' AND deleted_at_utc IS NULL",
                (supplier_id, cid)
            ).fetchone()
            if not valid_supplier:
                return jsonify({'status': 'error',
                                 'message': f'Unknown supplier_id: {supplier_id}'}), 400

        # Launch-readiness chain wave C1 (ROADMAP.md's 2026-08-30 "the
        # multi-branch capture defect" entry): same cross-tenant leak shape
        # as supplier_id immediately above -- `data.get('branch_id')` was
        # never validated, so a caller in company A could silently address
        # company B's branch. Resolved through _resolve_working_branch (see
        # its own docstring for the explicit -> pinned -> self-heal order);
        # a NEW PO now takes this device's pinned branch, not just
        # create_sale, when the caller sends no branch_id at all.
        branch_id, branch_err = _resolve_working_branch(conn, cid, data.get('branch_id'))
        if branch_err:
            return branch_err
        # purchase_orders.po_number carries a bare (not company-scoped, not
        # device-scoped) UNIQUE constraint, but _next_ref()'s counter resets
        # per company AND is a per-DEVICE table (`doc_sequences` is never
        # synced -- see _device_doc_discriminator's docstring). Two
        # different companies' first PO would otherwise both generate
        # "PO-000001" and collide in this shared multi-tenant database
        # (fixed first, appending a company fragment); two DEVICES on the
        # SAME company would independently generate the identical
        # "PO-000001-<cid8>" and collide the moment sync relayed both to a
        # third device -- the same AUDIT-032B wedge-sync failure chain
        # create_sale's identical fix closes (see
        # _device_doc_discriminator's docstring). Appending both fragments
        # keeps the sequential part human-readable/searchable while
        # guaranteeing global uniqueness without altering the shared
        # _next_ref helper or its format for other doc types.
        po_number = f"{_next_ref(conn, cid, 'po')}-{str(cid)[:8]}-{_device_doc_discriminator()}"
        total = _money(sum(float(i.get('unit_cost', 0)) * float(i.get('quantity', 0)) for i in items))
        # supplier_id was already resolved and validated above.
        payment_status = 'paid' if amount_paid >= total - 0.005 else ('partial' if amount_paid > 0.005 else 'unpaid')
        cur.execute("""
            INSERT INTO purchase_orders (company_id,po_number,supplier_id,branch_id,status,subtotal,total,notes,ordered_at,
                                         amount_paid,payment_status,due_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (cid, po_number, supplier_id, branch_id,
              'pending', total, total, data.get('notes',''),
              datetime.now().strftime('%Y-%m-%d'), amount_paid, payment_status, data.get('due_date')))
        po_id = cur.lastrowid
        for item in items:
            line = float(item.get('unit_cost',0)) * float(item.get('quantity',0))
            cur.execute("""
                INSERT INTO purchase_order_items (po_id,product_id,quantity,unit_cost,total)
                VALUES (?,?,?,?,?)
            """, (po_id, item['product_id'], item['quantity'], item.get('unit_cost',0), line))
        # AP ledger: record any down-payment now; push the unpaid balance onto supplier AP.
        if amount_paid > 0.005:
            _record_payment(conn, cid, 'supplier', supplier_id, 'out', amount_paid,
                            method=data.get('method', 'cash'), related_type='po', related_id=po_id,
                            doc_type='supplier_payment')
        balance = _money(total - amount_paid)
        if balance > 0.005 and supplier_id:
            _adjust_credit(conn, 'suppliers', supplier_id, cid, balance)
        _audit(conn, 'PO_CREATED', 'purchase_order', po_id, po_number)
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        current_app.logger.warning("create_purchase_order failed on a database constraint: %s", exc)
        return jsonify({
            'status': 'error',
            'message': 'This purchase order could not be created because of a conflicting record. Please try again.',
        }), 409
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        current_app.logger.exception("create_purchase_order failed: %s", exc)
        return jsonify({'status': 'error', 'message': 'Could not create this purchase order.'}), 400
    finally:
        conn.close()
    _sync_nudge()  # best-effort immediate push -- see commercial_runtime/sync/sync_service.py (only queues an event when amount_paid > 0)
    return jsonify({'status': 'success', 'data': {'id': po_id, 'po_number': po_number, 'payment_status': payment_status}})

@retail_bp.route('/purchase-orders/<int:po_id>', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def get_purchase_order(po_id):
    cid = _cid()
    conn = get_retail_conn()
    # Cross-tenant leak fix, 2026-08-29: same join-condition fix (and same
    # reasoning) as list_purchase_orders's identical comment above -- the
    # company condition belongs on the JOIN, not the WHERE, or a
    # supplier-less PO (a legitimate row) would 404 here instead of
    # returning with a blank supplier_name.
    po   = conn.execute("SELECT po.*,s.name as supplier_name FROM purchase_orders po LEFT JOIN suppliers s ON po.supplier_id=s.id AND s.company_id=po.company_id WHERE po.id=? AND po.company_id=?", (po_id, cid)).fetchone()
    if not po:
        conn.close(); return jsonify({'status': 'error', 'message': 'Not found'}), 404
    items = conn.execute("""
        SELECT poi.*, p.name as product_name, p.sku
        FROM purchase_order_items poi LEFT JOIN products p ON poi.product_id=p.id
        WHERE poi.po_id=?
    """, (po_id,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': {'po': dict(po), 'items': [dict(i) for i in items]}})

def _is_device_behind_on_sync():
    """True only when sync is CONFIGURED for this device AND this device is
    behind `_SYNC_STALE_THRESHOLD_SECONDS`. Launch-readiness Phase 7 stage
    7c-i (docs/launch-readiness/phase7-offline-ux.md, "DO block: receiving
    a purchase order"): the ONLY caller today is receive_purchase_order's
    guard below, but this is factored out as its own predicate because the
    two silence rules it enforces are not specific to that one route.

    Reached through the SAME module-level seam sync_health() itself uses
    (`_sync_get_active_health()`, imported at the top of this file) --
    never by reaching into app.py's own `_sync_service` variable, which
    would bypass the exact "configured vs not" distinction this function
    exists to make.

    Both silence rules from stage 7b apply here too, and matter MORE here
    because this one refuses work rather than merely rendering a banner:

      * `configured is False` -> never behind. Most installs never turn
        sync on (SYNC_RELAY_BASE_URL unset) and Android's SyncCoordinator
        owns its own loop -- neither has a second device to double-receive
        with, so there is nothing this guard would protect.
      * `never_synced is True` -> never behind. A device that has not
        completed a first sync has no basis for claiming it has drifted;
        there is nothing to have drifted FROM yet.
    """
    health = _sync_get_active_health()
    if not health.get('configured'):
        return False
    if health.get('never_synced'):
        return False
    secs = health.get('seconds_since_last_success')
    return isinstance(secs, (int, float)) and secs > _SYNC_STALE_THRESHOLD_SECONDS


def _evaluate_offline_sales_stop():
    """Launch-readiness Phase 7 stage 7c-ii (docs/launch-readiness/
    phase7-offline-ux.md "Decision 2"; ROADMAP.md's 2026-08-28 "retail
    schema v19" entry): the 72-hour hard stop on NEW SALES, behind a
    manager override -- the last behavioural piece of Phase 7, and "the
    only change in the whole programme that can refuse a sale" (this
    stage's own design note).

    Returns `(blocked, health)` rather than a bare bool: `create_sale`'s
    refusal message must state BOTH facts Decision 2 requires -- how long
    this device has been behind, and how many events are unsent -- and
    both live in the SAME `get_active_health()` snapshot this function
    already had to read to decide `blocked` in the first place. A second,
    separate call from the route to build the message could observe a
    different instant (a background timer tick landing in between) and
    describe a refusal that no longer matches the one just issued.

    Reached through the SAME module-level seam `_is_device_behind_on_sync`
    above already uses -- never a second sync service reference -- and it
    is a BLOCK, not the PO-receipt route's guard: the 30-minute
    `_SYNC_STALE_THRESHOLD_SECONDS` above governs a different, narrower
    hazard (this device cannot see a receipt already applied elsewhere);
    this one governs `_SYNC_SALES_STOP_THRESHOLD_SECONDS` (72 hours), a
    separate, wider constant on purpose -- see that constant's own comment
    in sync_service.py for why the two must never be collapsed into one.

    An install is blocked if and only if ALL of:
      * sync is CONFIGURED for this device;
      * this device HAS synced before (`never_synced` is False);
      * it has been behind by more than `_SYNC_SALES_STOP_THRESHOLD_SECONDS`;
      * there is NO valid manager override recorded
        (`health['offline_override_valid']`, computed by SyncService's own
        `_offline_override_valid` -- Step 2's "validated by comparison,
        never by expiry" rule).

    The first two are the SAME two silence rules `_is_device_behind_on_sync`
    already enforces, and they matter MOST here of anywhere in this file:
    most installs never turn sync on, and blocking their sales would be
    catastrophic and completely unjustified -- they have no second device
    to be out of step with, so `configured is False` must never block.
    A device that has never completed a first sync has no basis for
    claiming it has drifted, so `never_synced is True` must never block
    either -- collapsing that into "behind by a huge number" would trip
    this stop on a shop's very first day, before it has done anything
    wrong (the exact trap `get_health()`'s own `never_synced` field exists
    to prevent, stage 7a).
    """
    health = _sync_get_active_health()
    if not health.get('configured'):
        return False, health
    if health.get('never_synced'):
        return False, health
    secs = health.get('seconds_since_last_success')
    if not (isinstance(secs, (int, float)) and secs > _SYNC_SALES_STOP_THRESHOLD_SECONDS):
        return False, health
    if health.get('offline_override_valid'):
        return False, health
    return True, health


@retail_bp.route('/purchase-orders/<int:po_id>/receive', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.purchase.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def receive_purchase_order(po_id):
    """Mark PO as received and add stock to inventory.

    Stock-accuracy sweep -- this was a textbook check-then-act race, and
    the single most damaging one in this file. The "already received?"
    test read the PO on a connection holding no write lock, and the final
    `UPDATE purchase_orders SET status='received'` carried no status guard
    of its own. Two requests that both read 'pending' -- a double-clicked
    Receive button, or two devices against the same shared database --
    therefore BOTH walked the item loop and BOTH added the PO's
    quantities. A 10-unit PO landed as 20 units on hand plus two
    'purchase_in' ledger rows, and nothing anywhere reported it: the
    ledger and the balance still agreed with each other, they just both
    agreed on a number twice the size of the delivery.

    Fixed with the pattern create_sale already proves out below rather
    than a new one:

      * BEGIN IMMEDIATE takes SQLite's write lock BEFORE the PO row is
        read, so a second request blocks at the very top and only gets to
        read the status once the first has committed -- by which point it
        is 'received' and the second exits 409 having written nothing.
      * The status UPDATE additionally carries `AND status<>'received'`
        and its rowcount is checked. Belt and braces: even a future caller
        that somehow reaches this code without the lock still cannot
        double-apply, because the row it needs to flip is no longer there
        to flip.

    Launch-readiness Phase 7 stage 7c-i added a SECOND, wider guard in front
    of both of the above: `status='pending'` is read from THIS device's own
    database, and PO status is never synced (multi-device-design.md §8
    keeps it deliberately device-local), so -- the stage's reasoning went --
    the two guards above cannot see a receipt already applied by a
    DIFFERENT device, and two devices whose users both hold
    `retail.stock.adjust` could each receive the same PO once each, with
    both additive `purchase_in` movements surviving the sync merge, so the
    delivery would be double-counted. `_is_device_behind_on_sync()` blocked
    receipt whenever this device was behind, as the one case that hazard
    was thought to be preventable in.

    RETRACTED (2026-08-29): that premise is false, and the code already
    said so. `purchase_orders` does not merely fail to sync its `status`
    column -- the WHOLE TABLE is local-only and never queued to the sync
    outbox at all (see sync_service.py's module docstring: "`purchase_orders`
    ... stays local-only and is never pushed through this outbox at all";
    accept_reorder_request's docstring above says the identical thing for
    every PO created through this same route). A purchase order therefore
    exists on exactly ONE device: the one that created it. There is no
    second device holding a copy of it to double-receive ON, so the
    cross-device hazard this guard blocked was unreachable -- and blocking
    it anyway meant a shop whose device was simply behind on sync for
    something else entirely (a product edit, another sale) could not book
    in a delivery that had physically arrived. The guard has been removed.
    `_is_device_behind_on_sync()` itself is untouched and still guards
    create_sale's stage 7d-iii oversell relaxation, where the hazard IS
    real -- balances, unlike purchase orders, do sync.

    The two guards immediately above -- BEGIN IMMEDIATE plus the
    conditional `status<>'received'` UPDATE -- are the guards that were
    ever real for THIS route, and they are untouched: they still stop the
    one race that is actually reachable, a double-clicked Receive button or
    two requests against the SAME shared database, regardless of this
    device's sync state.
    """
    cid  = _cid()
    conn = get_retail_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()
        po  = cur.execute("SELECT * FROM purchase_orders WHERE id=? AND company_id=?", (po_id, cid)).fetchone()
        if not po:
            conn.rollback()
            return jsonify({'status': 'error', 'message': 'PO not found'}), 404
        if po['status'] == 'received':
            conn.rollback()
            return jsonify({'status': 'error', 'message': 'PO already received'}), 409

        items = cur.execute("SELECT * FROM purchase_order_items WHERE po_id=?", (po_id,)).fetchall()
        # `po['branch_id']` is a trusted value from THIS company's own PO row
        # (fetched WHERE company_id=? above) -- always set for a PO created
        # after this fix (create_purchase_order now resolves one via
        # _resolve_working_branch), so this fallback exists only for
        # legacy/pre-migration rows with a NULL branch_id. Launch-readiness
        # chain wave C1: that fallback is now pin-aware too, same reasoning
        # as every other _default_branch call site in this file.
        bid   = po['branch_id'] or _resolve_working_branch(conn, cid)[0]
        # v13 stamp, resolved once for the whole receipt: every line of one
        # delivery was received by one person at one terminal at one moment,
        # and re-resolving per line would let them claim otherwise.
        actor, terminal, utc_now = _stamp()
        # Resolved once for the whole receipt, same reasoning as `actor,
        # terminal, utc_now` on the line above -- every line of one delivery
        # was received at the SAME branch.
        receipt_branch_uid = _branch_uid(conn, bid)
        for item in items:
            qty = item['quantity']
            cur.execute("""
                INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand)
                VALUES (?,?,?,0)
            """, (cid, item['product_id'], bid))
            cur.execute("""
                UPDATE inventory_balances SET quantity_on_hand = quantity_on_hand + ?
                WHERE company_id=? AND product_id=? AND branch_id=?
            """, (qty, cid, item['product_id'], bid))
            movement_uid = _new_uid()
            cur.execute("""
                INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,unit_cost,reference,created_by,
                                                 uid,actor_user_uid,terminal_id,created_at_utc)
                VALUES (?,?,?,'purchase_in',?,?,?,?,?,?,?,?)
            """, (cid, item['product_id'], bid, qty, item['unit_cost'], po['po_number'], _uid(),
                  movement_uid, actor, terminal, utc_now))
            # Wave B: queue the matching sync event -- see create_sale's own
            # inventory_movement emission for the shape every writer shares.
            _queue_sync_event(cur, 'inventory_movement', movement_uid, 'create', {
                'uid': movement_uid, 'product_id': item['product_id'], 'branch_id': bid,
                'branch_uid': receipt_branch_uid,
                'movement_type': 'purchase_in', 'quantity': qty, 'unit_cost': item['unit_cost'],
                'reference': po['po_number'], 'notes': None, 'created_by': _uid(),
                'actor_user_uid': actor, 'terminal_id': terminal, 'created_at_utc': utc_now,
            })
            cur.execute("UPDATE purchase_order_items SET received_qty=? WHERE id=?", (qty, item['id']))

        cur.execute("UPDATE purchase_orders SET status='received', received_at=? "
                    "WHERE id=? AND company_id=? AND status<>'received'",
                    (datetime.now().strftime('%Y-%m-%d'), po_id, cid))
        if cur.rowcount != 1:
            conn.rollback()
            return jsonify({'status': 'error', 'message': 'PO already received'}), 409
        _audit(conn, 'PO_RECEIVED', 'purchase_order', po_id, po['po_number'])
        conn.commit()
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        current_app.logger.exception("receive_purchase_order(%s) failed: %s", po_id, exc)
        return jsonify({'status': 'error', 'message': 'Could not receive this purchase order.'}), 400
    finally:
        conn.close()
    _emit('StockReceived', {'po_id': po_id, 'po_number': po['po_number']})
    return jsonify({'status': 'success'})

@retail_bp.route('/purchase-orders/split-preview', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.purchase.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
# Gated although it writes nothing. It is a POST that reads supplier terms
# and unit costs to plan an order, so it is part of purchasing, and leaving
# the one non-writing POST in the file ungated would mean the guard test has
# to carry an exemption -- and an exemption is a shape the next new route can
# quietly take.
@mt_require_capability(CAP_STOCK_ADJUST)
def preview_po_split():
    """Preview-only: groups a basket into per-supplier slices (Thursday demo,
    Stream B). Computes and returns a SplitResult -- never writes anything to
    the database (no PO/purchase_order_items rows are created here; that
    lands with the routes that persist split_group_id/routing_status etc.
    later this week -- see core/retail/po_split.py's module docstring).

    Guarded with retail.purchase.create even though this route only reads/
    computes: a restricted-mode install should get the same consistent block
    as the real PO-creation route, rather than a preview of an action it
    could never actually take.

    Every product/supplier this route touches is looked up scoped to
    _cid() -- unlike create_purchase_order above (a real, pre-existing bug:
    AUDIT-follow-up, 2026-08-10 -- it never validates that supplier_id or any
    product_id in the request actually belongs to the caller's company,
    so a cross-tenant PO is creatable today). That bug is OUT OF SCOPE for
    this route to fix; this route just must not repeat it.
    """
    data = request.json or {}
    cid  = _cid()
    items = data.get('items') or []
    if not items:
        return jsonify({'status': 'error', 'message': 'At least one item required'}), 400

    conn = get_retail_conn()
    try:
        product_ids = []
        for it in items:
            pid = it.get('product_id')
            if not pid:
                return jsonify({'status': 'error', 'message': 'Every item requires a product_id'}), 400
            product_ids.append(pid)

        # launch-readiness Phase 6 stage 6b-iii-a: `AND deleted_at_utc IS
        # NULL` added -- this is a WRITE gate (it plans a purchase order a
        # caller may go on to actually create), not a display list, so a
        # tombstoned product must fall out of `products_by_id` below and hit
        # the existing "Unknown product_id" 400 exactly like an id that never
        # existed.
        #
        # `status='active'` joins it for the same two-population reason
        # `create_supplier_contact`'s comment (this file) spells out in
        # full -- a legacy-deleted product (`status='inactive'`,
        # `deleted_at_utc` NULL) needs the `status` half to fall out here
        # too, not just the tombstone half.
        placeholders = ','.join('?' * len(product_ids))
        prod_rows = conn.execute(
            f"SELECT id, name, sku, supplier_id, cost_price FROM products "
            f"WHERE company_id=? AND status='active' AND deleted_at_utc IS NULL AND id IN ({placeholders})",
            [cid, *product_ids]
        ).fetchall()
        products_by_id = {r['id']: dict(r) for r in prod_rows}

        basket = []
        supplier_ids = set()
        for it in items:
            pid = it['product_id']
            product = products_by_id.get(pid)
            if not product:
                # Named explicitly, not a generic error -- the caller needs to
                # know WHICH product_id in its basket doesn't belong to it.
                return jsonify({'status': 'error', 'message': f'Unknown product_id: {pid}'}), 400

            try:
                qty = float(it.get('quantity'))
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': f'Invalid quantity for product_id {pid}'}), 400
            if qty <= 0:
                return jsonify({'status': 'error',
                                 'message': f'Quantity must be greater than zero for product_id {pid}'}), 400

            unit_cost = it.get('unit_cost')
            if unit_cost is None:
                unit_cost = product['cost_price']
            else:
                try:
                    unit_cost = float(unit_cost)
                except (TypeError, ValueError):
                    return jsonify({'status': 'error',
                                     'message': f'Invalid unit_cost for product_id {pid}'}), 400

            supplier_id = product['supplier_id']
            if supplier_id:
                supplier_ids.add(supplier_id)
            basket.append({
                'product_id': pid, 'product_name': product['name'], 'sku': product['sku'],
                'supplier_id': supplier_id, 'quantity': qty, 'unit_cost': unit_cost,
            })

        suppliers_by_id = {}
        contacts_by_supplier = {}
        if supplier_ids:
            sp = ','.join('?' * len(supplier_ids))
            sup_rows = conn.execute(
                f"SELECT id, name, email, phone, min_order_value FROM suppliers "
                f"WHERE company_id=? AND id IN ({sp})",
                [cid, *supplier_ids]
            ).fetchall()
            suppliers_by_id = {r['id']: dict(r) for r in sup_rows}

            contact_rows = conn.execute(
                f"SELECT * FROM supplier_contacts WHERE company_id=? AND supplier_id IN ({sp})",
                [cid, *supplier_ids]
            ).fetchall()
            for r in contact_rows:
                contacts_by_supplier.setdefault(r['supplier_id'], []).append(dict(r))
    finally:
        conn.close()

    result = po_split.group_basket_by_supplier(basket, suppliers=suppliers_by_id, contacts=contacts_by_supplier)
    return jsonify({'status': 'success', 'data': result})

# ── Reorder Requests (feat/reorder-automation-foundation) ───────────────────────
# Foundation only: schema + the post-sale trigger (core/retail/reorder_hook.py,
# called from create_sale below) + this accept/decline surface for the Admin
# Center page (app-shell.js/subsystem-retail.js). The WhatsApp send itself is
# explicitly deferred -- see database/schema.py's
# _migrate_add_reorder_automation_foundation docstring.

@retail_bp.route('/reorder-requests', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def list_reorder_requests():
    """Company-scoped list of PENDING reorder requests, each carrying its
    product/branch context for the Admin Center page. Read-only, so --
    matching list_products/list_purchase_orders above -- this route carries
    no @require_license_capability guard and no is_admin_device check of
    its own; the admin-only gate is client-side (app-shell.js hides the nav
    entry unless GET /api/devices/me reports is_admin_device=true). An
    optional `?branch_id=` filters to one branch; omitted, every branch for
    this company is returned -- an admin reviewing requests across branches
    is the normal case this page exists for."""
    cid = _cid()
    conn = get_retail_conn()
    branch_id = request.args.get('branch_id')
    query = """
        SELECT r.*, p.name AS product_name, p.sku, p.reorder_level, p.supplier_id,
               b.name AS branch_name
        FROM reorder_requests r
        JOIN products p ON p.id = r.product_id
        LEFT JOIN branches b ON b.id = r.branch_id
        WHERE r.company_id=? AND r.status='pending'
    """
    params = [cid]
    if branch_id:
        query += " AND r.branch_id=?"
        params.append(branch_id)
    query += " ORDER BY r.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})


@retail_bp.route('/reorder-requests/<string:rid>/accept', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.reorder.manage", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def accept_reorder_request(rid):
    """Accepts a pending reorder request: creates a purchase_order LOCALLY
    ONLY -- deliberately never queued to sync_outbox. See
    database/schema.py's _migrate_add_reorder_automation_foundation
    docstring for exactly why: purchase_orders.id is still INTEGER
    AUTOINCREMENT, and Owner's relay (owner/app/sync/routes.py) 400-rejects
    the WHOLE push batch on the first non-UUID entity_id it sees -- syncing
    this PO would permanently jam every other entity's sync behind it. Only
    the reorder_requests status change itself is synced below -- that row's
    id IS a real client-generated UUID, so it safely crosses devices; the PO
    it spawns stays this device's own local procurement record, exactly
    like every PO created through the ordinary /purchase-orders route
    today (which was never synced either -- this doesn't regress anything).
    """
    cid = _cid()
    conn = get_retail_conn()
    _ensure_credit_schema(conn)
    try:
        cur = conn.cursor()
        req = conn.execute(
            "SELECT * FROM reorder_requests WHERE id=? AND company_id=?", (rid, cid)
        ).fetchone()
        if not req:
            return jsonify({'status': 'error', 'message': 'Reorder request not found'}), 404
        if req['status'] != 'pending':
            return jsonify({'status': 'error', 'message': 'This request was already resolved.'}), 409

        # launch-readiness Phase 6 stage 6b-iii-a: `AND deleted_at_utc IS
        # NULL` added -- this is the WRITE gate phase6b-deltas-and-tombstones.md
        # names for `list_reorder_requests`'s own INNER JOIN trap: that list
        # route deliberately stays unfiltered so a pending request for a
        # since-deleted product remains visible and declinable, and THIS is
        # where deletion is actually enforced -- a tombstoned product now
        # falls into the existing "Product no longer exists" 404 below,
        # exactly like a hard-deleted id already did, so the request can be
        # declined but never accepted.
        #
        # `status='active'` joins the tombstone check for the same
        # two-population reason `create_supplier_contact`'s comment (this
        # file) spells out in full -- a product deleted before tombstones
        # existed never got a `deleted_at_utc` stamp, so it would otherwise
        # slip past this gate and get accepted onto a new PO.
        product = conn.execute(
            "SELECT id, name, supplier_id, cost_price, reorder_level FROM products "
            "WHERE id=? AND company_id=? AND status='active' AND deleted_at_utc IS NULL",
            (req['product_id'], cid),
        ).fetchone()
        if not product:
            return jsonify({'status': 'error', 'message': 'Product no longer exists.'}), 404

        # Phase-1 foundation heuristic (see core/retail/reorder_hook.py's
        # module docstring): restock exactly back up to reorder_level, never
        # a demand-based forecast -- refining this is explicitly out of
        # scope for this foundation wave. Uses products.supplier_id (schema
        # v5) -- deliberately NOT a new default_supplier_id column, which
        # would just duplicate that existing, already-synced FK.
        qty = max(float(product['reorder_level'] or 0), 1)
        unit_cost = float(product['cost_price'] or 0)
        total = _money(unit_cost * qty)
        supplier_id = product['supplier_id']
        # `req['branch_id']` is a trusted value from THIS company's own
        # reorder_requests row -- the fallback below only applies to a
        # legacy row with no branch_id at all. Launch-readiness chain wave
        # C1: pin-aware, same reasoning as every other _default_branch call
        # site in this file.
        branch_id = req['branch_id'] or _resolve_working_branch(conn, cid)[0]

        # Identical AUDIT-032B treatment as create_purchase_order above (see
        # that route's comment for the full company-vs-company,
        # device-vs-device collision argument, and
        # _device_doc_discriminator's docstring for the wedge-sync failure
        # chain it closes) -- this route drafts a purchase_orders row from
        # the SAME _next_ref(conn, cid, 'po') call, so it carries the
        # identical bare-UNIQUE collision risk on po_number and needs the
        # identical fix. Fixing only the obvious mint site above and
        # missing this one would leave the bug fully live through the
        # reorder-accept path (2026-08-28 ROADMAP entry).
        po_number = f"{_next_ref(conn, cid, 'po')}-{str(cid)[:8]}-{_device_doc_discriminator()}"
        cur.execute("""
            INSERT INTO purchase_orders (company_id,po_number,supplier_id,branch_id,status,subtotal,total,notes,
                                         ordered_at,amount_paid,payment_status)
            VALUES (?,?,?,?,?,?,?,?,?,0,'unpaid')
        """, (cid, po_number, supplier_id, branch_id, 'pending', total, total,
              f'Auto-drafted from reorder request {rid}',
              datetime.now().strftime('%Y-%m-%d')))
        po_id = cur.lastrowid
        cur.execute("""
            INSERT INTO purchase_order_items (po_id,product_id,quantity,unit_cost,total)
            VALUES (?,?,?,?,?)
        """, (po_id, product['id'], qty, unit_cost, total))
        # Deliberately NOT _queue_sync_event(...) for this PO -- see this
        # route's own docstring above.

        now = datetime.now(timezone.utc).isoformat()
        # launch-readiness Phase 6 stage 6a-i: `status`/`resolved_at` are
        # both synced reorder_request fields (sync_service.py's apply
        # branch), so this bumps row_version in the SAME UPDATE, reusing
        # the SAME `now` already computed for `resolved_at` as
        # `updated_at_utc` too -- one instant, not two.
        cur.execute(
            "UPDATE reorder_requests SET status='accepted', resolved_at=?, "
            "row_version=row_version+1, updated_at_utc=? WHERE id=?",
            (now, now, rid),
        )
        row = conn.execute("SELECT row_version FROM reorder_requests WHERE id=?", (rid,)).fetchone()
        # launch-readiness Phase 6 stage 6b-i (changed-field deltas): this
        # route changes exactly `status`/`resolved_at` -- see the SET clause
        # two statements above -- so `_changed_fields` names exactly those
        # two, letting the apply side leave `draft_message` (carried here
        # only because the INSERT half needs a full snapshot) untouched if
        # some other device edited it in the meantime.
        _queue_sync_event(cur, 'reorder_request', rid, 'update', {
            'id': rid, 'branch_id': req['branch_id'], 'product_id': req['product_id'],
            'status': 'accepted', 'draft_message': req['draft_message'], 'resolved_at': now,
            'row_version': row['row_version'], 'updated_at_utc': now,
            '_changed_fields': ['status', 'resolved_at'],
        })
        _audit(conn, 'REORDER_REQUEST_ACCEPTED', 'reorder_request', rid,
               f'PO {po_number} drafted for product {product["name"]}')
        _audit(conn, 'PO_CREATED', 'purchase_order', po_id, po_number)
        conn.commit()
        _sync_nudge()
        return jsonify({'status': 'success', 'data': {'purchase_order_id': po_id, 'po_number': po_number}})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()


@retail_bp.route('/reorder-requests/<string:rid>/decline', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.reorder.manage", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def decline_reorder_request(rid):
    """Declines a pending reorder request -- marks it declined and queues
    the sync event. Creates nothing else; unlike accept, there is no
    purchase_order to (deliberately not) sync here."""
    cid = _cid()
    conn = get_retail_conn()
    try:
        cur = conn.cursor()
        req = conn.execute(
            "SELECT * FROM reorder_requests WHERE id=? AND company_id=?", (rid, cid)
        ).fetchone()
        if not req:
            return jsonify({'status': 'error', 'message': 'Reorder request not found'}), 404
        if req['status'] != 'pending':
            return jsonify({'status': 'error', 'message': 'This request was already resolved.'}), 409

        now = datetime.now(timezone.utc).isoformat()
        # launch-readiness Phase 6 stage 6a-i: same reasoning as
        # accept_reorder_request's identical comment above.
        cur.execute(
            "UPDATE reorder_requests SET status='declined', resolved_at=?, "
            "row_version=row_version+1, updated_at_utc=? WHERE id=?",
            (now, now, rid),
        )
        row = conn.execute("SELECT row_version FROM reorder_requests WHERE id=?", (rid,)).fetchone()
        # launch-readiness Phase 6 stage 6b-i: same reasoning as
        # accept_reorder_request's identical comment above.
        _queue_sync_event(cur, 'reorder_request', rid, 'update', {
            'id': rid, 'branch_id': req['branch_id'], 'product_id': req['product_id'],
            'status': 'declined', 'draft_message': req['draft_message'], 'resolved_at': now,
            'row_version': row['row_version'], 'updated_at_utc': now,
            '_changed_fields': ['status', 'resolved_at'],
        })
        _audit(conn, 'REORDER_REQUEST_DECLINED', 'reorder_request', rid)
        conn.commit()
        _sync_nudge()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()

# ── POS / Sales ───────────────────────────────────────────────────────────────

@retail_bp.route('/sales', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.sale.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_SELL)
def create_sale():
    """Server-authoritative sale creation (Wave 0 correction, AUDIT-002/AUDIT-003).

    The client may send only commercial intent: product_id + quantity per
    line, an optional per-line discount_pct (clamped, never trusted as a
    currency amount), payment_method/amount_paid (tender), customer_id,
    branch_id, and idempotency_key. unit_price, tax_rate, line_total,
    subtotal, discount_amount, tax_amount, and total are IGNORED if a client
    sends them -- they are resolved/computed here from the product table and
    core.retail.pricing, never from the request body. See
    docs/architecture/financial-authority-contracts.md.
    """
    data = request.json or {}
    cid  = _cid()
    conn = get_retail_conn()
    _ensure_credit_schema(conn)
    cur  = conn.cursor()
    try:
        idem = data.get('idempotency_key')
        if idem:
            # company_id-scoped for the same reason create_return's identical
            # lookup below already is (see its comment ~line 2081) -- this
            # one was simply never given the same treatment. Unscoped it is
            # worse here than a read leak: a caller whose idempotency_key
            # collided with ANOTHER company's sale was handed that company's
            # sale id and sale_number back inside a 'success' envelope, AND
            # this company's sale was never written at all -- no sales row,
            # no stock decrement, no payment. A real sale silently vanishing
            # while the client is told it succeeded is the worst possible
            # shape for this bug, so the scoping matters even though the
            # keys are client-generated UUIDs.
            ex = cur.execute("SELECT id,sale_number FROM sales WHERE idempotency_key=? AND company_id=?",
                              (idem, cid)).fetchone()
            if ex:
                conn.close()
                return jsonify({'status': 'success', 'data': {'id': ex['id'], 'sale_number': ex['sale_number']}})

        items_in = data.get('items') or []
        if not items_in:
            conn.close()
            return jsonify({'status': 'error', 'message': 'No items in sale.'}), 400

        # ── retail.discount ─────────────────────────────────────────────────
        # A discount is a FIELD on the sale, not an endpoint, so this is the
        # only place the capability can be enforced. Gating the whole route on
        # retail.discount would refuse the sale itself to a cashier who is
        # allowed to sell; gating nothing would leave retail.discount a code
        # no code path ever reads -- seeded, editable in the UI, and enforcing
        # nothing, which is worse than not having it, because the owner would
        # believe they had switched something off.
        #
        # Judged AFTER clamp_discount_pct, not on the raw field: the clamp
        # already floors a negative at 0 and caps 250 at 100, so a payload
        # carrying discount_pct=-5 or a stray "0" asks for no discount at all
        # and must not be refused as if it did. Checked BEFORE BEGIN
        # IMMEDIATE so a refusal never takes the write lock, and before any
        # row is written so the refusal is total rather than partial.
        wants_discount = any(
            tax_engine.clamp_discount_pct(item.get('discount_pct', 0)) > 0
            for item in items_in if isinstance(item, dict)
        )
        if wants_discount and not session_has_capability(CAP_DISCOUNT):
            conn.close()
            return jsonify({'status': 'error', 'message': DISCOUNT_DENIED_MESSAGE}), 403

        # ── Launch-readiness Phase 7 stage 7c-ii: the 72-hour offline stop ───
        # "It is a block with an override, NOT an absolute refusal." (design
        # note, docs/launch-readiness/phase7-offline-ux.md "Decision 2") --
        # refuses to take money, the most severe thing this application can
        # do to a business, so it is checked here: BEFORE BEGIN IMMEDIATE
        # (same reasoning as the discount check just above -- a refusal must
        # never take the write lock) and before any row of this sale is
        # written, so the refusal is total rather than partial. See
        # _evaluate_offline_sales_stop's own docstring for the full
        # four-condition guard (configured, ever-synced, past the
        # threshold, no valid override) and STEP 6 mutation proofs in
        # products/retail/tests/retail_offline_sales_stop_test.py for why
        # each condition is checked independently.
        blocked, _offline_health = _evaluate_offline_sales_stop()
        if blocked:
            conn.close()
            hours_offline = (_offline_health.get('seconds_since_last_success') or 0) / 3600.0
            pending_count = _offline_health.get('pending_count')
            return jsonify({
                'status': 'error',
                'message': (
                    f'This device has been offline for about {hours_offline:.1f} hours, '
                    f'with {pending_count} event(s) still waiting to sync. New sales are '
                    'blocked until a manager approves selling offline, or this device '
                    'reconnects and syncs.'
                ),
                'data': {'hours_offline': hours_offline, 'pending_count': pending_count},
            }), 403

        # ── v13 attribution stamp ────────────────────────────────────────────
        # Resolved ONCE per sale, then shared by the `sales` row and every
        # `inventory_movements` row this sale writes. They are one event: a
        # sale and the stock it moved happened at the same instant, on the same
        # terminal, under the same cashier, and re-resolving inside the line
        # loop would produce rows that disagree on all three for no reason.
        #
        # Resolved BEFORE `BEGIN IMMEDIATE`, deliberately. `_stamp()` opens the
        # REGISTRY database and reads local_device.json -- a few milliseconds of
        # I/O against files this transaction has nothing to do with. Doing that
        # while holding retail.db's write lock would lengthen every sale's hold
        # on the lock that AUDIT-009 put there to serialise oversell checks, on
        # a product that has already been bitten by lock-holding it did not
        # need (see create_return's `finally: conn.close()` and the leaked-WAL-
        # reader comment on it).
        #
        # The consequence, stated rather than hidden: under lock contention
        # `created_at_utc` marks when this till accepted the sale and
        # `created_at` marks when it won the lock, so they can differ by the
        # wait. Both are legitimate readings of "when", they differ by
        # milliseconds, and neither is off by HOURS -- which is the failure
        # this column exists to prevent and the only one at the scale that
        # matters.
        #
        # `utc_now` is NOT `now_local` below. That one is deliberately local
        # wall clock (there is a comment on it explaining that the dashboard
        # buckets by local date), and reusing it here would file local time in
        # a column whose entire purpose is to be the value two devices in two
        # timezones can compare -- the exact defect v13 added the column to
        # end, reintroduced at the very first write.
        actor, terminal, utc_now = _stamp()

        # BEGIN IMMEDIATE takes the write lock up front so a concurrent sale
        # can't read the same "stock is sufficient" snapshot before either
        # has committed -- the second request blocks here until the first
        # finishes, then re-checks stock against the now-updated balance
        # (AUDIT-009: rapid repeated requests must not oversell).
        conn.execute("BEGIN IMMEDIATE")
        # Stamp the sale in LOCAL time (the column default is UTC; all report queries
        # filter by local date — keeping them consistent avoids late-night sales
        # falling on the wrong day).
        now_local = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # Launch-readiness chain wave C1 (ROADMAP.md's 2026-08-30 "the
        # multi-branch capture defect" entry; docs/launch-readiness/
        # seats-and-chain-design.md §5.2 gap 1) -- THE defect this fix
        # closes. The POS client sends no branch_id at all, so this used to
        # resolve unconditionally to _default_branch(conn, cid) -- the
        # company's FIRST branch -- filing every sale on every till of a
        # chain under store 1. See _resolve_working_branch's own docstring
        # for the explicit -> this device's pinned branch -> self-heal
        # order that replaces it.
        bid, branch_err = _resolve_working_branch(conn, cid, data.get('branch_id'))
        if branch_err:
            conn.rollback(); conn.close()
            return branch_err
        # AUDIT-032C fix (DEFECT 3): `branches.id` is a plain per-device
        # autoincrement -- `branches` is in RETAIL_UID_TABLES for its `uid`
        # column alone, but `branch` is NOT one of Phase 5's five synced
        # entity types (wave B; see sync_service.py's module docstring), so
        # no branch row is ever created on a peer device by sync. A pulled
        # sale that carried only the raw integer `bid` would file itself
        # under WHATEVER branch that number happens to mean on the
        # RECEIVING device -- harmless where every device only ever has one
        # branch (id 1 means the same "the shop" everywhere), silently
        # wrong the instant either side has more than one. Resolved here,
        # once, alongside `bid` itself, and carried on the sync payload
        # below as `branch_uid` so the receiving device can resolve it back
        # to ITS OWN local branches.id (see sync_service.py's
        # `_resolve_branch_id`, which falls back -- visibly, via a logged
        # warning -- to that device's own default branch when it cannot).
        branch_uid_row = conn.execute("SELECT uid FROM branches WHERE id=?", (bid,)).fetchone()
        branch_uid = branch_uid_row['uid'] if branch_uid_row else None
        mode = _settings(conn, cid).get('tax_calculation_mode', tax_engine.DEFAULT_MODE)
        # feat/shift-cash-drawer (schema v10): best-effort stamp of the
        # currently open cash session, if any -- see _open_cash_session_id's
        # own docstring. NULL (an install that never opens a cash session)
        # changes nothing about this sale; never added to response_data
        # below, matching core/retail/reorder_hook.py's "never adds a key"
        # contract exactly (see retail_cash_drawer_regression_test.py).
        cash_session_id = _open_cash_session_id(conn, cid, bid)

        # ── Resolve authoritative line data (server is the sole financial
        # authority -- AUDIT-002/AUDIT-003). unit_price/tax_rate always come
        # from the product row; only quantity and discount_pct are accepted
        # as client-submitted commercial intent, and discount_pct is clamped.
        resolved_lines = []
        # Launch-readiness Phase 7 stage 7d-iii (docs/launch-readiness/
        # phase7-offline-ux.md "Correction to Decision 1"). True once AT
        # LEAST ONE line in this sale was sold past its recorded on-hand
        # figure -- feeds both `response_data['oversold_past_recorded_
        # stock']` below AND the write loop's gate for re-checking a
        # resulting balance against `stock_exceptions` (see that loop's own
        # comment for why the gate is SALE-level, not per-line).
        sale_oversold_past_recorded_stock = False
        subtotal = discount = tax = total = Decimal('0')
        # Resolved ONCE, before the item loop -- reuses the SAME predicate
        # stage 7c-i already built (`_is_device_behind_on_sync`, above),
        # never a second staleness check, so the two silence rules that
        # predicate already enforces (never behind when sync is
        # unconfigured; never behind on a device that has never synced)
        # protect the refusal below too, automatically. Resolved once for
        # the whole sale, not re-read per line, for the identical reason
        # `_evaluate_offline_sales_stop` above is: every line of one sale is
        # judged against the SAME snapshot of "is this device behind".
        behind_on_sync = _is_device_behind_on_sync()
        # Launch-readiness Phase 7 stage 7d-iii follow-up (two-lines-same-
        # product oversell fix, 2026-08-29): `inventory_balances` is NOT
        # decremented until the SEPARATE write loop further down (`for line
        # in resolved_lines:`, below) -- every iteration of the validation
        # loop below reads the exact same, still-untouched on-hand figure.
        # Comparing one line's own `qty` against that shared figure let two
        # lines for the SAME product each pass individually while their SUM
        # exceeded stock. Keyed on (product_id, branch_id) -- the exact key
        # `inventory_balances` itself is scoped by, alongside `company_id`,
        # which is constant for this whole request/loop (one `cid`; `bid`
        # is resolved ONCE above, before this loop -- this route accepts no
        # per-line branch_id, see `bid`'s own comment) -- so two lines for
        # the same product at genuinely different branches are never
        # conflated, and running demand is compared instead of one line in
        # isolation. See the balance check inside the loop below for where
        # this is read and updated.
        demand_by_product_branch = {}

        # Launch-readiness "promotions, wave 1" (schema v23, ROADMAP.md's
        # 2026-08-30 "retail schema v23 CLAIMED" entry). Loaded ONCE for the
        # whole sale, exactly like `behind_on_sync` above and for the same
        # reason -- every line of one sale is judged against the SAME
        # snapshot, and there will be tens of active rules in a real shop,
        # not thousands, so re-querying per line would be pure waste (see
        # core.retail.promotions.load_active_promotions's own docstring).
        # `now_local`, NOT a fresh `datetime.now()` call -- reuses the
        # EXACT value already computed above for this sale's own
        # `created_at`. A till is explicitly built to keep selling while
        # offline, so this device's own clock is the only clock available
        # at the moment a promotion's live-ness actually needs deciding.
        active_promotions = promo_engine.load_active_promotions(conn, cid, bid, now_local)

        for item in items_in:
            pid = item.get('product_id')
            # launch-readiness Phase 6 stage 6b-iii-a (deletion stops
            # overloading `status`): `AND deleted_at_utc IS NULL` added here
            # -- this is THE worst-outcome read path in the whole
            # enumeration. Before this stage `status='active'` alone was
            # enough to keep a tombstoned product off this query, because
            # `delete_product` wrote `status='inactive'` unconditionally
            # alongside the tombstone; that write is gone as of this stage,
            # so a deleted product with no filter here would stay `active`
            # and remain SELLABLE forever. A tombstoned id now falls through
            # to the same "Product {pid} not found." 400 below as an id that
            # never existed at all.
            # `category_id` added to this SELECT for promotions, wave 1
            # (schema v23) -- `resolve_line_discount_pct` below needs it to
            # match a category-scoped promotion against this line; nothing
            # before this stage ever read it here.
            product = cur.execute(
                "SELECT id, name, sell_price, tax_rate, status, category_id FROM products "
                "WHERE id=? AND company_id=? AND deleted_at_utc IS NULL",
                (pid, cid)
            ).fetchone()
            if not product:
                conn.rollback(); conn.close()
                return jsonify({'status': 'error', 'message': f'Product {pid} not found.'}), 400
            if product['status'] != 'active':
                conn.rollback(); conn.close()
                return jsonify({'status': 'error', 'message': f'Product "{product["name"]}" is not available for sale.'}), 400

            try:
                qty = float(item.get('quantity'))
            except (TypeError, ValueError):
                conn.rollback(); conn.close()
                return jsonify({'status': 'error', 'message': 'Invalid quantity.'}), 400
            if qty <= 0:
                conn.rollback(); conn.close()
                return jsonify({'status': 'error', 'message': 'Quantity must be greater than zero.'}), 400

            balance = cur.execute(
                "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
                (cid, pid, bid)
            ).fetchone()
            on_hand = float(balance['quantity_on_hand']) if balance else 0.0
            # Accumulate demand for this (product_id, branch_id) across the
            # whole loop -- see the comment above `demand_by_product_branch`'s
            # initialization for why this key and why now, not `qty` alone.
            demand_key = (pid, bid)
            requested_so_far = demand_by_product_branch.get(demand_key, 0.0) + qty
            demand_by_product_branch[demand_key] = requested_so_far
            if requested_so_far > on_hand:
                # Launch-readiness Phase 7 stage 7d-iii (docs/launch-
                # readiness/phase7-offline-ux.md "Correction to Decision
                # 1"). This refusal is CORRECT and stays exactly as it has
                # always been UNLESS `behind_on_sync` -- an install with
                # sync switched off (the MAJORITY install) keeps the hard
                # refusal forever, because its own balance is the ONLY
                # ledger there is: there is no "another device already sold
                # it" story that could make this figure stale-LOW, so
                # relaxing here would just let the till sell stock it does
                # not have. A device that has never synced is treated
                # identically, for the identical reason -- both silence
                # rules live inside `_is_device_behind_on_sync()` itself,
                # never re-derived here.
                #
                # `requested_so_far` (the running total for this product/
                # branch across this whole sale), not `qty` (one line's own
                # quantity) -- WHAT is compared to `on_hand` is what changed
                # here; WHEN this refuses vs. relaxes is untouched, still
                # gated purely on `behind_on_sync` below.
                if not behind_on_sync:
                    conn.rollback(); conn.close()
                    return jsonify({'status': 'error',
                                     'message': f'Insufficient stock for "{product["name"]}" (have {on_hand}, requested {requested_so_far}).'}), 400
                # This device IS behind: its on-hand figure can be
                # stale-LOW rather than accurate -- another till may have
                # received stock or taken a return this device has not
                # pulled yet -- so refusing a sale the cashier can see with
                # their own eyes is worse than the oversell it might cause
                # (the same "a refused sale is worse than an oversell"
                # principle multi-device-design.md §8 already states for
                # the cross-device merge case; this is the single-device
                # instance of it). The sale is allowed past the recorded
                # figure. Not silent: `sale_oversold_past_recorded_stock`
                # feeds the success response below, and -- if a resulting
                # balance in this sale actually lands negative once its
                # stock movement is written further down -- `stock_
                # exceptions` records it (see the write loop's own comment
                # for why the check happens AFTER the write, and why
                # "negative", not merely "relaxed", gates the record).
                #
                # Set from the SAME accumulated comparison as the refusal
                # just above, not re-derived from `qty` alone -- a relaxed
                # multi-line oversell (each line individually within stock,
                # their SUM past it) must still be flagged, or a resulting
                # negative balance would go unflagged and therefore unrecorded
                # by the write loop's gate below (see
                # products/retail/tests/retail_multiline_stock_test.py's
                # mutation proof 4).
                sale_oversold_past_recorded_stock = True

            # `manual_discount_pct` -- the CLIENT-SUBMITTED, clamped value.
            # This is the SAME value `wants_discount` (above, before BEGIN
            # IMMEDIATE) already judged the CAP_DISCOUNT gate on -- promotion
            # resolution happens AFTER that gate and must never feed back
            # into it. Feeding the PROMOTION-INFLATED `effective_discount_pct`
            # into that gate instead would mean a cashier without
            # CAP_DISCOUNT could no longer sell a PROMOTED item at all -- the
            # shop's own weekend offer would lock its own till. See the
            # RETAIL_SCHEMA_VERSION v23 comment in database/schema.py.
            manual_discount_pct = tax_engine.clamp_discount_pct(item.get('discount_pct', 0))
            # BEST PRICE WINS, never summed -- core.retail.promotions'
            # `resolve_line_discount_pct` returns max(promo_pct,
            # manual_discount_pct), never their sum. `applied_promotion` is
            # None when the manual discount won (including a tie) or no
            # promotion matched at all -- see that function's own docstring.
            effective_discount_pct, applied_promotion = promo_engine.resolve_line_discount_pct(
                active_promotions, pid, product['category_id'], manual_discount_pct
            )
            unit_price = float(product['sell_price'])
            tax_rate = float(product['tax_rate'])
            # `effective_discount_pct`, not the raw manual value -- this is
            # the ONLY place a promotion actually changes what a sale
            # charges, and it goes through the EXISTING calculate_line the
            # same way a manual discount always has. pricing.py is
            # unmodified by this change.
            calc = tax_engine.calculate_line(unit_price, qty, effective_discount_pct, tax_rate, mode=mode)

            resolved_lines.append({
                'product_id': pid, 'quantity': qty, 'unit_price': unit_price,
                'discount_pct': effective_discount_pct, 'tax_rate': tax_rate,
                'line_total': calc['taxable_amount'], 'branch_id': bid,
                # Carried through to the sale_items write loop below so a
                # winning promotion can be snapshotted into
                # sale_item_promotions -- `discount_amount` is calc's OWN
                # output (never re-derived), matching the "snapshot what was
                # actually applied" rule the RETAIL_SCHEMA_VERSION v23
                # comment states.
                'applied_promotion': applied_promotion, 'discount_amount': calc['discount_amount'],
            })
            subtotal += Decimal(str(calc['gross']))
            discount += Decimal(str(calc['discount_amount']))
            tax      += Decimal(str(calc['tax']))
            total    += Decimal(str(calc['total']))

        subtotal = float(subtotal.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        discount = float(discount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        tax      = float(tax.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        total    = float(total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

        # AUDIT-fix: amount_paid was never floored at 0. A negative value
        # (only reachable via a direct API call, never the POS UI, which
        # always sends Math.max(tendered, total)) inflated balance_due
        # beyond total, which could force is_credit=True and record a
        # customer owing MORE than the sale's own total.
        paid      = max(0.0, _money(data.get('amount_paid', total)))
        change    = max(0.0, _money(paid - total))
        pm        = data.get('payment_method', 'cash')
        customer_id = data.get('customer_id')
        balance_due = _money(total - paid)

        # ── Credit-sale rules (Accounts Receivable) ──────────────────────────
        # A credit sale leaves an unpaid balance owed by a NAMED customer. Walk-ins
        # cannot buy on credit; per-customer credit mode/limit is enforced.
        is_credit = (pm == 'credit') or (balance_due > 0.005)
        warning = None
        if is_credit:
            if not customer_id:
                conn.rollback(); conn.close()
                return jsonify({'status': 'error', 'message': 'Credit sales require a customer (walk-in not allowed).'}), 400
            # launch-readiness Phase 6 stage 6b-iii-b: `AND deleted_at_utc IS
            # NULL` added. Read carefully BEFORE this stage's edit: this
            # lookup already REFUSES outright (404 'Customer not found.')
            # when `customer_id` does not resolve -- it does not degrade to
            # a cash sale (create_sale already required a customer_id for
            # any credit sale, checked just above; a customer_id that
            # resolves to nothing is a hard error here, not a fallback path).
            # A tombstoned customer therefore needs no new branch: it simply
            # reads as "not found" and hits this SAME pre-existing 404,
            # exactly like an id that never existed -- a deleted customer
            # cannot be extended credit.
            #
            # `status='active'` joins the tombstone check for the same
            # two-population reason `create_supplier_contact`'s comment
            # (this file) spells out in full -- a customer deleted before
            # tombstones existed never got a `deleted_at_utc` stamp, so the
            # tombstone filter alone would let them straight back onto
            # credit.
            cust = cur.execute(
                "SELECT credit_mode,credit_limit,credit_balance FROM customers "
                "WHERE id=? AND company_id=? AND status='active' AND deleted_at_utc IS NULL",
                (customer_id, cid)).fetchone()
            if not cust:
                conn.rollback(); conn.close()
                return jsonify({'status': 'error', 'message': 'Customer not found.'}), 404
            settings = _settings(conn, cid)
            credit_mode = cust['credit_mode'] or settings['default_credit_mode']
            try:
                limit = float(cust['credit_limit'] if cust['credit_limit'] is not None else (settings['default_credit_limit'] or 0))
            except Exception:
                limit = 0.0
            cur_bal = float(cust['credit_balance'] or 0)
            if credit_mode == 'none':
                conn.rollback(); conn.close()
                return jsonify({'status': 'error', 'message': 'This customer is not allowed to buy on credit.'}), 400
            if credit_mode == 'limited' and (cur_bal + balance_due) > limit + 0.005:
                if settings['enforce_credit_limit'] == 'block':
                    conn.rollback(); conn.close()
                    return jsonify({'status': 'error',
                                    'message': f'Credit limit exceeded. Limit {limit:.2f}, outstanding {cur_bal:.2f}, this sale adds {balance_due:.2f}.'}), 400
                warning = 'Credit limit exceeded.'

        due_date = data.get('due_date')
        # sales.sale_number carries a bare (not company-scoped, not
        # device-scoped) UNIQUE constraint, but _next_ref()'s counter resets
        # per company AND is a per-DEVICE table (`doc_sequences` is never
        # synced -- see _device_doc_discriminator's docstring). Two
        # different companies' first sale would otherwise both generate
        # "SALE-000001" and collide in this shared multi-tenant database
        # (fixed first, appending a company fragment); two DEVICES on the
        # SAME company would independently generate the identical
        # "SALE-000001-<cid8>" and collide the moment sync relayed both to
        # a third device (AUDIT-032B -- see _device_doc_discriminator's
        # docstring for the full wedge-sync failure chain this closes).
        # Appending both fragments keeps the sequential part
        # human-readable/searchable while guaranteeing global uniqueness
        # without altering the shared _next_ref helper or its format for
        # other doc types.
        sale_number = f"{_next_ref(conn, cid, 'sale')}-{str(cid)[:8]}-{_device_doc_discriminator()}"

        # `cashier` still takes data.get('cashier', _uid()) -- a caller-supplied
        # display name where one is given, the local user id otherwise. That is
        # untouched on purpose: it is the only surviving record of who the shop
        # BELIEVED rang this, and v13's whole design principle is that the
        # structured columns sit beside the free text rather than overwrite it.
        # `actor_user_uid` is never derived from it, because a name is not an
        # identity and matching one to the other would be a guess.
        # Phase 5 (money-moving sync): the sale's own wire identity, resolved
        # ONCE here (not inline in the VALUES tuple) because both the INSERT
        # below AND the sync_outbox event right after it need the SAME value
        # -- `entity_id` must equal the row's own `uid`, or the receiving
        # device could never resolve sale_items/payments back to this sale.
        sale_uid = _new_uid()
        cur.execute("""
            INSERT INTO sales (company_id,sale_number,branch_id,customer_id,cashier,
                               subtotal,discount_amount,tax_amount,total,amount_paid,
                               change_amount,payment_method,status,idempotency_key,notes,created_at,due_date,
                               session_id,uid,actor_user_uid,terminal_id,created_at_utc)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'completed',?,?,?,?,?,?,?,?,?)
        """, (cid, sale_number, bid, customer_id, data.get('cashier', _uid()),
              subtotal, discount, tax, total, paid, change,
              pm, idem, data.get('notes',''), now_local, due_date, cash_session_id,
              sale_uid, actor, terminal, utc_now))
        sale_id = cur.lastrowid

        # ── Phase 5 sync emission: the sale itself ───────────────────────────
        # `company_id`, `idempotency_key` and `session_id` are all
        # deliberately ABSENT from this payload:
        #   * `company_id` -- meaningless cross-device (see sync_service.py's
        #     module docstring's "Cross-device company_id bug fix"); the
        #     receiving device stamps its own.
        #   * `idempotency_key` -- carries a BARE (not company-scoped) UNIQUE
        #     constraint on `sales`, and its whole job (letting THIS device
        #     detect a resubmission of a request it already wrote) is already
        #     done by the time this row exists. Carrying it onto a pulled
        #     copy would risk a genuine cross-device collision on that same
        #     bare index for no benefit -- the sync `uid` unique index is the
        #     correct, collision-safe idempotency key for a PULLED row.
        #   * `session_id` -- a local `cash_sessions.id` FK. cash_sessions is
        #     NOT one of Phase 5's five synced entity types (out of scope,
        #     wave B), so no matching row will ever exist on the receiving
        #     device; the apply side must never even try to preserve or
        #     remap it (see sync_service.py's sale branch for the full
        #     reasoning -- this is hazard #2 from the phase brief: a synced
        #     sale must never be re-attributed to any local drawer).
        _queue_sync_event(cur, 'sale', sale_uid, 'create', {
            'uid': sale_uid, 'sale_number': sale_number, 'branch_id': bid, 'branch_uid': branch_uid,
            'customer_id': customer_id, 'cashier': data.get('cashier', _uid()),
            'subtotal': subtotal, 'discount_amount': discount, 'tax_amount': tax, 'total': total,
            'amount_paid': paid, 'change_amount': change, 'payment_method': pm, 'status': 'completed',
            'notes': data.get('notes', ''), 'created_at': now_local, 'due_date': due_date,
            'actor_user_uid': actor, 'terminal_id': terminal, 'created_at_utc': utc_now,
        })

        for line in resolved_lines:
            pid, qty = line['product_id'], line['quantity']
            # A FRESH `_new_uid()` per line, inside the loop. `sale_items` has
            # its own partial UNIQUE index on uid, so hoisting one uid out of
            # the loop would make every multi-line sale fail on its second
            # line -- and, before v13's index existed, would have silently
            # given a peer device several rows it could not tell apart.
            item_uid = _new_uid()
            cur.execute("""
                INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,discount_pct,tax_rate,line_total,uid)
                VALUES (?,?,?,?,?,?,?,?)
            """, (sale_id, pid, qty, line['unit_price'], line['discount_pct'], line['tax_rate'],
                  line['line_total'], item_uid))
            # `cur.lastrowid` captured IMMEDIATELY after the INSERT it
            # describes and before any other `cur.execute()` runs -- same
            # discipline `_default_branch`'s own comment states in full:
            # sqlite3's `Cursor.lastrowid` reflects whichever INSERT the
            # cursor last executed, and `_queue_sync_event` two lines below
            # is itself an INSERT (into sync_outbox) that would overwrite it.
            # Needed for promotions, wave 1 (schema v23): `sale_item_
            # promotions.sale_item_id` references this row's own
            # autoincrement id, which exists nowhere else once this INSERT
            # has run.
            sale_item_id = cur.lastrowid
            # `sale_uid`, not `sale_id` -- the local autoincrement id means
            # nothing on another device. The receiving side resolves this
            # line's parent by looking up `sales.uid = sale_uid` locally (see
            # sync_service.py's sale_item branch); product_id is passed
            # through as-is because products are already a synced entity
            # whose `id` IS its cross-device wire identity (unlike sales).
            _queue_sync_event(cur, 'sale_item', item_uid, 'create', {
                'uid': item_uid, 'sale_uid': sale_uid, 'product_id': pid, 'quantity': qty,
                'unit_price': line['unit_price'], 'discount_pct': line['discount_pct'],
                'tax_rate': line['tax_rate'], 'line_total': line['line_total'],
            })
            # Promotions, wave 1 (schema v23): snapshot the WINNING
            # promotion, if any, onto this line. `applied_promotion` is None
            # whenever the manual discount won (including a tie) or nothing
            # matched -- see core.retail.promotions.resolve_line_discount_pct
            # -- so most sales write nothing here at all, matching the
            # "invisible unless opted in" contract. `discount_amount_
            # snapshot` is `calc['discount_amount']` from the SAME
            # calculate_line call that produced this line's own
            # `line_total`/`discount_pct` above (line['discount_amount']),
            # never re-derived -- a receipt reprinted next year, and a
            # return processed against it, must show what was ACTUALLY
            # charged, not a figure recomputed from whatever the promotion
            # row says today. No sync event: promotions/sale_item_
            # promotions are not one of Phase 5's synced entity types (see
            # database/schema.py's `_migrate_add_promotions` docstring),
            # so there is nothing to queue here.
            applied_promotion = line.get('applied_promotion')
            if applied_promotion:
                cur.execute("""
                    INSERT INTO sale_item_promotions
                        (company_id, sale_item_id, promotion_id, name_snapshot,
                         discount_pct_snapshot, discount_amount_snapshot)
                    VALUES (?,?,?,?,?,?)
                """, (cid, sale_item_id, applied_promotion['id'], applied_promotion['name'],
                      applied_promotion['discount_pct'], line['discount_amount']))
            movement_uid = _new_uid()
            cur.execute("""
                INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,reference,created_by,
                                                 uid,actor_user_uid,terminal_id,created_at_utc)
                VALUES (?,?,?,'sale_out',?,?,?,?,?,?,?)
            """, (cid, pid, bid, -qty, sale_number, _uid(),
                  movement_uid, actor, terminal, utc_now))
            # Wave B (stock-moving sync): the receiving device's own
            # `_apply_event` re-derives `inventory_balances` from this row
            # atomically (see sync_service.py's `inventory_movement` branch),
            # so a sale rung on one device now moves stock on every other one
            # too -- this is the ONE path that ever does. `branch_uid` reuses
            # the SAME value already resolved above for the `sale` event
            # itself (AUDIT-032C) -- one resolution per sale, not one per
            # line. Every one of the six writers into inventory_movements in
            # this product now queues this identical shape; see
            # `_apply_event`'s `inventory_movement` branch for the apply
            # side, and this phase's own module docstring in
            # sync_service.py for the full list of the other five
            # (create_product opening stock, adjust_stock, receive_purchase_
            # order, create_return, import_api.py's bulk stock declaration).
            _queue_sync_event(cur, 'inventory_movement', movement_uid, 'create', {
                'uid': movement_uid, 'product_id': pid, 'branch_id': bid, 'branch_uid': branch_uid,
                'movement_type': 'sale_out', 'quantity': -qty, 'unit_cost': 0,
                'reference': sale_number, 'notes': None, 'created_by': _uid(),
                'actor_user_uid': actor, 'terminal_id': terminal, 'created_at_utc': utc_now,
            })
            cur.execute("""
                UPDATE inventory_balances SET quantity_on_hand = quantity_on_hand - ?
                WHERE company_id=? AND product_id=? AND branch_id=?
            """, (qty, cid, pid, bid))
            # Launch-readiness Phase 7 stage 7d-iii (docs/launch-readiness/
            # phase7-offline-ux.md "Correction to Decision 1"; ROADMAP.md's
            # 2026-08-29 "Correction to the v20 claim" entry). Gated on
            # `sale_oversold_past_recorded_stock` -- the SALE-level flag, not
            # a per-line one -- deliberately: `qty > on_hand` is exactly the
            # condition that triggered the relaxation above, so the line
            # that actually got relaxed can ONLY ever land its own resulting
            # balance strictly negative (on_hand minus a qty that already
            # exceeded it is always < 0) -- checking ITS balance against `< 0`
            # would never observe anything else. What the sign check
            # actually needs to catch is a DIFFERENT line in the SAME sale
            # (a different product, unrelated to the one that got relaxed)
            # whose own write is perfectly ordinary and could legitimately
            # land on exactly zero -- Decision 4's "hitting exactly zero is
            # not an oversell" line must hold for THAT line too, which is
            # why the `< 0` filter below, not the gate, is what decides
            # whether to actually record. A sale with NO relaxed line at all
            # never pays the cost of the extra SELECT: `sale_oversold_past_
            # recorded_stock` stays False for the fully ordinary case,
            # including an ordinary sale rung on a device that merely
            # HAPPENS to be behind (test_a_behind_device_still_sells_
            # normally_when_stock_covers_the_sale).
            #
            # Read back rather than derived from `qty` -- same reasoning as
            # sync_service.py's `inventory_movement` apply branch: the
            # UPDATE above is a delta against whatever was already in the
            # row, so the resulting balance is a fact about the ROW, not
            # about this one line, and a second line of the SAME product
            # earlier in this very loop can already have moved it.
            #
            # `< 0`, not `<= 0` -- landing exactly on zero is not an
            # oversell (Decision 4's own "hitting exactly zero is not an
            # oversell" line), and recording one anyway would mean every
            # behind-sale that merely uses up the last unit gets flagged as
            # a business exception it never was.
            if sale_oversold_past_recorded_stock:
                new_balance = cur.execute(
                    "SELECT quantity_on_hand FROM inventory_balances "
                    "WHERE company_id=? AND product_id=? AND branch_id=?",
                    (cid, pid, bid),
                ).fetchone()[0]
                if new_balance < 0:
                    record_or_refresh_stock_exception(
                        conn, company_id=cid, product_id=pid, branch_id=bid,
                        observed_quantity_on_hand=new_balance,
                    )
                # No `else` branch that clears an exception when the
                # balance is >= 0 -- same posture as sync_service.py's own
                # apply-branch comment: nothing in this stage may
                # auto-resolve or delete a row (Decision 4).

        # Update customer total spent & loyalty points
        #
        # launch-readiness Phase 6 stage 6a-i -- THE TRAP this phase is built
        # around (phase6-catalogue-correctness.md, "Flagged, not fixed" in
        # the design doc). `total_spent`/`loyalty_points` are accumulators,
        # NOT synced fields: sync_service.py's customer apply branch carries
        # only name/phone/email/address/status (see
        # SYNCED_CUSTOMER_FIELDS above), on purpose -- last-write-wins on an
        # accumulator would LOSE points/spend, the same way it would lose
        # stock. This UPDATE therefore MUST NOT bump `row_version` and MUST
        # NOT queue a sync event, deliberately, on every single call --
        # this runs on EVERY sale, so it is the hottest write path any
        # customer row has. If it bumped, ringing sales for a customer on
        # till A would keep advancing that customer's row_version, and a
        # genuine name/phone correction made on till B would then arrive
        # with a LOWER version and be rejected as stale once stage 6a-ii's
        # gate is live -- a shop could become permanently unable to fix a
        # customer's phone number from any device but the one that happens
        # to sell to them least. See
        # products/retail/tests/retail_row_version_bump_test.py's
        # `test_loyalty_accumulator_sale_does_not_bump_row_version_or_emit`
        # for the mutation proof.
        if customer_id:
            pts = int(total / 10)  # 1 point per $10
            cur.execute("""
                UPDATE customers SET total_spent=total_spent+?, loyalty_points=loyalty_points+?
                WHERE id=? AND company_id=?
            """, (total, pts, customer_id, cid))

        # Ledger: record the amount actually RETAINED now (feeds the daily
        # cash summary/drawer close), and push any unpaid balance onto the
        # customer's AR.
        #
        # Real bug fixed here: `paid` is the gross amount tendered (e.g. $50
        # handed over for a $42 sale) -- `change` (computed above) is the
        # portion handed straight back out and never stays in the drawer.
        # This ledger entry used to record the full `paid`, so
        # cash_session_summary()'s cash_sales total (SUM of this same
        # ledger, filtered to method='cash') silently counted change given
        # back as money still in the drawer -- every cash sale with change
        # inflated the expected-cash figure by exactly that change amount,
        # showing a phantom shortage at close. net_received = min(paid,
        # total) is the correct amount actually retained: unchanged from
        # `paid` for a partial/credit sale (paid <= total, nothing given
        # back), reduced to `total` whenever change was given. The `sales`
        # row itself still stores the full amount_paid/change_amount split
        # untouched -- receipts keep showing the real tendered/change
        # breakdown; only this ledger entry (drawer math) changes.
        net_received = min(paid, total)
        if net_received > 0.005:
            _record_payment(conn, cid, ('customer' if customer_id else None), customer_id, 'in', net_received,
                            method=(pm if pm != 'credit' else 'cash'),
                            related_type='sale', related_id=sale_id, doc_type='receipt')
        if is_credit and balance_due > 0.005 and customer_id:
            _adjust_credit(conn, 'customers', customer_id, cid, balance_due)

        conn.commit()
        _sync_nudge()  # best-effort immediate push -- see commercial_runtime/sync/sync_service.py
        _emit('SaleCompleted', {'sale_id': sale_id, 'sale_number': sale_number, 'total': total,
                                 'payment_method': pm})

        response_data = {
            'id': sale_id, 'sale_number': sale_number,
            'idempotency_key': idem, 'currency': _settings(conn, cid)['base_currency'],
            'subtotal': subtotal, 'discount_amount': discount, 'tax_amount': tax,
            'change': round(change, 2), 'total': round(total, 2),
            'amount_paid': paid, 'balance_due': balance_due, 'warning': warning,
            'lines': resolved_lines, 'calculation_version': tax_engine.CALCULATION_VERSION,
            # Launch-readiness Phase 7 stage 7d-iii: NEVER silent. True only
            # when at least one line was allowed past its recorded on-hand
            # figure because this device is behind on sync (see the item
            # loop's own comment above) -- always present, not a
            # conditionally-added key like 'einvoice' below, so a future UI
            # stage can read this field unconditionally with no API-shape
            # change of its own. Whether the sale actually landed a balance
            # negative (and therefore whether `stock_exceptions` gained or
            # refreshed a row) is a SEPARATE fact this flag does not carry
            # -- a behind-sale that used exactly the last unit relaxes this
            # refusal without ever going negative, and Decision 4 is
            # explicit that landing on zero is not an oversell.
            'oversold_past_recorded_stock': sale_oversold_past_recorded_stock,
        }

        # docs/einvoicing/phase1/ -- best-effort, never blocks or fails the
        # sale that already committed above. Opened on its OWN connection
        # (see einvoice_adapter.enqueue_sale) so a failure here cannot roll
        # back or otherwise touch the sale transaction. Only adds an
        # 'einvoice' key to the response when the feature is actually
        # enqueued -- a disabled install's response is byte-for-byte
        # unchanged (see retail_einvoicing_regression_test.py).
        try:
            from database.schema import get_retail_conn as _get_retail_conn_for_einvoicing
            from core.retail.einvoice_adapter import enqueue_sale as _enqueue_einvoice_sale
            invoice_ref = f'AURA_RETAIL:sale:{sale_id}'
            if _enqueue_einvoice_sale(_get_retail_conn_for_einvoicing, company_id=cid,
                                       sale_id=sale_id, sale_number=sale_number):
                response_data['einvoice'] = {'invoice_ref': invoice_ref, 'status': 'queued'}
        except Exception as e:
            import logging
            logging.getLogger('aura.retail').warning('e-invoice enqueue skipped: %s', type(e).__name__)

        # feat/reorder-automation-foundation -- same "never touch the sale"
        # contract as the e-invoicing block just above: its own connection
        # (core/retail/reorder_hook.maybe_trigger_reorder), broad try/except,
        # log-only on failure. Unlike e-invoicing, this NEVER adds a key to
        # response_data -- reorder automation is invisible to the checkout
        # API's contract by design (see
        # retail_reorder_hook_regression_test.py, which asserts the response
        # is byte-for-byte identical whether this hook succeeds, no-ops, or
        # raises). Checks every DISTINCT product sold, not just one line --
        # a multi-item sale can drop several products below their own
        # reorder_level at once.
        try:
            from database.schema import get_retail_conn as _get_retail_conn_for_reorder
            from core.retail.reorder_hook import maybe_trigger_reorder as _maybe_trigger_reorder
            distinct_product_ids = list({line['product_id'] for line in resolved_lines})
            _maybe_trigger_reorder(_get_retail_conn_for_reorder, company_id=cid, branch_id=bid,
                                    product_ids=distinct_product_ids)
        except Exception as e:
            import logging
            logging.getLogger('aura.retail').warning('reorder hook skipped: %s', type(e).__name__)

        return jsonify({'status': 'success', 'data': response_data})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()

# ── Promotions (schema v23, launch-readiness "promotions, wave 1") ─────────
#
# Configuring a promotion (list/create/update/deactivate) is CAP_DISCOUNT:
# deciding to give value away at scale is exactly that authority. The one
# read a TILL needs (`/promotions/active`) is CAP_SELL instead -- a cashier
# has to be able to load it to ring a sale, and it is licensing-ungated for
# the same reason list_products/list_categories above are: a plain read the
# till needs to function, not a mutation. See the RETAIL_SCHEMA_VERSION v23
# comment in database/schema.py for the full design.

def _promotion_target_error(conn, cid, fields):
    """Shared tenancy validation for create_promotion/update_promotion: a
    non-empty product_id/category_id/branch_id must belong to THIS company,
    or return the error message to send back. Returns None when everything
    supplied checks out (including when nothing needing a check was
    supplied at all -- a PATCH that never touches these fields must not be
    forced to re-validate a value it did not send).

    AUDIT-032C's supplier_id lesson, repeated verbatim: an unvalidated FK
    here is the cross-tenant leak this codebase has already shipped once
    (create_purchase_order's supplier_id, before `6b79b5a`) -- a caller
    could otherwise file a promotion against ANOTHER company's product,
    category or branch. No literal FOREIGN KEY enforces this (see
    _migrate_add_promotions's own docstring for why); this company-scoped
    SELECT is the entire guard.
    """
    if fields.get('product_id'):
        row = conn.execute(
            "SELECT id FROM products WHERE id=? AND company_id=? AND deleted_at_utc IS NULL",
            (fields['product_id'], cid)
        ).fetchone()
        if not row:
            return f'Unknown product_id: {fields["product_id"]}'
    if fields.get('category_id'):
        row = conn.execute(
            "SELECT id FROM categories WHERE id=? AND company_id=? AND deleted_at_utc IS NULL",
            (fields['category_id'], cid)
        ).fetchone()
        if not row:
            return f'Unknown category_id: {fields["category_id"]}'
    if fields.get('branch_id'):
        # `branches` carries no `deleted_at_utc` of its own (see
        # `_default_branch` above, which checks none either) -- matched here
        # rather than inventing a check this table has never had.
        row = conn.execute(
            "SELECT id FROM branches WHERE id=? AND company_id=?",
            (fields['branch_id'], cid)
        ).fetchone()
        if not row:
            return f'Unknown branch_id: {fields["branch_id"]}'
    return None


@retail_bp.route('/promotions', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_DISCOUNT)
def list_promotions():
    """Every promotion this company has ever configured (active AND
    deactivated) -- the management screen's own list, not the till's. See
    `list_active_promotions` (/promotions/active) for the till-facing,
    currently-live-only read."""
    cid = _cid()
    conn = get_retail_conn()
    rows = conn.execute(
        "SELECT id, name, discount_pct, product_id, category_id, branch_id, "
        "starts_at, ends_at, status, created_at FROM promotions "
        "WHERE company_id=? ORDER BY created_at DESC", (cid,)
    ).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})


@retail_bp.route('/promotions', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.promotion.manage", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_DISCOUNT)
def create_promotion():
    data = request.json or {}
    cid = _cid()

    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'status': 'error', 'message': 'Promotion name required.'}), 400

    product_id = data.get('product_id')
    category_id = data.get('category_id')
    # EXACTLY one of product_id / category_id -- both present or both absent
    # are the same failure (`bool(x) == bool(y)` catches both: True==True
    # and False==False), matching database/schema.py's promotions table
    # comment ("product_id, category_id -- exactly one of the two").
    if bool(product_id) == bool(category_id):
        return jsonify({'status': 'error',
                         'message': 'A promotion must target exactly one of product_id or category_id.'}), 400

    try:
        discount_pct = float(data.get('discount_pct'))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Invalid discount_pct.'}), 400
    # (0, 100] -- a promotion of exactly 0% gives nothing away and is not a
    # promotion; MAX_DISCOUNT_PCT=100 is core.retail.pricing's own ceiling
    # (clamp_discount_pct), matched here rather than re-derived.
    if not (0 < discount_pct <= 100):
        return jsonify({'status': 'error',
                         'message': 'discount_pct must be greater than 0 and at most 100.'}), 400

    branch_id = data.get('branch_id')
    starts_at = data.get('starts_at')
    ends_at = data.get('ends_at')
    if starts_at and ends_at and ends_at < starts_at:
        return jsonify({'status': 'error', 'message': 'ends_at cannot be before starts_at.'}), 400

    conn = get_retail_conn()
    target_error = _promotion_target_error(conn, cid, {
        'product_id': product_id, 'category_id': category_id, 'branch_id': branch_id,
    })
    if target_error:
        conn.close()
        return jsonify({'status': 'error', 'message': target_error}), 400

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO promotions
            (company_id, name, discount_pct, product_id, category_id, branch_id,
             starts_at, ends_at, status)
        VALUES (?,?,?,?,?,?,?,?, 'active')
    """, (cid, name, discount_pct, product_id, category_id, branch_id, starts_at, ends_at))
    new_id = cur.lastrowid
    conn.commit(); conn.close()
    return jsonify({'status': 'success', 'data': {'id': new_id}})


@retail_bp.route('/promotions/<int:promotion_id>', methods=['PATCH'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.promotion.manage", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_DISCOUNT)
def update_promotion(promotion_id):
    data = request.json or {}
    cid = _cid()
    conn = get_retail_conn()
    existing = conn.execute(
        "SELECT * FROM promotions WHERE id=? AND company_id=?", (promotion_id, cid)
    ).fetchone()
    if not existing:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Promotion not found.'}), 404

    allowed = ['name', 'discount_pct', 'product_id', 'category_id', 'branch_id',
               'starts_at', 'ends_at', 'status']
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        conn.close()
        return jsonify({'status': 'error', 'message': 'No valid fields.'}), 400

    if 'name' in fields:
        fields['name'] = (fields['name'] or '').strip()
        if not fields['name']:
            conn.close()
            return jsonify({'status': 'error', 'message': 'Promotion name required.'}), 400

    if 'discount_pct' in fields:
        try:
            fields['discount_pct'] = float(fields['discount_pct'])
        except (TypeError, ValueError):
            conn.close()
            return jsonify({'status': 'error', 'message': 'Invalid discount_pct.'}), 400
        if not (0 < fields['discount_pct'] <= 100):
            conn.close()
            return jsonify({'status': 'error',
                             'message': 'discount_pct must be greater than 0 and at most 100.'}), 400

    # Validated against the RESULTING row (existing merged with this PATCH's
    # fields), not the PATCHed fields in isolation -- a PATCH that only
    # changes discount_pct must still be checked against this promotion's
    # EXISTING product_id/category_id exclusivity, and a PATCH that only
    # changes product_id must be checked against the EXISTING category_id it
    # did not touch. Matching update_product's own "read the row after
    # write" discipline, applied here before the write instead.
    merged = dict(existing)
    merged.update(fields)
    if bool(merged.get('product_id')) == bool(merged.get('category_id')):
        conn.close()
        return jsonify({'status': 'error',
                         'message': 'A promotion must target exactly one of product_id or category_id.'}), 400
    starts_at = merged.get('starts_at')
    ends_at = merged.get('ends_at')
    if starts_at and ends_at and ends_at < starts_at:
        conn.close()
        return jsonify({'status': 'error', 'message': 'ends_at cannot be before starts_at.'}), 400

    target_error = _promotion_target_error(conn, cid, fields)
    if target_error:
        conn.close()
        return jsonify({'status': 'error', 'message': target_error}), 400

    cur = conn.cursor()
    sets = ', '.join(f'{k}=?' for k in fields)
    cur.execute(
        f"UPDATE promotions SET {sets} WHERE id=? AND company_id=?",
        list(fields.values()) + [promotion_id, cid]
    )
    conn.commit(); conn.close()
    return jsonify({'status': 'success'})


@retail_bp.route('/promotions/<int:promotion_id>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.promotion.manage", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_DISCOUNT)
def delete_promotion(promotion_id):
    """Deactivate, not delete -- a status flip (active -> inactive), same
    shape as delete_category's tombstone above rather than a hard DELETE.
    `sale_item_promotions` rows already snapshot everything a past sale
    needs (see database/schema.py's RETAIL_SCHEMA_VERSION v23 comment), so a
    hard delete here would not even corrupt history the way it would on a
    table without a snapshot -- this is a design preference (an owner can
    see and reactivate a past promotion) rather than a correctness
    requirement."""
    cid = _cid()
    conn = get_retail_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE promotions SET status='inactive' WHERE id=? AND company_id=? AND status='active'",
        (promotion_id, cid)
    )
    if cur.rowcount == 0:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Promotion not found.'}), 404
    conn.commit(); conn.close()
    return jsonify({'status': 'success'})


@retail_bp.route('/promotions/active', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_SELL)
def list_active_promotions():
    """The till's own read -- CAP_SELL, not CAP_DISCOUNT, and no
    @require_license_capability (matches list_products/list_categories
    above: a plain read the till needs to function, not a mutation).

    BRANCH SCOPE -- settled, do not re-decide (ROADMAP.md's 2026-08-30
    "retail schema v23 CLAIMED" entry). The shipped frontend
    (products/retail/frontend/subsystem-retail.js, commit 3096bc4) calls
    this route with NO branch_id, because the POS has no client-side
    "current branch" concept and the frontend agent correctly declined to
    invent one. So `branch_id` is an OPTIONAL query parameter: when absent,
    the branch is resolved SERVER-SIDE via the SAME `_default_branch` helper
    create_sale itself uses. Which branch a till belongs to is not a
    client-supplied fact, and a client that could name any branch could ask
    for another branch's pricing -- so a SUPPLIED branch_id is validated
    against this company before use, exactly like create_promotion/
    update_promotion validate one on write.
    """
    cid = _cid()
    conn = get_retail_conn()
    raw_branch_id = request.args.get('branch_id')
    if raw_branch_id:
        row = conn.execute(
            "SELECT id FROM branches WHERE id=? AND company_id=?", (raw_branch_id, cid)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({'status': 'error', 'message': f'Unknown branch_id: {raw_branch_id}'}), 400
        bid = int(raw_branch_id)
    else:
        bid = _default_branch(conn, cid)
    # Local wall clock, matching create_sale's own `now_local` -- a till is
    # explicitly built to keep selling (and, here, keep PREVIEWING prices)
    # while offline, so this device's own clock is the only one available.
    now_local = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rows = promo_engine.load_active_promotions(conn, cid, bid, now_local)
    conn.close()
    return jsonify({'status': 'success', 'data': rows})


@retail_bp.route('/sales/recent', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def recent_sales():
    """Recent sales list. Also powers the Sales History screen's "browse all
    sales" view (frontend/subsystem-retail.js _renderSalesHistory) -- a
    dedicated invoices screen was requested (see friend hands-on-testing
    feedback logged in the sales-invoices-screen branch) but the existing
    /sales/<id> detail route already had everything needed; the only gap
    was a way to FIND a past sale beyond the last `limit` rows. Extended
    here, backward-compatibly (q/date_from/date_to all optional, default
    behavior unchanged), with:
      - q: LIKE search on sale_number OR customer name, same convention as
        list_customers() above.
      - date_from/date_to: inclusive `created_at` range, both ends still
        inclusive of the given calendar date -- see the conditions block
        below for why this is now a half-open range on the bare column
        rather than `date(created_at)` (schema v21, "the POS scale fix";
        the /reports/* endpoints below still use core/retail/metrics.py's
        own date(...) convention, deliberately left as-is -- see that
        module's own docstring for why it cannot be fixed the same way).
    No offset/page param was added -- this codebase's other list endpoints
    (products, customers, suppliers) all use the same "big LIMIT, no
    pagination" convention rather than true offset pagination, so q/date
    filtering (which scales to a company's full sales history without an
    unbounded row fetch) matches existing precedent better than introducing
    a new pagination pattern this file doesn't otherwise have.

    ── A TILL MAY LOOK A SALE UP; THE SALES BOOK IS A REPORT ────────────────
    This route used to serve every caller the same thing, and what it serves
    is `SELECT s.*` over `sales`: total, amount_paid, and (since v13)
    `actor_user_uid`. A cashier holds retail.sell / retail.refund /
    retail.cash.close and NOT retail.reports, so the product was answering

        GET /dashboard/stats  -> 403
        GET /reports/summary  -> 403
        GET /sales/recent?limit=100000&date_from=...&date_to=...
                              -> 200, the whole ledger those two are computed FROM

    Refusing the aggregate while serving the rows behind it is not a policy,
    it is an oversight -- anyone refused the summary could sum the book.

    The gate is NOT `@mt_require_capability(CAP_REPORTS)` on the route,
    because the returns counter depends on this exact route: frontend
    `_findSaleForReturn` fetches `?limit=200` to resolve a receipt number
    before a refund, and retail.refund is a CASHIER DEFAULT. Gating the whole
    route would refuse a cashier the lookup for a refund the product grants
    them -- a real regression in exchange for a real fix.

    So the split follows the one already made for `customer_statement` ("one
    customer's balance is a till fact, the list of everyone who owes the shop
    money is a report"). Without retail.reports:

      * `date_from` / `date_to` are REFUSED. An arbitrary historical range is
        the only way to reach sales older than the current page, and that is
        precisely what makes this the ledger rather than a lookup. Each bound
        is refused ON ITS OWN -- `date_from` alone reaches the whole book
        forwards, `date_to` alone reaches all of it backwards.
      * `limit` is capped at the till's own page size, so one request can no
        longer pull the entire history.
      * the identity columns are dropped (see SALES_ROW_IDENTITY_COLUMNS).

    `q` stays open at any capability: a receipt number is a single-sale
    question, which is the till fact this route exists to answer.

    A caller WITH retail.reports is unchanged in every respect -- the Sales
    History screen's date filters and the desktop's reporting still work.
    """
    cid   = _cid()
    # Clamped BOTH ends before anything else reads it -- see clamp_page_limit's
    # docstring for why `min(limit, N)` was not a ceiling and `?limit=-1`
    # returned the whole book to a cashier.
    limit = clamp_page_limit(request.args.get('limit', 50), 50, SALES_HISTORY_MAX_LIMIT)
    q         = request.args.get('q', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to   = request.args.get('date_to', '').strip()

    # Read ONCE: two calls could straddle a permission change mid-request and
    # decide the range and the redaction on different answers.
    may_read_the_book = session_has_capability(CAP_REPORTS)
    if not may_read_the_book:
        if date_from or date_to:
            return jsonify({'status': 'error', 'error': CAPABILITY_DENIED_MESSAGE,
                            'message': CAPABILITY_DENIED_MESSAGE, 'code': 403}), 403
        limit = min(limit, TILL_SALES_LOOKUP_MAX_LIMIT)

    conditions = ["s.company_id=?"]
    params = [cid]
    if q:
        conditions.append("(s.sale_number LIKE ? OR c.name LIKE ?)")
        params.extend([f'%{q}%', f'%{q}%'])
    # launch-readiness "the POS scale fix" (ROADMAP.md's 2026-08-29 v21
    # entry): rewritten from `date(s.created_at) >= date(?)` /
    # `date(s.created_at) <= date(?)` to a half-open range so the predicate
    # is SARGABLE -- wrapping the COLUMN in `date(...)` (the old form) means
    # SQLite must evaluate that function on every row before it can compare,
    # which no index can serve. Wrapping the PARAMETER instead (`date(?)` /
    # `date(?, '+1 day')` below) costs nothing per row -- SQLite computes
    # each bound once, then compares the bare column against it -- and is
    # deliberately NOT re-parsed in Python: `date()`'s own tolerance for
    # every legal SQLite date/datetime spelling is preserved exactly rather
    # than re-implemented, so a value the old form accepted is still
    # accepted and a value it rejected (date() returns NULL, so the
    # comparison is false and matches nothing) still is.
    #
    # SAME inclusive-both-ends semantics as before, restated as a half-open
    # range: start <= business day of the row <= end becomes
    # `created_at >= date(start) AND created_at < date(end, '+1 day')` --
    # the exclusive upper bound is midnight of the day AFTER `date_to`, so
    # every timestamp anywhere inside `date_to`'s calendar day still
    # matches, exactly as `date(s.created_at) <= date(date_to)` did.
    #
    # NOTE (see this change's own report): no index currently exists on
    # `sales.created_at` -- schema v13's `idx_sales_created_at_utc` covers
    # the DIFFERENT `created_at_utc` column, not this one -- so this rewrite
    # is sargable but not yet index-backed; EXPLAIN QUERY PLAN still shows
    # `SCAN s` today. It is still worth doing: it is no longer the reason an
    # index can't help, and a future `idx_sales_created_at` would apply to
    # this predicate with no further query change.
    if date_from:
        conditions.append("s.created_at >= date(?)")
        params.append(date_from)
    if date_to:
        conditions.append("s.created_at < date(?, '+1 day')")
        params.append(date_to)
    params.append(limit)

    conn  = get_retail_conn()
    rows  = conn.execute(f"""
        SELECT s.*, COALESCE(c.name,'Walk-in') as customer_name,
               COUNT(si.id) as item_count
        FROM sales s
        LEFT JOIN customers c ON s.customer_id=c.id
        LEFT JOIN sale_items si ON s.id=si.sale_id
        WHERE {' AND '.join(conditions)}
        GROUP BY s.id ORDER BY s.created_at DESC LIMIT ?
    """, params).fetchall()
    conn.close()
    payload = [dict(r) for r in rows]
    if not may_read_the_book:
        # Dropped AFTER the query rather than by narrowing the SELECT list:
        # `s.*` is what keeps this route working as `sales` gains columns, and
        # an explicit column list here would have to be revisited by every
        # future migration. The redaction is the thing that must be explicit.
        for row in payload:
            for column in SALES_ROW_IDENTITY_COLUMNS:
                row.pop(column, None)
    return jsonify({'status': 'success', 'data': payload})

@retail_bp.route('/sales/<int:sale_id>', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def get_sale(sale_id):
    """One sale, its lines, and WHO RANG IT.

    The customer side of this payload has always been resolved (`COALESCE(
    c.name,'Walk-in')`); the employee side never was. `s.*` carries
    `actor_user_uid`, which is a registry `users.uid` -- another database --
    so the Cashier cell on the desktop sale detail and Android's
    `actor_employee_id`/`actor_email` were reading three fields no backend
    code produced, and both rendered a truncated uuid4 instead of a person.

    Resolved through the SAME helper the by-employee report uses, so the two
    surfaces can never name the same uid differently.
    """
    cid  = _cid()
    conn = get_retail_conn()
    sale = conn.execute("""
        SELECT s.*, COALESCE(c.name,'Walk-in') as customer_name
        FROM sales s LEFT JOIN customers c ON s.customer_id=c.id
        WHERE s.id=? AND s.company_id=?
    """, (sale_id, cid)).fetchone()
    if not sale:
        conn.close(); return jsonify({'status': 'error', 'message': 'Not found'}), 404
    items = conn.execute("""
        SELECT si.*, p.name as product_name, p.sku
        FROM sale_items si LEFT JOIN products p ON si.product_id=p.id
        WHERE si.sale_id=?
    """, (sale_id,)).fetchall()
    conn.close()

    payload = dict(sale)
    actor_uid = payload.get('actor_user_uid')
    identity = _resolve_actor_identities(cid, [actor_uid]).get(actor_uid, _UNRESOLVED_IDENTITY)
    # Android reads the two raw columns (it distinguishes "account deleted
    # since" from "never recorded" by uid-present/names-absent); the desktop
    # reads the one display string. Both come from the same lookup, and all
    # three are JSON null together when the uid resolved to nobody -- never
    # '', never the free-text `cashier` column sitting right beside them.
    payload['actor_employee_id'] = identity['employee_id']
    payload['actor_email'] = identity['email']
    payload['employee_name'] = identity['employee_name']
    return jsonify({'status': 'success', 'data': {'sale': payload, 'items': [dict(i) for i in items]}})

# ── Held Sales (park / resume) ─────────────────────────────────────────────────
# "I had two transactions -- one was mid-payment, I forgot something, wanted
# to pause it, ring up the second customer, then come back and finish the
# first" -- real hands-on-testing feedback; the POS cart previously lived
# ONLY in RetailSystem._cart (frontend/subsystem-retail.js), reset to []
# every time _renderPOS() runs, so navigating away (or nothing at all --
# see _renderPOS's unconditional reset) silently dropped it.
#
# A held sale is PRE-completion cart state, not a commercial transaction --
# deliberately its own table (held_sales, schema.py v9->v11 -- see
# _migrate_add_held_sales for why this skips v10, claimed by the unmerged
# feat/shift-cash-drawer branch), never a row in sales/sale_items, and this
# section never calls into create_sale or touches
# its financial-record guarantees. subtotal/total stored here are
# DISPLAY-ONLY (rendered in the resume picker); POST /sales (unchanged)
# always recomputes the real, authoritative figures from live product data
# when a resumed cart is actually checked out, exactly as it would for any
# freshly-built cart -- AUDIT-002/AUDIT-003's server-authority guarantee is
# untouched by this feature.
#
# Local-only / not part of commercial_runtime/sync/'s outbox -- explicit
# design decision, not an oversight: a mid-edit cart on one till has no
# reason to appear on another device, and syncing it would require solving
# conflict resolution (two devices resuming/editing the same held sale) that
# this feature doesn't need to take on. If multi-device hold/resume is ever
# wanted, treat it as a new, separate design pass, not a bolt-on here.
#
# All three mutation routes below share POST /sales's own capability gate
# (retail.sale.create) -- holding, resuming, and discarding a held sale are
# all part of the same "can this install work a new sale at all" workflow,
# not separate capabilities. The GET (list) route has no capability gate,
# matching every other read-only list_* route in this file (always allowed,
# restricted-mode or not).

@retail_bp.route('/held-sales', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def list_held_sales():
    cid = _cid()
    conn = get_retail_conn()
    _ensure_credit_schema(conn)
    rows = conn.execute("""
        SELECT hs.id, hs.hold_number, hs.label, hs.item_count, hs.subtotal, hs.total,
               hs.customer_id, COALESCE(c.name,'Walk-in') as customer_name,
               hs.held_by, hs.created_at
        FROM held_sales hs LEFT JOIN customers c ON hs.customer_id=c.id AND c.company_id=hs.company_id
        WHERE hs.company_id=? ORDER BY hs.created_at DESC
    """, (cid,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@retail_bp.route('/held-sales', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.sale.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_SELL)
def hold_sale():
    """Snapshot the current in-progress cart; the frontend clears its own
    `_cart` immediately after a successful hold so the till is free for the
    next customer (mirrors how _checkout() already clears the cart on a
    successful sale)."""
    data = request.json or {}
    items = data.get('items') or []
    if not items:
        return jsonify({'status': 'error', 'message': 'Cannot hold an empty cart.'}), 400

    cid = _cid()
    conn = get_retail_conn()
    _ensure_credit_schema(conn)
    cur = conn.cursor()
    try:
        # Launch-readiness chain wave C1: pin-aware + validated, same order
        # as create_sale (this is part of the same sales flow -- a held
        # cart is a paused sale).
        bid, branch_err = _resolve_working_branch(conn, cid, data.get('branch_id'))
        if branch_err:
            return branch_err
        customer_id = data.get('customer_id') or None
        label = (data.get('label') or '').strip()[:200]
        discount_pct = tax_engine.clamp_discount_pct(data.get('discount_pct', 0))
        payment_method = data.get('payment_method', 'cash')

        # Client-submitted, DISPLAY-ONLY -- never fed into a financial record
        # (see section banner above). Falls back to summing the cart's own
        # line_total figures if the client didn't send pre-computed totals.
        item_count = len(items)
        subtotal = _money(data.get('subtotal', sum(float(i.get('line_total', 0)) for i in items)))
        total = _money(data.get('total', subtotal))

        snapshot = json.dumps({
            'items': items,
            'discount_pct': discount_pct,
            'payment_method': payment_method,
            'customer_id': customer_id,
        })

        hold_number = f"{_next_ref(conn, cid, 'hold')}-{str(cid)[:8]}"
        cur.execute("""
            INSERT INTO held_sales (company_id,branch_id,customer_id,hold_number,label,
                                     cart_json,item_count,subtotal,total,held_by)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (cid, bid, customer_id, hold_number, label, snapshot, item_count, subtotal, total, _uid()))
        held_id = cur.lastrowid
        _audit(conn, 'SALE_HELD', 'held_sale', held_id, f'{hold_number} items={item_count} total={total}')
        conn.commit()
        return jsonify({'status': 'success', 'data': {
            'id': held_id, 'hold_number': hold_number, 'item_count': item_count,
            'subtotal': subtotal, 'total': total,
        }})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()

@retail_bp.route('/held-sales/<int:held_id>/resume', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.sale.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_SELL)
def resume_held_sale(held_id):
    """Loads the snapshot back out and deletes the held row in the same
    transaction -- a resumed sale is either completed or re-held under a
    brand-new hold_number; there is no "resumed but still parked" state, so
    nothing lingers. company_id-scoped lookup, same as every other by-id
    route in this file -- a caller cannot resume another company's held sale
    by guessing an id. History of the action survives in audit_log even
    though the held_sales row itself is gone."""
    cid = _cid()
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT * FROM held_sales WHERE id=? AND company_id=?", (held_id, cid)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({'status': 'error', 'message': 'Held sale not found.'}), 404
        conn.execute("DELETE FROM held_sales WHERE id=? AND company_id=?", (held_id, cid))
        _audit(conn, 'SALE_RESUMED', 'held_sale', held_id, row['hold_number'])
        conn.commit()
        snapshot = json.loads(row['cart_json'])
        return jsonify({'status': 'success', 'data': {
            'id': row['id'], 'hold_number': row['hold_number'], 'label': row['label'],
            'items': snapshot.get('items', []),
            'discount_pct': snapshot.get('discount_pct', 0),
            'payment_method': snapshot.get('payment_method', 'cash'),
            'customer_id': snapshot.get('customer_id'),
        }})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()

@retail_bp.route('/held-sales/<int:held_id>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.sale.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_SELL)
def discard_held_sale(held_id):
    """Explicit delete only -- no silent/automatic expiry of stale held
    sales (see schema.py v3->v4 comment). A cashier who parked a sale by
    mistake, or one that's gone stale, must consciously discard it; nothing
    in this feature ever removes a held_sales row on its own."""
    cid = _cid()
    conn = get_retail_conn()
    row = conn.execute("SELECT hold_number FROM held_sales WHERE id=? AND company_id=?", (held_id, cid)).fetchone()
    if not row:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Held sale not found.'}), 404
    conn.execute("DELETE FROM held_sales WHERE id=? AND company_id=?", (held_id, cid))
    _audit(conn, 'HELD_SALE_DISCARDED', 'held_sale', held_id, row['hold_number'])
    conn.commit(); conn.close()
    return jsonify({'status': 'success'})

# ── Returns ───────────────────────────────────────────────────────────────────

@retail_bp.route('/returns', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def list_returns():
    cid  = _cid()
    conn = get_retail_conn()
    rows = conn.execute("""
        SELECT r.*, s.sale_number, COALESCE(c.name,'Walk-in') as customer_name
        FROM returns r
        LEFT JOIN sales s ON r.sale_id=s.id
        LEFT JOIN customers c ON s.customer_id=c.id
        WHERE r.company_id=? ORDER BY r.created_at DESC LIMIT 100
    """, (cid,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@retail_bp.route('/returns', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.return.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_REFUND)
def create_return():
    """Server-authoritative return creation (Wave 0 correction, AUDIT-004).

    The client sends only sale_id + {product_id, quantity} per line. unit_price/
    discount_pct/tax_rate/line_total, if sent, are IGNORED -- refund figures are
    always recomputed from the ORIGINAL sale_items row for that sale+product,
    proportionally to the quantity actually being returned, so tax and discount
    reverse correctly (previously the return's own refund_amount silently
    excluded tax entirely -- see docs/audit/03-retail-financial-audit.md and
    docs/corrections/wave0/retail-return-correction.md for the resulting,
    intentional refund_amount semantics change and the tests updated to match).
    Every return must reference a real sale belonging to this company, and the
    requested quantity (net of anything already returned against that same
    sale+product) may never exceed what was actually sold.
    """
    data   = request.json or {}
    cid    = _cid()
    items  = data.get('items', [])
    if not items:
        return jsonify({'status': 'error', 'message': 'No items to return'}), 400

    sale_id = data.get('sale_id')
    conn = get_retail_conn()
    _ensure_credit_schema(conn)
    cur  = conn.cursor()
    try:
        idem = data.get('idempotency_key')
        if idem:
            # company_id-scoped: an unscoped lookup would let a caller who
            # somehow knew/guessed another company's idempotency_key read
            # back that company's return id/number.
            ex = cur.execute("SELECT id,return_number FROM returns WHERE idempotency_key=? AND company_id=?",
                              (idem, cid)).fetchone()
            if ex:
                conn.close()
                return jsonify({'status': 'success', 'data': {'id': ex['id'], 'return_number': ex['return_number']}})

        # `uid` selected alongside the columns this route already needed --
        # Phase 5 (money-moving sync) needs the parent sale's WIRE identity
        # to stamp onto the return's own sync_outbox payload below (see that
        # payload's `sale_uid` field); the local integer `id` this route uses
        # everywhere else means nothing on another device.
        sale = cur.execute(
            "SELECT id, uid, branch_id, customer_id, total, amount_paid FROM sales WHERE id=? AND company_id=?",
            (sale_id, cid)
        ).fetchone()
        if not sale:
            conn.close()
            return jsonify({'status': 'error', 'message': 'Original sale not found.'}), 404

        # v13 attribution stamp, resolved once for the return and every row it
        # writes. Hoisted above BEGIN IMMEDIATE for the same reason create_sale
        # hoists its own -- no cross-database I/O while holding retail.db's
        # write lock; see the long note there.
        #
        # A refund is the single most-audited action a shop performs: money
        # leaves the till against no sale of its own. It is the write where
        # "the database cannot say who" costs the most.
        actor, terminal, utc_now = _stamp()

        # BEGIN IMMEDIATE: two returns against the same sale+product racing each
        # other must not both read the same "remaining returnable" snapshot.
        conn.execute("BEGIN IMMEDIATE")
        # `sale['branch_id']` -- the ORIGINAL sale's own branch -- wins
        # unconditionally: a return belongs at the branch the sale it
        # reverses happened at, not wherever this till is pinned. That tier
        # is untouched. Only the fallback used when a legacy sale carries no
        # branch_id at all is in scope for launch-readiness chain wave C1,
        # and `data.get('branch_id')` was UNVALIDATED there before this fix
        # -- the same cross-tenant leak shape create_purchase_order's
        # supplier_id comment describes -- so it now goes through
        # _resolve_working_branch instead of being trusted directly.
        if sale['branch_id']:
            bid = sale['branch_id']
        else:
            bid, branch_err = _resolve_working_branch(conn, cid, data.get('branch_id'))
            if branch_err:
                conn.rollback(); conn.close()
                return branch_err
        # AUDIT-032C fix (DEFECT 3) -- identical reasoning to create_sale's
        # own `branch_uid` resolution above (see that comment for the full
        # explanation of why the raw integer `bid` alone is unsafe to carry
        # onto a pulled row).
        branch_uid_row = conn.execute("SELECT uid FROM branches WHERE id=?", (bid,)).fetchone()
        branch_uid = branch_uid_row['uid'] if branch_uid_row else None
        mode = _settings(conn, cid).get('tax_calculation_mode', tax_engine.DEFAULT_MODE)
        # feat/shift-cash-drawer (schema v10): same best-effort session stamp
        # as create_sale above -- see _open_cash_session_id's docstring.
        cash_session_id = _open_cash_session_id(conn, cid, bid)

        resolved_items = []
        refund_total = Decimal('0')
        # AUDIT: quantity-validation-bypass -- the already_returned SELECT below
        # only sees return_items rows already COMMITTED before this request
        # started; it can never see sibling lines of this same request, since
        # those INSERTs only happen in the second loop after this whole
        # validation loop finishes. Without this in-request accumulator, two
        # lines in one payload for the same product_id would each be validated
        # against the same pre-request "already returned" snapshot and both
        # pass, refunding/restocking that product twice over. Track quantity
        # already claimed by earlier lines in THIS request per product_id and
        # subtract it from what remains returnable for later lines.
        claimed_this_request = {}
        for item in items:
            pid = item.get('product_id')
            try:
                qty = float(item.get('quantity'))
            except (TypeError, ValueError):
                conn.rollback(); conn.close()
                return jsonify({'status': 'error', 'message': 'Invalid return quantity.'}), 400
            if qty <= 0:
                conn.rollback(); conn.close()
                return jsonify({'status': 'error', 'message': 'Return quantity must be greater than zero.'}), 400

            sold = cur.execute(
                "SELECT quantity, unit_price, discount_pct, tax_rate FROM sale_items "
                "WHERE sale_id=? AND product_id=?", (sale_id, pid)
            ).fetchone()
            if not sold:
                conn.rollback(); conn.close()
                return jsonify({'status': 'error', 'message': f'Product {pid} was not part of sale {sale_id}.'}), 400

            already_returned = cur.execute(
                "SELECT COALESCE(SUM(ri.quantity),0) FROM return_items ri "
                "JOIN returns r ON ri.return_id = r.id "
                "WHERE r.sale_id=? AND ri.product_id=? AND r.company_id=?",
                (sale_id, pid, cid)
            ).fetchone()[0]
            already_claimed = claimed_this_request.get(pid, 0.0)
            remaining = float(sold['quantity']) - float(already_returned or 0) - already_claimed
            if qty > remaining + 0.0001:
                conn.rollback(); conn.close()
                return jsonify({'status': 'error', 'message':
                    f'Cannot return {qty} of product {pid}: only {remaining} remain returnable '
                    f'(sold {sold["quantity"]}, already returned {already_returned}, '
                    f'already claimed earlier in this request {already_claimed}).'}), 400
            claimed_this_request[pid] = already_claimed + qty

            calc = tax_engine.calculate_line(
                float(sold['unit_price']), qty, float(sold['discount_pct'] or 0),
                float(sold['tax_rate'] or 0), mode=mode)
            resolved_items.append({
                'product_id': pid, 'quantity': qty, 'unit_price': float(sold['unit_price']),
                'discount_amount': calc['discount_amount'], 'tax_amount': calc['tax'],
                'line_total': calc['total'],
            })
            refund_total += Decimal(str(calc['total']))

        refund = float(refund_total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        # returns.return_number carries a bare (not company-scoped, not
        # device-scoped) UNIQUE constraint, but _next_ref()'s counter resets
        # per company AND is a per-DEVICE table (`doc_sequences` is never
        # synced -- see _device_doc_discriminator's docstring). Two
        # different companies' first return would otherwise both generate
        # "RET-000001" and collide in this shared multi-tenant database
        # (fixed first, appending a company fragment); two DEVICES on the
        # SAME company would independently generate the identical
        # "RET-000001-<cid8>" and collide the moment sync relayed both to a
        # third device -- the same AUDIT-032B wedge-sync failure chain
        # create_sale's identical fix closes (see
        # _device_doc_discriminator's docstring). Appending both fragments
        # keeps the sequential part human-readable/searchable while
        # guaranteeing global uniqueness without altering the shared
        # _next_ref helper or its format for other doc types.
        ret_num = f"{_next_ref(conn, cid, 'return')}-{str(cid)[:8]}-{_device_doc_discriminator()}"
        # Write LOCAL time, not the UTC CURRENT_TIMESTAMP default: the dashboard nets
        # returns out of today's revenue by local date(created_at), so a UTC timestamp
        # would file a late-evening return under the wrong day and leave the KPI stale.
        now_local = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # `actor`/`terminal`/`utc_now` come from the _stamp() hoisted above
        # BEGIN IMMEDIATE. Same warning as create_sale: `utc_now` is a genuinely
        # UTC instant and is NOT `now_local`, which is local wall clock on
        # purpose (see the comment three lines up).
        ret_uid = _new_uid()
        cur.execute("""
            INSERT INTO returns (company_id,return_number,sale_id,branch_id,cashier,
                                 reason,refund_method,refund_amount,status,idempotency_key,created_at,
                                 session_id,uid,actor_user_uid,terminal_id,created_at_utc)
            VALUES (?,?,?,?,?,?,?,?,'completed',?,?,?,?,?,?,?)
        """, (cid, ret_num, sale_id, bid, _uid(),
              data.get('reason','Customer return'), data.get('refund_method','cash'), refund, idem, now_local,
              cash_session_id, ret_uid, actor, terminal, utc_now))
        ret_id = cur.lastrowid

        # ── Phase 5 sync emission: the return itself ─────────────────────────
        # `sale_uid` -- the ORIGINAL sale's wire identity, from the `uid`
        # column selected above -- not `sale_id` (local, meaningless
        # cross-device). The apply side resolves `returns.sale_id`'s real FK
        # by looking up `sales.uid = sale_uid` locally, exactly like
        # sale_item resolves its own parent (see sync_service.py). Same
        # `company_id` / `idempotency_key` / `session_id` omissions as
        # create_sale's own event, for the identical reasons.
        _queue_sync_event(cur, 'return', ret_uid, 'create', {
            'uid': ret_uid, 'sale_uid': sale['uid'], 'return_number': ret_num, 'branch_id': bid,
            'branch_uid': branch_uid,
            'cashier': _uid(), 'reason': data.get('reason', 'Customer return'),
            'refund_method': data.get('refund_method', 'cash'), 'refund_amount': refund,
            'status': 'completed', 'created_at': now_local,
            'actor_user_uid': actor, 'terminal_id': terminal, 'created_at_utc': utc_now,
        })

        for line in resolved_items:
            pid, qty = line['product_id'], line['quantity']
            # Fresh uid per line -- see the identical note in create_sale's
            # sale_items write about the partial UNIQUE index on uid.
            item_uid = _new_uid()
            cur.execute("""
                INSERT INTO return_items (return_id,product_id,quantity,unit_price,line_total,uid)
                VALUES (?,?,?,?,?,?)
            """, (ret_id, pid, qty, line['unit_price'], line['line_total'], item_uid))
            # `return_uid`, not `return_id` -- see the identical note on
            # sale_item's `sale_uid` above.
            _queue_sync_event(cur, 'return_item', item_uid, 'create', {
                'uid': item_uid, 'return_uid': ret_uid, 'product_id': pid, 'quantity': qty,
                'unit_price': line['unit_price'], 'line_total': line['line_total'],
            })
            cur.execute("""
                INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand)
                VALUES (?,?,?,0)
            """, (cid, pid, bid))
            cur.execute("""
                UPDATE inventory_balances SET quantity_on_hand=quantity_on_hand+?
                WHERE company_id=? AND product_id=? AND branch_id=?
            """, (qty, cid, pid, bid))
            movement_uid = _new_uid()
            cur.execute("""
                INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,reference,created_by,
                                                 uid,actor_user_uid,terminal_id,created_at_utc)
                VALUES (?,?,?,'return_in',?,?,?,?,?,?,?)
            """, (cid, pid, bid, qty, ret_num, _uid(),
                  movement_uid, actor, terminal, utc_now))
            # Wave B: queue the matching sync event -- see create_sale's own
            # inventory_movement emission for the shape every writer shares.
            # `branch_uid` reuses the value already resolved above for the
            # `return` event itself (AUDIT-032C).
            _queue_sync_event(cur, 'inventory_movement', movement_uid, 'create', {
                'uid': movement_uid, 'product_id': pid, 'branch_id': bid, 'branch_uid': branch_uid,
                'movement_type': 'return_in', 'quantity': qty, 'unit_cost': 0,
                'reference': ret_num, 'notes': None, 'created_by': _uid(),
                'actor_user_uid': actor, 'terminal_id': terminal, 'created_at_utc': utc_now,
            })

        # Real bug fixed here: a return against a sale that was never fully
        # paid (credit sale, or a partial-payment cash sale) never touched
        # the customer's credit_balance at all -- they'd still owe the full
        # original amount after getting the item back. sale.total/
        # amount_paid are the ORIGINAL sale's own immutable figures (never
        # edited after the fact, per this file's "never edit/delete, only
        # reverse" policy), so original_balance_due is exactly what was
        # ever actually owed on this specific sale. Credited amount is
        # capped at min(refund, original_balance_due, current
        # credit_balance): the first cap keeps a return that's smaller than
        # the outstanding balance from over-crediting; the second guards
        # against multiple partial returns against the same credit sale
        # ever driving credit_balance negative (this file has no per-return
        # AR-credited ledger to attribute exactly, so the customer's live
        # balance is the real, final backstop -- it can never go below what
        # they'd otherwise be credited elsewhere).
        if sale['customer_id']:
            original_balance_due = max(0.0, _money(float(sale['total']) - float(sale['amount_paid'] or 0)))
            if original_balance_due > 0.005:
                current_balance = cur.execute(
                    "SELECT COALESCE(credit_balance,0) FROM customers WHERE id=? AND company_id=?",
                    (sale['customer_id'], cid)
                ).fetchone()[0]
                ar_credit = min(refund, original_balance_due, float(current_balance))
                if ar_credit > 0.005:
                    _adjust_credit(conn, 'customers', sale['customer_id'], cid, -ar_credit)

        _audit(conn, 'RETURN_PROCESSED', 'return', ret_id, f'{ret_num} refund={refund}')
        conn.commit()
        _sync_nudge()  # best-effort immediate push -- see commercial_runtime/sync/sync_service.py
        return jsonify({'status': 'success', 'data': {
            'id': ret_id, 'return_number': ret_num, 'refund_amount': round(refund, 2),
            'idempotency_key': idem, 'items': resolved_items,
            'calculation_version': tax_engine.CALCULATION_VERSION,
        }})
    except Exception as e:
        conn.rollback(); conn.close()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        # Stock-accuracy sweep: the SUCCESS path above returned straight out
        # of the `try` after conn.commit() and never closed its connection --
        # only the two early-exit paths and the except branch ever did. Every
        # successful return therefore leaked one open SQLite handle, left to
        # be closed whenever CPython happened to collect it. On WAL that is a
        # lingering reader that holds back checkpointing, and it is a prime
        # suspect for the intermittent SQLITE_BUSY this app sees under a busy
        # returns counter. sqlite3.Connection.close() is idempotent, so the
        # pre-existing explicit close() calls on the other paths are left
        # exactly as they were rather than restructured -- this adds the one
        # guarantee that was missing without touching any working path.
        conn.close()

# ── Cash Drawer / Shift Management (feat/shift-cash-drawer, schema v10) ────────
# Real cash-drawer management: opening float, mid-shift float in/out (and
# ad-hoc paid-in/paid-out) tracking, and X/Z reports. X = a live, read-only
# snapshot of what the drawer SHOULD contain right now -- never locks
# anything, callable any number of times mid-shift. Z = the end-of-shift
# close-out: counts the drawer, compares counted vs. expected, records the
# variance, and locks the session (status -> 'closed'; no route below ever
# reopens or edits a closed session -- immutability once closed, matching
# this file's "never edit/delete, only reverse" policy for payments above).
#
# Every mutation route below is guarded with require_license_capability using
# NEW capability strings that are deliberately NOT in RETAIL_RESTRICTED_
# ALLOWLIST -- a restricted/expired license blocks opening a shift, recording
# a movement, or closing a shift, same default-blocked treatment as every
# other mutation not explicitly carved out (see that allowlist's own comment
# above).
#
# THE GET ROUTES. This comment used to say all four of them (current /
# x-report / list / get-one) carried no capability guard, "matching every
# other read-only report route in this file (daily_cash, aging_report,
# report_sales_trend)". That claim was false by the time it was written --
# all three of those routes had already been moved onto retail.reports -- and
# it aged into cover for a real hole: `x-report` is the drawer's entire money
# picture and it was readable by anyone who could reach the product at all.
#
# It then said the other three "return session STATE rather than a money
# report", and THAT claim aged the same way, for the same reason: a
# cash_sessions row carries `opening_float` and `variance`, which the runtime
# money sweep's own vocabulary calls transacted money. It read as true only
# because the sweep's fixture never opens a drawer, so every session response
# it ever measured was `null` and no money key was there to find. All four now
# carry retail.cash.close.
#
# WHAT THE CAPABILITY DOES NOT DECIDE is WHOSE drawer you may read. Phase 4
# splits that from the gate: your own till's drawer is yours (cash.close is
# enough), another till's is a report (retail.reports, or retail.cash.approve
# because you cannot accept a variance you may not look at). See
# _session_read_scope. Both facts are pinned from the live url_map rather than
# from a comment, by retail_route_capability_matrix_test.py's exhaustive sweep
# and by retail_drawer_terminal_scope_test.py.
# ── The drawer's state machine, and what a caller is allowed to see ──────────
#
# THE ENDED / CLOSED SPLIT (design §6, retail v16).
#
#   open    the till is live. Sales stamp into it, movements are accepted.
#   ended   a human counted the drawer and the count is locked. The drawer is
#           out of service. The VARIANCE that count produced has NOT been
#           accepted by anybody.
#   closed  an authority holding retail.cash.approve accepted that variance.
#
# Ending and approving are two authorities on purpose, and the reason is
# AUDIT-032, already closed once on the Owner side: a role that could both
# count its own drawer and sign off its own shortfall is a role that can steal
# in a straight line. `retail.cash.approve` is therefore withheld from every
# role that holds `retail.cash.close`, manager included
# (commercial_runtime/identity/user_accounts.py's ROLE_CAPABILITIES).
#
# THE CASHIER IS NEVER TRAPPED. Ending a shift requires retail.cash.close and
# NOTHING ELSE -- not approval, not a variance of zero, not a manager standing
# behind them. A till that refused to close on a short count would leave the
# person who is short standing at it, which is both cruel and the fastest way
# to teach a shop to stop counting honestly. They end; the shortfall is
# recorded; somebody with the authority accepts it later.
#
# A caller who holds BOTH (the shop owner, and the admin bypass) ends and
# approves in one act -- there is no second person for them to wait for, and
# manufacturing a two-step ceremony for a one-person shop would only teach
# everyone to click through it. It is not silent: `closed_by`/`closed_at`
# record who accepted it, and the audit trail says the ender approved their
# own count.
#
# FORCE-ENDED is the fourth shape and it is NOT a status of its own. A session
# the shop never counted -- swept up by v16's business-date force-end, or left
# standing between another shift and the terminal it is bound to -- is ENDED
# with `ended_by='System'`, an `ended_reason` out of
# schema.V16_UNVERIFIED_END_REASONS, and NULL `closing_float_counted` /
# `variance`. Nobody counted it, so its variance is UNKNOWN, not zero.
# Reporting 0.00 there would be the single worst thing this file could do,
# because 0.00 is the number a shop reads as "the drawer was fine".
#
# The three statuses come from database/schema.py (CASH_SESSION_STATUS_*),
# imported at the top of this file. They are the migration's vocabulary and
# there is exactly one copy of it.

#: What is known about the variance on a session, in one word.
VARIANCE_PENDING = 'pending'        # still open; there is no count yet
VARIANCE_NOT_COUNTED = 'not_counted'  # out of service, never counted: UNKNOWN
VARIANCE_UNVERIFIED = 'unverified'  # counted, nobody has accepted it yet
VARIANCE_APPROVED = 'approved'      # counted and accepted by cash.approve


def _cash_session_columns(conn):
    """The `cash_sessions` columns this database actually has.

    Phase 4's routes are written against the retail v16 contract
    (`ended_at`/`ended_by`, and `terminal_id` backfilled from the device
    fingerprint) while the migration that adds `ended_at`/`ended_by` is landing
    separately. Reading the real column list means this file works on both
    sides of that landing instead of crashing on one of them, and it is the
    same `PRAGMA table_info` idempotency check every migration in
    database/schema.py already uses.

    `terminal_id` is NOT conditional: v13 put it on cash_sessions years before
    this phase, so every install that can run this code has it. Only the two
    v16 columns are guarded.
    """
    try:
        return {row[1] for row in conn.execute('PRAGMA table_info(cash_sessions)').fetchall()}
    except Exception:
        return set()


def _ended_reason(sess):
    """v16's `ended_reason`, or None on a database that predates the column."""
    return (sess['ended_reason'] if 'ended_reason' in sess.keys() else None) or None


def _is_force_closed(sess):
    """True when this drawer went out of service without anybody counting it.

    TWO INDEPENDENT SIGNALS, and they are meant to agree -- schema.py's
    `_v16_force_end` docstring says so explicitly ("the reason text says
    `unverified_*` and the empty figures say the same thing structurally, so a
    consumer that reads either one gets the same answer"):

      the REASON      `ended_reason` in V16_UNVERIFIED_END_REASONS. Explicit,
                      and it survives an approval -- accepting an uncounted
                      drawer moves `status` to closed and must not make the
                      row look counted.
      the FIGURES     `closing_float_counted IS NULL` on a drawer that has
                      stopped trading. This is the one that works on a
                      database where `ended_reason` does not exist yet, and
                      the one that catches a force-end written by anything
                      other than v16.

    Either is sufficient. Requiring BOTH would mean a row where one signal was
    lost reads as properly counted, which is the direction that costs money.
    """
    status = (sess['status'] if 'status' in sess.keys() else None) or ''
    if status == CASH_SESSION_STATUS_OPEN:
        return False
    if _ended_reason(sess) in V16_UNVERIFIED_END_REASONS:
        return True
    return sess['closing_float_counted'] is None


def _variance_state(sess):
    """`(variance_status, variance, counted)` -- what is KNOWN, never inferred.

    The whole point of returning a triple is that a force-closed session's
    variance is None rather than 0.0. `sess['variance']` on such a row is NULL
    already; this function's job is to make sure nothing downstream helpfully
    coerces that NULL into a number on the way to a screen.
    """
    status = (sess['status'] if 'status' in sess.keys() else None) or ''
    if status == CASH_SESSION_STATUS_OPEN:
        return VARIANCE_PENDING, None, None
    if _is_force_closed(sess):
        return VARIANCE_NOT_COUNTED, None, None
    counted = sess['closing_float_counted']
    variance = sess['variance']
    state = VARIANCE_APPROVED if status == CASH_SESSION_STATUS_CLOSED else VARIANCE_UNVERIFIED
    return state, variance, counted


def _terminal_short(value):
    """A terminal id a human can read off a screen and say out loud.

    Last four characters of the uuid, uppercased. NOT a new identity: the full
    id travels beside it in `terminal_id` for anyone who needs to match the row
    against device_registry.devices, exactly as `_bdi()`'s own comment in
    subsystem-retail.js insists ("a truncated-only id cannot be matched against
    device_registry.devices when someone actually needs to identify a
    terminal"). None stays None -- an unidentified till must not be given a
    label that implies it was identified.
    """
    term = _terminal(value)
    if not term:
        return None
    return term.replace('-', '')[-4:].upper()


def _cash_session_public(sess, this_terminal=_UNSET):
    """The wire shape of one cash_sessions row.

    Starts from the raw row -- every field this route family has ever returned
    keeps its name and its meaning -- and adds the four facts a terminal-aware
    drawer screen cannot work without:

      terminal_id / terminal_short  WHICH till this drawer belongs to. The
                                    phase exists because that was unanswerable.
      is_this_terminal              whether the device asking owns it. The
                                    screen needs this to tell "your drawer"
                                    from "another till's drawer" without the
                                    reader having to compare two uuids by eye.
      variance_status               pending / not_counted / unverified /
                                    approved -- see the block comment above.
      force_closed                  nobody counted this one.
      ended_reason                  WHY nobody counted it, when v16 ended the
                                    shift: a business date that had already
                                    passed, or a collision on one terminal.
                                    Passed straight through rather than
                                    translated here, because the screen is
                                    where a reason becomes a sentence and the
                                    catalogs are where it becomes Arabic.

    `ended_at`/`ended_by` are surfaced from the v16 columns when they exist and
    fall back to `closed_at`/`closed_by` when they do not, so a screen written
    against the contract renders correctly on both sides of the migration
    rather than showing a blank where the count happened.
    """
    row = dict(sess)
    keys = set(sess.keys())
    term = _terminal(row.get('terminal_id'))
    mine = _this_terminal() if this_terminal is _UNSET else _terminal(this_terminal)

    variance_status, variance, counted = _variance_state(sess)
    forced = _is_force_closed(sess)

    row['terminal_id'] = term
    row['terminal_short'] = _terminal_short(term)
    row['is_this_terminal'] = (term == mine)
    row['variance_status'] = variance_status
    row['force_closed'] = forced
    # Re-stated from _variance_state rather than left as the raw column, so a
    # force-closed row can never hand a screen a variance of 0.00 for a drawer
    # that was never counted.
    row['variance'] = variance
    row['closing_float_counted'] = counted
    row['ended_reason'] = _ended_reason(sess)

    approved = (row.get('status') == CASH_SESSION_STATUS_CLOSED)
    if 'ended_at' in keys:
        # v16 has landed: two events, two column pairs. `ended_*` is the
        # person who counted, `closed_*` is the person who accepted the count.
        row['ended_at'] = row.get('ended_at')
        row['ended_by'] = row.get('ended_by')
        row['approved_at'] = row.get('closed_at') if approved else None
        row['approved_by'] = row.get('closed_by') if approved else None
    else:
        # Pre-v16 there is ONE column pair for two events, so the end is
        # recorded and the approval is not. Reporting the ender as the
        # approver would be a fabricated audit fact -- the exact category of
        # mistake schema.py's own migration docstring refuses when it declines
        # to backfill `created_at_utc` from this machine's offset -- so this
        # says None and means it. Who approved is still recoverable from
        # audit_log (CASH_VARIANCE_APPROVED), it is simply not a column yet.
        row['ended_at'] = None if forced else row.get('closed_at')
        row['ended_by'] = None if forced else row.get('closed_by')
        row['approved_at'] = None
        row['approved_by'] = None
    return row


def _same_actor(sess, actor_uid):
    """True when `actor_uid` is the person who ended this shift.

    Reads `ended_by` where v16 has landed and `closed_by` where it has not --
    which is the SAME fallback `_cash_session_public` documents, and it has to
    be, or "did the ender approve their own count?" would be answered from one
    column while the screen reported the other.
    """
    keys = sess.keys()
    ender = (sess['ended_by'] if 'ended_by' in keys else None) or (
        sess['closed_by'] if 'closed_by' in keys else None)
    return bool(ender) and str(ender) == str(actor_uid)


def _terminal_owns(sess, this_terminal=_UNSET):
    """True when THIS device is the terminal the drawer belongs to.

    The predicate every drawer WRITE is guarded by. Kept separate from
    `_session_read_scope` on purpose: reads widen with authority, writes never
    do, and one function returning both would make it one edit to give an
    owner the ability to file a movement on somebody else's till.
    """
    term = _terminal(sess['terminal_id'] if 'terminal_id' in sess.keys() else None)
    mine = _this_terminal() if this_terminal is _UNSET else _terminal(this_terminal)
    return term == mine


def _session_read_scope(sess, this_terminal=_UNSET):
    """`(is_mine, may_read)` for one session and the CURRENT request.

    THE RULE, stated once here and applied by every drawer read below:

      your OWN till's drawer is yours          -- retail.cash.close is enough,
                                                  which is what the route
                                                  decorator already required
      ANOTHER till's drawer is a REPORT        -- retail.reports, or
                                                  retail.cash.approve because
                                                  you cannot accept a variance
                                                  you are not allowed to look at

    This is the same distinction cash_session_x_report's own docstring already
    draws for its capability choice ("the person who counts the drawer is the
    person who closes it"), carried one step further now that "the drawer" is a
    specific till rather than a whole branch. Without it, `retail.cash.close`
    -- a CASHIER default -- would read every till in the shop by id, which is
    exactly the disclosure the x-report gate was added to stop, reopened
    through a different door.
    """
    term = _terminal(sess['terminal_id'] if 'terminal_id' in sess.keys() else None)
    mine = _this_terminal() if this_terminal is _UNSET else _terminal(this_terminal)
    is_mine = (term == mine)
    if is_mine:
        return True, True
    return False, (session_has_capability(CAP_REPORTS)
                   or session_has_capability(CAP_CASH_APPROVE))


#: The refusal a caller gets for another till's drawer. Its own sentence, not
#: CAPABILITY_DENIED_MESSAGE: "you may not do this" would send a cashier to
#: their owner asking for a permission that would not have helped, when the
#: real answer is "that drawer belongs to a different till".
FOREIGN_DRAWER_MESSAGE = (
    'That cash drawer belongs to a different terminal. Ask the shop owner to '
    'review it.'
)


def _cash_session_report(conn, cid, sess):
    """Live X/Z math for one cash_sessions row -- a pure read, safe to call
    from both GET .../x-report (mid-shift, non-destructive, callable any
    number of times) and POST .../close (which persists the SAME numbers
    this returns as the Z report's locked figures, computed inside that
    route's own BEGIN IMMEDIATE transaction so nothing can be added to the
    session between "compute expected" and "lock the session").

    expected_cash = opening_float
                    + cash_sales - cash_refunds
                    + float_in - float_out + paid_in - paid_out

    cash_sales is read from the SAME unified `payments` ledger daily_cash()
    above already trusts (direction='in', method='cash', related_type='sale')
    -- create_sale's own _record_payment call feeds that ledger, including
    the cash portion of a partial-credit sale (see create_sale's own comment
    on why a credit sale's upfront deposit is recorded there with
    method='cash'). cash_refunds is read directly from `returns.refund_amount`
    /`refund_method` instead -- create_return has NO _record_payment call at
    all (refunds never touch the `payments` ledger in this codebase today),
    so querying `payments` for refunds would silently undercount to zero.
    Both queries filter on sales.session_id/returns.session_id -- the direct
    FK stamp _open_cash_session_id() writes -- not a time-range, per this
    session_id column's own migration-docstring reasoning.

    paid_in/paid_out are folded into the same expected-cash total as
    float_in/float_out (not tracked-but-ignored): a paid_out (e.g. till cash
    used to pay a delivery driver COD) really does leave the drawer, and
    omitting it from the math would leave a permanent phantom variance at
    close for any install that actually uses that movement type.
    """
    session_id = sess['id']
    bid = sess['branch_id']
    keys = set(sess.keys())
    session_terminal = _terminal(sess['terminal_id'] if 'terminal_id' in keys else None)
    window_end = (
        sess['closed_at']
        or (sess['ended_at'] if 'ended_at' in keys else None)
        or _now()
    )

    cash_sales = conn.execute("""
        SELECT COALESCE(SUM(p.amount),0) FROM payments p
        JOIN sales s ON p.sale_id = s.id
        WHERE p.company_id=? AND s.branch_id=? AND s.session_id=?
          AND p.direction='in' AND p.method='cash' AND p.related_type='sale'
          AND COALESCE(p.status,'active')='active'
    """, (cid, bid, session_id)).fetchone()[0]

    cash_refunds = conn.execute("""
        SELECT COALESCE(SUM(refund_amount),0) FROM returns
        WHERE company_id=? AND branch_id=? AND session_id=?
          AND refund_method='cash' AND status='completed'
    """, (cid, bid, session_id)).fetchone()[0]

    movement_rows = conn.execute("""
        SELECT type, COALESCE(SUM(amount),0) as amount FROM cash_movements
        WHERE session_id=? GROUP BY type
    """, (session_id,)).fetchall()
    movements = {'float_in': 0.0, 'float_out': 0.0, 'paid_in': 0.0, 'paid_out': 0.0}
    for r in movement_rows:
        if r['type'] in movements:
            movements[r['type']] = float(r['amount'] or 0)

    opening_float = float(sess['opening_float'] or 0)
    expected = _money(
        opening_float + cash_sales - cash_refunds
        + movements['float_in'] - movements['float_out']
        + movements['paid_in'] - movements['paid_out']
    )

    # ── FOREIGN-TERMINAL CONTAMINATION, COUNTED RATHER THAN HIDDEN ───────────
    #
    # `_open_cash_session_id` now stamps sales with THIS terminal's session, so
    # nothing written from today on can land in another till's drawer. What
    # that fix cannot do is un-write the rows already stamped the old way: on
    # any shop that has been running more than one device, every session opened
    # before Phase 4 has other terminals' sales inside it, permanently, and the
    # arithmetic above will happily total them because it is summing exactly
    # the rows the FK says belong here.
    #
    # A number that is wrong for a knowable reason must SAY SO. So the report
    # counts the sales in this session whose terminal is provably a DIFFERENT
    # one and reports that count beside the money, and the drawer screen shows
    # it. This is the honest half of the phase: the going-forward bug is fixed,
    # the historical contamination is disclosed, and nobody reads a legacy Z
    # report as clean because the code that produced it had been patched.
    #
    # 'provably different' is doing real work. A sale with terminal_id NULL --
    # every sale rung before v13 -- is NOT evidence of another till; it is
    # evidence of nothing, and counting it as foreign would put a permanent
    # false alarm on every shop with history. It is counted separately, as
    # unattributed, which is what it is.
    foreign_sales = 0
    unattributed_sales = 0
    try:
        row = conn.execute("""
            SELECT
              SUM(CASE WHEN s.terminal_id IS NOT NULL AND TRIM(s.terminal_id) <> ''
                        AND s.terminal_id IS NOT ? THEN 1 ELSE 0 END) AS foreign_n,
              SUM(CASE WHEN s.terminal_id IS NULL OR TRIM(s.terminal_id) = ''
                       THEN 1 ELSE 0 END) AS unattributed_n
            FROM sales s
            WHERE s.company_id=? AND s.session_id=?
        """, (session_terminal, cid, session_id)).fetchone()
        foreign_sales = int(row['foreign_n'] or 0)
        unattributed_sales = int(row['unattributed_n'] or 0)
    except Exception:
        # Same posture as _open_cash_session_id: a disclosure ABOUT the numbers
        # must never be able to take the numbers down with it. -1 says "not
        # determined" and is distinguishable from 0, "determined to be clean" --
        # which is the whole distinction this block exists to preserve.
        foreign_sales = -1
        unattributed_sales = -1

    return {
        'session_id': session_id, 'status': sess['status'],
        'terminal_id': session_terminal,
        'terminal_short': _terminal_short(session_terminal),
        'is_this_terminal': (session_terminal == _this_terminal()),
        'opening_float': opening_float,
        'cash_sales': _money(cash_sales), 'cash_refunds': _money(cash_refunds),
        'movements': {k: _money(v) for k, v in movements.items()},
        'expected_cash': expected,
        'foreign_terminal_sales': foreign_sales,
        'unattributed_sales': unattributed_sales,
        'window_start': sess['opened_at'], 'window_end': window_end,
    }

_CASH_MOVEMENT_TYPES = frozenset({'float_in', 'float_out', 'paid_in', 'paid_out'})

@retail_bp.route('/cash-sessions/open', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.cash_session.open", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
# retail.cash.close covers the WHOLE drawer -- open, movements, close -- not
# just the closing act its name describes. They are one authority: a person
# who may not open a drawer cannot meaningfully close one, and float in/out
# is the same till and the same cash. Splitting them would need codes the
# design does not define. The authority that IS separate is accepting the
# variance a close records (retail.cash.approve), which is why that one has
# its own code and is withheld from every role that can close.
@mt_require_capability(CAP_CASH_CLOSE)
def open_cash_session():
    data = request.json or {}
    cid = _cid()
    conn = get_retail_conn()
    try:
        # Launch-readiness chain wave C1: pin-aware + validated, same order
        # as create_sale -- which branch's drawer this open belongs to must
        # follow the same till-identity rule a sale does.
        bid, branch_err = _resolve_working_branch(conn, cid, data.get('branch_id'))
        if branch_err:
            conn.close()
            return branch_err
        try:
            opening_float = _money(data.get('opening_float', 0))
        except Exception:
            conn.close()
            return jsonify({'status': 'error', 'message': 'Invalid opening float.'}), 400
        if opening_float < 0:
            conn.close()
            return jsonify({'status': 'error', 'message': 'Opening float cannot be negative.'}), 400

        # ONE OPEN DRAWER PER TERMINAL, not per branch. The branch-wide version
        # of this check is the front door of the bug this phase exists to fix:
        # it refused a second till in the same shop a drawer of its own, which
        # left that till selling into somebody else's, which is how a phone's
        # takings ended up in the desktop's Z report.
        terminal = _this_terminal()
        existing = conn.execute(
            "SELECT id FROM cash_sessions "
            f"WHERE company_id=? AND branch_id=? AND {_TERMINAL_IS} AND status='open'",
            (cid, bid, terminal)
        ).fetchone()
        if existing:
            conn.close()
            return jsonify({'status': 'error',
                            'message': 'A cash session is already open on this terminal.',
                            'data': {'session_id': existing['id'],
                                     'terminal_id': terminal,
                                     'terminal_short': _terminal_short(terminal)}}), 409

        session_id = str(_uuid.uuid4())
        now_local = _now()
        # v13 stamp. cash_sessions is in RETAIL_ACTOR_TABLES but NOT in
        # RETAIL_UID_TABLES, and correctly so: its `id` is already a
        # client-generated uuid4 (schema v10, same convention as
        # reorder_requests), so it needs no second wire identity -- adding one
        # would give the row two globally-unique names and force every consumer
        # to decide which one is canonical.
        #
        # `opened_by` keeps the local user id, untouched. `created_at_utc` sits
        # beside `opened_at`, which is _now() -- local, like every other clock
        # in this ledger.
        #
        # `terminal_id` is written from the SAME `terminal` local the
        # duplicate check above queried on, NOT from _stamp()'s second read of
        # the device identity. They are the same value in every ordinary run --
        # and "in every ordinary run" is exactly the property that makes a
        # disagreement here impossible to find later. A row scoped by one
        # reading and stamped by another is a drawer that cannot be found by
        # the terminal that owns it, which is this phase's bug wearing a
        # different hat. `_stamp()`'s terminal is deliberately discarded.
        actor, _stamp_terminal, utc_now = _stamp()
        try:
            conn.execute("""
                INSERT INTO cash_sessions (id,company_id,branch_id,opened_by,opened_at,opening_float,status,
                                           actor_user_uid,terminal_id,created_at_utc)
                VALUES (?,?,?,?,?,?,'open',?,?,?)
            """, (session_id, cid, bid, _uid(), now_local, opening_float,
                  actor, terminal, utc_now))
        except sqlite3.IntegrityError:
            # The partial UNIQUE index backstop. WHICH index fired depends on
            # whether retail v16 has landed, and the two mean different things,
            # so the refusal says which:
            #
            #   pre-v16   idx_cash_sessions_one_open_per_branch -- (company,
            #             branch). Another TERMINAL holds this branch's only
            #             drawer. The SELECT above passed because this terminal
            #             genuinely has none; the database is still enforcing
            #             the branch-wide rule Phase 4 replaces. Telling the
            #             cashier "already open on this terminal" there would
            #             be a flat lie, and would send them looking for a
            #             drawer that is not on their screen.
            #
            #   post-v16  UNIQUE(company_id, terminal_id) WHERE status='open'
            #             -- a concurrent open() from this same terminal won
            #             the race between the SELECT and this INSERT.
            conn.rollback()
            other = conn.execute(
                "SELECT id, terminal_id FROM cash_sessions "
                f"WHERE company_id=? AND branch_id=? AND status='open' AND NOT ({_TERMINAL_IS}) "
                "LIMIT 1",
                (cid, bid, terminal)
            ).fetchone()
            conn.close()
            if other is not None:
                return jsonify({
                    'status': 'error',
                    'message': ('Another terminal already has the drawer open on this branch, '
                                'and this install still enforces one drawer per branch. '
                                'Per-terminal drawers need the retail v16 migration.'),
                    'data': {'blocking_terminal_short':
                             _terminal_short(other['terminal_id'])},
                }), 409
            return jsonify({'status': 'error',
                            'message': 'A cash session is already open on this terminal.'}), 409
        _audit(conn, 'CASH_SESSION_OPENED', 'cash_session', session_id,
               f'opening_float={opening_float} terminal={_terminal_short(terminal)}')
        conn.commit()
        sess = conn.execute("SELECT * FROM cash_sessions WHERE id=?", (session_id,)).fetchone()
        conn.close()
        return jsonify({'status': 'success', 'data': _cash_session_public(sess, terminal)})
    except Exception as e:
        conn.rollback(); conn.close()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@retail_bp.route('/cash-sessions/current', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
# NOW GATED, and it used to be deliberately open ("till plumbing the POS reads
# before it can sell"). Two things changed. This response is a session row, and
# a session row carries `opening_float` and `variance` -- transacted money, by
# the runtime sweep's own vocabulary -- so "it returns state, not a report" was
# only ever true while the drawer was empty. And the answer it gives is now
# THIS TERMINAL's drawer, which is a thing only somebody who works this drawer
# has any use for. retail.cash.close is the code for exactly that, it is a
# cashier default, and the POS is driven by people who hold it. A till operator
# who does NOT hold it now gets a clean 403 the drawer bar renders as "you do
# not operate a drawer on this terminal" -- which is the truth, and is
# distinguishable from both "no drawer is open" and "could not load".
@mt_require_capability(CAP_CASH_CLOSE)
def current_cash_session():
    cid = _cid()
    conn = get_retail_conn()
    bid = int(request.args.get('branch_id') or _default_branch(conn, cid))
    terminal = _this_terminal()
    # THE ROUTE THE POS ASKS ON EVERY RENDER, and the one that used to answer
    # "the branch's open drawer" -- so a second till, which had never opened
    # anything, was shown the first till's drawer, its float, and a "Close
    # Shift (Z)" button wired to it.
    sess = conn.execute(
        "SELECT * FROM cash_sessions "
        f"WHERE company_id=? AND branch_id=? AND {_TERMINAL_IS} AND status='open'",
        (cid, bid, terminal)
    ).fetchone()
    # Counted whether or not THIS terminal has a drawer: "no drawer here, and
    # two other tills are trading" is a materially different screen from "no
    # drawer anywhere", and the difference is what tells a cashier they are
    # about to sell into nothing while the shop is open.
    others = conn.execute(
        "SELECT COUNT(*) FROM cash_sessions "
        f"WHERE company_id=? AND branch_id=? AND status='open' AND NOT ({_TERMINAL_IS})",
        (cid, bid, terminal)
    ).fetchone()[0]
    conn.close()
    return jsonify({
        'status': 'success',
        'data': _cash_session_public(sess, terminal) if sess else None,
        'terminal_id': terminal,
        'terminal_short': _terminal_short(terminal),
        'other_terminals_open': int(others or 0),
    })

@retail_bp.route('/cash-sessions', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
# Gated for the same reason `current` is, and harder: this one returns a LIST
# of drawers with `variance` and `opening_float` on every row. Ungated, it
# handed anybody who could reach the product every till's takings and every
# till's shortfall, company-wide, in one unauthenticated-except-for-login GET.
# The runtime money sweep could not see it because its fixture never opens a
# drawer, so every row it swept was empty and every money key was absent --
# a fixture that manufactured the state that hid the hole.
@mt_require_capability(CAP_CASH_CLOSE)
def list_cash_sessions():
    """This terminal's own drawer history -- widened to the whole shop for a
    caller who holds the authority to look at other tills.

    NOT a query parameter. `?scope=all` would put the decision in the URL,
    where the only thing standing between a cashier and every till's variance
    is remembering to check a string. The widening IS the capability: hold
    retail.reports (you may see the shop's numbers) or retail.cash.approve (you
    cannot accept a variance you may not look at), and you see every terminal;
    hold neither and you see the drawers this terminal worked. Every row says
    which till it belongs to either way, so the widened list is legible rather
    than a pile of uuids.
    """
    cid = _cid()
    # Clamped, not `int(...)`. The route it replaces read `int(request.args
    # .get('limit', 50))`, which SQLite reads as UNBOUNDED for `?limit=-1` and
    # which raises a 500 on `?limit=abc` -- both of which clamp_page_limit was
    # written for, and neither of which stops being true because the table is
    # cash drawers rather than sales.
    limit = clamp_page_limit(request.args.get('limit', 50), 50, 200)
    terminal = _this_terminal()
    # Read retail.cash.approve ONCE and reuse the verdict for both the gate
    # and the disclosure below -- the same fix close_cash_session's own
    # comment already argues for (~line 4111): "Calling session_has_capability()
    # twice ... is two registry reads that could disagree across a permission
    # change mid-request." Here a disagreement would be milder (a stale
    # 'may_approve' hint, not a misattributed approval) but it is the same
    # defect for the same reason, so it gets the same fix.
    may_approve = session_has_capability(CAP_CASH_APPROVE)
    all_terminals = session_has_capability(CAP_REPORTS) or may_approve
    conn = get_retail_conn()
    if all_terminals:
        rows = conn.execute(
            "SELECT * FROM cash_sessions WHERE company_id=? ORDER BY opened_at DESC LIMIT ?",
            (cid, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM cash_sessions "
            f"WHERE company_id=? AND {_TERMINAL_IS} ORDER BY opened_at DESC LIMIT ?",
            (cid, terminal, limit)
        ).fetchall()
    conn.close()
    return jsonify({
        'status': 'success',
        'data': [_cash_session_public(r, terminal) for r in rows],
        'terminal_id': terminal,
        'terminal_short': _terminal_short(terminal),
        'scope': 'all_terminals' if all_terminals else 'this_terminal',
        # So the screen can decide whether to OFFER approval rather than
        # offering it to everyone and letting the 403 explain. This is a
        # rendering hint and nothing else: /approve re-checks the capability
        # itself, because a hint the client could lie about is not a gate.
        'may_approve': may_approve,
    })

@retail_bp.route('/cash-sessions/<session_id>', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_CASH_CLOSE)
def get_cash_session(session_id):
    cid = _cid()
    conn = get_retail_conn()
    sess = conn.execute("SELECT * FROM cash_sessions WHERE id=? AND company_id=?", (session_id, cid)).fetchone()
    if not sess:
        conn.close(); return jsonify({'status': 'error', 'message': 'Cash session not found.'}), 404
    # COMPANY FIRST, TERMINAL SECOND, and the two refusals are deliberately
    # different. Another company's session is 404: its existence is not this
    # tenant's business. Another TERMINAL's session inside your own shop is
    # 403: it exists, you are simply not the till that worked it, and a 404
    # there would send a cashier hunting for a drawer their own owner can see.
    _mine, may_read = _session_read_scope(sess)
    if not may_read:
        conn.close()
        return jsonify({'status': 'error', 'message': FOREIGN_DRAWER_MESSAGE}), 403
    movements = conn.execute(
        "SELECT * FROM cash_movements WHERE session_id=? ORDER BY created_at", (session_id,)
    ).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': {
        'session': _cash_session_public(sess), 'movements': [dict(m) for m in movements],
    }})

@retail_bp.route('/cash-sessions/<session_id>/movements', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.cash_session.movement.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_CASH_CLOSE)
def create_cash_movement(session_id):
    data = request.json or {}
    cid = _cid()
    conn = get_retail_conn()
    try:
        sess = conn.execute("SELECT * FROM cash_sessions WHERE id=? AND company_id=?", (session_id, cid)).fetchone()
        if not sess:
            conn.close(); return jsonify({'status': 'error', 'message': 'Cash session not found.'}), 404
        # WRITES ARE OWN-TERMINAL ONLY, with no capability that widens them --
        # unlike the reads above, where retail.reports opens the whole shop.
        # Cash physically leaving a drawer can only be recorded by the device
        # standing at that drawer; a float_out filed from the back office
        # against a till across the room is a movement nobody witnessed, and it
        # lands in that till's expected-cash total as though somebody had. The
        # owner's authority here is to APPROVE what the till recorded, not to
        # record it on the till's behalf.
        if not _terminal_owns(sess):
            conn.close()
            return jsonify({'status': 'error', 'message': FOREIGN_DRAWER_MESSAGE}), 403
        if sess['status'] != CASH_SESSION_STATUS_OPEN:
            conn.close(); return jsonify({'status': 'error', 'message': 'Cash session is closed.'}), 409

        mtype = data.get('type')
        if mtype not in _CASH_MOVEMENT_TYPES:
            conn.close()
            return jsonify({'status': 'error',
                             'message': f"type must be one of {sorted(_CASH_MOVEMENT_TYPES)}."}), 400
        try:
            amount = _money(data.get('amount'))
        except Exception:
            conn.close()
            return jsonify({'status': 'error', 'message': 'Invalid amount.'}), 400
        if amount <= 0.005:
            conn.close()
            return jsonify({'status': 'error', 'message': 'Amount must be greater than zero.'}), 400

        movement_id = str(_uuid.uuid4())
        now_local = _now()
        # v13 stamp. Actor triple only -- `id` is already a client-generated
        # uuid4, exactly as on cash_sessions above.
        #
        # Cash moving in or out of a drawer against no transaction is, along
        # with the 'ADJ' stock adjustment, one of the two writes in this file
        # with no document behind it at all. `created_by` alone has never been
        # able to say WHICH till the money left.
        actor, terminal, utc_now = _stamp()
        conn.execute("""
            INSERT INTO cash_movements (id,session_id,type,amount,reason,created_by,created_at,
                                        actor_user_uid,terminal_id,created_at_utc)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (movement_id, session_id, mtype, amount, data.get('reason', ''), _uid(), now_local,
              actor, terminal, utc_now))
        _audit(conn, 'CASH_MOVEMENT_RECORDED', 'cash_session', session_id, f'{mtype} amount={amount}')
        conn.commit()
        movement = conn.execute("SELECT * FROM cash_movements WHERE id=?", (movement_id,)).fetchone()
        conn.close()
        return jsonify({'status': 'success', 'data': dict(movement)})
    except Exception as e:
        conn.rollback(); conn.close()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@retail_bp.route('/cash-sessions/<session_id>/x-report', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_CASH_CLOSE)
def cash_session_x_report(session_id):
    """The drawer's live money picture: cash sales, cash refunds, every
    float/paid-in/paid-out movement, expected cash and the running variance.

    GATED ON retail.cash.close, and it shipped gated on NOTHING -- see the
    block comment above, which claimed this matched the other read-only report
    routes at a point when all three of those had already moved onto a
    capability. Anyone who could reach the product could read any till's
    takings.

    retail.cash.close rather than retail.reports, deliberately. The person who
    counts the drawer is the person who closes it -- the stated reason
    cash.close is a cashier default -- and the close-out modal fetches THIS
    route to show expected-vs-counted before it will let anyone submit
    (frontend/cash-drawer.js). Putting a shift's own numbers behind a
    manager-and-above code would have gated the reading behind an authority
    the person doing the counting does not have. Matching close_cash_session's
    authority keeps the report and the act it feeds on one permission.

    WHAT PHASE 4 ADDS: that reasoning is about YOUR drawer. Carried unchanged
    onto a route that can now name a specific till, retail.cash.close -- a
    CASHIER default -- would read every other till's takings, refunds, paid-outs
    and variance by id. That is the same disclosure this route was gated to
    stop, reached through a different door. So another terminal's X report is
    a REPORT: retail.reports, or retail.cash.approve (you cannot accept a
    variance you may not look at). See _session_read_scope.

    A BRAND-NEW SHOP COULD NOT READ ITS OWN FIRST DRAWER -- gap reproduced
    against a fresh install with zero sales. `_cash_session_report` reads
    `payments.direction`/`payments.related_type`, two columns that only exist
    once `_ensure_credit_schema()` has run its lazy ALTER TABLE pass -- and
    nothing on the drawer's read path ever called it, because credit/AR-AP
    was built after cash drawers were and nobody noticed the drawer routes
    never got the same bootstrap every payment-touching route already has
    (see the many `_ensure_credit_schema(conn)` call sites elsewhere in this
    file). On a fresh `payments` table -- id/company_id/sale_id/method/
    amount/reference/status/idempotency_key/created_at/uid, no `direction`
    -- this route raised an UNCAUGHT sqlite3.OperationalError('no such
    column: p.direction'): no try/except existed here at all, so the
    JSON-consuming frontend (frontend/cash-drawer.js, which fetches this
    route to build the close-out modal) got Flask's default error response
    instead of a body it could read. Calling `_ensure_credit_schema` here,
    before the query that needs its columns, is the fix; catching the
    (now much less likely, but not impossible) failure and answering with a
    JSON envelope instead of an uncaught exception closes the rest of the
    gap -- see close_cash_session for the identical fix on the write side.
    """
    cid = _cid()
    conn = get_retail_conn()
    try:
        _ensure_credit_schema(conn)
        sess = conn.execute("SELECT * FROM cash_sessions WHERE id=? AND company_id=?", (session_id, cid)).fetchone()
        if not sess:
            return jsonify({'status': 'error', 'message': 'Cash session not found.'}), 404
        _mine, may_read = _session_read_scope(sess)
        if not may_read:
            return jsonify({'status': 'error', 'message': FOREIGN_DRAWER_MESSAGE}), 403
        report = _cash_session_report(conn, cid, sess)
        return jsonify({'status': 'success', 'data': report})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()

@retail_bp.route('/cash-sessions/<session_id>/close', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.cash_session.close", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_CASH_CLOSE)
def close_cash_session(session_id):
    """END this terminal's shift: count the drawer, lock the count.

    ── retail.cash.close AND NOTHING MORE, ON PURPOSE ───────────────────────
    Ending a shift is not the same act as accepting what the count says, and
    conflating them is how a till traps the person standing at it. A cashier
    who is $40 short must be able to finish, hand over and go home; the money
    is already gone and holding the drawer open does not bring it back. So
    this route asks for the code every cashier holds, and it NEVER refuses a
    close because of what the variance turned out to be.

    What it does instead is record whether the variance has been ACCEPTED,
    and by whom, in `status`:

      'ended'   the count is locked, the drawer is out of service, and the
                variance is UNVERIFIED. This is the ordinary outcome for a
                cashier or a manager -- retail.cash.approve is withheld from
                every role that can close, which is AUDIT-032's whole
                mechanism (see user_accounts.ROLE_CAPABILITIES).
      'closed'  the caller also holds retail.cash.approve, so the count was
                accepted in the same act by somebody entitled to accept it.
                In practice this is the shop owner, who has no second person
                to wait for. Recorded, not silent: `closed_by`/`closed_at`
                name the acceptor and the audit line says the ender approved
                their own count.

    Both are terminal for the till in exactly the way `closed` always was --
    `current` stops returning the session, movements are refused -- so nothing
    downstream that asked "is this drawer still live?" changes meaning.
    """
    data = request.json or {}
    cid = _cid()
    # Read the approval authority ONCE, before the transaction, and carry the
    # verdict. Calling session_has_capability() twice (once to choose the
    # status, once to decide what to write) is two registry reads that could
    # disagree across a permission change mid-request, and the row would then
    # say 'closed' with no acceptor recorded -- an approval attributed to
    # nobody, which is worse than no approval at all.
    may_approve = session_has_capability(CAP_CASH_APPROVE)
    conn = get_retail_conn()
    try:
        # A BRAND-NEW SHOP COULD NOT CLOSE ITS FIRST DRAWER -- reproduced
        # against a fresh install with zero sales. `_cash_session_report`
        # below (used to compute this close's own expected-cash figure)
        # reads `payments.direction`/`payments.related_type`, columns added
        # lazily by `_ensure_credit_schema()`; nothing on the drawer's write
        # path ever called it. On a fresh `payments` table this raised
        # sqlite3.OperationalError('no such column: p.direction'), caught by
        # the generic `except Exception as e` below and returned as
        # `{'message': 'no such column: p.direction'}` -- a raw SQL string in
        # a customer-facing body -- AND the drawer could never be closed at
        # all: the one promise Phase 4 makes.
        #
        # Called HERE, before BEGIN IMMEDIATE, deliberately: `_ensure_credit_
        # schema` runs its own ALTER TABLE / CREATE INDEX statements and
        # commits them (see its own docstring), and running that INSIDE this
        # route's immediate transaction would commit -- and so silently end
        # -- that transaction early, breaking the "same snapshot" guarantee
        # the BEGIN IMMEDIATE comment right below exists to make. Matches the
        # identical fix (and reasoning) in cash_session_x_report above, the
        # other route that calls `_cash_session_report`.
        _ensure_credit_schema(conn)
        # BEGIN IMMEDIATE: the expected-cash figure locked into this Z report
        # must be computed from the SAME snapshot the close actually commits
        # against -- a movement or sale racing in between "compute expected"
        # and "lock the session" must not be silently dropped from the
        # numbers this session is closed with.
        conn.execute("BEGIN IMMEDIATE")
        sess = conn.execute("SELECT * FROM cash_sessions WHERE id=? AND company_id=?", (session_id, cid)).fetchone()
        if not sess:
            conn.rollback(); conn.close()
            return jsonify({'status': 'error', 'message': 'Cash session not found.'}), 404
        # A drawer is counted by the till it belongs to. Same rule as
        # create_cash_movement, and no authority widens it: a closing count
        # filed from another device is a count of a drawer the device cannot
        # see, and it would overwrite the real one.
        if not _terminal_owns(sess):
            conn.rollback(); conn.close()
            return jsonify({'status': 'error', 'message': FOREIGN_DRAWER_MESSAGE}), 403
        if sess['status'] != CASH_SESSION_STATUS_OPEN:
            conn.rollback(); conn.close()
            return jsonify({'status': 'error', 'message': 'Cash session is already closed.'}), 409

        try:
            counted = _money(data.get('closing_float_counted'))
        except Exception:
            conn.rollback(); conn.close()
            return jsonify({'status': 'error', 'message': 'Invalid closing float counted.'}), 400
        if counted < 0:
            conn.rollback(); conn.close()
            return jsonify({'status': 'error', 'message': 'Closing float counted cannot be negative.'}), 400

        report = _cash_session_report(conn, cid, sess)
        expected = report['expected_cash']
        variance = _money(counted - expected)
        now_local = _now()
        new_status = CASH_SESSION_STATUS_CLOSED if may_approve else CASH_SESSION_STATUS_ENDED
        actor_uid = _uid()

        # WHICH COLUMNS EXIST decides how the two events are recorded, and both
        # spellings are honest about what they can prove:
        #
        #   with v16     `ended_by`/`ended_at` record the person who counted.
        #                `closed_by`/`closed_at` stay NULL unless this same
        #                caller also accepted the count, in which case both
        #                pairs are written in this one statement.
        #   without v16  there is one column pair for two events, so it records
        #                the ender -- the fact that actually happened here --
        #                and approve_cash_variance leaves it alone rather than
        #                overwriting the ender with the acceptor.
        columns = _cash_session_columns(conn)
        if 'ended_at' in columns and 'ended_by' in columns:
            conn.execute("""
                UPDATE cash_sessions SET status=?, ended_by=?, ended_at=?,
                       closed_by=?, closed_at=?,
                       closing_float_counted=?, closing_float_expected=?, variance=?
                WHERE id=?
            """, (new_status, actor_uid, now_local,
                  actor_uid if may_approve else None, now_local if may_approve else None,
                  counted, expected, variance, session_id))
        else:
            conn.execute("""
                UPDATE cash_sessions SET status=?, closed_by=?, closed_at=?,
                       closing_float_counted=?, closing_float_expected=?, variance=?
                WHERE id=?
            """, (new_status, actor_uid, now_local, counted, expected, variance, session_id))

        _audit(conn, 'CASH_SESSION_ENDED', 'cash_session', session_id,
               f'counted={counted} expected={expected} variance={variance} '
               f'status={new_status} terminal={_terminal_short(sess["terminal_id"])}')
        if may_approve:
            # Written as its OWN audit line even though it happened in the same
            # request. "The person who counted also accepted it" is the fact
            # AUDIT-032 is about, and a fact that only exists as an implication
            # of two other fields is a fact nobody will ever query for.
            _audit(conn, 'CASH_VARIANCE_APPROVED', 'cash_session', session_id,
                   f'variance={variance} approved_by={actor_uid} self_approved=1')
        conn.commit()

        sess = conn.execute("SELECT * FROM cash_sessions WHERE id=?", (session_id,)).fetchone()
        conn.close()
        report['status'] = new_status
        report['closing_float_counted'] = counted
        report['variance'] = variance
        report['variance_status'] = (
            VARIANCE_APPROVED if may_approve else VARIANCE_UNVERIFIED)
        report['self_approved'] = bool(may_approve)

        # WhatsApp shift-close report -- best-effort, never blocks or fails
        # the close that already committed above. Opens its OWN connection
        # (whatsapp_hook.queue_shift_close_report), same reasoning as the
        # e-invoicing call site in create_sale(): a failure here cannot roll
        # back or otherwise touch the response this route already built.
        try:
            branch_conn = get_retail_conn()
            branch_row = branch_conn.execute(
                "SELECT name FROM branches WHERE id=? AND company_id=?", (sess['branch_id'], cid)
            ).fetchone()
            branch_conn.close()
            _whatsapp_hook.queue_shift_close_report(
                get_retail_conn, company_id=cid, branch_id=sess['branch_id'],
                branch_name=(branch_row['name'] if branch_row else None), report=report,
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"WhatsApp shift-close report enqueue failed: {e}")

        return jsonify({'status': 'success',
                        'data': {'session': _cash_session_public(sess), 'report': report}})
    except Exception as e:
        conn.rollback(); conn.close()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@retail_bp.route('/cash-sessions/<session_id>/approve', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.cash_session.close", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
# retail.cash.approve, and this is the FIRST route in the product to carry it.
# The code has existed since the capability pass, seeded on every account and
# granted to nobody but the owner, with a comment on the eight-code map saying
# "NO ROUTE TODAY, and none is invented here: cash_sessions has only
# open/closed, with no state for 'closed, variance not yet accepted' until
# retail v16 adds the ENDED/CLOSED split". That state now exists, so the route
# does too.
#
# Withheld from ROLE_MANAGER as well as ROLE_CASHIER, deliberately, and that is
# the entire guard: a role holding this alongside retail.cash.close could count
# its own drawer and sign off its own shortfall in two clicks with nobody else
# involved. That is AUDIT-032, closed once already on the Owner side. Nothing
# below re-checks it -- the decorator IS the check, and the seeding table is
# where the property lives.
@mt_require_capability(CAP_CASH_APPROVE)
def approve_cash_variance(session_id):
    """Accept the variance an ENDED shift recorded, moving it to CLOSED.

    NOT terminal-scoped, and that asymmetry is the point of the whole split.
    Every WRITE to a live drawer is own-terminal-only, because only the device
    at the till can witness the cash. Approval is the opposite kind of act: it
    is somebody in the back office, or at another till entirely, looking at a
    count that has already been locked and saying yes. Requiring them to walk
    to the terminal would mean the only person who could approve a shortfall is
    the person standing where the shortfall happened.

    Company scoping still applies in full -- a session from another tenant is
    404, exactly as everywhere else in this file.
    """
    data = request.json or {}
    cid = _cid()
    conn = get_retail_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        sess = conn.execute("SELECT * FROM cash_sessions WHERE id=? AND company_id=?",
                            (session_id, cid)).fetchone()
        if not sess:
            conn.rollback(); conn.close()
            return jsonify({'status': 'error', 'message': 'Cash session not found.'}), 404

        status = sess['status']
        if status == CASH_SESSION_STATUS_OPEN:
            conn.rollback(); conn.close()
            return jsonify({'status': 'error',
                            'message': 'This drawer is still open. It has to be counted before '
                                       'its variance can be accepted.'}), 409
        if status == CASH_SESSION_STATUS_CLOSED:
            conn.rollback(); conn.close()
            return jsonify({'status': 'error',
                            'message': 'This variance has already been accepted.'}), 409

        # A DRAWER NOBODY COUNTED CANNOT BE RUBBER-STAMPED BY ACCIDENT.
        #
        # A force-closed session -- swept up by v16's business-date force-close,
        # or abandoned on a till that died -- has NO count and therefore an
        # UNKNOWN variance, not a variance of zero. Accepting one is a real and
        # legitimate act (the owner acknowledges the shop will never know what
        # was in that drawer), but it is a different act from accepting a
        # counted shortfall, and it must not be reachable by the same one-click
        # path. So it needs saying out loud, in the request body.
        #
        # The explicit flag is here rather than as a confirm dialog in the UI
        # because a client-side confirmation is not a guard -- this route is
        # reachable without the client.
        if _is_force_closed(sess):
            if not data.get('acknowledge_uncounted'):
                conn.rollback(); conn.close()
                return jsonify({
                    'status': 'error',
                    'message': ('This drawer was never counted, so its variance is unknown. '
                                'Accepting it records that the shop will never know what was '
                                'in it -- confirm that explicitly.'),
                    'data': {'requires': 'acknowledge_uncounted',
                             'variance_status': VARIANCE_NOT_COUNTED},
                }), 409

        actor_uid = _uid()
        now_local = _now()
        columns = _cash_session_columns(conn)
        if 'ended_at' in columns and 'ended_by' in columns:
            # v16: `closed_*` is the acceptor's slot and `ended_*` already
            # holds the person who counted, so writing here loses nothing.
            conn.execute(
                "UPDATE cash_sessions SET status=?, closed_by=?, closed_at=? WHERE id=?",
                (CASH_SESSION_STATUS_CLOSED, actor_uid, now_local, session_id))
        else:
            # Pre-v16 `closed_by`/`closed_at` are the only record of WHO ENDED
            # the shift. Overwriting them with the acceptor would destroy the
            # one identity this row has in order to store a second one -- so
            # the status moves and the acceptor is recorded in the audit trail
            # only. Stated in the response too, so no caller reads a missing
            # `approved_by` as an approval that never happened.
            conn.execute("UPDATE cash_sessions SET status=? WHERE id=?",
                         (CASH_SESSION_STATUS_CLOSED, session_id))

        _audit(conn, 'CASH_VARIANCE_APPROVED', 'cash_session', session_id,
               f'variance={sess["variance"]} approved_by={actor_uid} '
               f'self_approved={1 if _same_actor(sess, actor_uid) else 0} '
               f'uncounted={1 if _is_force_closed(sess) else 0}')
        conn.commit()
        approved = conn.execute("SELECT * FROM cash_sessions WHERE id=?", (session_id,)).fetchone()
        conn.close()
        return jsonify({'status': 'success', 'data': {
            'session': _cash_session_public(approved),
            # SURFACED, NOT BLOCKED. In a one-person shop the owner is the only
            # authority there is, and refusing a self-approval would leave that
            # shop with a drawer it can never close. In a shop with staff it is
            # information the owner wants, so it is reported here and written
            # into the audit line above rather than inferred by whoever
            # eventually reads the row.
            'self_approved': _same_actor(sess, actor_uid),
            'approver_recorded': ('ended_at' in columns and 'ended_by' in columns),
        }})
    except Exception as e:
        conn.rollback(); conn.close()
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ── Reports ───────────────────────────────────────────────────────────────────
#
# TWO RULES APPLY TO EVERY WINDOW BUILT BELOW, and they were introduced one
# wave apart, which is exactly how the second one came to be missed.
#
# 1. THE CLOCK IS READ HERE, NEVER IN metrics. Every route passes an explicit
#    `now` into metrics.period_*(), and metrics REQUIRES it -- the period
#    constructors have no default. core/retail/metrics.py used to be able to
#    read the clock itself, and a test that freezes `api.retail_api.datetime`
#    -- which is how every clock-sensitive test in this suite is written --
#    does not freeze a `datetime` imported inside metrics. A frozen-clock test
#    against such a route silently measures the real wall clock and passes for
#    the wrong reason. That is the hazard that produced the hourly-chart
#    regression.
#
# 2. THAT CLOCK IS THE SHOP'S, NOT THE DEVICE'S. `datetime.now()` is the
#    REPORTING DEVICE's wall clock. metrics buckets every row it returns onto
#    the SHOP's business date (schema v13's `created_at_utc`, converted through
#    the shop's declared IANA zone and rolled back by the trading day's start
#    hour). Feeding a device clock into a window whose rows are bucketed on the
#    shop clock puts the two one day apart whenever the shop has rolled over
#    and the device has not -- and because `period_days(n, now)` ENDS on
#    `now.date()`, a business date one day past that end falls out of the whole
#    window, however many days wide it is. Concretely: a shop three hours east
#    of the device reading it, at device-local 22:30, returned today_sales=
#    100.00 on /dashboard/stats and revenue 0.00 from /reports/summary with an
#    empty sales-trend beside it.
#
#    So the conversion -- `metrics.business_now(conn, cid, datetime.now())` --
#    is applied at EVERY window built here, not only on the dashboard. Rule 1
#    was enforced from the day it was written and rule 2 was applied to
#    dashboard_stats alone, which left five report routes plus the AI sales
#    context building their windows from the device clock against rows bucketed
#    on the shop's.
#
#    A route needing more than one period reads the clock ONCE into a local and
#    feeds that local to each constructor (see dashboard_stats) -- separate
#    reads could straddle midnight and put two figures in one response on
#    different days.
#
# Both rules are asserted by products/retail/tests/
# retail_report_clock_agreement_test.py: behaviourally, against a shop whose
# declared zone differs from the device's, and structurally, by an AST scan of
# this file that fails on the next window built from a bare `datetime.now()`.

@retail_bp.route('/reports/sales-trend', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def report_sales_trend():
    """Day-by-day net revenue. Optional `?branch_id=` scopes to one branch.

    Was gross SUM(total) with AVG(total) as the average ticket, so its day
    totals summed HIGHER than the Revenue KPI card rendered beside them on
    the same page, and its "average ticket" was a different number from the
    summary route's. Both figures now come from metrics.revenue_by_day(),
    which nets refunds into the day they were processed and derives the
    average ticket as net revenue / transactions."""
    cid       = _cid()
    days      = int(request.args.get('days', 14))
    branch_id = request.args.get('branch_id')
    conn = get_retail_conn()
    rows = metrics.revenue_by_day(conn, cid, metrics.period_days(days, metrics.business_now(conn, cid, datetime.now())), branch_id)
    conn.close()
    return jsonify({'success': True,
                    'labels': [r['day'] for r in rows],
                    'data':   [r['revenue'] for r in rows],
                    'transactions': [r['transactions'] for r in rows],
                    'avg_ticket': [r['avg_ticket'] for r in rows]})

@retail_bp.route('/reports/top-products', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def report_top_products():
    """Best sellers WITHIN `?days=` (default 30), optionally scoped with
    `?branch_id=`.

    This route had no date filter at all: it was an all-time query sitting
    on a days-scoped page, so its top product could report more revenue than
    the page's own 30-day total -- the single most visible symptom of the
    "no shared period" problem. It is now the same Period as every sibling
    widget, and its units/revenue are net of return_items."""
    cid       = _cid()
    days      = int(request.args.get('days', 30))
    limit     = int(request.args.get('limit', 10))
    branch_id = request.args.get('branch_id')
    conn  = get_retail_conn()
    rows = metrics.top_products(
        conn, cid,
        metrics.period_days(days, metrics.business_now(conn, cid, datetime.now())),
        branch_id, limit)
    conn.close()
    return jsonify({'success': True,
                    'labels': [r['name'] for r in rows],
                    'data':   [r['units_sold'] for r in rows],
                    'revenue': [r['revenue'] for r in rows],
                    'profit':  [r['profit'] for r in rows]})

@retail_bp.route('/reports/payment-methods', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def report_payment_methods():
    """Net revenue per tender. Gained `?branch_id=` here: without it the
    Reports page's branch dropdown moved the trend/top-products charts and
    left this doughnut showing every branch, on the same screen."""
    cid       = _cid()
    days      = int(request.args.get('days', 30))
    branch_id = request.args.get('branch_id')
    conn = get_retail_conn()
    rows = metrics.revenue_by_payment_method(
        conn, cid,
        metrics.period_days(days, metrics.business_now(conn, cid, datetime.now())),
        branch_id)
    conn.close()
    return jsonify({'success': True, 'data': rows})

@retail_bp.route('/reports/by-employee', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def report_by_employee():
    """Takings per employee over `?days=` (default 30), optional `?branch_id=`.

    THIS ROUTE SHIPPED AS A 404. Both clients were written against it -- the
    desktop Reports panel and the Android EmployeeSalesScreen -- and both had
    been fetching a path the url_map never carried.

    ENVELOPE: `{'status': 'success', 'success': true, 'data': [...]}`, and the
    two discriminators are deliberate rather than sloppy. Its five nearest
    neighbours (sales-trend / top-products / payment-methods / summary /
    by-branch -- the exact widgets that sit on the same Reports page) all
    return `{'success': True, ...}` with no `status` key; the other ~74
    jsonify sites in this file return `{'status': 'success', ...}`. The
    desktop hard-gates on `body.status !== 'success'` and Android
    deserializes `success`, so ONE of the two shapes would have forced an
    already-shipped client to change. Emitting both costs a key that Gson
    ignores and the desktop never looks at, and lets neither client move.
    Do not "tidy" one of them away.

    THE ROWS are metrics.revenue_by_employee()'s figures verbatim -- this
    route computes no money of its own -- plus the identity fields resolved
    from registry.db in one batched lookup (see _resolve_actor_identities).
    NO `limit`: metrics deliberately has none, because a payroll-shaped
    number that silently stops at ten people is worse than no number.

    THE UNATTRIBUTED BUCKET (actor_user_uid IS NULL -- all pre-v13 history)
    is returned as an ordinary row with all four identity fields null. Not
    filtered, not hoisted to a separate key, and above all not named: a
    server-side "Unknown"/"POS" would be an English word rendered inside an
    RTL Arabic layout AND would defeat both clients' own three-state
    classifiers. The words are the client's job. There is exactly one such
    row -- a GROUP BY guarantees it, and that is a hard contract term rather
    than a nicety, because Android's LazyColumn crashes ("Key was already
    used") on a second one.
    """
    cid = _cid()
    raw_days = request.args.get('days', 30)
    try:
        days = int(raw_days)
    except (TypeError, ValueError):
        # `{'status': 'error', 'message': ...}` and NOT a mirrored
        # `'success': False`. The dual discriminator is a success-path
        # concession to two already-shipped clients; on the error path every
        # route in this file agrees on one shape, and the desktop's own reader
        # reasons explicitly from "the error envelope carries neither
        # discriminator, so this cannot read a failure as a success".
        return jsonify({'status': 'error',
                        'message': 'days must be a whole number of days.'}), 400
    branch_id = request.args.get('branch_id')

    conn = get_retail_conn()
    # `datetime.now()` INLINE, exactly as every sibling above -- see this
    # section's header comment. metrics imports no `datetime` at all, so a
    # frozen-clock test freezes THIS name or it proves nothing. And it is
    # converted onto the shop's business day before it becomes a window,
    # because revenue_by_employee buckets its rows there (rule 2): a takings-
    # per-employee figure that silently omitted the shift currently on the
    # floor is a payroll number, not a rounding difference.
    rows = metrics.revenue_by_employee(
        conn, cid,
        metrics.period_days(days, metrics.business_now(conn, cid, datetime.now())),
        branch_id)
    conn.close()

    identities = _resolve_actor_identities(cid, [r['actor_user_uid'] for r in rows])
    data = [dict(row, **identities.get(row['actor_user_uid'], _UNRESOLVED_IDENTITY))
            for row in rows]
    return jsonify({'status': 'success', 'success': True, 'data': data})

def _compute_report_summary(conn, cid, days, branch_id=None):
    """The Reports page's KPI cards, the emailed report body and the
    WhatsApp daily summary all read this one dict -- kept as a named helper
    (rather than folding metrics.summary() straight into the routes) because
    three callers already depend on this signature. Does not close `conn` --
    same convention as every other helper in this file that takes a
    connection instead of opening its own (_settings, _record_payment, ...).

    The whole query set that used to live here now lives in
    core/retail/metrics.py, because the figures it produced were only ever
    HALF the story: this helper netted returns out and the charts drawn
    beside its numbers did not.

    TWO figures this helper returns deliberately change value with the move:
      * `cogs` (and therefore gross_profit/margin_pct) is now NET of
        returned units. It was gross, which -- against already-net revenue
        -- understated gross profit by the full cost of every refund and
        disagreed with top-products' own profit column. See metrics' #4.
      * `revenue_change` compares against a prior window of the SAME LENGTH
        as the current one (`preceding_period`). It used to be one day
        shorter -- a `days=30` request weighed 31 days of current revenue
        against 30 days of prior revenue -- biasing the figure upward on
        every report ever sent.

    THE WINDOW IS ON THE SHOP'S CLOCK (rule 2 in this section's header), and
    this helper is where that matters most, because two of its three callers
    are not a screen anybody is looking at: the emailed summary and the
    WhatsApp daily summary. A wrong figure on the Reports page is at least
    visible beside the KPI card contradicting it; the same figure sent to an
    owner's phone arrives with nothing to contradict it. Note also that the
    daily summary calls this with `days=1` -- a one-day window, where being
    off by a day reports the wrong day outright rather than merely trimming
    an end off a fortnight."""
    return metrics.summary(
        conn, cid,
        metrics.period_days(days, metrics.business_now(conn, cid, datetime.now())),
        branch_id)

@retail_bp.route('/reports/summary', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def report_summary():
    """Gained `?branch_id=`: these ARE the KPI cards, and before this they
    ignored the page's branch dropdown entirely, so picking a branch moved
    the charts underneath and left the cards on all-branches."""
    cid       = _cid()
    days      = int(request.args.get('days', 30))
    branch_id = request.args.get('branch_id')
    conn = get_retail_conn()
    data = _compute_report_summary(conn, cid, days, branch_id)
    conn.close()
    return jsonify({'success': True, 'data': data})

def _render_report_email_body(data):
    """Plain-text only, no templating engine (matches this whole feature's
    "no templating engine dependency" scope, same restraint
    smtp_client.py's own docstring names for the transport layer)."""
    lines = [
        f"Aura Retail -- {data['period_days']}-day summary report",
        "",
        f"Revenue: {data['revenue']:.2f} ({data['revenue_change']:+.1f}% vs prior period)",
        f"Transactions: {data['transactions']}",
        f"Average ticket: {data['avg_ticket']:.2f}",
        f"COGS: {data['cogs']:.2f}",
        f"Gross profit: {data['gross_profit']:.2f}",
        f"Margin: {data['margin_pct']:.1f}%",
        f"Current inventory value: {data['inventory_value']:.2f}",
    ]
    return '\n'.join(lines)

# docs/einvoicing/phase1/'s outbox pattern, mirrored for this new trigger
# (feat/email-outbox-foundation, CLAUDE.md's "New async/external-facing
# features... should follow the e-invoicing outbox pattern" convention):
# ALWAYS queued via EmailOutboxRepository, never sent inline/synchronously
# from this request -- "could be asked when the person wants" (the user's
# own framing) means on-demand at request time, not scheduled, but it is
# still the background worker (commercial_runtime/notifications/worker.py)
# that actually talks to SMTP, exactly like a sale's e-invoice is enqueued
# here and submitted later by OutboxWorker.
@retail_bp.route('/reports/email', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.report.email", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_REPORTS)
def report_summary_email():
    cid  = _cid()
    data_in = request.json or {}
    days = int(data_in.get('days', 30))
    conn = get_retail_conn()
    try:
        recipient = (data_in.get('recipient') or '').strip()
        if not recipient:
            recipient = _notification_settings.recipient_for(conn, cid, 'reports_recipient') or ''
        if not recipient:
            return jsonify({'status': 'error',
                             'message': 'No recipient provided and no default reports_recipient configured'}), 400
        if not _notification_settings.is_enabled(conn, cid):
            return jsonify({'status': 'error',
                             'message': 'Email notifications are not enabled for this company'}), 409

        summary = _compute_report_summary(conn, cid, days)
        row_id = _EmailOutboxRepository(conn).enqueue(
            company_id=cid, email_type='report_summary', recipient=recipient,
            subject=f"Aura Retail -- {days}-day summary report",
            body_text=_render_report_email_body(summary),
        )
        conn.commit()
        return jsonify({'status': 'success', 'data': {'queued': row_id is not None, 'recipient': recipient}})
    finally:
        conn.close()

# On-demand WhatsApp report send -- sibling of POST /reports/email above,
# same "queued, never sent inline" outbox pattern (commercial_runtime/
# notifications/whatsapp_worker.py drains it later, exactly like
# OutboxWorker/EmailOutboxWorker do for their own channels). Reuses the
# EXISTING "retail.report.email" capability string rather than minting a new
# one -- this is still "send a report", the channel is an implementation
# detail a license capability has no reason to distinguish.
@retail_bp.route('/reports/whatsapp', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.report.email", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_REPORTS)
def report_whatsapp():
    cid = _cid()
    data_in = request.json or {}
    report_type = data_in.get('report_type')
    if report_type not in ('daily_sales_summary', 'ar_overdue_alert'):
        return jsonify({'status': 'error',
                         'message': "report_type must be 'daily_sales_summary' or 'ar_overdue_alert'."}), 400
    conn = get_retail_conn()
    try:
        if not _whatsapp_settings.is_enabled(conn, cid):
            return jsonify({'status': 'error', 'message': 'WhatsApp reports are not enabled for this company'}), 409
        if _whatsapp_settings.template_name_for(conn, cid, report_type) is None:
            return jsonify({'status': 'error',
                             'message': f'No template name configured for {report_type}'}), 400

        if report_type == 'daily_sales_summary':
            summary = _compute_report_summary(conn, cid, 1)
            queued = _whatsapp_hook.queue_daily_sales_summary(
                conn, company_id=cid, business_name='Aura Retail', summary=summary,
            )
        else:
            # Same bucketing logic as GET /reports/aging(type='receivable') --
            # duplicated here as a handful of lines rather than refactoring
            # that route's inline query into a shared helper under time
            # pressure; see aging_report() above for the reference version
            # this must stay in sync with if that route's math ever changes.
            _ensure_credit_schema(conn)
            parties = conn.execute(
                "SELECT id, credit_balance FROM customers WHERE company_id=? AND COALESCE(credit_balance,0)>0.005",
                (cid,),
            ).fetchall()
            today = datetime.now()
            overdue_total = 0.0
            bucket_90_plus = 0.0
            customer_count = 0
            for p in parties:
                oldest = conn.execute(
                    "SELECT MIN(created_at) FROM sales WHERE company_id=? AND customer_id=? "
                    "AND (total-amount_paid)>0.005",
                    (cid, p['id']),
                ).fetchone()[0]
                days = 0
                if oldest:
                    try:
                        days = (today - datetime.strptime(str(oldest)[:10], '%Y-%m-%d')).days
                    except Exception:
                        days = 0
                if days <= 0:
                    continue  # 'current' bucket is not overdue
                bal = float(p['credit_balance'] or 0)
                overdue_total += bal
                customer_count += 1
                if days > 90:
                    bucket_90_plus += bal
            queued = _whatsapp_hook.queue_ar_overdue_alert(
                conn, company_id=cid, overdue_total=overdue_total,
                customer_count=customer_count, bucket_90_plus=bucket_90_plus,
            )

        if queued == 0:
            conn.rollback()
            return jsonify({'status': 'error',
                             'message': 'No WhatsApp recipients are subscribed to this report'}), 400
        conn.commit()
        return jsonify({'status': 'success', 'data': {'queued': queued}})
    finally:
        conn.close()

@retail_bp.route('/reports/by-branch', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def report_by_branch():
    """Revenue/transactions per branch for the Reports page's
    branch-comparison chart.

    Every active branch appears -- including ones with zero sales in the
    window -- rather than only branches that happened to sell something.
    This is what makes a comparison chart meaningful (a branch with 0
    revenue is a real, visible bar, not a silently missing one).

    Deliberately NOT filtered by the page's own `?branch_id=` selector --
    "compare branches" and "scope to one branch" are contradictory asks for
    the same chart, so this route always returns every branch regardless of
    what the branch dropdown is set to; only `days` (the shared date-range
    control) applies here. metrics.revenue_by_branch() takes no branch_id at
    all for exactly this reason -- it is the one documented exception to the
    module's branch predicate.

    The hand-rolled version this replaced carried a correlated refunds
    subquery LEFT JOINed onto `branches` and grouped by `rt.refunds` to stop
    it fanning out the aggregates -- correct, but the sort of SQL nobody
    wants to re-derive. metrics does the same netting by merging two flat
    GROUP BYs in Python, and its avg_ticket is net revenue / transactions
    rather than the AVG(s.total) this route used to report (which was gross
    AND disagreed with the summary card's average ticket)."""
    cid  = _cid()
    days = int(request.args.get('days', 30))
    conn = get_retail_conn()
    rows = metrics.revenue_by_branch(
        conn, cid,
        metrics.period_days(days, metrics.business_now(conn, cid, datetime.now())))
    conn.close()
    return jsonify({'success': True,
                    'labels':        [r['branch_name'] for r in rows],
                    'data':          [r['revenue'] for r in rows],
                    'transactions':  [r['transactions'] for r in rows],
                    'avg_ticket':    [r['avg_ticket'] for r in rows],
                    'branch_ids':    [r['branch_id'] for r in rows]})

# ── Branches ──────────────────────────────────────────────────────────────────

@retail_bp.route('/branches', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def list_branches():
    cid  = _cid()
    conn = get_retail_conn()
    rows = conn.execute("SELECT * FROM branches WHERE company_id=? AND status='active' ORDER BY name", (cid,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@retail_bp.route('/branches', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.settings.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_EMPLOYEES)
def create_branch():
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Branch name required'}), 400
    cid = _cid()
    conn = get_retail_conn()
    cur  = conn.cursor()
    # v13 `uid` -- the same wire identity _default_branch's self-healed branch
    # gets, so a branch is named identically on the wire however it came into
    # existence.
    branch_uid = _new_uid()
    cur.execute("INSERT INTO branches (company_id,name,address,phone,uid) VALUES (?,?,?,?,?)",
                (cid, data['name'], data.get('address',''), data.get('phone',''), branch_uid))
    nid = cur.lastrowid
    # Wave B: `branch` is now a synced entity type -- see
    # sync_service.py's module docstring and `_default_branch`'s own
    # identical emission for the self-healed case.
    _queue_sync_event(cur, 'branch', branch_uid, 'create', {
        'uid': branch_uid, 'name': data['name'], 'address': data.get('address', ''),
        'phone': data.get('phone', ''), 'status': 'active',
    })
    conn.commit(); conn.close()
    _sync_nudge()
    return jsonify({'status': 'success', 'data': {'id': nid}})

# ══════════════════════════════════════════════════════════════════════════════
#  CREDIT & PAYMENTS — lightweight AR/AP ledger (Phase 1)
#  • Every money movement lives in the unified `payments` ledger (the existing
#    retail payments table, extended). Customer AR / supplier AP balances are
#    maintained from it. A future double-entry GL CONSUMES these records — it does
#    not replace them (keeps POS/purchasing/customer flows untouched).
#  • Records are immutable: never edited — only voided/reversed (status flag).
#  • Money is Decimal-quantized to 2dp to avoid float drift.
#  • References are readable + sequential + searchable (SALE-/REC-/PO-/PAY-).
#  • Schema is currency-, attachment-, supplier-terms- and aging-ready without
#    forcing the UI now.
# ══════════════════════════════════════════════════════════════════════════════

def _money(x):
    try:
        return float(Decimal(str(x or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    except Exception:
        return 0.0

def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

_REF_PREFIX = {'sale': 'SALE', 'receipt': 'REC', 'po': 'PO', 'supplier_payment': 'PAY', 'return': 'RET', 'hold': 'HOLD'}

def _next_ref(conn, cid, doc_type):
    """Atomic, zero-padded, human-readable + searchable reference (e.g. REC-000053)."""
    conn.execute("INSERT OR IGNORE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,0)", (cid, doc_type))
    conn.execute("UPDATE doc_sequences SET last_no=last_no+1 WHERE company_id=? AND doc_type=?", (cid, doc_type))
    n = conn.execute("SELECT last_no FROM doc_sequences WHERE company_id=? AND doc_type=?", (cid, doc_type)).fetchone()[0]
    return f"{_REF_PREFIX.get(doc_type, doc_type.upper())}-{int(n):06d}"

#: Width of the per-device suffix `_device_doc_discriminator()` appends to a
#: newly minted sale_number/return_number. Deliberately the SAME width the
#: pre-existing per-COMPANY fragment already uses (`str(cid)[:8]`, see
#: create_sale/create_return's own comments below) -- a uuid4-derived value
#: truncated to 8 hex characters still carries ~32 bits of entropy, and a
#: single shop running even a few dozen simultaneous tills has a collision
#: probability many orders of magnitude below anything worth defending
#: against. Same argument the company fragment already relies on, restated
#: here for the device fragment rather than re-derived.
_DOC_DISCRIMINATOR_WIDTH = 8

# Populated at most once per PROCESS -- see _device_doc_discriminator's
# docstring, tier 3. Never written to disk: that tier is reached only when
# persist_doc_discriminator() itself raises (its file write failing, or an
# ImportError on a stripped Android build), so there is nothing safe to
# persist to.
_process_doc_discriminator = None


def _device_doc_discriminator():
    """Stable per-device fragment for a newly minted sale_number/return_number.

    THE ORIGINAL FIX for AUDIT-032B (two tills wedge sync permanently on the
    second till's first sale): `_next_ref` above increments `doc_sequences`,
    a table that is per-device and never synced (it has no entry in
    sync_service.py's entity_type allowlist at all). Two DEVICES on the SAME
    company therefore both start their counter at 0, and without a
    per-device fragment would both mint the identical string
    "SALE-000001-<cid8>" -- harmless until the SECOND device's sale is
    pulled by any THIRD device (or the first device pulls it back), where it
    collides with the first device's own row on `sales.sale_number`'s bare
    UNIQUE index, raises `sqlite3.IntegrityError` out of `_apply_event`, and
    wedges that device's `sync_cursor` at the failing event forever -- every
    row behind it, from every device, catalogue included, stops arriving.
    The counter itself is not the bug (it is correctly atomic and gapless
    PER DEVICE, exactly as `_next_ref`'s own docstring promises); the bug is
    that its output was never made to disambiguate BETWEEN devices, only
    between companies (see the pre-existing `str(cid)[:8]` fragment this one
    sits beside).

    AUDIT-032D -- why this is no longer just "peek, then fall back to the
    CREATING call": the first version of this fix, when `local_terminal_id()`
    returned None (a brand-new till that has never opened a drawer or logged
    in under Phase 4's terminal scoping -- `init_retail()`, and therefore a
    device's very first sale, runs before any of that), fell back to
    `local_device_uuid()` -- the CREATING twin of the exact same device
    identity file `local_terminal_id()` peeks. That call site was
    deliberately unlike every other read in this file (`_stamp()`, `_this_
    terminal()`) in that it WROTE `device/local_device.json` into existence.
    The premise ("minting a number is not an authorization question") was
    right; the conclusion was wrong, because the function it called has an
    authorization side effect downstream: Phase 4's drawer scoping
    (`_terminal_owns()` below) reads that exact same file via `local_
    terminal_id()`. A drawer OPENED on that brand-new till stamps
    `terminal_id = None` (no identity yet); ringing that shift's first sale
    then MINTED the identity mid-shift; every float/close call for the REST
    of that shift compared the session's stamped `None` against the now-
    real, freshly-materialized terminal uuid -- permanent 403, and the
    till's first shift could never be closed. Same lesson this repo already
    wrote down in docs/einvoicing/phase1/invoice-numbering-audit.md: a
    number minted for one purpose must never acquire another purpose's
    obligations.

    THE FIX: document numbering now owns its OWN persisted discriminator
    (`commercial_runtime.identity.device_context.peek_doc_discriminator()` /
    `persist_doc_discriminator()`, `<AURA_APP_DATA>/device/
    doc_discriminator.json`) -- entirely separate from `local_device.json`.
    Two tiers, each falling back to the next only when the one above
    genuinely cannot answer:

      1. `peek_doc_discriminator()` -- read-only, never creates anything.
         The common case: this device has minted at least one document
         before, so a value is already on disk.

      2. `persist_doc_discriminator(seed=local_terminal_id())` -- reached
         only when tier 1 returned None, i.e. this device's very first
         document. `local_terminal_id()` here is ALSO a read-only peek (of
         `local_device.json`, unchanged from before) -- passed only as a
         SEED for traceability when a terminal identity happens to already
         exist; when it does not (the brand-new-till case that broke drawer
         closing), `persist_doc_discriminator()` mints its own fresh uuid4
         and persists THAT, never touching `local_device.json` either way.
         Once persisted, every later call -- on this device, forever,
         including after a terminal identity eventually IS established --
         reads that SAME value back (see `persist_doc_discriminator()`'s own
         docstring for why re-deriving from a live terminal id on every call
         would be wrong: it would split one till's numbering into two
         series).

      3. A value cached in this process only. Reached only when tier 2
         itself raises (device_context's file write failing, or, on a
         stripped Android build, an ImportError). Nothing safe to persist in
         that branch, so a value that is at least stable for the life of
         THIS process is strictly better than a fresh one per call, which
         would let this SAME device mint two colliding numbers within a
         single run. A process restart on a permanently-broken install gets
         a new value each time -- an accepted, narrow residual, matching
         `local_terminal_id()`'s own documented posture on its identical
         corrupt-state case ("a real problem, but the correct place to
         surface it is device resolution, not the middle of a sale").

    Existing rows are never touched -- only the SHAPE of newly minted
    numbers changes, exactly like the pre-existing company fragment before
    it (see docs/einvoicing/phase1/invoice-numbering-audit.md's "Follow-ups"
    section for that precedent, including the grep-confirmed absence of any
    code that parses/reconstructs a sale_number by format -- re-confirmed
    for this change too).

    Never raises, and -- unlike the AUDIT-032B version of this function --
    never CREATES device identity either: matches every other identity
    helper in this file (`_stamp()`, `local_terminal_id()`, `_this_
    terminal()`) on both counts now. A sale is the business, its number is
    bookkeeping, and minting one must never manufacture state that changes
    who is allowed to close a drawer.
    """
    global _process_doc_discriminator
    try:
        from commercial_runtime.identity.device_context import (
            peek_doc_discriminator,
            persist_doc_discriminator,
        )
        value = peek_doc_discriminator()
        if not value:
            value = persist_doc_discriminator(seed=local_terminal_id())
    except Exception:
        if _process_doc_discriminator is None:
            _process_doc_discriminator = local_terminal_id() or _new_uid()
        value = _process_doc_discriminator
    return str(value)[:_DOC_DISCRIMINATOR_WIDTH]

_DEFAULT_SETTINGS = {
    'base_currency': 'USD',
    'default_credit_mode': 'none',      # none | limited | unlimited
    'default_credit_limit': '0',
    'enforce_credit_limit': 'warn',     # warn | block
    # 'after_discount' (default) | 'before_discount' -- see core/retail/pricing.py,
    # the single source of truth for what these two modes mean and compute.
    'tax_calculation_mode': tax_engine.DEFAULT_MODE,
    # ── Branding ("make the system be brandable of whatever institute or
    # coop or foundation bought it") -- short text only. See
    # _SETTINGS_BLOB_PREFIX below for the ONE branding value (the logo) that
    # deliberately does NOT live here.
    'branding_business_name': '',
    'branding_address': '',
    'branding_phone': '',
    'branding_tax_number': '',
    'branding_receipt_header': '',
    'branding_receipt_footer': '',
}
_DEFAULT_METHODS = [('Cash', 'cash'), ('Card', 'card'), ('Bank Transfer', 'bank'),
                    ('Mobile Wallet', 'wallet'), ('Check', 'check')]

# `_settings()` below is called from inside create_sale() (for base_currency)
# MULTIPLE times per sale -- see that function and _record_payment(). A
# settings value that can grow to hundreds of KB (the branding logo, stored
# as a data-URI image -- see branding_logo_set below) would therefore be
# read out of SQLite, added to this dict, and immediately discarded, on
# EVERY sale, which would silently undo the POS performance work in
# dfc0ef0/17efa5b. Any settings key that can hold more than a short string
# MUST be written under this prefix so _settings() skips it, and read back
# through its own dedicated endpoint instead -- never through this dict.
_SETTINGS_BLOB_PREFIX = 'blob_'
_BRANDING_LOGO_KEY = _SETTINGS_BLOB_PREFIX + 'branding_logo'

_CREDIT_SCHEMA_READY = False
def _ensure_credit_schema(conn):
    """Idempotent migration. Extends the existing retail tables; safe on live data."""
    global _CREDIT_SCHEMA_READY
    if _CREDIT_SCHEMA_READY:
        return
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS payment_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER,
            name TEXT, type TEXT DEFAULT 'other', is_active INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS retail_settings (
            company_id INTEGER, skey TEXT, svalue TEXT, PRIMARY KEY (company_id, skey)
        );
        CREATE TABLE IF NOT EXISTS doc_sequences (
            company_id INTEGER, doc_type TEXT, last_no INTEGER DEFAULT 0, PRIMARY KEY (company_id, doc_type)
        );
        CREATE INDEX IF NOT EXISTS idx_payments_reference ON payments(reference);
    """)
    def addcol(table, col, decl):
        try:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        except Exception:
            pass
    # Extend the unified payments ledger (the existing retail payments table).
    for col, decl in [
        ('party_type', "TEXT"), ('party_id', "INTEGER"), ('direction', "TEXT"),
        ('currency', "TEXT DEFAULT 'USD'"), ('fx_rate', "REAL DEFAULT 1"),
        ('related_type', "TEXT"), ('related_id', "INTEGER"), ('notes', "TEXT"),
        ('reversal_of', "INTEGER"), ('voided_by', "TEXT"), ('voided_at', "TEXT"),
        ('created_by', "TEXT"), ('device', "TEXT"),
    ]:
        addcol('payments', col, decl)
    # schema.py v21 (_migrate_add_lookup_indexes) carries the identical
    # CREATE INDEX for every install that already has these two columns by
    # the time it runs. A BRAND NEW install does not -- this file's
    # migration chain runs before this function is ever called, so
    # party_type/party_id do not exist yet at that point -- and this is the
    # only place that catches that case, immediately after the ADD COLUMN
    # pair just above created them. IF NOT EXISTS, so whichever of the two
    # sites runs first wins and the other is a no-op.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_payments_party ON payments(party_type, party_id)"
    )
    addcol('customers', 'credit_mode', "TEXT DEFAULT 'none'")   # none | limited | unlimited
    addcol('customers', 'credit_limit', "REAL DEFAULT 0")
    addcol('customers', 'credit_balance', "REAL DEFAULT 0")
    addcol('suppliers', 'payment_terms', "TEXT DEFAULT 'none'") # none | net7 | net15 | net30 | custom
    addcol('suppliers', 'credit_balance', "REAL DEFAULT 0")
    addcol('sales', 'due_date', "TEXT")
    addcol('purchase_orders', 'amount_paid', "REAL DEFAULT 0")
    addcol('purchase_orders', 'payment_status', "TEXT DEFAULT 'unpaid'")  # paid | partial | unpaid/credit
    addcol('purchase_orders', 'due_date', "TEXT")
    # Wave 0 (AUDIT-004): returns need the same duplicate-submission protection
    # sales already had. SQLite can't add a UNIQUE column via ALTER TABLE, so a
    # partial unique index does the job instead (NULLs -- pre-Wave-0 rows -- are
    # exempt, matching sqlite's own UNIQUE-column NULL semantics).
    addcol('returns', 'idempotency_key', "TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_returns_idempotency "
        "ON returns(idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    conn.commit()
    _CREDIT_SCHEMA_READY = True

def _settings(conn, cid):
    s = dict(_DEFAULT_SETTINGS)
    for r in conn.execute("SELECT skey,svalue FROM retail_settings WHERE company_id=?", (cid,)).fetchall():
        # See _SETTINGS_BLOB_PREFIX's comment above -- this dict is rebuilt
        # on every call, including several times per create_sale(), so a
        # blob-shaped value (the branding logo) must never reach it. Any
        # future settings key that can hold more than a short string needs
        # the same prefix, not just this one.
        if r['skey'].startswith(_SETTINGS_BLOB_PREFIX):
            continue
        s[r['skey']] = r['svalue']
    return s

def _seed_methods(conn, cid):
    if conn.execute("SELECT COUNT(*) FROM payment_methods WHERE company_id=?", (cid,)).fetchone()[0] == 0:
        for i, (name, typ) in enumerate(_DEFAULT_METHODS):
            conn.execute("INSERT INTO payment_methods (company_id,name,type,is_active,sort_order) VALUES (?,?,?,1,?)",
                         (cid, name, typ, i))

def _record_payment(conn, cid, party_type, party_id, direction, amount, method='cash',
                    related_type=None, related_id=None, notes='', device=None, doc_type='receipt'):
    """Append one immutable money-movement row to the ledger. Returns its reference."""
    amt = _money(amount)
    if amt <= 0:
        return None
    cur = _settings(conn, cid)['base_currency']
    ref = _next_ref(conn, cid, doc_type)
    # v13 `uid` only. `payments` is in RETAIL_UID_TABLES but NOT in
    # RETAIL_ACTOR_TABLES -- it already carries `created_by` AND `device` of
    # its own (the AR/AP ledger shipped with both), so v13 had no missing
    # who/where to add here; what it lacked was a name a peer device could use.
    #
    # One helper, every money movement: a sale's retained cash, a customer
    # payment, a supplier payment, a PO down-payment and a void's reversal all
    # funnel through this single INSERT, so stamping it once covers the whole
    # ledger and no route can write an unnamed payment by going around it.
    pay_uid = _new_uid()
    created_by = _uid()
    created_at = _now()
    sale_id_fk = related_id if related_type == 'sale' else None
    conn.execute("""INSERT INTO payments
        (company_id,reference,party_type,party_id,direction,amount,currency,fx_rate,method,
         related_type,related_id,sale_id,notes,status,created_by,device,created_at,uid)
        VALUES (?,?,?,?,?,?,?,1,?,?,?,?,?, 'active', ?, ?, ?, ?)""",
        (cid, ref, party_type, party_id, direction, amt, cur, method,
         related_type, related_id, sale_id_fk,
         notes, created_by, device, created_at, pay_uid))

    # ── Phase 5 sync emission: the payment itself ────────────────────────────
    # ONE funnel, every money movement (see this function's own comment
    # above) -- a sale's retained cash, a customer/supplier account payment,
    # a PO down-payment. Every one of them gets exactly one sync event here,
    # rather than each call site having to remember to queue its own.
    #
    # `sale_uid` is resolved ONLY when this payment is FK-tied to a sale
    # (`related_type == 'sale'`, e.g. create_sale's own retained-cash entry)
    # -- `payments.sale_id REFERENCES sales(id)` is the ONE real FK this
    # table declares (related_id/related_type carry no FK at all, e.g. a PO
    # down-payment's related_id, so they are passed through as-is, an opaque
    # local-only reference exactly like branch_id elsewhere in this phase).
    # The apply side resolves the real local `sales.id` by looking up
    # `sales.uid = sale_uid`, exactly like sale_item/return resolve theirs.
    sale_uid = None
    if related_type == 'sale' and related_id:
        row = conn.execute("SELECT uid FROM sales WHERE id=? AND company_id=?", (related_id, cid)).fetchone()
        sale_uid = row['uid'] if row else None
    _queue_sync_event(conn, 'payment', pay_uid, 'create', {
        'uid': pay_uid, 'reference': ref, 'party_type': party_type, 'party_id': party_id,
        'direction': direction, 'amount': amt, 'currency': cur, 'method': method,
        'related_type': related_type, 'related_id': related_id, 'sale_uid': sale_uid,
        'notes': notes, 'status': 'active', 'created_by': created_by, 'device': device,
        'created_at': created_at,
    })
    return ref

def _adjust_credit(conn, table, pid, cid, delta):
    """Decimal-safe balance update (avoids SQL float accumulation). Returns new balance.

    launch-readiness Phase 6 stage 6a-i: `credit_balance` is a SECOND
    accumulator column, on both `customers` and `suppliers`, found by
    searching every UPDATE against these five tables rather than trusting
    the design doc's own count -- it is not in SYNCED_CUSTOMER_FIELDS or
    SYNCED_SUPPLIER_FIELDS above, sync_service.py's customer/supplier apply
    branches never mention it, and this function's own callers never queue
    a catalogue sync event around this call. Structurally identical to
    total_spent/loyalty_points at create_sale's loyalty-accumulator UPDATE
    (see that site's own comment): device-local, never synced, and this
    UPDATE deliberately touches ONLY `credit_balance`, so it correctly stays
    outside every row_version bump this stage adds -- there is nothing to
    change here, and that omission is the point, not an oversight.
    """
    row = conn.execute(f"SELECT credit_balance FROM {table} WHERE id=? AND company_id=?", (pid, cid)).fetchone()
    base = Decimal(str(row['credit_balance'] if row and row['credit_balance'] is not None else 0))
    newbal = (base + Decimal(str(delta))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    conn.execute(f"UPDATE {table} SET credit_balance=? WHERE id=? AND company_id=?", (float(newbal), pid, cid))
    return float(newbal)

# ── Settings (credit / currency) ──────────────────────────────────────────────
@retail_bp.route('/settings/credit', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def credit_settings_get():
    cid = _cid(); conn = get_retail_conn(); _ensure_credit_schema(conn)
    s = _settings(conn, cid); conn.close()
    return jsonify({'status': 'success', 'data': s})

@retail_bp.route('/settings/credit', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.settings.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_EMPLOYEES)
def credit_settings_set():
    cid = _cid(); data = request.json or {}
    conn = get_retail_conn(); _ensure_credit_schema(conn)
    for k in ('base_currency', 'default_credit_mode', 'default_credit_limit', 'enforce_credit_limit'):
        if k in data:
            conn.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?) "
                         "ON CONFLICT(company_id,skey) DO UPDATE SET svalue=excluded.svalue",
                         (cid, k, str(data[k])))
    conn.commit(); s = _settings(conn, cid); conn.close()
    return jsonify({'status': 'success', 'data': s})

# ── Settings (tax calculation policy) ─────────────────────────────────────────
# See core/retail/pricing.py -- the single source of truth for what these two
# modes mean and compute. This setting is read by the POS cart on load
# (static/js/subsystem-retail.js) so its live per-keystroke recalculation
# matches whatever the company has configured here.
@retail_bp.route('/settings/tax', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def tax_settings_get():
    cid = _cid(); conn = get_retail_conn(); _ensure_credit_schema(conn)
    mode = _settings(conn, cid).get('tax_calculation_mode', tax_engine.DEFAULT_MODE)
    conn.close()
    return jsonify({'status': 'success', 'data': {
        'tax_calculation_mode': tax_engine.normalize_mode(mode),
        'available_modes': list(tax_engine.VALID_MODES),
    }})

@retail_bp.route('/settings/tax', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.settings.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_EMPLOYEES)
def tax_settings_set():
    cid = _cid(); data = request.json or {}
    if 'tax_calculation_mode' not in data:
        return jsonify({'status': 'error', 'message': 'tax_calculation_mode is required'}), 400
    mode = tax_engine.normalize_mode(data.get('tax_calculation_mode'))
    conn = get_retail_conn(); _ensure_credit_schema(conn)
    conn.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?) "
                 "ON CONFLICT(company_id,skey) DO UPDATE SET svalue=excluded.svalue",
                 (cid, 'tax_calculation_mode', mode))
    conn.commit(); conn.close()
    try:
        from commercial_runtime.security.audit import record as _sec_audit
        _sec_audit(cid, _uid(), 'RETAIL_TAX_MODE_CHANGED', entity_type='SETTINGS',
                   context={'tax_calculation_mode': mode})
    except Exception:
        pass
    return jsonify({'status': 'success', 'data': {'tax_calculation_mode': mode}})

# ── Settings (this device's branch pin) ────────────────────────────────────────
# Launch-readiness chain wave C1 (ROADMAP.md's 2026-08-30 "the multi-branch
# capture defect" entry; docs/launch-readiness/seats-and-chain-design.md
# §5.2 gap 1). Which branch a till physically stands in is not a company-wide
# setting -- it is a fact about THIS device, so it is stored in config.json
# (see onboarding_routes.get_device_branch_uid/set_device_branch_uid), never
# in `retail_settings` (company-scoped, and would converge across every
# device on the licence the moment sync ran).

@retail_bp.route('/device/branch', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_SELL)
def get_device_branch():
    """The till's own read -- CAP_SELL, matching list_active_promotions'
    identical reasoning: a cashier needs to see which branch this till is
    pinned to (or that it is UNPINNED, the state that silently produces
    wrong data on a chain -- see _resolve_working_branch's own docstring)
    even without CAP_EMPLOYEES. No @require_license_capability, matching
    list_active_promotions/list_products: a plain read the till needs to
    function, not a mutation."""
    cid = _cid()
    conn = get_retail_conn()
    branch_uid = _onboarding_get_device_branch_uid()
    row = None
    if branch_uid:
        row = conn.execute(
            "SELECT id, name FROM branches WHERE uid=? AND company_id=?",
            (branch_uid, cid),
        ).fetchone()
    conn.close()
    # `row` is None both when unpinned and when the pin does not (yet)
    # resolve locally -- in the second case the pin is still reported back
    # so the settings screen can show "pinned to a branch not seen on this
    # device yet" rather than silently looking unpinned (this route never
    # falls back to _resolve_working_branch/_default_branch; a read must
    # not self-heal a branch as a side effect of merely loading a screen).
    return jsonify({'status': 'success', 'data': {
        'branch_uid': branch_uid,
        'branch_id': row['id'] if row else None,
        'branch_name': row['name'] if row else None,
    }})


@retail_bp.route('/device/branch', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.settings.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_EMPLOYEES)
def set_device_branch():
    """Pins (or, given a null/empty `branch_uid`, clears) this device's
    working branch. Administrative act -- CAP_EMPLOYEES, matching
    tax_settings_set/credit_settings_set's identical settings-write gate --
    validates the uid belongs to a branch of THIS company before writing,
    the same cross-tenant guard _resolve_working_branch applies to every
    other by-id write in this file."""
    data = request.json or {}
    uid = (data.get('branch_uid') or '').strip() or None
    cid = _cid()
    conn = get_retail_conn()
    try:
        branch_row = None
        if uid:
            branch_row = conn.execute(
                "SELECT id, name FROM branches WHERE uid=? AND company_id=?",
                (uid, cid),
            ).fetchone()
            if not branch_row:
                return jsonify({'status': 'error', 'message': f'Unknown branch_uid: {uid}'}), 400
        _onboarding_set_device_branch_uid(uid)
        _audit(conn, 'DEVICE_BRANCH_PINNED' if uid else 'DEVICE_BRANCH_UNPINNED',
               'branch', branch_row['id'] if branch_row else None,
               f"branch_uid={uid!r}" + (f" name={branch_row['name']!r}" if branch_row else ""))
        conn.commit()
    finally:
        conn.close()
    return get_device_branch()

# ── Settings (the shop's own clock) ───────────────────────────────────────────
#
# WHY THIS ROUTE EXISTS. `core/retail/metrics.py` decides which trading DAY
# every sale in the shop's history belongs to by reading two keys out of
# `retail_settings`: `business_timezone` and `business_day_start_hour`.
# Nothing in this repository has ever WRITTEN either of them. They are not in
# `/settings/credit`'s allowlist and not in `/settings/tax`'s, so the only way
# to set them was to edit retail.db by hand -- which means that on every
# install in the field the shop clock is permanently undeclared and the whole
# business-date feature is unreachable code. A read path with no write path is
# a feature nobody has.
#
# MODELLED ON tax_settings_set, NOT credit_settings_set. The credit route
# writes `str(data[k])` straight through with no validation at all; the tax
# route validates through the module that will later READ the value
# (`tax_engine.normalize_mode`) and records a security-audit row. This setting
# is the more consequential of the two -- changing it re-files revenue between
# days -- so it follows the stricter sibling.
#
# VALIDATION LIVES IN metrics, NOT HERE. `metrics.parse_business_zone` is
# public and its docstring says outright that this route must use it. The
# reader is deliberately TOLERANT (an unparseable value logs a warning and
# leaves the shop unconfigured, because a report is still worth drawing); the
# writer must be STRICT, because silently degrading a value an owner just
# typed into a settings screen leaves them believing the shop is on Amman time
# when it is not. Tolerant read + strict write is only safe while both consult
# ONE definition of "valid" -- a private zone check here would be a second one,
# and read-side tolerance drifting from write-side rejection is exactly how a
# settings screen ends up accepting a value no report can use.

def _timezone_database_available():
    """Whether this install can resolve ANY IANA zone.

    Probed through `parse_business_zone` itself rather than through
    `zoneinfo.available_timezones()` so there is still only one definition of
    "resolvable" -- and 'UTC' is the probe because it exists in every real tz
    database, so failing on it means the DATABASE is missing, not the key.

    THIS IS NOT PARANOIA. `zoneinfo` is stdlib and always imports, but the
    data it reads is not bundled with CPython: Windows ships no
    /usr/share/zoneinfo, `tzdata` is in none of requirements/*.txt, it is not
    in the Chaquopy pip block for the Android build, and the PyInstaller spec
    does not collect it. On all three of those targets, TODAY, every zone name
    fails. Answering that with "Asia/Amman is not a valid timezone" reads like
    a typo in the settings screen and sends whoever is debugging it to
    entirely the wrong place, so the two failures get different status codes
    and different words.
    """
    return metrics.parse_business_zone('UTC') is not None


@retail_bp.route('/settings/business-day', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def business_day_settings_get():
    """What the REPORTS are actually using -- not merely what is stored.

    A value that is in the table but cannot be resolved is reported as
    undeclared, because that is the honest answer about the shop's clock; the
    `timezone_database_available` flag is what tells a settings screen whether
    the cause is the value or the install, so it can say so instead of
    offering a field that silently cannot be saved."""
    cid = _cid()
    conn = get_retail_conn(); _ensure_credit_schema(conn)
    boundary = metrics.business_day(conn, cid)
    conn.close()
    return jsonify({'status': 'success', 'data': {
        'business_timezone': getattr(boundary.zone, 'key', None),
        'business_day_start_hour': boundary.day_start_hour,
        'timezone_database_available': _timezone_database_available(),
    }})


@retail_bp.route('/settings/business-day', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.settings.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_EMPLOYEES)
def business_day_settings_set():
    """Declare (or clear) the shop's timezone and the hour its day begins.

    Explicit `null` CLEARS a key rather than storing an empty string, and
    clearing means "not declared" -- which is NOT the same as UTC, and not the
    same as midnight-anchored-by-accident. `ZoneInfo('UTC')` is a real zone; a
    settings screen that had to write it to mean "unset" would silently
    re-file the trading history of every shop that merely stopped declaring.
    """
    cid = _cid(); data = request.json or {}
    known = ('business_timezone', 'business_day_start_hour')
    present = [k for k in known if k in data]
    if not present:
        return jsonify({'status': 'error',
                        'message': f'one of {", ".join(known)} is required'}), 400

    writes = []   # (skey, svalue-or-None-to-clear)

    if 'business_timezone' in data:
        raw = data['business_timezone']
        if raw is None or not str(raw).strip():
            writes.append((metrics.BUSINESS_TIMEZONE_SETTING, None))
        else:
            zone = metrics.parse_business_zone(raw)
            if zone is None:
                if not _timezone_database_available():
                    # 503, not 400: nothing is wrong with what they typed, and
                    # telling them it is would send them to fix the wrong
                    # thing. The install is missing a package.
                    return jsonify({'status': 'error', 'message': (
                        'This installation has no timezone database, so no timezone can be '
                        'validated or saved. Install the `tzdata` package on the server '
                        '(it is not bundled with Python on Windows or Android). This is an '
                        'environment problem, not a problem with the value entered.')}), 503
                return jsonify({'status': 'error', 'message': (
                    f'{raw!r} is not an IANA timezone name. Expected something like '
                    f'"Asia/Amman" -- a fixed offset ("+02:00"), an abbreviation ("EET") '
                    f'or a number is not a timezone.')}), 400
            # The zone's OWN canonical key, never the raw text: it round-trips
            # through parse_business_zone by construction, so what is stored
            # is exactly what the reader will resolve.
            writes.append((metrics.BUSINESS_TIMEZONE_SETTING,
                           getattr(zone, 'key', None) or str(raw).strip()))

    if 'business_day_start_hour' in data:
        raw = data['business_day_start_hour']
        if raw is None or not str(raw).strip():
            writes.append((metrics.BUSINESS_DAY_START_SETTING, None))
        else:
            try:
                hour = int(str(raw).strip())
            except ValueError:
                hour = None
            if hour is None or not 0 <= hour <= 23:
                return jsonify({'status': 'error', 'message': (
                    f'{raw!r} is not an hour of the day. Expected a whole number 0-23.')}), 400
            # Cross-check against the READER before storing. metrics'
            # `_day_start_hour` is deliberately tolerant (it falls back to
            # midnight rather than refusing to draw a report), so it cannot be
            # used AS the validator the way parse_business_zone can -- but it
            # can be used to prove the strict check above still agrees with it.
            # If metrics ever narrows its range, this fails loudly here rather
            # than accepting a value the reader will quietly discard.
            if metrics._day_start_hour(str(hour)) != hour:
                log.error("retail settings: the business-day writer accepted hour %r but "
                          "metrics reads it back as %r -- the two definitions have drifted",
                          hour, metrics._day_start_hour(str(hour)))
                return jsonify({'status': 'error', 'message': (
                    f'{raw!r} is not an hour of the day. Expected a whole number 0-23.')}), 400
            writes.append((metrics.BUSINESS_DAY_START_SETTING, str(hour)))

    conn = get_retail_conn(); _ensure_credit_schema(conn)
    for skey, svalue in writes:
        if svalue is None:
            conn.execute("DELETE FROM retail_settings WHERE company_id=? AND skey=?", (cid, skey))
        else:
            conn.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?) "
                         "ON CONFLICT(company_id,skey) DO UPDATE SET svalue=excluded.svalue",
                         (cid, skey, svalue))
    conn.commit()
    boundary = metrics.business_day(conn, cid)
    conn.close()

    effective = {'business_timezone': getattr(boundary.zone, 'key', None),
                 'business_day_start_hour': boundary.day_start_hour}
    try:
        from commercial_runtime.security.audit import record as _sec_audit
        # Audited for the same reason the tax mode is, only more so: this
        # decides which trading day every figure in the shop's history is
        # counted on, so "the numbers changed and nobody touched a sale" has
        # to have an answer.
        _sec_audit(cid, _uid(), 'RETAIL_BUSINESS_DAY_CHANGED', entity_type='SETTINGS',
                   context=dict(effective))
    except Exception:
        pass
    return jsonify({'status': 'success', 'data': effective})

# ── Settings (branding) ───────────────────────────────────────────────────────
# The product owner's ask, verbatim: "Make the system be brandable of
# whatever institute or coop or foundation bought it -- for the invoice and
# the other stuff that you see suitable." subsystem-retail.js used to print
# the literal string "Aura Retail" on every receipt, with no shop name,
# address or tax number anywhere on it -- a fresh install could not print a
# receipt legally adequate for its OWN shop, let alone a co-op's brand.
# retail_settings is already a per-company key/value table (_DEFAULT_SETTINGS
# above), so this is new keys, never a schema change.
_BRANDING_TEXT_KEYS = (
    'branding_business_name', 'branding_address', 'branding_phone',
    'branding_tax_number', 'branding_receipt_header', 'branding_receipt_footer',
)

@retail_bp.route('/settings/branding', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def branding_settings_get():
    """No @mt_require_capability -- matches tax_settings_get, not
    credit_settings_set's write-side gate. A cashier who is not the admin
    device still has to print a branded receipt, so the READ side of
    branding cannot sit behind the Admin Center's device gate even though
    writing it does (see branding_settings_set below)."""
    cid = _cid(); conn = get_retail_conn(); _ensure_credit_schema(conn)
    s = _settings(conn, cid)
    has_logo = conn.execute(
        "SELECT 1 FROM retail_settings WHERE company_id=? AND skey=?",
        (cid, _BRANDING_LOGO_KEY),
    ).fetchone() is not None
    # E-invoicing seller identity -- READ-ONLY, DISPLAY ONLY. Never written,
    # never reconciled with branding_business_name/branding_tax_number above.
    # commercial_runtime/einvoicing/settings.py's seller_name/seller_tin are
    # a SECOND source of truth for "the shop's name and tax number" -- the
    # identity JoFotara actually submits to the Jordanian tax authority --
    # and two of them silently disagreeing is a compliance question for the
    # shop to resolve deliberately, not a sync bug for this route to paper
    # over. Shown only once the company has actually started configuring it
    # (either field non-empty); an install that has never touched
    # e-invoicing gets no block at all. This is a plain read through that
    # module's own public get_setting() -- the same retail_conn, since
    # products/retail/backend/app.py wires einvoicing's conn_factory to
    # get_retail_conn -- never a write, never a touch of that module itself.
    einvoicing_seller = None
    try:
        from commercial_runtime.einvoicing import settings as _einvoicing_settings
        seller_name = _einvoicing_settings.get_setting(conn, cid, 'seller_name')
        seller_tin = _einvoicing_settings.get_setting(conn, cid, 'seller_tin')
        if seller_name or seller_tin:
            einvoicing_seller = {'seller_name': seller_name, 'seller_tin': seller_tin}
    except Exception:
        # Best-effort only, same discipline as the frontend's own
        # _einvoiceReceiptBlock try/except: a broken read of an OPTIONAL,
        # OFF-BY-DEFAULT feature's settings must never break the branding
        # screen or a receipt for every shop that has never touched
        # e-invoicing.
        einvoicing_seller = None
    conn.close()
    data = {k: s[k] for k in _BRANDING_TEXT_KEYS}
    data['has_logo'] = has_logo
    data['einvoicing_seller'] = einvoicing_seller
    return jsonify({'status': 'success', 'data': data})

@retail_bp.route('/settings/branding', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.settings.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_EMPLOYEES)
def branding_settings_set():
    cid = _cid(); data = request.json or {}
    conn = get_retail_conn(); _ensure_credit_schema(conn)
    for k in _BRANDING_TEXT_KEYS:
        if k in data:
            conn.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?) "
                         "ON CONFLICT(company_id,skey) DO UPDATE SET svalue=excluded.svalue",
                         (cid, k, str(data[k] or '')))
    conn.commit(); s = _settings(conn, cid); conn.close()
    try:
        from commercial_runtime.security.audit import record as _sec_audit
        _sec_audit(cid, _uid(), 'RETAIL_BRANDING_UPDATED', entity_type='SETTINGS',
                   context={k: s.get(k, '') for k in _BRANDING_TEXT_KEYS})
    except Exception:
        pass
    return jsonify({'status': 'success', 'data': {k: s[k] for k in _BRANDING_TEXT_KEYS}})

# ── Settings (branding logo) ────────────────────────────────────────────────
# Deliberately its own endpoint, never folded into branding_settings_get/set
# above -- see _SETTINGS_BLOB_PREFIX's comment on _settings(). Read only when
# the shell renders the sidebar or a receipt actually prints, never as a
# side effect of any other settings read.
#
# 300KB decoded is generous for a receipt/thermal-printer logo (typically
# rendered under 25mm wide) and small enough that a shop pasting in a
# multi-megabyte photo gets told so in plain language, rather than the
# receipt silently failing to print or the settings screen hanging on a
# giant payload.
_MAX_LOGO_BYTES = 300 * 1024

# Anchored, and restricted to the base64 alphabet plus '=' padding --
# deliberately stricter than "starts with data:image/ and contains
# ;base64,". A logo is operator-entered content that later gets embedded in
# an <img src="..."> on the printed receipt; a looser check would let a
# crafted value break out of that attribute (e.g. a subtype string carrying
# a literal `"`) and this repo has XSS tests specifically because a name
# once reached the page unescaped. Restricting the whole value to a known
# image MIME subtype and the base64 alphabet makes that a non-issue at the
# source, on top of subsystem-retail.js still escaping it before it reaches
# innerHTML.
_LOGO_DATA_URI_RE = re.compile(r'^data:image/(png|jpe?g|gif|webp);base64,([A-Za-z0-9+/]+={0,2})$')

def _decoded_logo_size(logo):
    """Decoded byte length of a validated `data:image/...;base64,<payload>`
    string, or None if `logo` doesn't match that shape at all."""
    m = _LOGO_DATA_URI_RE.match(logo)
    if not m:
        return None
    payload = m.group(2)
    return (len(payload) * 3 // 4) - payload.count('=')

@retail_bp.route('/settings/branding/logo', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def branding_logo_get():
    """Same read-side reasoning as branding_settings_get: any logged-in
    retail user, not just an admin device -- printing a receipt is not an
    admin-only action."""
    cid = _cid(); conn = get_retail_conn(); _ensure_credit_schema(conn)
    row = conn.execute(
        "SELECT svalue FROM retail_settings WHERE company_id=? AND skey=?",
        (cid, _BRANDING_LOGO_KEY),
    ).fetchone()
    conn.close()
    return jsonify({'status': 'success', 'data': {'logo': row['svalue'] if row else None}})

@retail_bp.route('/settings/branding/logo', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.settings.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_EMPLOYEES)
def branding_logo_set():
    cid = _cid(); data = request.json or {}
    logo = (data.get('logo') or '').strip()
    if not logo:
        return jsonify({'status': 'error', 'message': 'No logo image provided.'}), 400
    size = _decoded_logo_size(logo)
    if size is None:
        return jsonify({'status': 'error', 'message':
                         'Logo must be a PNG, JPEG, GIF or WebP image (as a data URL).'}), 400
    if size > _MAX_LOGO_BYTES:
        return jsonify({'status': 'error', 'message': (
            f'This logo is {size // 1024}KB, over the {_MAX_LOGO_BYTES // 1024}KB limit. '
            f'Choose a smaller image.')}), 400
    conn = get_retail_conn(); _ensure_credit_schema(conn)
    conn.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?) "
                 "ON CONFLICT(company_id,skey) DO UPDATE SET svalue=excluded.svalue",
                 (cid, _BRANDING_LOGO_KEY, logo))
    conn.commit(); conn.close()
    return jsonify({'status': 'success', 'data': {'has_logo': True}})

@retail_bp.route('/settings/branding/logo', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.settings.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_EMPLOYEES)
def branding_logo_delete():
    cid = _cid(); conn = get_retail_conn(); _ensure_credit_schema(conn)
    conn.execute("DELETE FROM retail_settings WHERE company_id=? AND skey=?", (cid, _BRANDING_LOGO_KEY))
    conn.commit(); conn.close()
    return jsonify({'status': 'success', 'data': {'has_logo': False}})

# ── Configurable payment methods ──────────────────────────────────────────────
@retail_bp.route('/payment-methods', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def payment_methods_list():
    cid = _cid(); conn = get_retail_conn(); _ensure_credit_schema(conn); _seed_methods(conn, cid); conn.commit()
    rows = conn.execute("SELECT id,name,type,is_active,sort_order FROM payment_methods "
                        "WHERE company_id=? AND is_active=1 ORDER BY sort_order,name", (cid,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@retail_bp.route('/payment-methods', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.settings.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_EMPLOYEES)
def payment_methods_add():
    cid = _cid(); data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Method name required'}), 400
    conn = get_retail_conn(); _ensure_credit_schema(conn)
    conn.execute("INSERT INTO payment_methods (company_id,name,type,is_active,sort_order) VALUES (?,?,?,1,?)",
                 (cid, data['name'], data.get('type', 'other'), int(data.get('sort_order', 99))))
    conn.commit(); conn.close()
    return jsonify({'status': 'success'})

# ── Customer credit (Accounts Receivable) ─────────────────────────────────────
@retail_bp.route('/customers/receivables', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def customers_receivables():
    cid = _cid(); conn = get_retail_conn(); _ensure_credit_schema(conn)
    rows = conn.execute("SELECT id,name,phone,credit_mode,credit_limit,credit_balance FROM customers "
                        "WHERE company_id=? AND COALESCE(credit_balance,0) > 0.005 ORDER BY credit_balance DESC", (cid,)).fetchall()
    # Must use the same >0.005 filter as the rows query above. Without it, a
    # customer with a negative (overpaid) credit_balance -- reachable because
    # customer_payment() below only validates amount>0 and never caps it at
    # the outstanding balance -- silently nets against and understates the
    # genuine receivables of every other customer, so total_receivable no
    # longer equals the sum of the rows actually shown to the user.
    total = conn.execute("SELECT COALESCE(SUM(credit_balance),0) FROM customers "
                         "WHERE company_id=? AND COALESCE(credit_balance,0) > 0.005", (cid,)).fetchone()[0]
    conn.close()
    return jsonify({'status': 'success', 'total_receivable': _money(total), 'data': [dict(r) for r in rows]})

@retail_bp.route('/customers/<string:cust_id>/statement', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
# Deliberately UNGATED, and deliberately asymmetric with supplier_statement
# below, which does carry retail.reports. This is ONE named customer's
# ledger, and a cashier taking a payment at the till has to be able to see
# what that customer owes -- customer_payment() right below is a till
# operation. suppliers_payables / supplier_statement are the other direction:
# procurement, money the shop owes, nothing a till ever needs. The whole
# debtor BOOK (customers_receivables above) stays on retail.reports for the
# same reason -- one customer's balance is a till fact, the list of everyone
# who owes the shop money is a report.
def customer_statement(cust_id):
    cid = _cid(); conn = get_retail_conn(); _ensure_credit_schema(conn)
    cust = conn.execute("SELECT id,name,phone,credit_mode,credit_limit,credit_balance FROM customers WHERE id=? AND company_id=?",
                        (cust_id, cid)).fetchone()
    if not cust:
        conn.close(); return jsonify({'status': 'error', 'message': 'Customer not found'}), 404
    charges = conn.execute("SELECT sale_number AS ref, created_at, (total - amount_paid) AS amount, 'charge' AS kind "
                           "FROM sales WHERE company_id=? AND customer_id=? AND (total - amount_paid) > 0.005",
                           (cid, cust_id)).fetchall()
    receipts = conn.execute("SELECT id, reference AS ref, created_at, amount, 'payment' AS kind "
                            "FROM payments WHERE company_id=? AND party_type='customer' AND party_id=? "
                            "AND direction='in' AND COALESCE(status,'active')='active'", (cid, cust_id)).fetchall()
    events = [dict(r) for r in charges] + [dict(r) for r in receipts]
    events.sort(key=lambda e: e.get('created_at') or '')
    run = Decimal('0'); out = []
    for e in events:
        amt = Decimal(str(e['amount'] or 0))
        run = (run + amt) if e['kind'] == 'charge' else (run - amt)
        out.append({'ref': e['ref'], 'date': e['created_at'], 'kind': e['kind'], 'payment_id': e.get('id'),
                    'amount': _money(e['amount']), 'running_balance': float(run.quantize(Decimal('0.01')))})
    conn.close()
    return jsonify({'status': 'success', 'data': {'customer': dict(cust), 'events': out, 'balance': _money(cust['credit_balance'])}})

@retail_bp.route('/customers/<string:cust_id>/payments', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.customer.payment.record", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_SELL)
def customer_payment(cust_id):
    cid = _cid(); data = request.json or {}
    amt = _money(data.get('amount', 0))
    if amt <= 0:
        return jsonify({'status': 'error', 'message': 'Amount must be positive'}), 400
    conn = get_retail_conn(); _ensure_credit_schema(conn)
    cust = conn.execute("SELECT id,credit_balance FROM customers WHERE id=? AND company_id=?", (cust_id, cid)).fetchone()
    if not cust:
        conn.close(); return jsonify({'status': 'error', 'message': 'Customer not found'}), 404
    try:
        ref = _record_payment(conn, cid, 'customer', cust_id, 'in', amt, method=data.get('method', 'cash'),
                              notes=data.get('notes', ''), device=data.get('device'), doc_type='receipt')
        newbal = _adjust_credit(conn, 'customers', cust_id, cid, -amt)
        _audit(conn, 'CUSTOMER_PAYMENT', 'customer', cust_id, f'{ref} amount={amt}')
        conn.commit(); conn.close()
        _sync_nudge()  # best-effort immediate push -- see commercial_runtime/sync/sync_service.py
        return jsonify({'status': 'success', 'data': {'reference': ref, 'new_balance': newbal}})
    except Exception as e:
        conn.rollback(); conn.close(); return jsonify({'status': 'error', 'message': str(e)}), 500

# ── Supplier credit (Accounts Payable) ────────────────────────────────────────
@retail_bp.route('/suppliers/payables', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def suppliers_payables():
    cid = _cid(); conn = get_retail_conn(); _ensure_credit_schema(conn)
    rows = conn.execute("SELECT id,name,phone,payment_terms,credit_balance FROM suppliers "
                        "WHERE company_id=? AND COALESCE(credit_balance,0) > 0.005 ORDER BY credit_balance DESC", (cid,)).fetchall()
    # Same fix as customers_receivables() above -- filter the total the same
    # way as the listed rows so an overpaid (negative-balance) supplier can't
    # silently net against and understate the total_payable shown to the user.
    total = conn.execute("SELECT COALESCE(SUM(credit_balance),0) FROM suppliers "
                         "WHERE company_id=? AND COALESCE(credit_balance,0) > 0.005", (cid,)).fetchone()[0]
    conn.close()
    return jsonify({'status': 'success', 'total_payable': _money(total), 'data': [dict(r) for r in rows]})

@retail_bp.route('/suppliers/<string:sid>/statement', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def supplier_statement(sid):
    cid = _cid(); conn = get_retail_conn(); _ensure_credit_schema(conn)
    sup = conn.execute("SELECT id,name,phone,payment_terms,credit_balance FROM suppliers WHERE id=? AND company_id=?",
                       (sid, cid)).fetchone()
    if not sup:
        conn.close(); return jsonify({'status': 'error', 'message': 'Supplier not found'}), 404
    charges = conn.execute("SELECT po_number AS ref, created_at, (total - COALESCE(amount_paid,0)) AS amount, 'charge' AS kind "
                           "FROM purchase_orders WHERE company_id=? AND supplier_id=? AND (total - COALESCE(amount_paid,0)) > 0.005",
                           (cid, sid)).fetchall()
    payments = conn.execute("SELECT id, reference AS ref, created_at, amount, 'payment' AS kind "
                            "FROM payments WHERE company_id=? AND party_type='supplier' AND party_id=? "
                            "AND direction='out' AND COALESCE(status,'active')='active'", (cid, sid)).fetchall()
    events = [dict(r) for r in charges] + [dict(r) for r in payments]
    events.sort(key=lambda e: e.get('created_at') or '')
    run = Decimal('0'); out = []
    for e in events:
        amt = Decimal(str(e['amount'] or 0))
        run = (run + amt) if e['kind'] == 'charge' else (run - amt)
        out.append({'ref': e['ref'], 'date': e['created_at'], 'kind': e['kind'], 'payment_id': e.get('id'),
                    'amount': _money(e['amount']), 'running_balance': float(run.quantize(Decimal('0.01')))})
    conn.close()
    return jsonify({'status': 'success', 'data': {'supplier': dict(sup), 'events': out, 'balance': _money(sup['credit_balance'])}})

@retail_bp.route('/suppliers/<string:sid>/payments', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.supplier.manage", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
# retail.employees (= owner authority), NOT retail.stock.adjust like the rest
# of the supplier surface. Receiving a delivery and PAYING for it are two
# different authorities on purpose: a manager who could do both could receive
# a short delivery and settle it in full, and nobody else in the shop would
# ever see the two facts side by side. The person who signs for the goods is
# not the person who signs the cheque.
@mt_require_capability(CAP_EMPLOYEES)
def supplier_payment(sid):
    cid = _cid(); data = request.json or {}
    amt = _money(data.get('amount', 0))
    if amt <= 0:
        return jsonify({'status': 'error', 'message': 'Amount must be positive'}), 400
    conn = get_retail_conn(); _ensure_credit_schema(conn)
    sup = conn.execute("SELECT id,credit_balance FROM suppliers WHERE id=? AND company_id=?", (sid, cid)).fetchone()
    if not sup:
        conn.close(); return jsonify({'status': 'error', 'message': 'Supplier not found'}), 404
    try:
        ref = _record_payment(conn, cid, 'supplier', sid, 'out', amt, method=data.get('method', 'cash'),
                              notes=data.get('notes', ''), device=data.get('device'), doc_type='supplier_payment')
        newbal = _adjust_credit(conn, 'suppliers', sid, cid, -amt)
        _audit(conn, 'SUPPLIER_PAYMENT', 'supplier', sid, f'{ref} amount={amt}')
        conn.commit(); conn.close()
        _sync_nudge()  # best-effort immediate push -- see commercial_runtime/sync/sync_service.py
        return jsonify({'status': 'success', 'data': {'reference': ref, 'new_balance': newbal}})
    except Exception as e:
        conn.rollback(); conn.close(); return jsonify({'status': 'error', 'message': str(e)}), 500

@retail_bp.route('/purchase-orders/<int:po_id>/pay', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.purchase.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_EMPLOYEES)
def pay_purchase_order(po_id):
    cid = _cid(); data = request.json or {}
    amt = _money(data.get('amount', 0))
    if amt <= 0:
        return jsonify({'status': 'error', 'message': 'Amount must be positive'}), 400
    conn = get_retail_conn(); _ensure_credit_schema(conn)
    po = conn.execute("SELECT id,supplier_id,total,COALESCE(amount_paid,0) AS amount_paid,po_number FROM purchase_orders WHERE id=? AND company_id=?",
                      (po_id, cid)).fetchone()
    if not po:
        conn.close(); return jsonify({'status': 'error', 'message': 'PO not found'}), 404
    try:
        new_paid = _money(po['amount_paid'] + amt)
        status = 'paid' if new_paid >= _money(po['total']) - 0.005 else 'partial'
        conn.execute("UPDATE purchase_orders SET amount_paid=?, payment_status=? WHERE id=? AND company_id=?",
                     (new_paid, status, po_id, cid))
        ref = _record_payment(conn, cid, 'supplier', po['supplier_id'], 'out', amt, method=data.get('method', 'cash'),
                              related_type='po', related_id=po_id, notes=data.get('notes', ''),
                              device=data.get('device'), doc_type='supplier_payment')
        if po['supplier_id']:
            _adjust_credit(conn, 'suppliers', po['supplier_id'], cid, -amt)
        _audit(conn, 'PO_PAYMENT', 'purchase_order', po_id, f"{ref} amount={amt}")
        conn.commit(); conn.close()
        _sync_nudge()  # best-effort immediate push -- see commercial_runtime/sync/sync_service.py
        return jsonify({'status': 'success', 'data': {'reference': ref, 'payment_status': status, 'amount_paid': new_paid}})
    except Exception as e:
        conn.rollback(); conn.close(); return jsonify({'status': 'error', 'message': str(e)}), 500

# ── Cash summary + aging (structured for future dashboard KPIs) ────────────────
@retail_bp.route('/reports/daily-cash', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def daily_cash():
    cid = _cid(); day = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')
    conn = get_retail_conn(); _ensure_credit_schema(conn)
    rows = conn.execute("""SELECT direction, method, COALESCE(SUM(amount),0) AS amount, COUNT(*) AS count
        FROM payments WHERE company_id=? AND date(created_at)=? AND COALESCE(status,'active')='active'
        GROUP BY direction, method""", (cid, day)).fetchall()
    cash_in = _money(sum(r['amount'] for r in rows if r['direction'] == 'in'))
    cash_out = _money(sum(r['amount'] for r in rows if r['direction'] == 'out'))
    conn.close()
    return jsonify({'status': 'success', 'data': {
        'date': day, 'cash_in': cash_in, 'cash_out': cash_out, 'net': _money(cash_in - cash_out),
        'by_method': [dict(r) for r in rows],
    }})

@retail_bp.route('/reports/aging', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def aging_report():
    """Simplified aging: buckets each party's balance by the age of its oldest unpaid
    document. Structured so a richer FIFO allocation can replace it without API change."""
    cid = _cid(); kind = request.args.get('type', 'receivable')
    conn = get_retail_conn(); _ensure_credit_schema(conn)
    buckets = {'current': 0.0, '1_30': 0.0, '31_60': 0.0, '61_90': 0.0, '90_plus': 0.0}
    today = datetime.now()
    if kind == 'payable':
        parties = conn.execute("SELECT id, credit_balance FROM suppliers WHERE company_id=? AND COALESCE(credit_balance,0)>0.005", (cid,)).fetchall()
        oldest_sql = "SELECT MIN(created_at) FROM purchase_orders WHERE company_id=? AND supplier_id=? AND (total-COALESCE(amount_paid,0))>0.005"
    else:
        parties = conn.execute("SELECT id, credit_balance FROM customers WHERE company_id=? AND COALESCE(credit_balance,0)>0.005", (cid,)).fetchall()
        oldest_sql = "SELECT MIN(created_at) FROM sales WHERE company_id=? AND customer_id=? AND (total-amount_paid)>0.005"
    for p in parties:
        oldest = conn.execute(oldest_sql, (cid, p['id'])).fetchone()[0]
        days = 0
        if oldest:
            try:
                days = (today - datetime.strptime(str(oldest)[:10], '%Y-%m-%d')).days
            except Exception:
                days = 0
        bal = float(p['credit_balance'] or 0)
        key = 'current' if days <= 0 else '1_30' if days <= 30 else '31_60' if days <= 60 else '61_90' if days <= 90 else '90_plus'
        buckets[key] += bal
    conn.close()
    return jsonify({'status': 'success', 'type': kind, 'data': {k: _money(v) for k, v in buckets.items()}})

# ── Accounting export -- CSV, for a finance department's own ERP/auditor ──────
#
# The ledger (sales/payments/cash drawer) has always been correct; there was
# never a way to HAND it to anyone outside this product. A senior review named
# exactly this -- "no journal export, no CSV dump" -- as the kind of gap that
# loses a hypermarket deal: the numbers are right, but a finance department
# cannot get them into whatever system their auditor actually reads.
#
# Three exports, all CAP_REPORTS (the same authority every other route that
# discloses the shop's financial position already needs -- report_summary,
# daily_cash, aging_report, all immediately above), all company-scoped, all
# date-ranged on the SAME half-open bounds `recent_sales` uses (`col >=
# date(?) AND col < date(?, '+1 day')`, schema v21's "the POS scale fix") so
# the predicate stays sargable and never re-wraps the column in `date(...)`.
#
# STREAMED, not built as one string. A year of hypermarket sales is hundreds
# of thousands of rows across sales+sale_items -- the largest join in this
# database -- and this is exactly the endpoint most likely to be pointed at
# the whole thing at once. `conn.execute(...)` + `cursor.fetchmany(...)` in a
# loop, one CSV chunk yielded per batch, so memory stays bounded at one batch
# regardless of how many rows match.

#: Rows fetched from the cursor per CSV chunk yielded. Small enough that a
#: slow client never holds a huge buffer server-side, large enough that this
#: isn't one round trip to sqlite per output row.
_EXPORT_CSV_BATCH_SIZE = 500


def _export_money(value):
    """Format one exported money cell with the SAME rounding rule the ledger
    itself is written with -- core/retail/pricing.py's `_money`, imported
    here as `tax_engine._money`, NOT a second hand-rolled implementation.
    (This file also carries its OWN `_money` a few thousand lines up, for
    header-level credit/payment figures -- functionally identical, same
    Decimal/ROUND_HALF_UP/2dp rule, but the task that produced this export
    named core/retail/pricing.py specifically, and an export that quietly
    used a different rounding function than the one it was told to is
    exactly the kind of drift that makes an export disagree with the books
    by a cent.) `None` -- an unset closing figure, a payments column an
    older install never populated -- is left blank rather than coerced into
    a false "0.00"; a blank cell tells a bookkeeper "no value", a zero tells
    them "value was zero", and those are not the same claim.
    """
    if value is None:
        return ''
    return tax_engine._money(Decimal(str(value)))


def _export_date_range():
    """Both `date_from` and `date_to` are REQUIRED for every export below,
    unlike `recent_sales` (where either bound alone is meaningful for a
    till's own lookup). An accounting export has no equivalent "just the
    last page" use -- an unbounded request against the largest tables in
    the database is not an export, it is an outage -- so there is no
    default range to fall back to.

    Returns `(date_from, date_to, None)` on success, or `(None, None,
    error_response)` on failure, so a caller can `if err: return err` in one
    line rather than repeating the validation and the 400 body three times.
    """
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    if not date_from or not date_to:
        return None, None, (
            jsonify({'status': 'error', 'message': 'date_from and date_to are required'}), 400
        )
    return date_from, date_to, None


def _stream_csv_export(conn, sql, params, header, money_columns, filename):
    """Turn one company-scoped, date-ranged SELECT into a streamed CSV
    response. `money_columns` names the header columns (by label, not
    index, so a route's own column order can change without silently
    formatting the wrong cell) that get `_export_money`'s rounding; every
    other cell is written through unchanged.

    `conn` is owned by this function from here on -- opened by the caller,
    closed in the generator's `finally` so it lives exactly as long as the
    response takes to stream, the same connection lifetime every other
    route in this file gives its own per-request connection.
    """
    money_idx = [header.index(c) for c in money_columns]

    def _generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(header)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        try:
            cursor = conn.execute(sql, params)
            while True:
                batch = cursor.fetchmany(_EXPORT_CSV_BATCH_SIZE)
                if not batch:
                    break
                for row in batch:
                    values = list(row)
                    for idx in money_idx:
                        values[idx] = _export_money(values[idx])
                    writer.writerow(values)
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)
        finally:
            conn.close()

    return Response(
        stream_with_context(_generate()),
        content_type='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Cache-Control': 'no-store',
        },
    )


@retail_bp.route('/reports/export/sales', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def export_sales_csv():
    """One CSV row per SOLD LINE ITEM (not per sale) -- an auditor or an ERP
    import wants the same granularity the till itself recorded, and a sale
    total with no line detail behind it is a number nobody can reconcile
    against inventory. Sale-level figures (subtotal/discount/tax/total/
    amount_paid) are repeated on every line of a multi-item sale rather than
    written once, matching how a flat accounting export is meant to be
    read -- one row is one complete, self-contained fact.

    Company-scoped through `s.company_id` alone: `sale_items` carries no
    company_id of its own, but every row it returns is reached by joining
    FROM an already company-filtered `sales` row via `si.sale_id=s.id`, so
    no other tenant's line item can appear no matter what `si` contains.
    """
    cid = _cid()
    date_from, date_to, err = _export_date_range()
    if err:
        return err
    header = [
        'sale_id', 'sale_number', 'created_at', 'branch_id', 'customer_id', 'customer_name',
        'cashier', 'payment_method', 'sale_status', 'product_id', 'product_name', 'sku',
        'quantity', 'unit_price', 'discount_pct', 'tax_rate', 'line_total',
        'sale_subtotal', 'sale_discount_amount', 'sale_tax_amount', 'sale_total', 'sale_amount_paid',
    ]
    money_columns = [
        'unit_price', 'line_total',
        'sale_subtotal', 'sale_discount_amount', 'sale_tax_amount', 'sale_total', 'sale_amount_paid',
    ]
    sql = (
        "SELECT s.id, s.sale_number, s.created_at, s.branch_id, s.customer_id, "
        "COALESCE(c.name,'Walk-in'), s.cashier, s.payment_method, s.status, "
        "si.product_id, p.name, p.sku, "
        "si.quantity, si.unit_price, si.discount_pct, si.tax_rate, si.line_total, "
        "s.subtotal, s.discount_amount, s.tax_amount, s.total, s.amount_paid "
        "FROM sales s "
        "JOIN sale_items si ON si.sale_id = s.id "
        "LEFT JOIN customers c ON s.customer_id = c.id "
        "LEFT JOIN products p ON si.product_id = p.id "
        "WHERE s.company_id=? AND s.created_at >= date(?) AND s.created_at < date(?, '+1 day') "
        "ORDER BY s.id, si.id"
    )
    conn = get_retail_conn()
    return _stream_csv_export(
        conn, sql, (cid, date_from, date_to), header, money_columns,
        f'sales_export_{date_from}_{date_to}.csv',
    )


@retail_bp.route('/reports/export/payments', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def export_payments_csv():
    """One CSV row per payment ledger entry -- every direction, every method,
    both till receipts (`sale_id` set) and standalone AR/AP payments
    (`party_type`/`party_id` set, `related_type`/`related_id` naming what it
    settled).

    `_ensure_credit_schema(conn)` runs first, matching every other route in
    this file that reads `direction`/`party_type`/`party_id`/`notes` --
    those four are added lazily by that function rather than living in
    `payments`' base CREATE TABLE (see that function's own docstring), so a
    brand-new install that has never touched a credit/payment route needs
    it called here too or this query raises `no such column`.
    """
    cid = _cid()
    date_from, date_to, err = _export_date_range()
    if err:
        return err
    conn = get_retail_conn()
    _ensure_credit_schema(conn)
    header = [
        'payment_id', 'created_at', 'direction', 'method', 'amount', 'currency',
        'party_type', 'party_id', 'sale_id', 'related_type', 'related_id',
        'reference', 'status', 'notes',
    ]
    money_columns = ['amount']
    sql = (
        "SELECT id, created_at, direction, method, amount, currency, "
        "party_type, party_id, sale_id, related_type, related_id, "
        "reference, status, notes "
        "FROM payments "
        "WHERE company_id=? AND created_at >= date(?) AND created_at < date(?, '+1 day') "
        "ORDER BY id"
    )
    return _stream_csv_export(
        conn, sql, (cid, date_from, date_to), header, money_columns,
        f'payments_export_{date_from}_{date_to}.csv',
    )


@retail_bp.route('/reports/export/cash-sessions', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def export_cash_sessions_csv():
    """One CSV row per CLOSED drawer -- a Z report, the persisted CLOSING
    figures (`opening_float`/`closing_float_counted`/`closing_float_
    expected`/`variance`), not a re-derived live X report.

    Deliberately NOT built on `_cash_session_report` (the function behind
    `cash_session_x_report`): that recomputes a LIVE picture for one session
    by re-summing its cash sales, refunds and movements at read time.
    Re-running that per historical row for a year of shifts would mean
    re-summing every sale and refund this company ever recorded, once per
    session, on every export. `cash_sessions` already stores the closing
    figures the moment a drawer closes (`close_cash_session`) -- that IS
    what a Z report is: the numbers as they stood the moment the drawer
    closed, not the numbers as they'd compute today. Filtered to
    `closed_at IS NOT NULL` so an open drawer (no closing figures yet)
    never appears with blank money columns pretending to be a report.

    Date-ranged on `closed_at`, not `opened_at` -- a Z report belongs to the
    accounting period the shift ENDED in, matching how every other
    period-close document in this file (sales, payments) is ranged on the
    timestamp that makes it final.
    """
    cid = _cid()
    date_from, date_to, err = _export_date_range()
    if err:
        return err
    header = [
        'session_id', 'branch_id', 'opened_by', 'opened_at', 'closed_by', 'closed_at',
        'opening_float', 'closing_float_counted', 'closing_float_expected', 'variance', 'status',
    ]
    money_columns = ['opening_float', 'closing_float_counted', 'closing_float_expected', 'variance']
    sql = (
        "SELECT id, branch_id, opened_by, opened_at, closed_by, closed_at, "
        "opening_float, closing_float_counted, closing_float_expected, variance, status "
        "FROM cash_sessions "
        "WHERE company_id=? AND closed_at IS NOT NULL "
        "AND closed_at >= date(?) AND closed_at < date(?, '+1 day') "
        "ORDER BY closed_at"
    )
    conn = get_retail_conn()
    return _stream_csv_export(
        conn, sql, (cid, date_from, date_to), header, money_columns,
        f'cash_session_zreports_{date_from}_{date_to}.csv',
    )

# ── Void (immutability: never edit/delete, only reverse) ──────────────────────
@retail_bp.route('/payments/<int:pid>/void', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.settings.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
# retail.refund, not an owner code, because of what this route has already
# been narrowed to: it REFUSES any receipt belonging to a sale (see the long
# comment below) and only ever voids a customer-account, supplier-account or
# PO payment. That is reversing money that was taken or paid in error, which
# is the refund authority -- the same one create_return needs.
@mt_require_capability(CAP_REFUND)
def void_payment(pid):
    cid = _cid(); data = request.json or {}
    conn = get_retail_conn(); _ensure_credit_schema(conn)
    p = conn.execute("SELECT * FROM payments WHERE id=? AND company_id=?", (pid, cid)).fetchone()
    if not p:
        conn.close(); return jsonify({'status': 'error', 'message': 'Payment not found'}), 404
    if (p['status'] or 'active') != 'active':
        conn.close(); return jsonify({'status': 'error', 'message': 'Payment is not active'}), 409
    # Stock-accuracy sweep: voiding a SALE receipt through this route
    # reversed nothing about the sale it belonged to -- not the sales row,
    # not the sale_items, and above all not the stock those items had
    # already decremented. BOTH halves are refused, walk-in and named
    # customer alike, because both end somewhere incoherent:
    #
    #   * WALK-IN. create_sale passes party_type/party_id as None (see its
    #     _record_payment call), so the _adjust_credit block below is
    #     skipped entirely and the void did precisely one thing: delete the
    #     money from the ledger. Goods gone, sale still 'completed' and
    #     still recorded as paid, nobody owing anything. Daily-cash and the
    #     X/Z drawer report lose the cash; inventory never hears about it.
    #   * NAMED CUSTOMER. An earlier pass allowed this, on the reasoning
    #     that _adjust_credit at least turns the paid sale back into a debt.
    #     It does not hold up. `sales.amount_paid` is left at the full
    #     amount, so customer_statement() -- which builds its charge list
    #     from `sales WHERE (total - amount_paid) > 0.005` and its receipt
    #     list from ACTIVE payments only -- now sees neither the charge nor
    #     the receipt and computes a running balance of zero, while
    #     `customers.credit_balance` says the customer owes the money. Two
    #     screens, two different answers, and the stock is gone in both.
    #     That is a worse end state than refusing, not a blunter one.
    #
    # This route is not the place to invent a sale reversal. The codebase
    # already has the one operation that reverses a sale correctly and
    # atomically -- POST /returns (create_return above), which restocks
    # every line, writes the matching 'return_in' ledger rows, and refunds
    # through the same money ledger in one transaction. So refuse the
    # half-reversal and name the operation that does it properly, rather
    # than silently performing the destructive half here.
    #
    # Deliberately NOT affected: customer- and supplier-ACCOUNT payments
    # (customer_payment/supplier_payment above) and PO payments, which carry
    # related_type NULL or 'po'. Those move money only -- no goods, no
    # sales row to contradict -- so voiding one is complete on its own and
    # stays available; the statement views already carry their payment_id
    # for exactly that. Nothing in products/retail/frontend calls this route
    # today, so the refusal takes no button away from anyone; it closes the
    # API path before a screen is built on top of it.
    if p['related_type'] == 'sale':
        conn.close()
        return jsonify({'status': 'error',
                         'message': 'This receipt belongs to a sale. Process a return against that sale '
                                    'instead -- a return reverses the stock and the money together.'}), 409

    # AUDIT: the authority to reverse must equal the authority to create --
    # the same principle retail.discount is enforced under in create_sale
    # and update_customer above. The decorator above this handler is only
    # the FLOOR (CAP_REFUND); by this point p['related_type'] != 'sale', so
    # what is left is exactly the two shapes this route was already narrowed
    # to -- a customer-account receipt or a supplier-account/PO payment --
    # and those two do NOT share an authority upstream, so they must not
    # share one here either:
    #
    #   * customer_payment (~3421) is gated CAP_SELL. Voiding a
    #     party_type='customer' row is therefore a till-level correction --
    #     the same tier as create_return, which is why CAP_REFUND (the
    #     decorator floor) is already the right and sufficient check for it.
    #   * supplier_payment (~3495) and pay_purchase_order's inline
    #     down-payment (~3535, and create_purchase_order's own amount_paid
    #     branch) are gated CAP_EMPLOYEES -- owner authority, on purpose:
    #     receiving a delivery and paying for it are different authorities,
    #     and separately, money paid OUT to a supplier is the shop's own
    #     money leaving, not till cash. Both write party_type='supplier'
    #     (related_type is NULL for the direct payment, 'po' for the PO
    #     down-payment -- either way the party is the supplier), so a single
    #     check on party_type covers both without caring which of the two
    #     created the row. Without this, a cashier who could never CREATE a
    #     supplier payment could still VOID one -- undoing money paid out
    #     and silently reopening the payable -- using only the refund
    #     authority a till legitimately needs for its own receipts. That is
    #     the asymmetry this check exists to close.
    #
    # Checked BEFORE any UPDATE and before this handler's write ever takes
    # place, so a refusal is total rather than partial -- same placement
    # rule create_sale's discount check documents for BEGIN IMMEDIATE.
    if p['party_type'] == 'supplier' and not session_has_capability(CAP_EMPLOYEES):
        conn.close()
        return jsonify({'status': 'error', 'error': CAPABILITY_DENIED_MESSAGE,
                         'message': CAPABILITY_DENIED_MESSAGE}), 403

    try:
        conn.execute("UPDATE payments SET status='voided', voided_by=?, voided_at=?, notes=COALESCE(notes,'')||? WHERE id=?",
                     (_uid(), _now(), f" [VOID: {data.get('reason','')}]", pid))
        # Reverse the balance effect of the voided receipt/payment.
        if p['party_id'] and p['party_type'] in ('customer', 'supplier'):
            table = 'customers' if p['party_type'] == 'customer' else 'suppliers'
            # 'in' reduced AR / 'out' reduced AP, so voiding adds it back.
            _adjust_credit(conn, table, p['party_id'], cid, _money(p['amount']))
        _audit(conn, 'PAYMENT_VOIDED', 'payment', pid, data.get('reason', ''))
        # AUDIT-032A fix (DEFECT 2): this route mutates `payments.status` in
        # place -- the ONE documented exception to Phase 5's "money is an
        # immutable fact, corrected by a new row, never edited in place"
        # posture (see the "CREDIT & PAYMENTS" section header above: "never
        # edited -- only voided/reversed (status flag)" already SAYS this is
        # how payments work; what was missing is that the mutation never
        # told the other devices). Before this fix, no sync_outbox row was
        # ever queued for a void at all, so a receipt cancelled on THIS
        # device stayed live money on every other device forever -- their
        # copy of the row never changed, and every company-wide revenue read
        # on them kept counting it.
        #
        # `uid` only -- a payment created before v13's uid backfill has no
        # wire identity and was never syncable to begin with (see
        # `_local_id_by_uid`'s identical "no uid, nothing to resolve"
        # posture); voiding it locally is still correct, it just has no peer
        # copy to correct. The apply side (sync_service.py's payment branch)
        # accepts ONLY this exact transition -- status -> 'voided', nothing
        # else, never `amount` -- so a device cannot use this path to
        # rewrite money it did not ring; see that branch's own comment for
        # why a general `update` was rejected.
        if p['uid']:
            _queue_sync_event(conn, 'payment', p['uid'], 'update', {'uid': p['uid'], 'status': 'voided'})
        conn.commit(); conn.close()
        _sync_nudge()  # best-effort immediate push -- see commercial_runtime/sync/sync_service.py
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback(); conn.close(); return jsonify({'status': 'error', 'message': str(e)}), 500

# ── Audit Log (read-only viewer) ────────────────────────────────────────────
# _audit() above has written to this table from ~26 real call sites (product
# CRUD, stock adjustments, customer/supplier CRUD, PO lifecycle, reorder
# accept/decline, returns/refunds, customer/supplier/PO payments, payment
# voids) since the very first version of this file, with real user_id
# attribution on every row -- but until this route, nothing ever read it
# back. Admin-device gated (see _is_admin_device above) rather than visible
# to every user: this is the one surface in the app that shows what EVERY
# user in the company did, including reversals of their own sales/refunds.
@retail_bp.route('/audit-log', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
# retail.reports ON TOP OF the admin-device check inside the handler, not
# instead of it. They are different axes and both are load-bearing: the
# device check answers "is this the shop's admin terminal?", the capability
# answers "is this person allowed to read the shop's records?". A manager on
# the admin device still should not be handed every user's refund and void
# trail just because of where they are standing.
@mt_require_capability(CAP_REPORTS)
def list_audit_log():
    cid = _cid()
    if not _is_admin_device(cid):
        return jsonify({'status': 'error',
                         'message': 'The Audit Log is only visible on this company\'s admin device.'}), 403

    conn = get_retail_conn()
    try:
        try:
            page = int(request.args.get('page', 1))
        except (TypeError, ValueError):
            page = 1
        page = max(1, page)
        # Same helper as /sales/recent. This site was already correct -- it is
        # where the right shape was copied FROM -- but routing both through one
        # function means a future third caller cannot get half of it, which is
        # exactly how /sales/recent ended up with the ceiling and not the floor.
        limit = clamp_page_limit(request.args.get('limit', 50), 50, 200)  # a viewer, not a bulk export
        offset = (page - 1) * limit

        where = ['company_id=?']
        params = [cid]
        date_from = request.args.get('date_from')
        if date_from:
            where.append('date(timestamp) >= date(?)')
            params.append(date_from)
        date_to = request.args.get('date_to')
        if date_to:
            where.append('date(timestamp) <= date(?)')
            params.append(date_to)
        action = request.args.get('action')
        if action:
            where.append('action=?')
            params.append(action)
        entity = request.args.get('entity')
        if entity:
            where.append('entity=?')
            params.append(entity)
        where_sql = ' AND '.join(where)

        total = conn.execute(f'SELECT COUNT(*) FROM audit_log WHERE {where_sql}', params).fetchone()[0]
        rows = conn.execute(f'''
            SELECT id, user_id, action, entity, entity_id, details, timestamp
            FROM audit_log WHERE {where_sql}
            ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?
        ''', params + [limit, offset]).fetchall()

        # Real values currently on record for THIS company, not a hardcoded
        # action/entity list -- so the frontend's filter dropdowns can never
        # drift out of sync with whatever _audit() call sites actually exist.
        actions = [r[0] for r in conn.execute(
            'SELECT DISTINCT action FROM audit_log WHERE company_id=? ORDER BY action', (cid,)).fetchall()]
        entities = [r[0] for r in conn.execute(
            'SELECT DISTINCT entity FROM audit_log WHERE company_id=? AND entity IS NOT NULL ORDER BY entity', (cid,)).fetchall()]

        return jsonify({'status': 'success', 'data': [dict(r) for r in rows],
                         'meta': {'total': total, 'page': page, 'limit': limit,
                                  'actions': actions, 'entities': entities}})
    finally:
        conn.close()

# ── Demo seed/wipe ─────────────────────────────────────────────────────────────
# Hard production boundary (Phase 1 remediation): these two routes delete data.
# Before the fix they ran an UNSCOPED `DELETE FROM` across every retail table --
# no company_id filter at all, so ANY authenticated retail user in ANY company
# could wipe every tenant's retail data with one call. They also had no
# environment gate, so they were reachable inside the exact same shipped
# AuraEnterprise.exe a paying customer runs.
#
# Fixed shape, all four required:
#   1. core.security.modes.retail_demo_mode_enabled() -- False in every frozen
#      build, full stop, regardless of environment variables (see modes.py).
#   2. Caller must be a company admin (session['mt_role']=='admin') -- the
#      only "high-level administrator permission" this codebase's session
#      model actually distinguishes today (see RETAIL_SECURITY_PHASE_1.md for
#      why finer-grained RBAC wiring is deferred).
#   3. Caller must pass an explicit, company-specific confirmation token
#      (`confirm: "WIPE-<company_id>"` / "SEED-<company_id>") -- a deliberate
#      action that can't be triggered by a stray click or a replayed request
#      for a different company.
#   4. Every statement is scoped `WHERE company_id=?` and the whole operation
#      runs in one transaction with rollback on failure, so a half-completed
#      wipe/seed can never leave the DB inconsistent and another company's
#      rows are structurally unreachable by this code, not just "not selected."

def _require_retail_demo_mode():
    from commercial_runtime.security.modes import retail_demo_mode_enabled
    if not retail_demo_mode_enabled():
        return jsonify({'error': 'Demo reset is not available in this build.'}), 404
    return None


def _require_company_admin():
    if session.get('mt_role') != 'admin' and session.get('role_level', 0) < 4:
        return jsonify({'error': 'Administrator permission required.'}), 403
    return None


def _confirmation_token(prefix, cid):
    """The one spelling of a company-scoped confirmation token.

    Extracted so `_require_confirmation` (which CHECKS it) and any route that
    has to TELL a caller what to send back (see inventory_reconciliation)
    cannot drift apart. Two f-strings that must stay byte-identical forever is
    exactly the shape of thing that silently stops matching -- and the failure
    would be a repair button that answers 400 on every click, with a message
    quoting the very string the client just sent.
    """
    return f'{prefix}-{cid}'


def _require_confirmation(expected_prefix, cid):
    data = request.get_json(silent=True) or {}
    expected = _confirmation_token(expected_prefix, cid)
    if (data.get('confirm') or '').strip() != expected:
        return jsonify({
            'error': f'Confirmation required. Resend the request with {{"confirm": "{expected}"}}.'
        }), 400
    return None


# (table, statement) pairs, parent-before-child order preserved from the
# original code. Three of these tables (sale_items/return_items/
# purchase_order_items) have NO company_id column of their own -- they are
# scoped only through their parent row's company_id (sale_id/return_id/
# po_id FK) -- so they must be deleted via a subquery against the parent,
# not a direct `WHERE company_id=?` (that would raise "no such column").
_WIPE_STATEMENTS = (
    ('return_items', 'DELETE FROM return_items WHERE return_id IN (SELECT id FROM returns WHERE company_id=?)'),
    ('returns', 'DELETE FROM returns WHERE company_id=?'),
    ('sale_items', 'DELETE FROM sale_items WHERE sale_id IN (SELECT id FROM sales WHERE company_id=?)'),
    ('sales', 'DELETE FROM sales WHERE company_id=?'),
    ('inventory_movements', 'DELETE FROM inventory_movements WHERE company_id=?'),
    ('inventory_balances', 'DELETE FROM inventory_balances WHERE company_id=?'),
    ('purchase_order_items', 'DELETE FROM purchase_order_items WHERE po_id IN (SELECT id FROM purchase_orders WHERE company_id=?)'),
    ('purchase_orders', 'DELETE FROM purchase_orders WHERE company_id=?'),
    ('products', 'DELETE FROM products WHERE company_id=?'),
    ('categories', 'DELETE FROM categories WHERE company_id=?'),
    ('customers', 'DELETE FROM customers WHERE company_id=?'),
    ('suppliers', 'DELETE FROM suppliers WHERE company_id=?'),
    ('payments', 'DELETE FROM payments WHERE company_id=?'),
    ('tax_rates', 'DELETE FROM tax_rates WHERE company_id=?'),
    ('branches', 'DELETE FROM branches WHERE company_id=?'),
    ('audit_log', 'DELETE FROM audit_log WHERE company_id=?'),
)


@retail_bp.route('/demo-wipe', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.settings.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_EMPLOYEES)
def demo_wipe():
    for guard in (_require_retail_demo_mode(), _require_company_admin(), _require_confirmation('WIPE', _cid())):
        if guard is not None:
            return guard

    cid = _cid()
    conn = get_retail_conn()
    try:
        conn.execute("BEGIN TRANSACTION")
        for _table, stmt in _WIPE_STATEMENTS:
            conn.execute(stmt, (cid,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        from commercial_runtime.security.audit import record as _sec_audit, SECURITY_CONFIG_FAILURE
        _sec_audit(cid, _uid(), SECURITY_CONFIG_FAILURE, context={'action': 'demo_wipe', 'error': type(e).__name__})
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

    from commercial_runtime.security.audit import record as _sec_audit, DEMO_RESET_EXECUTED
    _sec_audit(cid, _uid(), DEMO_RESET_EXECUTED, context={'action': 'demo_wipe'})
    return jsonify({'success': True})


@retail_bp.route('/demo-seed', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.settings.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_EMPLOYEES)
def demo_seed():
    for guard in (_require_retail_demo_mode(), _require_company_admin(), _require_confirmation('SEED', _cid())):
        if guard is not None:
            return guard

    from database.schema import _seed_retail as _sr
    cid = _cid()
    conn = get_retail_conn()
    cur = conn.cursor()
    try:
        conn.execute("BEGIN TRANSACTION")
        for _table, stmt in _WIPE_STATEMENTS:
            conn.execute(stmt, (cid,))
        _sr(conn, cur, company_id=cid)
        conn.commit()
    except Exception as e:
        conn.rollback()
        from commercial_runtime.security.audit import record as _sec_audit, SECURITY_CONFIG_FAILURE
        _sec_audit(cid, _uid(), SECURITY_CONFIG_FAILURE, context={'action': 'demo_seed', 'error': type(e).__name__})
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()

    from commercial_runtime.security.audit import record as _sec_audit, DEMO_RESET_EXECUTED
    _sec_audit(cid, _uid(), DEMO_RESET_EXECUTED, context={'action': 'demo_seed'})
    return jsonify({'status': 'success', 'message': 'Retail seeded.'})


# ── Inventory reconciliation (maintenance) ────────────────────────────────────
# Stock-accuracy sweep. `inventory_balances.quantity_on_hand` is a mutable
# STORED cache written by five independent paths (create_sale,
# create_return, receive_purchase_order, adjust_stock, and import_api's
# _handle_retail_products), each of which is also expected to append the
# matching signed row to `inventory_movements` -- and nothing in this
# codebase ever checked that the two still agreed. That is why every stock
# bug here has been silent AND permanent: fixing a writer stops new drift,
# but the drift already baked into a customer's database stays there
# forever with no operation able to see it, let alone undo it.
#
# Exposed the same way this file's other maintenance operations are (see
# demo_wipe/demo_seed above and commercial_runtime/backup/routes.py): plain
# admin-gated HTTP routes on the existing blueprint, no new surface, no CLI.
# Split deliberately in two:
#
#   GET  /inventory/reconciliation         read-only drift report, safe on a
#                                          live till, safe to call any time;
#                                          company admin, because what it
#                                          RETURNS is the whole catalogue
#                                          and stock position, not because
#                                          of what it writes
#   POST /inventory/reconciliation/repair  rewrites the cache from the
#                                          ledger; company admin + an
#                                          explicit company-specific
#                                          confirmation token, exactly the
#                                          shape demo_wipe uses
#
# The repair is NOT folded into the report and NOT run automatically:
# drift is evidence that one of the five writers is broken, and a
# self-healing read would erase that evidence and hide the next
# regression. See core/retail/stock_reconciliation.py for why the repair
# writes no correcting movement row.

# "Report every (product, branch) pair, drifted or not."
#
# compute_drift's `tolerance` is the width below which stored-minus-ledger
# counts as agreement; a NEGATIVE width is the coherent reading of the same
# parameter for "nothing counts as agreement", so every key its UNION produces
# comes back. That is how this file asks "how many pairs are there?" WITHOUT
# writing a second copy of the key-set SQL -- see inventory_reconciliation.
#
# Not zero: exact float equality is what compute_drift's own DEFAULT_TOLERANCE
# comment exists to avoid, and `abs(drift) <= 0` would drop every pair that
# agrees exactly -- i.e. precisely the healthy ones this count is for.
_ALL_PAIRS_TOLERANCE = -1.0


@retail_bp.route('/inventory/reconciliation', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def inventory_reconciliation():
    """Report (never repair) every product/branch balance that disagrees
    with the inventory_movements ledger it is supposed to be a cache of.

    Company-admin gated, like its repair twin. Read-only does not mean
    low-value: this response is a full dump of the company's catalogue and
    stock position -- product name, SKU, branch and quantity for every
    drifted (product, branch) -- with no pagination and no filtering. Login
    plus a retail-subsystem permission is what a cashier has, and a cashier
    has no business exporting the whole stock ledger. The gate matches what
    the endpoint DISCLOSES, not how little it writes.
    """
    admin_guard = _require_company_admin()
    if admin_guard is not None:
        return admin_guard

    cid = _cid()
    conn = get_retail_conn()
    try:
        # ONE call, not two, and the drifted rows are a SUBSET of what it
        # returns rather than a second query. See _ALL_PAIRS_TOLERANCE.
        #
        # Filtering here duplicates compute_drift's own `abs(drift) <=
        # tolerance` comparison, and that is the lesser of the two available
        # duplications: the alternative -- re-typing _DRIFT_SQL's UNION over
        # inventory_balances and inventory_movements to COUNT the key set --
        # would put the definition of "which (product, branch) pairs exist"
        # in two files, and the copy in THIS one would go quietly wrong the
        # first time the reconciliation module widened its key set. What is
        # duplicated below is the operator; the NUMBER still comes from the
        # module, and retail_stock_accuracy_screen_route_test.py pins the two
        # against each other on a fixture that exercises both directions of
        # the union plus a sub-tolerance residue.
        examined = stock_reconciliation.compute_drift(conn, cid, tolerance=_ALL_PAIRS_TOLERANCE)
        rows = [r for r in examined
                if abs(r['drift']) > stock_reconciliation.DEFAULT_TOLERANCE]
    except sqlite3.DatabaseError as exc:
        current_app.logger.exception("inventory_reconciliation failed: %s", exc)
        return jsonify({'status': 'error', 'message': 'Could not reconcile inventory.'}), 400
    finally:
        conn.close()
    return jsonify({'status': 'success', 'data': {
        'drift_count': len(rows),
        # Net, not absolute: a +10/-10 pair nets to zero and that is the
        # honest headline number for "is the company's total stock figure
        # overstated?". drift_count is what says how many rows are wrong.
        'net_drift': round(sum(r['drift'] for r in rows), 4),
        # HOW MUCH WAS LOOKED AT. Without this, `drift_count: 0` is the same
        # response from a healthy 500-product shop and from a company with no
        # stock records at all, and the Stock accuracy screen's empty state
        # would be asserting an OUTCOME ("your stock is accurate") on evidence
        # that only says a query returned nothing. The screen renders the two
        # differently and cannot do that without this number.
        #
        # `pairs_examined >= drift_count` holds BY CONSTRUCTION here, because
        # `rows` is a filter over the very list this counts -- not by two
        # queries agreeing.
        'pairs_examined': len(examined),
        # What the repair twin will demand back, verbatim.
        #
        # It is not a secret and cannot be used as one: _require_confirmation
        # compares it against the CALLER'S OWN session company, so a token
        # handed to an admin of company A is refused for every other company,
        # and this route already required company-admin before it got here.
        # What the token actually buys -- a mutation that cannot be triggered
        # by a stray click or replayed at a different tenant -- is untouched.
        #
        # It is here because without it the repair is UNREACHABLE from any
        # frontend: the token embeds the company id, and no surface in
        # products/retail/frontend knows the company id (nor does
        # /api/auth/session return it). The alternative -- teaching the client
        # to build "RECONCILE-<id>" itself -- would put the token's format in
        # a second file. See _confirmation_token.
        #
        # No `unrepairable_count` beside it on purpose: `rows[].repairable` is
        # already the exact predicate repair_drift skips on, and a second
        # aggregate computed here is a second thing that can disagree with the
        # repair about what it is going to do.
        'repair_confirmation': _confirmation_token('RECONCILE', cid),
        'rows': rows,
    }})


@retail_bp.route('/inventory/reconciliation/repair', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.stock.adjust", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def repair_inventory_reconciliation():
    """Rewrite every drifted cached balance from the ledger, in one
    transaction, with one audit_log row per repaired balance.

    BEGIN IMMEDIATE wraps the recompute AND the overwrite together: without
    it a sale committing between the two would be counted in the ledger
    total that was read and then overwritten away by the balance that was
    written, i.e. the repair itself would create fresh drift.
    """
    cid = _cid()
    for guard in (_require_company_admin(), _require_confirmation('RECONCILE', cid)):
        if guard is not None:
            return guard

    conn = get_retail_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # `actor_user_id` is passed because THIS caller has one. repair_drift
        # writes its own summary audit row inside this transaction and leaves
        # the actor NULL when nobody supplied it -- the honest answer for a
        # repair run from a maintenance prompt, and the wrong one for a button
        # somebody pressed. The Stock accuracy screen made this route reachable
        # from a UI for the first time, so "who rewrote the shop's stock" now
        # has a real answer and it belongs in the trail.
        result = stock_reconciliation.repair_drift(conn, cid, actor_user_id=_uid())
        for row in result['repaired']:
            _audit(conn, 'STOCK_RECONCILED', 'product', row['product_id'],
                   f"branch={row['branch_id']}, stored={row['stored_balance']} -> "
                   f"ledger={row['ledger_balance']} (drift={row['drift']})")
        conn.commit()
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        current_app.logger.exception("repair_inventory_reconciliation failed: %s", exc)
        return jsonify({'status': 'error', 'message': 'Could not repair inventory balances.'}), 400
    finally:
        conn.close()
    return jsonify({'status': 'success', 'data': {
        'repaired_count': len(result['repaired']),
        'skipped_count': len(result['skipped']),
        'repaired': result['repaired'],
        'skipped': result['skipped'],
    }})


# ── Stock exceptions (oversell queue) ───────────────────────────────────────
#
# Launch-readiness Phase 7 stage 7d-i (docs/launch-readiness/
# phase7-offline-ux.md "Decision 4"). `stock_exceptions` is written from
# exactly one place, the sync apply site (commercial_runtime/sync/
# sync_service.py's `_record_or_refresh_stock_exception`), never from an
# HTTP route -- that stays true after stage 7d-ii below: resolving a row
# never inserts, refreshes or reopens one, it only stamps `resolved_at_utc`
# on a row this apply site already wrote.
#
# Gated CAP_REPORTS with no company-admin requirement, matching the
# majority of this file's other read-only reports (dashboard_stats,
# report_summary, daily_cash, aging_report, ...), NOT `inventory_
# reconciliation`'s stricter admin-only pairing: that pair is gated to the
# owner because its response is an UNPAGINATED DUMP OF THE WHOLE CATALOGUE
# AND STOCK POSITION. This route discloses only the (typically small) set
# of products currently oversold -- an operational report a manager acts
# on, the same tier as every other CAP_REPORTS read below.
@retail_bp.route('/inventory/stock-exceptions', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def list_stock_exceptions():
    """Every OPEN oversell exception for this company, most recently
    detected first, with the context an owner needs to act on one: which
    product, which branch, how negative, and when it was detected.

    Resolved rows (`resolved_at_utc IS NOT NULL`) are never returned here --
    stage 7d-i writes no resolution path at all (Decision 4: resolving one
    is a stock-movement-writing act for stage 7d-ii), so every row this
    route can ever see today is open by construction; the filter is kept
    explicit anyway so this route's contract does not silently change the
    day 7d-ii starts writing `resolved_at_utc`.

    LEFT JOIN, not an inner join, on both `products` and `branches`: an
    exception must stay visible even if the product it names is later
    tombstoned or the branch renamed away -- the oversell already happened
    and hiding the record because a foreign row moved would be exactly the
    kind of silent drop this queue exists to replace.
    """
    cid = _cid()
    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT se.id AS id, se.product_id AS product_id, p.name AS product_name, "
            "p.sku AS sku, se.branch_id AS branch_id, br.name AS branch_name, "
            "se.observed_quantity_on_hand AS observed_quantity_on_hand, "
            "se.detected_at_utc AS detected_at_utc "
            "FROM stock_exceptions se "
            "LEFT JOIN products p ON p.id = se.product_id AND p.company_id = ? "
            "LEFT JOIN branches br ON br.id = se.branch_id AND br.company_id = ? "
            "WHERE se.company_id = ? AND se.resolved_at_utc IS NULL "
            "ORDER BY se.detected_at_utc DESC",
            (cid, cid, cid),
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        current_app.logger.exception("list_stock_exceptions failed: %s", exc)
        return jsonify({'status': 'error', 'message': 'Could not list stock exceptions.'}), 400
    finally:
        conn.close()
    return jsonify({'status': 'success', 'data': {
        'exceptions': [dict(r) for r in rows],
        'count': len(rows),
    }})


# Stage 7d-ii: a human decision closes ONE open exception. Modelled on
# `adjust_stock` immediately above -- same pair of gates (Decision 4: this is
# a ledger write, not an acknowledgement, so it needs `retail.stock.adjust`
# even though the LIST route above only needs `retail.reports` to read the
# queue), same BEGIN IMMEDIATE + read-balance-then-write-movement shape when
# a count is supplied, same containment shape as delete_category/
# create_purchase_order (IntegrityError -> 409, other DatabaseError -> 400,
# connection always closed).
@retail_bp.route('/inventory/stock-exceptions/<string:exception_id>/resolve', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.stock.adjust", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
@mt_require_capability(CAP_STOCK_ADJUST)
def resolve_stock_exception(exception_id):
    """Resolves ONE open `stock_exceptions` row with a human decision --
    launch-readiness Phase 7 stage 7d-ii (docs/launch-readiness/
    phase7-offline-ux.md "Decision 4").

    Two independent things this route can do, controlled by whether the
    caller supplies `counted_quantity`:

    1. COUNTED. A human counted the shelf and the true figure differs from
       what THIS device's ledger currently shows. The correction is written
       through the EXACT SAME machinery `adjust_stock` uses two routes above
       -- a real `inventory_movement` (stock_in/stock_out, signed by the
       delta between the balance read HERE, fresh, inside this transaction,
       and the count) plus the matching `inventory_balances` UPDATE, emitted
       to `sync_outbox` like any other movement. Never a direct balance
       write: `compute_drift` has to keep agreeing with the ledger, and
       every other device only ever learns about this correction because it
       is an ordinary movement event, not a special case they would need
       their own code to understand.

       The delta is computed against the CURRENT balance, not the
       exception's own stale `observed_quantity_on_hand` -- other movements
       (a further oversold merge, a delivery, an unrelated adjustment) may
       have landed against this product/branch in the time between
       detection and resolution, and a physical count is a statement about
       the shelf right now, not about what the queue recorded then. If the
       count already matches the live balance (within the same epsilon
       tolerance `adjust_stock` and the reconciler use), no movement is
       written -- there is nothing to correct, and a zero-quantity movement
       would be noise in the ledger for no reason.

    2. UNCOUNTED (backorder). Decision 4's own §5 resolutions are backorder,
       substitute and refund, and a backorder legitimately leaves the
       balance negative until the delivery lands -- so a count can never be
       mandatory. `counted_quantity` is simply omitted and no movement is
       written; the row still closes.

    Either way a note is REQUIRED, carried into `_audit` (never onto the
    `stock_exceptions` row itself -- the table gained no new column in this
    stage; schema is frozen here, see database/schema.py's v20 comment). An
    exception resolved with no note is indistinguishable from someone
    clearing the queue to make it go away, which is exactly what Decision 4
    says this route must never allow.

    Re-resolving an already-closed row is refused with 409 and writes
    NOTHING -- no second movement, no re-stamped `resolved_at_utc`.
    `resolved_at_utc IS NULL` is checked once on read and enforced again in
    the closing UPDATE's own WHERE clause, both inside the SAME BEGIN
    IMMEDIATE transaction, so two concurrent resolutions of the same row can
    never both succeed.
    """
    data = request.json or {}
    cid = _cid()
    note = (data.get('note') or '').strip()
    if not note:
        return jsonify({'status': 'error', 'message': 'A note explaining the resolution is required.'}), 400

    raw_qty = data.get('counted_quantity')
    counted_quantity = None
    if raw_qty not in (None, ''):
        try:
            counted_quantity = float(raw_qty)
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'message': 'Invalid counted_quantity'}), 400

    conn = get_retail_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        exception_row = conn.execute(
            "SELECT id, product_id, branch_id, resolved_at_utc FROM stock_exceptions "
            "WHERE id=? AND company_id=?",
            (exception_id, cid)
        ).fetchone()
        if not exception_row:
            conn.rollback()
            return jsonify({'status': 'error', 'message': 'Exception not found'}), 404
        if exception_row['resolved_at_utc'] is not None:
            conn.rollback()
            return jsonify({'status': 'error', 'message': 'This exception was already resolved.'}), 409

        pid, bid = exception_row['product_id'], exception_row['branch_id']
        new_qty = None
        movement_uid = None
        if counted_quantity is not None:
            conn.execute("""
                INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand)
                VALUES (?,?,?,0)
            """, (cid, pid, bid))
            current = conn.execute(
                "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
                (cid, pid, bid)
            ).fetchone()
            on_hand = float(current['quantity_on_hand'] or 0) if current else 0.0
            delta = counted_quantity - on_hand
            # Same epsilon tolerance adjust_stock and the reconciler compare
            # with (stock_reconciliation.py) -- quantity_on_hand is REAL and
            # accumulated, so a fractional-unit product can sit a hair off
            # an exact integer count without a real discrepancy existing.
            if abs(delta) > stock_reconciliation.DEFAULT_TOLERANCE:
                conn.execute("""
                    UPDATE inventory_balances SET quantity_on_hand = quantity_on_hand + ?
                    WHERE company_id=? AND product_id=? AND branch_id=?
                """, (delta, cid, pid, bid))
                actor, terminal, utc_now = _stamp()
                movement_type = 'stock_in' if delta > 0 else 'stock_out'
                movement_uid = _new_uid()
                conn.execute("""
                    INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,reference,notes,created_by,
                                                     uid,actor_user_uid,terminal_id,created_at_utc)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (cid, pid, bid,
                      movement_type,
                      delta, 'EXCEPTION_RESOLVE', note, _uid(),
                      movement_uid, actor, terminal, utc_now))
                # Wave B shape, identical to adjust_stock's own emission just
                # above: the correction is only useful if it travels.
                _queue_sync_event(conn.cursor(), 'inventory_movement', movement_uid, 'create', {
                    'uid': movement_uid, 'product_id': pid, 'branch_id': bid, 'branch_uid': _branch_uid(conn, bid),
                    'movement_type': movement_type, 'quantity': delta, 'unit_cost': 0,
                    'reference': 'EXCEPTION_RESOLVE', 'notes': note, 'created_by': _uid(),
                    'actor_user_uid': actor, 'terminal_id': terminal, 'created_at_utc': utc_now,
                })
            new_row = conn.execute(
                "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
                (cid, pid, bid)
            ).fetchone()
            new_qty = float(new_row['quantity_on_hand']) if new_row else None

        now = now_utc_iso()
        close = conn.execute(
            "UPDATE stock_exceptions SET resolved_at_utc=? WHERE id=? AND company_id=? AND resolved_at_utc IS NULL",
            (now, exception_id, cid)
        )
        if close.rowcount == 0:
            # Only reachable if another transaction closed this exact row
            # between the SELECT above and here -- BEGIN IMMEDIATE's write
            # lock should make that impossible on SQLite, but the WHERE
            # clause is kept as the same defense-in-depth belt-and-braces
            # every gated UPDATE in this file carries (see delete_product,
            # delete_category): fail closed rather than trust the earlier
            # read alone.
            conn.rollback()
            return jsonify({'status': 'error', 'message': 'This exception was already resolved.'}), 409

        _audit(conn, 'STOCK_EXCEPTION_RESOLVED', 'stock_exception', exception_id,
               f'product={pid}, branch={bid}, counted_quantity={counted_quantity}, note={note}')
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        current_app.logger.warning("resolve_stock_exception(%s) failed on a database constraint: %s", exception_id, exc)
        return jsonify({'status': 'error', 'message': 'This exception could not be resolved.'}), 409
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        current_app.logger.exception("resolve_stock_exception(%s) failed: %s", exception_id, exc)
        return jsonify({'status': 'error', 'message': 'Could not resolve this exception.'}), 400
    finally:
        conn.close()
    return jsonify({'status': 'success', 'new_stock': new_qty, 'branch_id': bid, 'movement_uid': movement_uid})


# ── Sync conflicts (discarded catalogue edits) ──────────────────────────────
#
# Launch-readiness Phase 6 stage 6a-ii (docs/launch-readiness/
# phase6-catalogue-correctness.md Task B; database/schema.py's
# `_migrate_add_sync_conflicts_and_drop_quantity_reserved`, retail v17) writes
# a `sync_conflicts` row every time an incoming catalogue write is discarded
# as stale, instead of the silent drop design §6 explicitly forbids. Until
# now nothing could read it back at all -- ROADMAP.md's 2026-08-29 "the two
# exception queues both need ONE screen, not two" entry: grep found zero
# references in this file and zero in the frontend -- so a rejection that
# was deliberately made visible in the database was invisible through every
# interface that exists. This route closes that half.
#
# Gated CAP_REPORTS with no company-admin requirement, mirroring
# list_stock_exceptions above verbatim: same read-only disclosure tier (a
# small, operational set of recently-discarded catalogue edits, not
# inventory_reconciliation's unpaginated whole-catalogue dump), same
# reasoning for why owner-only would be the wrong axis to gate on.

# Bookkeeping keys every catalogue payload carries that no operator ever
# typed -- see list_sync_conflicts' own docstring below for why the payload
# is safe to INSPECT (never a credential) and never safe to RETURN verbatim.
_SYNC_CONFLICT_PAYLOAD_METADATA_KEYS = frozenset({
    'id', 'company_id', 'row_version', 'updated_at_utc', 'deleted_at_utc',
})


def _sync_conflict_changed_fields(payload):
    """Which FIELD NAMES a discarded catalogue edit would have changed --
    never the values (list_sync_conflicts never returns those). Every
    catalogue write site that can lose a reject-stale race (update_category/
    update_product/update_customer/update_supplier/accept_reorder_request
    and their delete siblings; see each one's own `_queue_sync_event` call)
    already stamps its own outbox payload with `_changed_fields`, a list of
    column names -- so this reads THAT list back rather than computing a
    second one that could disagree with it.

    A `create` event's payload (create_product et al.) carries no such key
    -- a create overwrites the row wholesale, so every business key the
    payload sets IS what "changed". Falls back to the payload's own keys,
    minus the bookkeeping ones above, for that case and for any payload old
    enough on the wire to predate `_changed_fields` entirely."""
    changed = payload.get('_changed_fields')
    if isinstance(changed, list) and changed:
        return sorted(str(f) for f in changed)
    return sorted(
        k for k in payload.keys()
        if k not in _SYNC_CONFLICT_PAYLOAD_METADATA_KEYS and k != '_changed_fields'
    )


@retail_bp.route('/inventory/sync-conflicts', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
@mt_require_capability(CAP_REPORTS)
def list_sync_conflicts():
    """Every `sync_conflicts` row for this company, most recently detected
    first -- the context a person needs to decide whether a rejected edit
    still matters: which record, what kind of write was discarded, the
    version comparison that caused the rejection, when, and which fields
    the discarded edit would have changed.

    NEVER `incoming_payload` RAW. That column is the whole discarded record
    as JSON, written verbatim by `_record_sync_conflict`
    (commercial_runtime/sync/sync_service.py) precisely because none of the
    five catalogue types ever carries a credential -- but "safe to store" is
    not "safe to render". It is arbitrary operator-entered data (a product
    name, a customer address, a supplier's notes) from a DIFFERENT device,
    which is exactly the trust boundary retail_pos_name_xss_test.js and
    retail_customer_modal_xss_test.js exist to guard on this device's OWN
    catalogue -- dumping a second device's unvalidated payload straight into
    a page would reopen the identical hole one hop upstream. What is
    actually useful to a human deciding whether to redo an edit is WHICH
    FIELDS it touched, not the values -- so `_sync_conflict_changed_fields`
    above reads the field NAMES back out of the payload (a fixed vocabulary
    of column names no operator ever typed) and nothing else leaves this
    function.

    READ-ONLY, deliberately -- there is no resolve/acknowledge route for
    this table and none should be added without deciding the point below on
    purpose:

    `sync_conflicts` carries no `resolved_at_utc` (or equivalent) column,
    unlike `stock_exceptions` -- adding one would be a schema version for a
    queue that does not need it. More fundamentally, there is no honest
    "fix" action to offer. A `stock_exceptions` row names a real, unresolved
    problem (a negative balance) that a human decision closes. A
    `sync_conflicts` row names an edit that ALREADY LOST to a newer one --
    the incoming `row_version` was not strictly greater than the local
    row's, so the local value is, by this system's own conflict rule, the
    more current one. There is nothing here to re-apply: replaying the
    discarded payload over the value that legitimately won would be
    reintroducing the stale write the reject-stale gate was built to
    refuse. The operator's real option, if the discarded edit still
    matters, is to make the edit again on a device that is caught up --
    which is an ordinary catalogue write through the ordinary routes, not a
    special action this route would need to expose.
    """
    cid = _cid()
    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT id, entity_type, entity_id, event_type, local_row_version, "
            "incoming_row_version, incoming_payload, detected_at_utc "
            "FROM sync_conflicts WHERE company_id = ? ORDER BY detected_at_utc DESC",
            (cid,),
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        current_app.logger.exception("list_sync_conflicts failed: %s", exc)
        return jsonify({'status': 'error', 'message': 'Could not list sync conflicts.'}), 400
    finally:
        conn.close()

    conflicts = []
    for r in rows:
        try:
            payload = json.loads(r['incoming_payload']) or {}
        except (TypeError, ValueError):
            # A row whose payload somehow failed to parse still names a
            # real conflict -- the entity/version/when facts below are
            # known from their own columns, independent of the payload.
            # Only the changed-fields summary degrades, to an empty list,
            # rather than the whole row vanishing from the queue.
            payload = {}
        conflicts.append({
            'id': r['id'],
            'entity_type': r['entity_type'],
            'entity_id': r['entity_id'],
            'event_type': r['event_type'],
            'local_row_version': r['local_row_version'],
            'incoming_row_version': r['incoming_row_version'],
            'detected_at_utc': r['detected_at_utc'],
            'changed_fields': _sync_conflict_changed_fields(payload),
        })
    return jsonify({'status': 'success', 'data': {
        'conflicts': conflicts,
        'count': len(conflicts),
    }})


# ── Sync health ───────────────────────────────────────────────────────────────

@retail_bp.route('/sync/health', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def sync_health():
    """Multi-device sync health for this device. Reached through the same
    module-level seam nudge() already uses (never by reaching into app.py's
    own _sync_service variable) -- see commercial_runtime/sync/sync_service.py.

    {"configured": false} is the honest answer on two real installs and is
    NOT an error: SYNC_RELAY_BASE_URL unset (the default -- most installs
    never turn sync on), and Android (Kotlin's SyncCoordinator owns that
    loop). The frontend banner must stay completely silent on it."""
    return jsonify({'status': 'success', 'data': _sync_get_active_health()})


@retail_bp.route('/sync/offline-override', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
# No @require_license_capability here: this route is not in docs/licensing/
# phase7/retail-restriction-capability-matrix.md, which predates
# launch-readiness Phase 7 (offline UX) entirely and does not cover ANY
# sync route -- /sync/health just above carries none either. Adding one
# here would also be moot in practice: a restricted/expired licence already
# blocks retail.sale.create outright (that matrix's own "Always blocked"
# section), so gating this route on licensing state could never deny a
# sale a restricted install was not already refusing.
#
# CAP_CASH_APPROVE, not CAP_STOCK_ADJUST -- Decision 2 in docs/launch-
# readiness/phase7-offline-ux.md: this is already this codebase's "a
# manager accepts an anomaly rather than the system refusing" authority
# (it is what approves a cash variance, approve_cash_variance above).
# CAP_STOCK_ADJUST is master-data editing, a different kind of permission
# that happens to be held by the same people. Reusing an existing code is
# deliberate, not a shortcut: CAPABILITY_CODES (user_accounts.py) is a
# fixed tuple of exactly eight, and that tuple IS the seeding contract --
# a ninth code means a registry migration and a re-seed of every existing
# account, which this stage does not need and was told explicitly not to
# invent.
@mt_require_capability(CAP_CASH_APPROVE)
def sync_offline_override():
    """Launch-readiness Phase 7 stage 7c-ii (docs/launch-readiness/
    phase7-offline-ux.md "Decision 2"; ROADMAP.md's 2026-08-28 "retail
    schema v19" entry): a manager's approval to keep selling past the
    72-hour hard stop `_evaluate_offline_sales_stop` (create_sale, above)
    enforces.

    Records "now" unconditionally whenever sync is configured -- there is
    no precondition that this device must currently BE past the 72-hour
    threshold to record one; a manager who overrides pre-emptively is
    harmless, since Step 2's validity rule (`offline_override_at >
    the most recent successful sync instant`) means the override simply
    does nothing until create_sale's guard would otherwise have blocked,
    and is silently invalidated the moment this device syncs again.

    `{"configured": false}` -- sync not configured on this device -- is
    refused with 400 rather than silently accepted: there is nothing to
    override (create_sale's guard can never block a device with no active
    SyncService), and accepting the call anyway would let a manager
    believe they had granted an approval that governs nothing.

    Every use is logged via `_audit`, carrying the elapsed offline time
    and the unsent count AT THE MOMENT OF APPROVAL -- read from the SAME
    `get_active_health()` snapshot used to decide whether to accept the
    call at all, for the identical single-snapshot reasoning
    `_evaluate_offline_sales_stop` documents on its own `(blocked, health)`
    return shape. An override that left no trace of what was accepted
    would be a bypass, not an approval.
    """
    health = _sync_get_active_health()
    if not health.get('configured'):
        return jsonify({
            'status': 'error',
            'message': 'Sync is not configured on this device; there is nothing to override.',
        }), 400

    hours_offline = (health.get('seconds_since_last_success') or 0) / 3600.0
    pending_count = health.get('pending_count')
    accepted = _sync_record_offline_override()
    if not accepted:
        # Defensive only -- unreachable in practice, since `configured`
        # being True above means `get_active_health()` read a real
        # registered SyncService, and `_sync_record_offline_override` (the
        # module-level dispatcher) consults the identical `_active_service`
        # global. Refused rather than silently swallowed, matching this
        # file's "no error sentinel that can also arrive legitimately"
        # discipline: a caught-but-unexplained failure here would report
        # success on an override that was never actually persisted.
        return jsonify({
            'status': 'error',
            'message': 'Sync is not configured on this device; there is nothing to override.',
        }), 400

    conn = get_retail_conn()
    try:
        _audit(conn, 'OFFLINE_SALES_OVERRIDE', 'sync_freshness', None,
               f'hours_offline={hours_offline:.1f} pending_count={pending_count} approved_by={_uid()}')
        conn.commit()
    finally:
        conn.close()
    return jsonify({'status': 'success', 'data': _sync_get_active_health()})


# ── AI Assistant (sidebar chat) ─────────────────────────────────────────────
# Wires up the previously-dead "AI Assistant" sidebar button (see
# app-shell.js's `hasAI` gate and frontend/sub-ai.js's SubAI module) to a
# real, already-deployed cloud LLM. See config.py's AURA_AI_* block for the
# endpoint/token/timeout configuration and why it defaults to a real,
# already-provisioned endpoint rather than the empty-by-default
# OWNER_LICENSING_BASE_URL / SYNC_RELAY_BASE_URL pattern.

AI_SYSTEM_PREFACE = (
    "You are a helpful assistant embedded in Aura Retail, a point-of-sale "
    "system. Answer in 2-3 short sentences, practically, no long lists "
    "unless the user explicitly asks for step-by-step detail."
)

# Small model (phi3.5:3.8b as of 2026-08-13, ~4GB resident; see config.py's
# AURA_AI_MODEL_NAME comment for the benchmark that picked it over phi3:mini
# and two larger 7B-class candidates) on a small CPU-only droplet -- keep
# the prompt itself small so latency stays reasonable. Caps mirror
# _AI_HISTORY_TURNS below. Real measured throughput on this droplet is
# ~8.5-9.4 tokens/sec (phi3:mini's baseline was ~6.6-7), so
# _AI_REPLY_MAX_TOKENS bounds worst-case generation time almost as much as
# prompt size does -- see _AI_REPLY_MAX_TOKENS' own comment for the incident
# that made this explicit.
_AI_MESSAGE_MAX_CHARS = 4000
_AI_HISTORY_TURN_MAX_CHARS = 2000
_AI_HISTORY_TURNS = 10

# 2026-08-12: a real, realistic prompt ("How do I add a new product?")
# produced a 254-token reply that took 36.5s to generate against the real
# droplet -- verified by direct timed curl, not assumed -- well past the
# then-15s server timeout. The earlier ~7s benchmark this route's comments
# reference was a 2-token "Say OK" reply, not representative. Capping the
# model's own output length (via Ollama's num_predict) directly bounds
# worst-case generation time, on top of the system preface asking for
# brevity -- two independent mitigations, since a model can ignore prompt
# instructions but num_predict is enforced by the server regardless.
_AI_REPLY_MAX_TOKENS = 150

# 2026-08-13, speed pass: direct on-droplet benchmarking (SSH, `GET
# /api/ps`) showed Ollama's default idle-unload cost a real ~4.89s
# `load_duration` on the first request after idle vs ~0.08s once warm -- a
# real, measured latency tax on the FIRST message of a session (or any
# message after a >~5min gap), separate from the per-token generation cost
# `_AI_REPLY_MAX_TOKENS` bounds. `keep_alive` tells Ollama how long to keep
# the model resident after a request; it is a TOP-LEVEL field of the
# `/api/generate` request body -- a sibling of `model`/`prompt`/`stream`,
# NOT a member of `options` (getting this wrong silently does nothing, since
# Ollama ignores unknown fields inside `options`). 30 minutes covers a real
# chat session's think-time between messages without paying the cold-load
# penalty on every turn; it does NOT help the very first request after a
# genuinely idle period. Costs ~4GB of resident RAM on the droplet for up to
# 30 min after the last request -- accepted, this droplet is dedicated to
# this feature (8GB box, model is the only real resident consumer).
_AI_KEEP_ALIVE = '30m'


# ── AI Assistant language support -- 2026-08-13 ─────────────────────────────
# AI_SYSTEM_PREFACE never told the model what language to answer in, so an
# Arabic-speaking user got whatever phi3.5:3.8b happened to default to (see
# retail_ai_language_test.py's module docstring for the real before/after
# transcripts this was verified against). Two signals decide the reply
# language, in priority order:
#   1. The client-supplied UI locale (`AuraI18n.current`, sub-ai.js's `send()`)
#      -- an explicit user choice, sent as the `lang` field on the request
#      body. This WINS even when it disagrees with the message's own script:
#      an English-UI user who happens to type an Arabic product name still
#      gets an English reply. That is a deliberate decision, not an
#      oversight -- the UI locale is the strongest signal of what language
#      the user actually wants to read.
#   2. A cheap heuristic on the message text itself (script-ratio, not
#      "contains any character") -- the fallback for callers that don't send
#      `lang` at all (non-browser callers, older frontend builds) or send a
#      garbage value.
# `_resolve_ai_language()` below is the single choke point for this
# decision -- see its own docstring for why the client value is whitelisted
# rather than trusted.
_AI_SUPPORTED_LANGS = ('en', 'ar')

_AI_LANGUAGE_INSTRUCTIONS = {
    'en': "Reply in English.",
    'ar': "Reply ONLY in Arabic (العربية). Do not answer in English.",
}

# Written in English even for the Arabic case -- a 3.8B instruction-tuned
# model follows English instructions more reliably than Arabic ones in
# practice; the literal 'العربية' token is an in-language anchor, not the
# instruction language itself.

# Arabic, Arabic Supplement, Arabic Extended-A, and Arabic Presentation
# Forms A/B. U+0660-0669 (Arabic-Indic digits) falls inside the first range
# on purpose -- a user typing Arabic-Indic numerals is an Arabic-locale
# user, not a numeric-only message.
_ARABIC_CHAR_RE = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]')
_LATIN_CHAR_RE = re.compile(r'[A-Za-z]')


def _detect_message_language(message):
    """Cheap heuristic fallback for when the caller sends no `lang` field
    (or an invalid one) -- a ratio of scripts, not "contains any Arabic
    character", so an Arabic product name embedded in an English question
    ("how many منتج do I have") doesn't flip the whole reply to Arabic,
    while a genuinely Arabic question with one embedded Latin SKU still
    resolves to Arabic. No tunable threshold constant on purpose: "whichever
    script the caller actually wrote more of" is self-evidently correct at
    both extremes and needs no calibration. Empty or numeric-only input
    returns 'en' (the product's existing default), never raises."""
    text = message or ''
    arabic_count = len(_ARABIC_CHAR_RE.findall(text))
    latin_count = len(_LATIN_CHAR_RE.findall(text))
    return 'ar' if arabic_count > latin_count else 'en'


def _resolve_ai_language(requested, message):
    """Single choke point for the client-locale-wins-but-whitelisted
    decision documented in the module comment above `_AI_SUPPORTED_LANGS`.

    `requested` is `data.get('lang')` from the request body -- untrusted
    client input that gets concatenated into an LLM prompt below
    (_build_ai_prompt). It is WHITELISTED, never interpolated: anything that
    isn't exactly 'en'/'ar' after strip+lower is discarded wholesale, not
    sanitized, because an unvalidated string here would be a direct
    prompt-injection channel into `_AI_LANGUAGE_INSTRUCTIONS`. Non-str
    values (None, a number, a dict/list from a malformed client) hit the
    isinstance guard and fall straight through to the heuristic rather than
    raising -- the language signal is a nice-to-have, never a reason to 500
    a chat message.

    The heuristic fallback is a real, live path (not dead code): it's the
    only signal available for a non-browser caller, an older frontend build
    that never sends `lang`, or a tampered/garbage value."""
    if isinstance(requested, str):
        normalized = requested.strip().lower()
        if normalized in _AI_SUPPORTED_LANGS:
            return normalized
    return _detect_message_language(message)


# ── AI Assistant retrieval context (RAG) -- 2026-08-13 ──────────────────────
# THE single place real business data is allowed to enter the AI prompt.
# Server-side retrieval ONLY: the model is never given database access, a
# tool, or any ability to run its own query -- it only ever sees the small,
# pre-filtered, already company-scoped text _build_ai_context() returns
# below. Every query in this block filters on company_id=cid using the SAME
# _cid() value every other route in this file scopes its reads/writes to
# (see this file's `_cid()` near the top) -- per CLAUDE.md's "every business
# table is company_id-scoped" rule, a query here that skipped that filter
# would be a real cross-tenant data leak into an LLM prompt, not a
# simplification. See retail_ai_rag_multitenant_test.py for the isolation
# test this rule is verified against.
#
# Kept intentionally small and cheap: a lightweight keyword match on the
# user's OWN message (never the model's output) picks AT MOST one data
# category, so an irrelevant message ("hello") costs nothing extra and a
# real business question costs exactly one indexed, LIMIT-bounded query --
# never a full-table dump. This can't be "dump the whole database into every
# prompt" both because the CPU-bound model can't afford the extra prompt
# tokens (see _AI_MESSAGE_MAX_CHARS' comment above) and because a company
# could plausibly have thousands of products/sales rows.
_AI_CONTEXT_MAX_CHARS = 600

# 2026-08-14: _AI_INTENT_KEYWORDS was English-only, so an Arabic business
# question (e.g. "كم عدد المنتجات لدي؟" -- "how many products do I have")
# matched no category at all: _detect_ai_intent() returned None,
# _build_ai_context() short-circuited to '' with zero DB queries, and the
# reply still rendered correctly in Arabic (_resolve_ai_language() is
# independent of this) but with none of the real company data the whole RAG
# upgrade exists to inject. Each English bucket below now has an Arabic
# counterpart covering the same intents, not a literal translation of every
# English entry -- picked to match how these questions are actually phrased
# in Arabic, same as the English list was.
_AI_INTENT_KEYWORDS = {
    # Dict order = match priority: 'low_stock' is checked before the generic
    # 'products' bucket so "what's low on stock" / "what needs reordering"
    # returns the actual low-stock list, not just a plain product count.
    'low_stock': (
        'low stock', 'low on stock', 'reorder', 'running out', 'running low', 'restock', 'out of stock',
        'مخزون منخفض', 'منخفض المخزون', 'إعادة الطلب', 'اعادة الطلب', 'أعد الطلب',
        'نفد المخزون', 'نفدت الكمية', 'أوشك على النفاد', 'اوشك على النفاد', 'إعادة تخزين', 'اعادة تخزين',
    ),
    'sales': (
        'sale', 'sales', 'revenue', 'sold', 'transaction', 'best seller', 'top seller', 'top product', 'income',
        'مبيعات', 'بيع', 'إيراد', 'ايراد', 'الأكثر مبيعا', 'الاكثر مبيعا', 'معاملة', 'دخل',
    ),
    'customers': ('customer', 'client', 'عميل', 'عملاء', 'زبون', 'زبائن'),
    'suppliers': ('supplier', 'vendor', 'مورد', 'موردين', 'موردون'),
    'products':  (
        'product', 'item', 'sku', 'inventory', 'catalog', 'stock',
        'منتج', 'منتجات', 'صنف', 'أصناف', 'اصناف', 'مخزون', 'كتالوج',
    ),
}


def _detect_ai_intent(message):
    """Keyword match ONLY on the user's message text -- cheap, deterministic,
    no model call involved in deciding what to fetch. Returns the first
    matching category (see _AI_INTENT_KEYWORDS' ordering note) or None if the
    message doesn't look like a business-data question at all."""
    text = message.lower()
    for category, keywords in _AI_INTENT_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return category
    return None


def _ai_context_low_stock(conn, cid):
    # launch-readiness Phase 6 stage 6b-iii-a: `AND p.deleted_at_utc IS NULL`
    # added -- an AI-facing count/list must not surface a deleted product any
    # more than the dashboard it mirrors does.
    rows = conn.execute("""
        SELECT p.name, p.sku, COALESCE(b.qty, 0) as on_hand, p.reorder_level
        FROM products p
        LEFT JOIN (SELECT product_id, SUM(quantity_on_hand) as qty
                   FROM inventory_balances WHERE company_id=? GROUP BY product_id) b ON p.id=b.product_id
        WHERE p.company_id=? AND p.status='active' AND p.deleted_at_utc IS NULL
              AND COALESCE(b.qty,0) <= p.reorder_level
        ORDER BY COALESCE(b.qty,0) ASC LIMIT 5
    """, (cid, cid)).fetchall()
    if not rows:
        return "Low-stock check: no products are currently at or below their reorder level."
    items = '; '.join(f"{r['name']} ({r['on_hand']:.0f} on hand, reorder level {r['reorder_level']:.0f})" for r in rows)
    return f"Low-stock products (most urgent first, showing up to 5): {items}."


def _ai_context_products(conn, cid):
    # launch-readiness Phase 6 stage 6b-iii-a: `AND deleted_at_utc IS NULL`
    # added to both queries below, alongside the existing `status='active'`.
    total = conn.execute(
        "SELECT COUNT(*) FROM products WHERE company_id=? AND status='active' AND deleted_at_utc IS NULL", (cid,)
    ).fetchone()[0] or 0
    examples = conn.execute(
        "SELECT name, sku FROM products WHERE company_id=? AND status='active' AND deleted_at_utc IS NULL "
        "ORDER BY created_at DESC LIMIT 3", (cid,)
    ).fetchall()
    text = f"This company has {total} active product(s)."
    if examples:
        text += " Examples: " + ', '.join(f"{r['name']} (SKU {r['sku']})" for r in examples) + "."
    return text


def _ai_context_sales(conn, cid):
    """The assistant must quote the same revenue the dashboard prints, or it
    contradicts the screen the user is looking at while asking -- so this
    reads metrics.revenue()/transactions() rather than being the eighth
    hand-written SUM(total) in this file. "Overall" for top products is
    genuinely lifetime -- the sentence says so out loud -- hence
    period_all_time() rather than a days window; it still goes through the
    same Period machinery so the scoping is explicit instead of implied by
    an absent date predicate.

    ON THE SHOP'S CLOCK, for the same reason as every report route (rule 2 in
    the Reports section header) and with one extra edge of its own: this
    sentence says "TODAY'S sales" in words, to a user who is asking a question
    in a chat box. A number that disagrees with the dashboard card three
    inches away is bad; a SENTENCE asserting it, which the user will believe
    over a chart, is worse. `period_all_time`'s end is also a date, so it takes
    the converted clock too -- a shop already past midnight would otherwise
    have its newest sales fall outside "everything ever recorded"."""
    # ONE converted instant for both periods -- see the Reports note. Two
    # separate reads could straddle midnight and put the two figures in one
    # sentence on different days.
    now = metrics.business_now(conn, cid, datetime.now())
    today_p = metrics.period_today(now)
    revenue = metrics.revenue(conn, cid, today_p)
    txns = metrics.transactions(conn, cid, today_p)
    top = metrics.top_products(conn, cid, metrics.period_all_time(now), limit=3)
    text = f"Today's sales: {revenue:.2f} total across {txns} transaction(s)."
    if top:
        text += " Top-selling products overall: " + ', '.join(
            f"{r['name']} ({r['units_sold']:.0f} sold)" for r in top) + "."
    return text


def _ai_context_customers(conn, cid):
    # launch-readiness Phase 6 stage 6b-iii-a: `AND deleted_at_utc IS NULL`
    # added. No `status='active'` filter here either before or after this
    # stage -- matching this query's own pre-existing (unfiltered-by-status)
    # behaviour rather than introducing an unrelated second change.
    total = conn.execute(
        "SELECT COUNT(*) FROM customers WHERE company_id=? AND deleted_at_utc IS NULL", (cid,)
    ).fetchone()[0] or 0
    return f"This company has {total} customer(s) on file."


def _ai_context_suppliers(conn, cid):
    # launch-readiness Phase 6 stage 6b-iii-a: `AND deleted_at_utc IS NULL`
    # added alongside the existing `status='active'`.
    total = conn.execute(
        "SELECT COUNT(*) FROM suppliers WHERE company_id=? AND status='active' AND deleted_at_utc IS NULL", (cid,)
    ).fetchone()[0] or 0
    return f"This company has {total} active supplier(s) on file."


_AI_CONTEXT_BUILDERS = {
    'low_stock': _ai_context_low_stock,
    'products':  _ai_context_products,
    'sales':     _ai_context_sales,
    'customers': _ai_context_customers,
    'suppliers': _ai_context_suppliers,
}


def _build_ai_context(cid, message):
    """Fetch a small, real, company-scoped data summary for the ONE category
    (if any) _detect_ai_intent() matched. `cid` must be the caller's own
    _cid() -- never trust a company id from the request body, there isn't
    one; this function only ever takes the value the session already
    resolved, same as every other route. Returns '' (no extra context, no
    extra query) for anything that doesn't look like a business question."""
    category = _detect_ai_intent(message)
    if not category:
        return ''
    builder = _AI_CONTEXT_BUILDERS.get(category)
    if not builder:
        return ''
    conn = get_retail_conn()
    try:
        text = builder(conn, cid)
    except Exception as e:
        # Real business data is a nice-to-have for a sharper answer, never a
        # requirement -- a query failure here (locked DB, unusual
        # mid-migration schema state, etc. -- see CLAUDE.md's Sync section)
        # must degrade to a plain chatbot reply, not break the whole route.
        current_app.logger.warning('AI context lookup failed for category=%s: %s', category, type(e).__name__)
        text = ''
    finally:
        conn.close()
    return text[:_AI_CONTEXT_MAX_CHARS]


def _build_ai_prompt(message, history, context='', lang='en'):
    """Ollama's /api/generate (used for both phi3:mini and phi3.5:3.8b, the
    small models this route has run) takes one flat prompt string, not a
    structured chat-messages array, so multi-turn context has to be
    flattened here. `history` is the optional client-supplied
    [{role, content}] list -- only the most recent turns are kept and each
    turn is truncated, so a long-running chat session can't balloon the
    prompt sent to a ~4GB model on a small CPU-only droplet. `context`, if
    non-empty, is the server-fetched, already company-scoped data summary
    from _build_ai_context() -- injected as its own labeled block so the
    model can tell it apart from conversation history, with an explicit
    instruction not to invent numbers when real data was/wasn't supplied.

    `lang` (2026-08-13, 'en' or 'ar', see _resolve_ai_language()) selects
    the language instruction appended right after AI_SYSTEM_PREFACE, before
    the RAG context block -- AI_SYSTEM_PREFACE's own text is never modified,
    since the same brevity instruction applies regardless of language.
    Defaults to 'en' so every existing/future caller that doesn't pass
    `lang` behaves exactly as before this change."""
    lines = [AI_SYSTEM_PREFACE, _AI_LANGUAGE_INSTRUCTIONS.get(lang, _AI_LANGUAGE_INSTRUCTIONS['en'])]
    if context:
        lines.append('')
        lines.append(
            "Real data for this business (use these exact figures if relevant; "
            "do not invent numbers not shown here):"
        )
        lines.append(context)
    lines.append('')
    for turn in (history or [])[-_AI_HISTORY_TURNS:]:
        if not isinstance(turn, dict):
            continue
        role = 'User' if turn.get('role') == 'user' else 'Assistant'
        content = str(turn.get('content') or '')[:_AI_HISTORY_TURN_MAX_CHARS].strip()
        if content:
            lines.append(f"{role}: {content}")
    lines.append(f"User: {message}")
    lines.append("Assistant:")
    return "\n".join(lines)


def _ai_upstream_payload(prompt, stream):
    """The single builder for the request body sent to Ollama's
    /api/generate, used by BOTH the streaming and non-streaming branches of
    ai_chat() below -- one place so the two branches can never silently
    drift on model/cap/keep_alive. `keep_alive` is deliberately a top-level
    key here (a sibling of `model`/`prompt`/`stream`), not nested inside
    `options` -- see _AI_KEEP_ALIVE's own comment for why that placement
    matters."""
    return {
        'model': AURA_AI_MODEL_NAME,
        'prompt': prompt,
        'stream': bool(stream),
        'keep_alive': _AI_KEEP_ALIVE,
        'options': {'num_predict': _AI_REPLY_MAX_TOKENS},
    }


@retail_bp.route('/ai/chat', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
# retail.reports, because of what _build_ai_context() puts in the prompt: a
# company-scoped summary of this install's real business data. The assistant
# can therefore be asked "what did we take today" and answer, which makes this
# route a reporting surface wearing a chat box. Gating it on anything less
# would leave a way to read the numbers that the reports themselves refuse.
@mt_require_capability(CAP_REPORTS)
def ai_chat():
    """Proxy one chat turn to the hosted AI assistant. Must never crash or
    hang the app: any network failure, timeout, non-200, or unparseable
    response returns a clean 503 JSON error -- never a 500/stack trace --
    that the frontend renders as "AI assistant is temporarily unavailable."
    The bearer token lives only in this process's env/config; it is never
    included in the response sent to the browser.

    2026-08-13 (RAG): also injects a small, server-fetched, company-scoped
    business-data summary (see _build_ai_context()) so the assistant can
    answer real questions like "how many products do I have" instead of
    being a pure text chatbot with no access to this install's data. `cid`
    is resolved via _cid() -- the SAME session-derived company id every
    other route in this file uses -- BEFORE any DB read, so a user can only
    ever get their own company's data injected into their own chat prompt.

    2026-08-13 (speed + Arabic): `stream` (request body, bool) opts into an
    NDJSON streaming reply instead of the original one-shot JSON response --
    OPT-IN, not the default, so this route's existing JSON contract stays
    byte-for-byte unchanged for every caller that doesn't ask (matches
    CLAUDE.md's "invisible unless opted in" philosophy, and is what keeps
    retail_ai_rag_multitenant_test.py's non-streaming assertions green with
    zero edits to that file). `lang` (request body, 'en'/'ar') selects the
    reply language via _resolve_ai_language() -- see that function's
    docstring for why the client value is whitelisted rather than trusted."""
    cid = _cid()
    data = request.get_json(silent=True) or {}
    message = str(data.get('message') or '').strip()[:_AI_MESSAGE_MAX_CHARS]
    if not message:
        return jsonify({'success': False, 'error': 'Message is required.'}), 400

    history = data.get('history')
    if not isinstance(history, list):
        history = []

    lang = _resolve_ai_language(data.get('lang'), message)
    # Strict identity check, not truthiness: a sloppy or hostile client
    # sending "true"/1/"yes" for `stream` must NOT silently flip this
    # route's response content-type out from under a caller that only
    # expects the original JSON shape.
    want_stream = data.get('stream') is True
    context = _build_ai_context(cid, message)
    prompt = _build_ai_prompt(message, history, context, lang)
    payload = _ai_upstream_payload(prompt, want_stream)
    headers = {'Authorization': f'Bearer {AURA_AI_BEARER_TOKEN}'}

    if not want_stream:
        try:
            resp = requests.post(
                AURA_AI_ENDPOINT_URL, headers=headers, json=payload,
                timeout=AURA_AI_TIMEOUT_SECONDS,
            )
        except requests.exceptions.RequestException as e:
            # 2026-08-12: real observed latency for a REALISTIC prompt on this
            # model/droplet is ~35s uncapped (see _AI_REPLY_MAX_TOKENS' comment)
            # -- a timeout here now means the upstream host is genuinely
            # unreachable/overloaded even with the reply-length cap in place,
            # not a bug in this route.
            current_app.logger.warning('AI assistant proxy request failed: %s', type(e).__name__)
            return jsonify({'success': False, 'error': 'AI assistant is temporarily unavailable.'}), 503

        if resp.status_code != 200:
            current_app.logger.warning('AI assistant proxy got HTTP %s from upstream', resp.status_code)
            return jsonify({'success': False, 'error': 'AI assistant is temporarily unavailable.'}), 503

        try:
            body = resp.json() or {}
        except ValueError:
            body = {}
        reply = str(body.get('response') or '').strip()
        # 2026-08-13: log only generation stats (never prompt/reply text --
        # that is real customer business data via the RAG context above) so
        # slow-generation and truncation patterns are diagnosable in
        # production without logging anything sensitive.
        current_app.logger.info(
            'AI chat (non-stream): eval_count=%s eval_duration_s=%s done_reason=%s',
            body.get('eval_count'), (body.get('eval_duration') or 0) / 1e9, body.get('done_reason'),
        )

        if not reply:
            return jsonify({'success': False, 'error': 'AI assistant is temporarily unavailable.'}), 503

        return jsonify({'success': True, 'data': {'reply': reply}})

    # ── Streaming branch (2026-08-13 speed pass) ────────────────────────────
    # Total generation time for a realistic ~150-token reply is CPU-bound at
    # ~17-27s on this droplet either way (measured 2026-08-13, see this
    # branch's own commit message for the before/after numbers) -- streaming
    # does NOT change that. What it changes is time-to-first-visible-token:
    # the browser can start rendering the
    # reply as soon as the first fragment arrives instead of waiting for the
    # entire generation to finish, which is what made the non-streaming path
    # above genuinely race its own 45s timeout on this exact realistic
    # prompt (observed 503 in manual testing) -- a stream has no equivalent
    # single deadline, since `timeout=` below becomes a PER-READ (inter-chunk)
    # inactivity timeout once `stream=True`, not a total-request timeout.
    try:
        resp = requests.post(
            AURA_AI_ENDPOINT_URL, headers=headers, json=payload,
            timeout=AURA_AI_TIMEOUT_SECONDS, stream=True,
        )
    except requests.exceptions.RequestException as e:
        current_app.logger.warning('AI assistant proxy request failed: %s', type(e).__name__)
        return jsonify({'success': False, 'error': 'AI assistant is temporarily unavailable.'}), 503

    if resp.status_code != 200:
        # With stream=True, requests returns as soon as response HEADERS
        # arrive, so a non-200 upstream status is still caught here, before
        # any byte is committed to our own client -- that's what lets this
        # branch keep the exact same 503 JSON contract as the non-streaming
        # path for connect failures and auth/upstream errors. Once the
        # generator below starts yielding 200 OK has already been sent to
        # the browser and the response shape is frozen to NDJSON.
        resp.close()
        current_app.logger.warning('AI assistant proxy got HTTP %s from upstream', resp.status_code)
        return jsonify({'success': False, 'error': 'AI assistant is temporarily unavailable.'}), 503

    # stream_with_context() (used below) keeps the request context alive for
    # the generator's lifetime, so current_app.logger would still resolve
    # correctly inside _generate() even without this -- captured into a
    # plain local anyway, defensively, so this generator's logging never
    # depends on Flask's context-preservation behavior at all.
    logger = current_app.logger

    def _generate():
        got_any = False
        try:
            for line in resp.iter_lines():
                if not line:
                    continue  # Ollama sends occasional blank keep-alive lines
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue  # a malformed upstream line must never abort an otherwise-good stream
                frag = obj.get('response')
                if frag:
                    got_any = True
                    yield json.dumps({'delta': frag}) + '\n'
                if obj.get('done'):
                    logger.info(
                        'AI chat (stream): eval_count=%s eval_duration_s=%s done_reason=%s',
                        obj.get('eval_count'), (obj.get('eval_duration') or 0) / 1e9, obj.get('done_reason'),
                    )
                    break
        except Exception as e:
            # Broad by design: ChunkedEncodingError, ConnectionError, a
            # socket read timeout, and any JSON edge case all must collapse
            # to the same friendly in-band error line -- a half-sent NDJSON
            # stream can never surface a raw traceback to the browser, the
            # same "never a 500" contract the non-streaming branch keeps via
            # its try/except above.
            logger.warning('AI assistant stream failed mid-generation: %s', type(e).__name__)
            yield json.dumps({'error': 'AI assistant is temporarily unavailable.'}) + '\n'
            return
        finally:
            resp.close()

        if not got_any:
            yield json.dumps({'error': 'AI assistant is temporarily unavailable.'}) + '\n'
        else:
            yield json.dumps({'done': True}) + '\n'

    # NDJSON with our OWN envelope ({"delta":...}/{"done":true}/{"error":...}),
    # not a raw passthrough of Ollama's wire format: Ollama's final `done`
    # object carries a multi-KB `context` token-id array and other model
    # internals that would waste bandwidth and leak upstream implementation
    # detail into the browser if re-emitted verbatim. NDJSON rather than
    # SSE: EventSource cannot issue a POST, so SSE's one real advantage
    # (a native browser client) is unavailable here anyway, and its
    # `data:`/blank-line framing would be pure overhead on top of the
    # getReader() loop sub-ai.js has to write either way. `ensure_ascii`
    # (json.dumps' default) keeps the wire pure ASCII -- Arabic goes out as
    # \uXXXX escapes -- so no charset negotiation anywhere in the chain
    # (waitress -> WebView2/browser -> TextDecoder -> JSON.parse) can
    # corrupt it; JSON.parse restores the exact characters on the other end.
    return Response(
        stream_with_context(_generate()),
        content_type='application/x-ndjson; charset=utf-8',
        headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'},
    )
