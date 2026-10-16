"""Aura Retail -- sync outbox PAYLOAD COVERAGE ratchet (2026-09-21, widened
2026-09-22 to cover every module that queues a sync event, not just
retail_api.py -- see "── CROSS-MODULE WIDENING" below).

── THE HAZARD ────────────────────────────────────────────────────────────────
Multi-device sync serialises a row into `sync_outbox` using a HARDCODED,
EXPLICIT COLUMN LIST at each `_queue_sync_event(cur, entity_type, id,
event_type, {...})` call site, never `SELECT *`. The receiving side
(`commercial_runtime/sync/sync_service.py`) reads each column it knows with
`p.get(...)` and SILENTLY IGNORES any payload key it does not recognise --
by design, and proven elsewhere in this suite.

The consequence: when a synced table gains a column and nobody edits its
payload dict, the column never converges between devices -- no error, no
warning, no failing test, forever. Schema v39 just added exactly such a pair
(`sale_items.unit_cost`, `products.avg_cost` -- see database/schema.py's own
"cost basis" migration, `_migrate_add_cost_basis` / retail_v39_cost_basis_
test.py), which is what prompted this file.

── CROSS-MODULE WIDENING (2026-09-22) ────────────────────────────────────────
The original version of this file parsed ONLY retail_api.py. That is how
`products.avg_cost` reached retail_api.py's own `create_product` payload
(schema v39) and this ratchet went green, while `api/import_api.py`'s
`_handle_retail_products` create-event -- a SECOND writer of the exact same
`products` row, via the CSV/bulk importer -- had no `avg_cost` key at all,
completely invisible to a ratchet that never looked at that file. A product
onboarded on day one via a catalogue upload (the common case for a new
install) had its cost basis silently stranded on the importing device
forever. `retail_import_sync_test.py::test_imported_product_queues_the_
same_create_event_as_the_normal_route` caught the SYMPTOM by diffing one
pair of payloads by hand; this ratchet is supposed to make the whole CLASS
of bug structurally impossible, and until this widening it could not, for
any table a second module also writes.

Two changes close that gap:

1. MODULE DISCOVERY, not a hardcoded second filename. `_discover_sync_
   modules()` walks every `.py` file under `BACKEND_DIR` (products/retail/
   backend/) and keeps any whose source TEXT contains the literal string
   `_queue_sync_event` -- so a THIRD module that starts queuing sync events
   tomorrow is picked up automatically, the same way import_api.py should
   have been from the start. As of 2026-09-22 this finds four files:
   `api/retail_api.py` and `api/import_api.py` (both genuinely CALL the
   function -- each defines its own local copy, per each file's own
   "PEER ROUTE MODULE" comment on why it is duplicated rather than
   imported) plus `database/schema.py` and `core/retail/reorder_hook.py`
   (both only MENTION the name in a comment/docstring; neither has a real
   `ast.Call` node naming it, so both contribute zero literal and zero
   nonliteral sites to every table and are otherwise inert here -- see
   "TABLES DELIBERATELY LEFT OUT" below for what reorder_hook.py's own,
   differently-named sync-queuing helper means for this file's honesty).
   A plain substring check, not an identifier-boundary regex, on purpose:
   the string is specific enough that the extra precision would not change
   which files match, and a comment-only false positive costs nothing
   (`ast.walk` only ever counts genuine calls).

2. INTERSECTION, not union, across modules. `payload_keys_for`/
   `union_payload_keys` are UNCHANGED and still answer "what does ONE
   file's AST say" -- still used exactly as before to combine
   inventory_movements' six same-module call sites, which this file's
   author verified by hand carry an identical key set. What is NEW is
   `per_module_payload_keys()`, which runs that same per-file extraction
   INDEPENDENTLY for every discovered module and keeps the results
   SEPARATE (keyed by path), and `intersection_of_module_keys()`, which
   takes the set intersection of every writing module's own key set. A
   column present in retail_api.py's payload but ABSENT from import_api.py's
   is, by construction, not in that intersection -- unioning them together
   (the tempting, wrong fix) would have made retail_api.py's coverage
   silently paper over import_api.py's gap, exactly backwards from what a
   ratchet is for. `missing_coverage()` itself did not need to change at
   all: it already only ever asked "is this column in the key set I was
   given", and now the key set it is given is the cross-module
   intersection instead of one file's raw keys. `_assert_full_coverage`
   below names, per missing column, which module(s) DO carry it and which
   don't -- "carried by X but missing from the rest" versus "carried by
   none" -- so a genuine total gap (like the pre-fix `sale_items.unit_cost`)
   and a cross-module inconsistency (like the pre-fix `products.avg_cost`
   import gap) read as different failures, not one undifferentiated red.

── APPROACH: BOTH SIDES DERIVED FROM REAL ARTEFACTS, NEITHER HAND-TYPED ──────
COLUMN side: a REAL migrated retail database, built the same two-stage way
retail_gl_schema_test.py's `_install()` already does (fresh temp
AURA_APP_DATA, the genuine `database.schema.init_retail()` boot path) --
PLUS a second stage that helper does not need: `api.retail_api.
_ensure_credit_schema(conn)`, called directly against the same connection.
That function ALTERs 13 columns onto `payments` alone, plus `customers.
credit_mode/credit_limit/credit_balance`, `suppliers.payment_terms/
credit_balance`, `sales.due_date`, and `retail_settings` itself -- all at
REQUEST time, never in database/schema.py's versioned migration chain (see
this repo's own memory note, "Lazy columns + migration order"). Skipping
this second stage would silently probe an incomplete schema and under-report
real columns -- `sales.due_date` does not exist at all without it, and
`payments` shows 10 columns instead of its real 23. `_ensure_credit_schema`
gates itself with a PROCESS-GLOBAL flag (`_CREDIT_SCHEMA_READY`), not a
per-connection one, so `_install()` below resets that flag before every call
or a second call in the same test process would silently no-op against a
brand new, still-bare database.

PAYLOAD side: parsed with `ast` from every module `_discover_sync_modules()`
finds (see "CROSS-MODULE WIDENING" above), the same tool and the same
reasoning retail_capability_ratchet_ast.py already uses elsewhere in this
suite -- read that file's own docstring on why AST beats regex here (a
regex over `'sale_number':\\s*sale_number` etc. is defeated by the first
reformat, an f-string, or a reordered key; AST reads what Python itself
would execute). This repo's own history is the argument against two
hand-typed copies of the same list: `MarkGeometryParityContractTest` was
rewritten to parse the real SVG after its hardcoded version proved it could
only catch itself -- two copies compare equal to each other while both drift
from the product. `payload_keys_for()` below walks every
`_queue_sync_event(...)` call in ONE module's tree, keeps only calls whose
`entity_type`/`event_type` STRING LITERALS match what a caller asked for,
and collects the STRING-LITERAL keys of the payload dict *only when that
payload is itself a literal `ast.Dict`* at the call site -- a call whose
payload is a bound name, a helper-function call, or `dict(row) | {...}` is
counted separately (`nonliteral_sites`) rather than silently contributing
zero keys, so a shape change this parser cannot see through fails LOUDLY
(`test_*_outbox_payload_covers_every_column` asserts the TOTAL
`nonliteral_sites`, summed across every discovered module, is `0` for every
table this file claims to cover) instead of quietly passing.

Honest limit, stated once rather than per call site: this parser is
SINGLE-SHAPE. It understands exactly the shape every covered table in this
file actually uses -- a literal dict as the 5th positional argument. It does
NOT resolve a payload built by a helper function (`quotation`/`doc_series`,
see below) or reassembled from a fetched row (`products`' own `update`
event, `dict(row) | {...}` -- deliberately a DIFFERENT, self-updating
pattern that this file does not need to ratchet, since only the literal-dict
`create` event is the hazard this file exists to catch, and TRACKED_TABLES
below only ever names a `create` pair. A `dict(row) | {...}` payload is NOT
actually `SELECT *`-sourced anywhere in this codebase, in either
retail_api.py or import_api.py -- both use an explicit column list that must
be kept in sync by hand exactly like a literal dict, and `import_api.py`'s
own two product-`update` call sites were found missing `avg_cost` in their
SELECT during this same pass, fixed alongside the `create` payload even
though this ratchet's `event_type='create'` scope cannot see either one.
That correction narrows the ORIGINAL, pre-2026-09-22 version of this
paragraph's premise; left written down rather than quietly reworded, per
this codebase's own "leave a trail" convention). Widening the parser to
follow a helper function or a SELECT-sourced merge was possible (find the
relevant statement, same technique) but was left undone for the tables this
file explicitly excludes below, in the same "don't chase every shape, chase
the ones actually used and say so" spirit retail_capability_ratchet_ast.py's
own honest-limits section models.

── TABLES COVERED (12) ───────────────────────────────────────────────────────
sales, sale_items, products, categories, customers, suppliers, branches,
inventory_movements, returns, return_items, payments, reorder_requests --
every synced entity type, across every module `_discover_sync_modules()`
finds, whose `create` event queues a literal dict at the call site.
`sale_items`/`sales`/`products` are the three the task that produced the
ORIGINAL version of this file named explicitly; the other six covered by the
first 2026-09-21/22 passes (everything except `payments`) were found by
grepping every `_queue_sync_event(` call site in retail_api.py and share the
identical literal-dict-at-`create` shape. `payments` joined this list
2026-09-22 -- see "PAYMENTS: THE FOLLOW-UP RESOLVED" below for why it was
originally left out and what changed. `reorder_requests` joined it the same
day, in a SEPARATE pass -- see "SECOND-SHAPE WIDENING" below: its `create`
event is queued by `core/retail/reorder_hook.py`'s `_queue_reorder_sync_event`,
a differently-named, differently-shaped duplicate of `_queue_sync_event`'s own
INSERT, which needed its own extractor rather than the `_is_queue_call`
machinery every other table in this list uses. Of these 12, `products` is
also written by `import_api.py` (see "CROSS-MODULE WIDENING" above) and
`inventory_movements` is ALSO written by `import_api.py`'s own stock-
declaration block, verified by hand to carry the identical 13-key shape as
retail_api.py's six call sites; `reorder_requests` is written by
`core/retail/reorder_hook.py` alone; every other table in this list is
written by retail_api.py alone.

── PAYMENTS: THE FOLLOW-UP RESOLVED (2026-09-22) ─────────────────────────────
The original version of this file left `payments` out entirely: its `create`
payload IS a literal dict (retail_api.py, `_record_payment`'s own
`_queue_sync_event(conn, 'payment', pay_uid, 'create', {...})` call), but
four of its live columns (`fx_rate`, `reversal_of`, `voided_by`, `voided_at`)
had no comment anywhere in retail_api.py stating whether their absence was
deliberate or a real gap, and writing a reasoned exclusion for a column that
could not be verified would have been exactly the "rubber stamp" this file's
own EXCLUSIONS-reason-length test exists to prevent.

Read column by column, with every writer traced (not guessed at), all four
turned out to have a real, checkable reason to stay off the wire -- none had
to be added to the payload:

* `fx_rate` -- multi-currency is not reachable code today. `_record_payment`
  has no `fx_rate` parameter at all, its own INSERT bakes in the literal `1`
  rather than binding one, and sync_service.py's payment `create` apply
  branch bakes in the identical literal `1` on its own INSERT -- both sides
  already agree, permanently, because nothing anywhere ever sets this column
  to anything else. `core/retail/general_ledger.py`'s own module docstring
  names multi-currency explicitly as scope this codebase does not yet cover.
* `reversal_of` -- declared by `_ensure_credit_schema`'s lazy ALTER but never
  written by any INSERT or UPDATE against `payments` anywhere in this
  codebase, verified by grepping every one (there is exactly one INSERT and
  four UPDATEs against this table in the whole backend, and none of the five
  touches this column). The one reversal-shaped operation that exists -- a
  bounced/cancelled cheque -- records a brand-new `payments` row through the
  same `_record_payment` funnel (with its own deterministic, syncable uid),
  not a pointer back at the original row through this column. A dead column
  has nothing to converge; if a future write path ever populated it, it
  would also need a `_uid` sidecar the way `sale_id`/`return_id` already get
  one, since a bare `payments.id` is a local autoincrement key with no
  meaning on a receiving device -- exactly the "can this reference survive
  the local-autoincrement-vs-uid question" test this section's own task
  named, and today's honest answer is that it cannot, because nothing tries.
* `voided_by` / `voided_at` -- a void IS a money event, and the money fact
  itself (the payment is no longer live) already converges: `create_sale`'s
  sibling route `void_payment` queues a `payment`/`update` event carrying
  `status: 'voided'`, and sync_service.py's payment apply branch applies
  exactly that transition. What does NOT converge is who performed the void
  and exactly when -- and that is by an existing, audited design decision
  (AUDIT-032A / DEFECT 2), not a gap this file is inventing an excuse for:
  sync_service.py's payment `update` branch accepts ONLY the literal shape
  `{'uid': ..., 'status': 'voided'}`, and its own comment says `amount` "and
  every other column is DELIBERATELY absent from both this check and the
  UPDATE below -- accepting a general update would let one device rewrite
  money it did not ring". That sentence was written before `payments` had a
  ratchet to name its columns individually, but it already covers
  `voided_by`/`voided_at` by construction. Widening the apply side to trust
  exactly those two additional fields (never a general update) is a
  legitimate future change -- but it is a change to sync_service.py, which
  this file does not own and which was under active edit by another agent
  while this analysis was done; it is not something to add unilaterally to
  only the send side while the receive side's own security boundary stays
  put.

`id`, `company_id`, `sale_id`, `idempotency_key` are excluded for the same
reasons this file already gives every other table's local-identity/tenant
columns -- see EXCLUSIONS['payments'] below for each, in full.

── TABLES DELIBERATELY LEFT OUT, AND WHY ─────────────────────────────────────
* `reorder_request` -- retail_api.py's own two `_queue_sync_event` call
  sites for it (`accept_reorder_request`, `decline_reorder_request`) both
  emit `update` events that happen to carry a full snapshot (retail_api.py's
  own comment: "carried here only because the INSERT half needs a full
  snapshot"), never a `create`. CORRECTION (2026-09-22, cross-module
  widening pass): the ORIGINAL version of this bullet said `reorder_request`
  "has NO create outbox event at all", which is not true -- there IS one,
  `core/retail/reorder_hook.py`'s `_maybe_raise_reorder_request` calls
  `_queue_reorder_sync_event(conn, ..., 'create', ...)`, a DELIBERATE,
  documented duplicate of the identical `INSERT INTO sync_outbox` statement
  under a DIFFERENT function name (that module's own docstring: "this module
  runs outside any Flask request context ... cannot import that helper
  without creating a core/retail -> api import ... a deliberate, minimal
  duplicate of that one INSERT statement, not a divergent implementation").
  Because it is a different name, `_discover_sync_modules()`'s substring
  scan for the literal `_queue_sync_event` finds the FILE (it mentions the
  name in that same docstring) but the AST walk finds no matching `ast.Call`
  node in it -- so this ratchet, even widened, still cannot see that
  `create` event's payload. Left out rather than silently claimed as
  covered: adding `reorder_requests` to TRACKED_TABLES would require either
  widening `_is_queue_call` to also accept `_queue_reorder_sync_event` (a
  real design decision -- would it also start matching some future THIRD
  helper name that means something else?) or hand-parsing that one
  function's `return`-free INSERT specially, and `core/retail/
  reorder_hook.py` is not a file this pass owns; reported instead of fixed
  (see the task report this pass was done under). Kept out of TRACKED_TABLES
  to keep this file's per-table comparison anchored to shapes it can
  actually verify rather than a judgment call about a helper it cannot
  parse.

  SECOND CORRECTION (2026-09-22, second-shape widening pass): `reorder_
  request` is NO LONGER left out. This pass owns BOTH `core/retail/
  reorder_hook.py` and this test file, so the two design questions the
  paragraph above left open were answered directly rather than deferred
  again: hand-parsing `_queue_reorder_sync_event`'s own INSERT was chosen
  over widening `_is_queue_call`, because widening it would not actually
  have removed the "which helper means what" judgment call, only hidden it
  inside a fatter matcher -- see `reorder_payload_keys_for`'s own docstring,
  "SECOND-SHAPE WIDENING", for the full reasoning, including why a purely
  INSERT-keyed matcher (ignoring function names entirely) was considered and
  rejected too. `reorder_requests` is now in TRACKED_TABLES with its own
  `EXCLUSIONS` entry; see "TABLES COVERED" above.
* `quotation`, `doc_series` -- payload assembled by a dedicated helper
  function (`_quotation_sync_header_payload`, `_doc_series_sync_payload`)
  and passed in as a bound name, not a literal dict at the `_queue_sync_
  event` call site -- the one non-literal shape this parser deliberately
  does not chase (see the approach section above). `payload_keys_for()`
  correctly reports these as `nonliteral_sites`, not zero keys.
* `retail_setting` -- not a fixed-schema row at all: a generic
  `{skey, svalue}` pair representing ONE arbitrary named setting.
  `retail_settings`' only columns are `company_id`/`skey`/`svalue`, and the
  question this ratchet asks ("does the payload cover every column of the
  row?") does not meaningfully apply to a key-value store where the payload
  IS the row's only two non-tenant columns by construction.
* `user`, `user_permission`, `cheque`, `cheque_event` -- not examined for
  this pass; out of the money/catalogue surface the task that produced this
  file was scoped to.

── EXCLUSIONS ARE EXPLICIT AND REASONED ──────────────────────────────────────
`EXCLUSIONS` below is `{table: {column: reason}}`. `test_every_exclusion_
has_a_real_reason` fails the suite if any reason is missing, not a string,
under `MIN_REASON_LEN` (40) characters, or a single token -- an exclusion
without a real reason is how this ratchet rots into a rubber stamp. Every
reason here is grounded in something actually read in this codebase (a
quoted code comment, an authoritative allowlist constant, or a verified
absence from every apply-branch INSERT in sync_service.py), not a guess.

── PROVEN NOT VACUOUS -- see the tests in the "NOT-VACUOUS PROOF" section ────
Both directions, driven entirely through synthetic inputs (`missing_
coverage()` called with hand-built sets, `payload_keys_for()` parsing
synthetic source strings, and one full synthetic-table + synthetic-source
pipeline test) -- this file owns no right to edit retail_api.py or
database/schema.py, so nothing here touches either.

── CURRENT STATE: GREEN, AND WHY THAT IS TRUSTWORTHY NOW ─────────────────────
As of 2026-09-21 (single-module version of this file), three columns were
genuinely uncovered with no exclusion, on purpose: `sale_items.unit_cost`
and `products.avg_cost` (schema v39's own two new columns from the "cost
basis" migration, database/schema.py's `_migrate_add_cost_basis`) and
`sales.points_redeemed_amount` (an older, unrelated loyalty-redemption gap,
schema v27). All three were closed in retail_api.py by other work landing in
this same tree before this pass started (`sale_item`'s `create` payload now
carries `unit_cost`; `create_product`'s payload and `update_product`'s own
SELECT now both carry `avg_cost`; `create_sale`'s payload now carries
`points_redeemed_amount`) -- this file did not make any of those three
edits; it only widened to ALSO look at `import_api.py`, which is where this
pass's OWN work happened.

That widening immediately found a FOURTH, previously invisible instance of
the identical bug class: `import_api.py`'s own `product`/`create` payload
(the bulk/CSV importer's equivalent of `create_product`) had no `avg_cost`
key at all -- retail_api.py's fix never touched that file, and the
single-module version of this ratchet had no way to see it. Fixed in this
same pass (see "CROSS-MODULE WIDENING" above); `retail_sync_payload_
coverage_test.py`'s own per-table tests below now assert the CROSS-MODULE
intersection, not one file's keys, specifically so this class cannot recur
silently for `products` or any other table more than one module writes.

Do not silence a genuine finding with an exclusion just to turn this suite
green -- an exclusion for a column that actually never reaches a peer
device defeats the entire purpose of this file (stated explicitly because a
future migration WILL reopen one of these, and the tempting "fix" when this
suite goes red again is the wrong one). All 11 tracked tables currently pass
clean, including `payments`: every one of its eight schema-only columns was
traced to a real writer (or the proven absence of one) before being
excluded -- see "PAYMENTS: THE FOLLOW-UP RESOLVED" above -- not waved
through because the table was new to this file.

── A KNOWN GAP THIS FILE DOES NOT FIX ────────────────────────────────────────
There is NO schema-version skew guard anywhere in the sync paths: no route
checks `RETAIL_SCHEMA_VERSION` before push, pull, or apply. Even a
CORRECTLY listed column (everything this file's exclusions and passing
tables cover) is silently dropped by an older peer that has never run the
migration adding it -- `sync_service.py`'s `p.get(key)` on a device that
predates the column simply never sees `key` in the payload it emits, and an
apply branch reading a key the SENDER'S payload never had behaves exactly
like the hazard this file guards against, for a structural reason this file
cannot see (it is not a payload-list bug, it is two devices running
different `RETAIL_SCHEMA_VERSION`s talking to each other at all). Writing
that down as a known, unclosed gap rather than building version gating,
which is a separate design this file was explicitly told not to attempt.

Run:
    pytest products/retail/tests/retail_sync_payload_coverage_test.py -q
"""
import ast
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

#: The literal function name every real sync-queuing call site names (either
#: as a direct definition+call, per retail_api.py/import_api.py's matching
#: "PEER ROUTE MODULE" comments on why each keeps its own copy, or merely
#: mentioned in a comment, per database/schema.py and core/retail/
#: reorder_hook.py -- see this module's docstring, "CROSS-MODULE WIDENING",
#: for why a comment-only file is harmless to include here).
QUEUE_EVENT_FN = '_queue_sync_event'


def _discover_sync_modules(root):
    """Every `.py` file under `root` whose source TEXT contains the literal
    string `QUEUE_EVENT_FN` -- discovered by walking the filesystem rather
    than a hardcoded second (or third) filename, so a module that starts
    queuing sync events tomorrow is picked up automatically instead of
    staying invisible to this ratchet the way `import_api.py` was until
    this pass. See this module's docstring, "CROSS-MODULE WIDENING", for
    the full reasoning and what it found. Returns a sorted list of `Path`s
    for determinism (dict iteration order would otherwise depend on
    filesystem walk order, which is not guaranteed across platforms).
    """
    matches = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != '__pycache__']
        for fname in filenames:
            if not fname.endswith('.py'):
                continue
            path = Path(dirpath) / fname
            try:
                text = path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                continue
            if QUEUE_EVENT_FN in text:
                matches.append(path)
    return sorted(matches)


#: Parsed once at import time -- pure and side-effect-free, unlike `_install()`
#: below, which does real filesystem/database I/O and is deliberately called
#: fresh per test, matching retail_gl_schema_test.py's own per-test `_install()`
#: convention rather than a shared pytest fixture. Keyed by path (not a single
#: tree) so a coverage failure can name exactly which module(s) disagree --
#: see `_assert_full_coverage` below.
_SYNC_MODULE_PATHS = _discover_sync_modules(BACKEND_DIR)
_SYNC_MODULE_TREES = {
    p: ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in _SYNC_MODULE_PATHS
}

_TMP_DIRS = []


# ── Module-level boot for the ONE functional (real-HTTP) test below ─────────
# `require_license_capability`'s db_path (commercial_runtime/licensing_
# contracts/flask_guard.py's `make_capability_guard`) is closed over ONCE, at
# api/retail_api.py's own first import -- that factory's own docstring says
# "Called once per product, at route-module import time" -- and
# `config.DATABASE_DIR` / `commercial_runtime/identity/registry_db.py`'s
# `DB_PATH` are each resolved from `AURA_APP_DATA` at THEIR OWN first import,
# the identical one-shot pattern. None of the three re-reads its directory
# per request. Every other test in this file imports api.retail_api/
# database.schema for the FIRST time from inside `_install()` (called fresh
# per test, each against its own temp dir) -- harmless for them, since none
# of those 25 tests ever go through a Flask route or the licensing guard;
# they call `_ensure_credit_schema(conn)` directly. The ONE test below that
# DOES go through a real route needs those closures bound to a directory
# this file controls, which only works if this block is the very first thing
# in the whole process to import api.retail_api/config/registry_db --
# guaranteed by running at MODULE import time, before any test function
# (including the first coverage test's own `_install()` call) has run.
# Mirrors retail_money_sync_test.py's own module-level boot (see that file's
# docstring for the full "AURA_APP_DATA trap" reasoning), scoped to its own
# dedicated temp dir, cleaned up in `teardown_module` below alongside every
# other temp dir this file mints.
_FUNCTIONAL_DATA = tempfile.mkdtemp(prefix='aura-retail-sync-payment-functional-')
os.makedirs(os.path.join(_FUNCTIONAL_DATA, 'database', 'subsystems'), exist_ok=True)
os.environ.update(AURA_STANDALONE='1', AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=_FUNCTIONAL_DATA)
os.environ.pop('AURA_DEV', None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(_FUNCTIONAL_DATA, product_code='AURA_RETAIL', platform='WINDOWS')

import app as _functional_app_module  # noqa: E402
_FUNCTIONAL_APP = _functional_app_module.init_app()
_FUNCTIONAL_APP.config['TESTING'] = True


def teardown_module(module):
    shutil.rmtree(_FUNCTIONAL_DATA, ignore_errors=True)
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _fresh_app_data(prefix):
    tmp = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp
    return tmp


def _install():
    """A REAL, fully migrated retail DB, lazy columns included -- see this
    module's own docstring, "APPROACH", for why the second stage
    (`_ensure_credit_schema`) is not optional here the way it is for
    retail_gl_schema_test.py's identical-looking `_install()`.
    """
    tmp = _fresh_app_data('aura-retail-sync-coverage-')
    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    db_path = os.path.join(sch.SUBSYS_DIR, 'retail.db')

    import api.retail_api as retail_api_module
    conn = sqlite3.connect(db_path)
    try:
        # Process-global gate, not per-connection -- reset it so THIS
        # connection genuinely gets the lazy ALTER TABLE pass rather than
        # silently no-op'ing because some earlier test in this process
        # already flipped the flag against a DIFFERENT database.
        retail_api_module._CREDIT_SCHEMA_READY = False
        retail_api_module._ensure_credit_schema(conn)
    finally:
        conn.close()
    return db_path


def _table_columns(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return {r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()}
    finally:
        conn.close()


# ── AST extraction ───────────────────────────────────────────────────────────
# `QUEUE_EVENT_FN` is defined once, above, next to `_discover_sync_modules` --
# module discovery and call-site matching must agree on the exact same
# literal name, so it is a single constant, not two copies.


def _is_queue_call(node):
    """`_queue_sync_event(...)` in either the bare or a dotted spelling --
    same dual-spelling posture retail_capability_ratchet_ast.py's
    `is_capability_call` takes, for the identical reason: accepting a
    spelling nobody uses yet costs nothing, and missing one is how a real
    call site goes unexamined."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == QUEUE_EVENT_FN
    if isinstance(func, ast.Attribute):
        return func.attr == QUEUE_EVENT_FN
    return False


def payload_keys_for(tree, entity_type, event_type):
    """AST-derived payload key set for every `_queue_sync_event(cur,
    entity_type, id, event_type, {...})` call in `tree` whose `entity_type`/
    `event_type` STRING LITERALS match the arguments given, restricted to
    call sites whose payload argument is itself a literal `ast.Dict`.

    Returns `(keys, literal_sites, nonliteral_sites)`. A matching call whose
    payload is NOT a literal dict (a bound name, a helper-function call, a
    `dict(row) | {...}` merge) contributes NO keys and is counted in
    `nonliteral_sites` instead of `literal_sites` -- callers that claim full
    coverage of a table must assert `nonliteral_sites == 0`, or a payload
    construction that changed shape would silently look like zero keys
    rather than failing loudly. See this module's docstring, "APPROACH",
    for the full reasoning and its honest limits.
    """
    keys = set()
    literal_sites = 0
    nonliteral_sites = 0
    for node in ast.walk(tree):
        if not _is_queue_call(node):
            continue
        args = node.args
        # The 5-positional-argument shape every call site in retail_api.py
        # actually uses: (cur_or_conn, entity_type, entity_id, event_type,
        # payload). Anything else is not a shape this module understands and
        # is skipped rather than guessed at.
        if len(args) < 5:
            continue
        et_node, _eid_node, evt_node, payload_node = args[1], args[2], args[3], args[4]
        if not (isinstance(et_node, ast.Constant) and et_node.value == entity_type):
            continue
        if not (isinstance(evt_node, ast.Constant) and evt_node.value == event_type):
            continue
        if isinstance(payload_node, ast.Dict):
            literal_sites += 1
            for k in payload_node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
        else:
            nonliteral_sites += 1
    return keys, literal_sites, nonliteral_sites


def union_payload_keys(tree, sources):
    """Union of `payload_keys_for` across several `(entity_type, event_type)`
    pairs -- needed for `inventory_movement`, which queues its `create` event
    from six different call sites (one per `movement_type`: opening_stock,
    purchase_in, transfer_out, transfer_in, sale_out, return_in) that this
    file's author verified by hand all carry the identical key set before
    relying on their union here."""
    keys = set()
    literal_total = 0
    nonliteral_total = 0
    for entity_type, event_type in sources:
        k, lit, nonlit = payload_keys_for(tree, entity_type, event_type)
        keys |= k
        literal_total += lit
        nonliteral_total += nonlit
    return keys, literal_total, nonliteral_total


# ── SECOND-SHAPE WIDENING (2026-09-22, reorder_request) ─────────────────────
# `core/retail/reorder_hook.py` queues its own 'reorder_request'/'create'
# sync_outbox row through `_queue_reorder_sync_event`, a DELIBERATE duplicate
# of `_queue_sync_event`'s own INSERT statement under a DIFFERENT name (that
# module's own docstring: avoiding a core/retail -> api import -- every other
# dependency in this codebase runs the other way). Because the name differs,
# `payload_keys_for`'s `_is_queue_call` -- which only ever looked for literal
# `QUEUE_EVENT_FN` calls -- could not see it, and `reorder_requests` sat out
# of TRACKED_TABLES entirely with a written "TABLES DELIBERATELY LEFT OUT"
# reason (see this module's docstring). This section closes that gap.
#
# CHOICE MADE, AND WHY: teach the ratchet the second call-site name, rather
# than keying purely on the raw `INSERT INTO sync_outbox` statement with no
# function name involved at all (the more general-SOUNDING option). That
# INSERT-keyed approach was worked through on paper first and rejected: it
# does not actually remove the function-name dependency, it only relocates
# it. Look at where each shape keeps its LITERAL values:
#   * `_queue_sync_event`'s own two real definitions (retail_api.py /
#     import_api.py) do their raw INSERT with `entity_type` / `event_type` /
#     `payload` as bound PARAMETER NAMES, not literals -- the literal values
#     only exist one frame up, at every ROUTE HANDLER that calls
#     `_queue_sync_event(cur, 'sale', ..., 'create', {...})`. A matcher keyed
#     on the INSERT statement alone would see three bound names there, not
#     three literals, and would still have to walk back up to the CALLER to
#     recover them -- which is exactly the call-site matching this file
#     already does for that shape.
#   * `_queue_reorder_sync_event` inverts which half is literal where:
#     `entity_type` ('reorder_request') is hardcoded literal INSIDE its own
#     INSERT, and the payload dict is only literal INSIDE that same function
#     body (wrapped in `json.dumps(...)`) -- but `event_type` is a bound
#     parameter there too, only literal at ITS OWN caller
#     (reorder_hook.py's `_maybe_create_request_for_product`).
# A single matcher spanning both shapes would still have to know, per shape,
# which frame to read which fact from -- i.e. it would still be keyed on
# which helper is in play, just with the branching hidden inside one fatter
# function instead of two smaller, named ones. So this file keeps the two
# shapes as two explicit, documented extractors: `payload_keys_for`
# (unchanged, above) for `_queue_sync_event`'s shape, and
# `reorder_payload_keys_for` (below) for `_queue_reorder_sync_event`'s. A
# future THIRD helper with a THIRD shape still needs its own extractor --
# an accepted, stated limit, not an oversight, matching this module's own
# docstring ("APPROACH": "chase the shapes actually used and say so") rather
# than preemptively generalising for a shape nothing uses yet.
#
# `payload_keys_for`/`union_payload_keys` above remain BYTE-FOR-BYTE
# unchanged by this pass -- the merge happens one layer up, in
# `per_module_payload_keys` below, so the single-module machinery's own
# NOT-VACUOUS proofs (and their exact literal/nonliteral-count assertions)
# stay valid with no risk of this widening silently changing their answers.
REORDER_QUEUE_FN = '_queue_reorder_sync_event'
REORDER_ENTITY_TYPE = 'reorder_request'


def _is_reorder_queue_call(node):
    """`_queue_reorder_sync_event(...)` in either spelling -- same
    dual-spelling posture as `_is_queue_call` above, for the same reason:
    accepting a spelling nobody uses yet costs nothing, missing one is how a
    real call site goes unexamined."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == REORDER_QUEUE_FN
    if isinstance(func, ast.Attribute):
        return func.attr == REORDER_QUEUE_FN
    return False


def _find_function_def(tree, name):
    """The first `def name(...)` found anywhere in `tree` (module-level or
    nested), or `None`. `core/retail/reorder_hook.py` defines
    `_queue_reorder_sync_event` exactly once, so "first" is unambiguous for
    the one real module this is ever run against."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _sync_outbox_insert_dict_keys(func_node, entity_type):
    """Walks `func_node`'s OWN body for a `<obj>.execute("... INSERT INTO
    sync_outbox ...", (...))` call -- the shape `_queue_reorder_sync_event`
    uses to build its INSERT directly, rather than delegating to
    `_queue_sync_event`. Column order matches database/schema.py's
    `CREATE TABLE sync_outbox` / every real INSERT against it: `(id,
    entity_type, entity_id, event_type, payload, created_at)` -- so
    `entity_type` is `params[1]` and `payload` is `params[4]`.

    Returns the payload dict's literal string keys when this function's own
    INSERT genuinely targets `entity_type` AND the payload argument is
    exactly `json.dumps({...})` wrapping a literal dict (the one shape
    `_queue_reorder_sync_event` actually uses today). Returns `None` when no
    matching INSERT is found, or the payload isn't that exact shape --
    `None` is distinct from "found, zero keys" so the caller can tell "not
    recognised" apart from "recognised, empty payload" and count the former
    as a nonliteral site instead of silently reporting zero keys.
    """
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == 'execute'):
            continue
        if not node.args:
            continue
        sql_node = node.args[0]
        if not (isinstance(sql_node, ast.Constant) and isinstance(sql_node.value, str)):
            continue
        if 'INSERT INTO sync_outbox' not in sql_node.value:
            continue
        if len(node.args) < 2 or not isinstance(node.args[1], ast.Tuple):
            continue
        params = node.args[1].elts
        if len(params) < 5:
            continue
        et_node, payload_node = params[1], params[4]
        if not (isinstance(et_node, ast.Constant) and et_node.value == entity_type):
            continue
        if (isinstance(payload_node, ast.Call)
                and isinstance(payload_node.func, ast.Attribute)
                and payload_node.func.attr == 'dumps'
                and payload_node.args
                and isinstance(payload_node.args[0], ast.Dict)):
            dict_node = payload_node.args[0]
            return {k.value for k in dict_node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return None


def reorder_payload_keys_for(tree, entity_type, event_type):
    """`payload_keys_for`'s counterpart for `_queue_reorder_sync_event` call
    sites -- see "SECOND-SHAPE WIDENING" above for the full shape and why
    this needed its own extractor. Returns `(keys, literal_sites,
    nonliteral_sites)`, the same shape `payload_keys_for` returns, so
    `per_module_payload_keys` below can fold both together symmetrically.

    Scoped to `REORDER_ENTITY_TYPE` immediately, before touching the tree at
    all: the call-site match below (`event_type` literal at the CALL) and
    the definition-site match inside `_sync_outbox_insert_dict_keys`
    (`entity_type` literal INSIDE the function) are two independent checks
    against two different nodes. Without this early return, a module
    carrying `_queue_reorder_sync_event`'s one real call -- whose literal
    `event_type` is 'create', the SAME literal nearly every OTHER tracked
    table's `create` event also uses -- would spuriously report a
    nonliteral site for every one of those unrelated tables purely because
    their `entity_type` never matched 'reorder_request'.
    `test_reorder_payload_keys_for_ignores_unrelated_entity_types` below
    mutation-proves this guard actually matters, not merely reads as a
    plausible-sounding optimisation.
    """
    if entity_type != REORDER_ENTITY_TYPE:
        return set(), 0, 0
    keys = set()
    literal_sites = 0
    nonliteral_sites = 0
    for node in ast.walk(tree):
        if not _is_reorder_queue_call(node):
            continue
        evt_value = None
        # Positional signature (core/retail/reorder_hook.py):
        # (conn, request_id, company_id, branch_id, product_id, status,
        # draft_message, timestamp, event_type, *, row_version=None,
        # updated_at_utc=None) -- event_type is the 9th positional
        # parameter, index 8.
        if len(node.args) > 8 and isinstance(node.args[8], ast.Constant):
            evt_value = node.args[8].value
        else:
            for kw in node.keywords:
                if kw.arg == 'event_type' and isinstance(kw.value, ast.Constant):
                    evt_value = kw.value.value
        if evt_value != event_type:
            continue
        func_def = _find_function_def(tree, REORDER_QUEUE_FN)
        if func_def is None:
            nonliteral_sites += 1
            continue
        found_keys = _sync_outbox_insert_dict_keys(func_def, entity_type)
        if found_keys is None:
            nonliteral_sites += 1
        else:
            literal_sites += 1
            keys |= found_keys
    return keys, literal_sites, nonliteral_sites


def missing_coverage(schema_columns, payload_keys, exclusions):
    """The ratchet's actual question, as a pure function: which schema
    columns are covered by NEITHER the payload key set NOR a reasoned
    exclusion? Deliberately kept pure and separate from any AST/DB plumbing
    so it can be proven correct with synthetic inputs alone -- see the
    NOT-VACUOUS PROOF section below.
    """
    return set(schema_columns) - set(payload_keys) - set(exclusions.keys())


# ── Cross-module coverage (2026-09-22 widening) ──────────────────────────────
# See this module's docstring, "CROSS-MODULE WIDENING", for the full "why".
# `payload_keys_for`/`union_payload_keys` above are UNCHANGED and still
# answer "what does ONE module's AST say" -- the functions below run that
# per-module, keep every module's result SEPARATE, and only THEN combine
# them, so a column present in one writer and absent from another cannot be
# masked the way a plain union across files would mask it.

def per_module_payload_keys(trees_by_path, sources):
    """`union_payload_keys`, run independently against EACH discovered
    module's own AST tree. Returns `{path: (keys, literal_sites,
    nonliteral_sites)}` for every module that has AT LEAST ONE call (literal
    or not) matching `sources` -- a module with zero matching calls is
    omitted entirely, so it can never be mistaken for "this module covers
    the table with an empty payload" (that would silently make the
    intersection below empty for every table with even one non-writing
    module in `trees_by_path`, which is most of them)."""
    result = {}
    for path, tree in trees_by_path.items():
        keys, lit, nonlit = union_payload_keys(tree, sources)
        # SECOND-SHAPE WIDENING (2026-09-22): also fold in any
        # `_queue_reorder_sync_event`-shaped sites this module carries --
        # see `reorder_payload_keys_for`'s own docstring above for the full
        # "why". `reorder_payload_keys_for` early-returns (set(), 0, 0) for
        # every entity_type except 'reorder_request', so this is a no-op for
        # every module/table pair except core/retail/reorder_hook.py's own
        # ('reorder_request', 'create').
        for entity_type, event_type in sources:
            rkeys, rlit, rnonlit = reorder_payload_keys_for(tree, entity_type, event_type)
            keys |= rkeys
            lit += rlit
            nonlit += rnonlit
        if lit or nonlit:
            result[path] = (keys, lit, nonlit)
    return result


def intersection_of_module_keys(per_module):
    """The cross-module coverage question, as a pure function: which columns
    does EVERY module that writes this table's payload agree to carry? A
    column present in module A but missing from module B is, by
    construction, not in this intersection -- this is the operation that
    catches "present on one path, missing from another" (the real
    `products.avg_cost` bug this pass fixed: present in retail_api.py's
    create_product, absent from import_api.py's create-event, until this
    pass added it there too). Deliberately excludes any module whose
    `literal_sites` is 0: a purely-nonliteral writer contributes no keys
    either way, and `_assert_full_coverage`'s own nonliteral-sites
    assertion is what should catch that module, loudly -- letting it also
    empty out the intersection here would produce a confusing SECOND
    failure mode for the identical root cause. Returns the empty set if no
    module has a literal site at all, matching `missing_coverage`'s own
    "no coverage at all" behaviour when given an empty key set."""
    literal_keysets = [keys for keys, lit, _ in per_module.values() if lit > 0]
    if not literal_keysets:
        return set()
    return set.intersection(*literal_keysets)


# ── NESTED snapshots (W2b/W3a, schema v50) ───────────────────────────────────
# A `sale_item` `create` payload now carries two NESTED snapshots: `modifiers`
# (a list of dicts, one per extra) and `promotion` (null or one dict). Their
# rows live in `sale_item_modifiers` / `sale_item_promotions`, two real tables
# with real columns -- so a column added to either must reach the wire or be
# excluded with a reason, exactly like a top-level table. The top-level
# extractor above cannot see them: the nested value is not a literal at the
# `_queue_sync_event` call but a NAME (`modifier_snapshots_wire`,
# `promotion_snapshot`) bound earlier in the SAME function.
#
# `nested_payload_keys_for` therefore resolves that name inside the enclosing
# function -- the one extra shape it understands, stated plainly:
#   * `NAME = {literal dict}`            (a snapshot built in one go), and
#   * `NAME.append({literal dict})`      (a list of snapshots built in a loop).
# `NAME = None` is ignored (the "no promotion" arm). Anything else bound to the
# name -- a helper call, a comprehension, a dict(row) merge -- cannot be read,
# and is counted as a NONLITERAL site so the shape change fails LOUDLY instead
# of the table silently reading as "covers nothing" or "covers everything".
# A payload key that holds a literal dict / list-of-dicts directly is read too.

def _dict_literal_keys(node):
    return {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _resolve_nested_keys(func_def, name):
    """Keys of every literal dict bound to / appended to `name` inside
    `func_def`; `(keys, literal_count, nonliteral_count)`."""
    keys, literal, nonliteral = set(), 0, 0
    for node in ast.walk(func_def):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    if isinstance(node.value, ast.Dict):
                        keys |= _dict_literal_keys(node.value)
                        literal += 1
                    elif isinstance(node.value, ast.Constant) and node.value.value is None:
                        pass
                    elif isinstance(node.value, (ast.List, ast.Tuple)) and not node.value.elts:
                        pass
                    else:
                        nonliteral += 1
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == 'append' and isinstance(node.func.value, ast.Name)
              and node.func.value.id == name and node.args):
            if isinstance(node.args[0], ast.Dict):
                keys |= _dict_literal_keys(node.args[0])
                literal += 1
            else:
                nonliteral += 1
    return keys, literal, nonliteral


def nested_payload_keys_for(tree, entity_type, event_type, payload_key):
    """Keys of the nested snapshot dict(s) a `_queue_sync_event(<entity_type>,
    <event_type>, {...})` payload carries under `payload_key`. Returns
    `(keys, literal_sites, nonliteral_sites)` like `payload_keys_for`."""
    keys, literal, nonliteral = set(), 0, 0
    for func_def in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for call in ast.walk(func_def):
            if not _is_queue_call(call) or len(call.args) < 5:
                continue
            et, evt, payload = call.args[1], call.args[3], call.args[4]
            if not (isinstance(et, ast.Constant) and et.value == entity_type
                    and isinstance(evt, ast.Constant) and evt.value == event_type):
                continue
            if not isinstance(payload, ast.Dict):
                nonliteral += 1
                continue
            value = None
            for k, v in zip(payload.keys, payload.values):
                if isinstance(k, ast.Constant) and k.value == payload_key:
                    value = v
            if value is None:
                # The key is absent from this writer's payload altogether: a
                # real, literal site that carries NOTHING -- the intersection
                # then correctly reports every column as uncovered.
                literal += 1
                continue
            if isinstance(value, ast.Dict):
                keys |= _dict_literal_keys(value)
                literal += 1
            elif isinstance(value, ast.Name):
                k2, lit2, non2 = _resolve_nested_keys(func_def, value.id)
                keys |= k2
                literal += lit2 if lit2 else 0
                nonliteral += non2
                if not lit2 and not non2:
                    nonliteral += 1      # the name is bound to nothing we can read
            else:
                nonliteral += 1
    return keys, literal, nonliteral


def per_module_nested_keys(trees_by_path, entity_type, event_type, payload_key):
    result = {}
    for path, tree in trees_by_path.items():
        keys, lit, nonlit = nested_payload_keys_for(tree, entity_type, event_type, payload_key)
        if lit or nonlit:
            result[path] = (keys, lit, nonlit)
    return result


# ── Which (entity_type, event_type) pair(s) are this table's full-row
#    baseline, and the reasoned exclusions for every column its baseline
#    legitimately does not carry. See this module's docstring for the full
#    "why" behind every table and every exclusion. ───────────────────────────

TRACKED_TABLES = {
    'sales': [('sale', 'create')],
    'sale_items': [('sale_item', 'create')],
    'products': [('product', 'create')],
    'categories': [('category', 'create')],
    'customers': [('customer', 'create')],
    'suppliers': [('supplier', 'create')],
    'branches': [('branch', 'create')],
    'inventory_movements': [('inventory_movement', 'create')],
    'returns': [('return', 'create')],
    'return_items': [('return_item', 'create')],
    'payments': [('payment', 'create')],
    # reorder_requests joined 2026-09-22 -- see this module's docstring,
    # "SECOND-SHAPE WIDENING", for why its ('reorder_request', 'create')
    # event needed its own extractor (`reorder_payload_keys_for`) rather
    # than the `_is_queue_call` machinery every table above this comment
    # uses.
    'reorder_requests': [('reorder_request', 'create')],
    # v44: the one entity that carries a single-computer shop's balance across
    # the switch to derived balances; emitted by core/retail/party_balance.py.
    'party_balance_adjustments': [('party_balance_adjustment', 'create')],
    # W4a (schema v45): purchase orders now travel between computers -- the
    # header's `create` and each line's `create`. The later header `update`
    # ({uid, status, received_at}) is a status merge, not a full-row baseline.
    #
    # W4a-2 (schema v46): the header's `update` now carries the two SET-ONCE
    # stamps `cancelled_at` / `closed_at` ({uid, cancelled_at, closed_at}), so
    # it joins the sources: a module's keys are the UNION over its listed
    # sources, which is how a column legitimately carried only by the update
    # (the stamps) counts as covered, while `ap_basis` -- immutable, so it must
    # ride the create -- is still found only if some source carries it.
    'purchase_orders': [('purchase_order', 'create'), ('purchase_order', 'update')],
    'purchase_order_items': [('purchase_order_item', 'create')],
    # W4a-2: a delivery is an immutable synced fact -- header and lines.
    'purchase_order_receipts': [('purchase_order_receipt', 'create')],
    'purchase_order_receipt_lines': [('purchase_order_receipt_line', 'create')],
    # W3b (schema v47): a transfer's header and lines now travel between
    # computers. Both are built from a create PLUS an update, so each table's
    # sources list both: the create carries what never changes (branches by
    # uid, creator, line product and quantity sent) and the update carries
    # the facts a send/receive learn (status and its four stamps; a line's
    # unit_cost and quantity_received). A column only the update carries
    # counts as covered; one neither carries is found.
    'stock_transfers': [('stock_transfer', 'create'), ('stock_transfer', 'update')],
    'stock_transfer_items': [('stock_transfer_item', 'create'), ('stock_transfer_item', 'update')],
    # STOCK-TAKE (schema v57): a count's header and lines travel between computers. The header is
    # a `create` (branch by uid, note, who/when) plus `update`s carrying the state and its stamps;
    # a line is the SAME full snapshot on `create` and `update` (its uid is derived from count +
    # product, so either computer may be first to count it).
    'stock_counts': [('stock_count', 'create'), ('stock_count', 'update')],
    'stock_count_lines': [('stock_count_line', 'create'), ('stock_count_line', 'update')],
    # W2a (schema v48): a loyalty ledger row is an immutable synced fact, so its
    # `create` is the whole baseline (no update exists). Every column of
    # `loyalty_ledger` must ride it or be excluded below with a reason. The
    # SALE side of the same wave -- `sales.points_earned` -- needs no entry
    # here: `sales` is already tracked, and the new column has NO exclusion, so
    # the sales test goes red the moment the sale payload stops carrying it.
    'loyalty_ledger': [('loyalty_entry', 'create')],
    # W2b/W3a (schema v50): promotions and the extras configuration now travel
    # between computers. Each is a mutable row announced by a `create` (the full
    # snapshot) and `update`s that re-send the full snapshot plus
    # `_changed_fields`, so both events are listed and both must be literal.
    'promotions': [('promotion', 'create'), ('promotion', 'update')],
    'modifier_groups': [('modifier_group', 'create'), ('modifier_group', 'update')],
    'modifier_options': [('modifier_option', 'create'), ('modifier_option', 'update')],
    'product_modifier_groups': [('product_modifier_group', 'create'),
                                ('product_modifier_group', 'update')],
}

# W2b/W3a (schema v50): tables whose rows ride NESTED inside another entity's
# payload. `{table: (entity_type, event_type, payload_key, {wire key: column})}`.
# The wire keys that differ from their column name are mapped here (the
# snapshot columns carry a `_snapshot` suffix on the table, none on the wire);
# `test_nested_wire_mapping_names_real_columns` proves every mapped target is a
# real column, so a typo here goes red instead of quietly covering nothing.
NESTED_TABLES = {
    'sale_item_modifiers': ('sale_item', 'create', 'modifiers', {
        'group_name': 'group_name_snapshot', 'name': 'name_snapshot',
        'price_delta': 'price_delta_snapshot', 'cost_delta': 'cost_delta_snapshot',
    }),
    'sale_item_promotions': ('sale_item', 'create', 'promotion', {
        'name': 'name_snapshot', 'discount_pct': 'discount_pct_snapshot',
        'discount_amount': 'discount_amount_snapshot',
        # The wire names the promotion by UID; the table's `promotion_id` is the
        # receiving computer's own id for it, resolved from this uid.
        'promotion_uid': 'promotion_id',
    }),
}

MIN_REASON_LEN = 40

EXCLUSIONS = {
    'sales': {
        'id': "Local SQLite autoincrement primary key; sales is a RETAIL_UID_TABLES "
              "entity whose real cross-device identity is `uid` (already in the "
              "payload), so the sender's own local `id` is meaningless on a "
              "receiving device and is never sent.",
        'company_id': "Tenant-isolation key derived locally at onboarding, not a "
                       "business attribute of the sale; the receiving device always "
                       "stamps its own company_id on apply, never the sender's.",
        'idempotency_key': "Carries a bare, not company-scoped, UNIQUE constraint "
                            "whose whole job is letting THIS device detect its own "
                            "resubmission before this row even exists; retail_api.py's "
                            "own comment at this call site says the sync `uid` unique "
                            "index is the correct, collision-safe idempotency key for "
                            "a pulled row instead.",
        'session_id': "A local cash_sessions.id foreign key; cash_sessions is not one "
                       "of the synced entity types, so no matching row will ever exist "
                       "on a receiving device, and retail_api.py's own comment says the "
                       "apply side must never try to preserve or remap it.",
        # 'points_redeemed_amount' deliberately has NO entry -- see this
        # module's docstring, "CURRENT STATE": a real, uncovered gap.
    },
    'sale_items': {
        'id': "Local SQLite autoincrement primary key with no cross-device meaning; "
              "sale_items is resolved on the wire by its own `uid` column, which the "
              "payload already carries.",
        'sale_id': "A local autoincrement foreign key into this device's own "
                   "sales.id; the payload carries sale_uid instead, and the "
                   "receiving device resolves the real parent by looking up "
                   "sales.uid = sale_uid locally, per retail_api.py's own comment "
                   "at this call site.",
        # 'unit_cost' deliberately has NO entry -- see this module's
        # docstring, "CURRENT STATE": the hazard this file was written for.
    },
    'products': {
        'company_id': "Tenant-isolation key; the receiving device stamps its own "
                       "company_id on apply rather than trusting the sender's, "
                       "identical to every other synced catalogue entity in this file.",
        'status': "sync_service.py's product apply branch reads this with "
                  "p.get('status', 'active') specifically because a create event's "
                  "payload never carries it, preserving the create-time default the "
                  "local INSERT already applies -- deliberate, not an oversight, per "
                  "that branch's own comment.",
        'deleted_at_utc': "A row emitted by a `create` event has, by definition, "
                          "never been deleted; the apply-side INSERT leaves this "
                          "column at its implicit NULL exactly like the local INSERT "
                          "that produced the row in the first place.",
        'created_at': "Row-insertion bookkeeping timestamp, not a business-meaningful "
                      "event time (unlike sales/returns/inventory_movements' "
                      "created_at, which their payloads DO carry); every product "
                      "apply-branch INSERT in sync_service.py omits it too, "
                      "confirming the omission is consistent product-wide, not a "
                      "one-off gap.",
        # 'avg_cost' deliberately has NO entry -- see this module's
        # docstring, "CURRENT STATE": schema v39's other uncovered gap.
    },
    'categories': {
        'company_id': "Tenant-isolation key; retail_api.py's own comment on this "
                       "call site says explicitly that the receiving device stamps "
                       "its own company_id, never the sender's.",
        'created_at': "Row-insertion bookkeeping timestamp; sync_service.py's "
                      "category apply-branch INSERT never references it either, the "
                      "same consistent master-data-wide omission products/customers/"
                      "suppliers share.",
        'deleted_at_utc': "A row emitted by a `create` event has never been deleted "
                          "yet; NULL is the correct value on both devices without "
                          "being sent.",
    },
    'customers': {
        'company_id': "Tenant-isolation key, stamped locally by the receiving "
                       "device on apply, never taken from the wire.",
        'status': "Parameterized the same way as the product upsert -- "
                  "sync_service.py's customer branch comment says so explicitly -- "
                  "so a create event correctly never carries it.",
        'created_at': "Row-insertion bookkeeping timestamp; the customer "
                      "apply-branch INSERT in sync_service.py never references it, "
                      "matching every other master-data entity in this file.",
        'deleted_at_utc': "A freshly created customer has never been deleted; NULL "
                          "is correct on both devices without being sent.",
        'loyalty_points': "Not in SYNCED_CUSTOMER_FIELDS (retail_api.py's own "
                          "authoritative allowlist of which customer attributes sync "
                          "at all); retail_api.py's _adjust_credit docstring names "
                          "this column device-local and never synced, deliberately, "
                          "alongside total_spent and credit_balance.",
        'total_spent': "Not in SYNCED_CUSTOMER_FIELDS; retail_api.py's "
                       "_adjust_credit docstring explicitly names total_spent, "
                       "alongside loyalty_points, as a device-local accumulator "
                       "this sync path deliberately never carries.",
        # REHEARSAL-1007 B2: 'credit_mode'/'credit_limit' deliberately have NO entry any
        # more -- they are synced customer attributes now and must stay on the wire.
        'credit_balance': "retail_api.py's _adjust_credit docstring states plainly "
                          "that credit_balance is 'device-local, never synced ... "
                          "and that omission is the point, not an oversight', found "
                          "by searching every UPDATE against this table rather than "
                          "trusting a design doc's count.",
    },
    'suppliers': {
        'company_id': "Tenant-isolation key, stamped locally by the receiving "
                       "device on apply, never taken from the wire, identical to "
                       "every other synced entity.",
        'status': "sync_service.py's supplier branch comment says explicitly this "
                  "is parameterized for the same reason as the product upsert, so "
                  "a create event correctly never carries it.",
        'created_at': "Row-insertion bookkeeping timestamp; the supplier "
                      "apply-branch INSERT in sync_service.py never references it, "
                      "matching every other master-data entity in this file.",
        'deleted_at_utc': "A freshly created supplier has never been deleted; NULL "
                          "is correct on both devices without being sent.",
        'payment_terms': "Not in SYNCED_SUPPLIER_FIELDS, the code-owned allowlist "
                         "of the only supplier attributes this entity type's sync "
                         "payload is responsible for; out of scope by that same "
                         "design.",
        'credit_balance': "sync_service.py's supplier branch comment says this is "
                          "'Distinct from credit_balance, which never appears in "
                          "this payload -- see retail_api.py's _adjust_credit', "
                          "whose own docstring calls the omission deliberate, not "
                          "an oversight.",
        'min_order_value': "Not in SYNCED_SUPPLIER_FIELDS and read only by a "
                           "reporting query elsewhere in retail_api.py; never "
                           "written through any synced entity's apply branch, so "
                           "it is out of this entity type's scope by the same "
                           "design as payment_terms.",
    },
    'branches': {
        'id': "Local SQLite autoincrement primary key with no cross-device "
              "meaning; branches is in RETAIL_UID_TABLES specifically because its "
              "real wire identity is `uid`, which the payload already carries.",
        'company_id': "Tenant-isolation key, stamped locally by the receiving "
                       "device on apply, identical to every other synced entity.",
        'created_at': "Row-insertion bookkeeping timestamp; the branch "
                      "apply-branch INSERT in sync_service.py never references it "
                      "either.",
    },
    'inventory_movements': {
        'id': "Local SQLite autoincrement primary key with no cross-device "
              "meaning; this table's real wire identity is `uid`, which every "
              "create payload already carries.",
        'company_id': "Tenant-isolation key, stamped locally by the receiving "
                       "device on apply, identical to every other synced entity.",
        'created_at': "sync_service.py's own comment at this apply branch states "
                      "created_at is 'deliberately OMITTED from both the column "
                      "list and the payload (falls through to the column's own "
                      "DEFAULT CURRENT_TIMESTAMP at apply time)'; the separate "
                      "created_at_utc column IS synced and is the wire-accurate "
                      "movement time.",
    },
    'returns': {
        'id': "Local SQLite autoincrement primary key with no cross-device "
              "meaning; returns is a RETAIL_UID_TABLES entity whose real wire "
              "identity is `uid`, already in the payload.",
        'company_id': "Tenant-isolation key, stamped locally by the receiving "
                       "device on apply, identical to every other synced entity.",
        'sale_id': "A local autoincrement foreign key into this device's own "
                   "sales.id; the payload carries sale_uid instead, and "
                   "retail_api.py's own comment says the apply side resolves the "
                   "real parent by looking up sales.uid = sale_uid locally.",
        'session_id': "retail_api.py's own comment at this call site says this "
                      "shares the identical company_id/idempotency_key/session_id "
                      "omission reasoning as create_sale's event -- a local "
                      "cash_sessions FK with no matching synced entity.",
        'idempotency_key': "Same bare, not company-scoped, resubmission-detection "
                           "key as sales.idempotency_key; retail_api.py's own "
                           "comment at this call site cites create_sale's identical "
                           "reasoning for omitting it.",
    },
    'return_items': {
        'id': "Local SQLite autoincrement primary key with no cross-device "
              "meaning; this table's real wire identity is `uid`, already in the "
              "payload.",
        'return_id': "A local autoincrement foreign key into this device's own "
                     "returns.id; the payload carries return_uid instead, resolved "
                     "locally by the receiving device the same way sale_item "
                     "resolves its own parent.",
        'sale_item_id': "A local integer FK with no cross-device meaning; "
                        "retail_api.py's own comment says the payload carries "
                        "sale_item_uid instead as a best-effort resolution aid for "
                        "a LATER sync wave, and the apply branch does not consume "
                        "it yet either.",
    },
    # payments joined this ratchet 2026-09-22 -- see this module's docstring,
    # "PAYMENTS: THE FOLLOW-UP RESOLVED", for the full column-by-column
    # investigation behind every reason below. Every one of these eight was
    # traced to a real writer (or a verified absence of one), not guessed at.
    'payments': {
        'id': "Local SQLite autoincrement primary key; payments is in "
              "RETAIL_UID_TABLES (database/schema.py) specifically because its "
              "real wire identity is `uid`, which the create payload already "
              "carries -- the sender's own local id is meaningless on a "
              "receiving device, identical to every other RETAIL_UID_TABLES "
              "entity in this file.",
        'company_id': "Tenant-isolation key; sync_service.py's payment create "
                      "branch INSERTs `local_company_id`, never a value read off "
                      "the incoming payload, identical to every other synced "
                      "entity in this file.",
        'sale_id': "A local autoincrement foreign key into this device's own "
                   "sales.id; the payload carries `sale_uid` instead (only "
                   "populated when related_type=='sale'), and sync_service.py's "
                   "payment create branch resolves the real parent by looking up "
                   "sales.uid = sale_uid locally -- the identical pattern "
                   "sale_items/returns already use in this file.",
        'idempotency_key': "A base schema column (database/schema.py's payments "
                           "CREATE TABLE) that `_record_payment`'s own INSERT "
                           "never populates -- its column list omits "
                           "idempotency_key entirely, confirmed by grepping every "
                           "INSERT against this table (there is exactly one in "
                           "the whole backend). Unlike sales/returns, which use "
                           "this column for real client-resubmission detection, "
                           "payments relies on the deterministic `pay_uid` "
                           "parameter for the identical collision-safe role (see "
                           "_record_payment's own cheque-crossing docstring), so "
                           "every payments.idempotency_key is NULL forever and "
                           "there is no live value to ever put on the wire.",
        'fx_rate': "Multi-currency is not reachable code today: `_record_payment` "
                  "has no fx_rate parameter at all, its own INSERT bakes in the "
                  "literal `1` rather than binding one, and sync_service.py's "
                  "payment create branch's own INSERT bakes in the identical "
                  "literal `1` -- both sides already permanently agree because "
                  "nothing anywhere ever sets this column to anything else. "
                  "core/retail/general_ledger.py's own module docstring names "
                  "multi-currency explicitly as scope this codebase does not "
                  "yet cover.",
        'reversal_of': "Declared by _ensure_credit_schema's lazy ALTER but never "
                       "written by any INSERT or UPDATE against payments "
                       "anywhere in this codebase -- verified by grepping every "
                       "one (one INSERT, four UPDATEs total in the whole "
                       "backend, none touching this column). The one "
                       "reversal-shaped operation that exists, a bounced/"
                       "cancelled cheque, records a brand-new payments row "
                       "through the same _record_payment funnel with its own "
                       "syncable uid rather than pointing back at the original "
                       "through this column. A dead column has nothing to "
                       "converge; even if a future write path populated it, a "
                       "bare payments.id is a local autoincrement key with no "
                       "meaning on a receiving device and would need a _uid "
                       "sidecar this column does not have today, the same "
                       "resolution sale_id/return_id already needed and got.",
        'voided_by': "Deliberately excluded from the wire by an existing, "
                     "audited design (AUDIT-032A / DEFECT 2): sync_service.py's "
                     "payment `update` apply branch accepts ONLY the literal "
                     "shape {'uid': ..., 'status': 'voided'} that retail_api.py's "
                     "void_payment route queues, and that branch's own comment "
                     "says 'amount (and every other column) is DELIBERATELY "
                     "absent from both this check and the UPDATE below -- "
                     "accepting a general update would let one device rewrite "
                     "money it did not ring'. The money fact itself (the "
                     "payment is no longer live) already converges via "
                     "`status`; who performed the void is local audit "
                     "attribution this design does not carry cross-device, on "
                     "purpose, not an oversight this file is inventing an "
                     "excuse for.",
        'voided_at': "Same AUDIT-032A / DEFECT 2 exclusion as voided_by "
                     "immediately above: sync_service.py's payment `update` "
                     "branch trusts only the fixed {'uid','status'} shape from "
                     "a peer device, and widening it to also carry a timestamp "
                     "is a change to that apply-side security boundary, not "
                     "something this file adds unilaterally to only the send "
                     "side while sync_service.py is owned and actively edited "
                     "elsewhere.",
        'cash_session_id': "DEVICE-LOCAL BY DESIGN (schema v43, DAY-1c): which "
                           "cash drawer on THIS till physically took a "
                           "customer's cash. A cash_sessions id means nothing "
                           "on another till -- sessions are per terminal, and "
                           "stamping a peer's payment with a session it never "
                           "opened is exactly the cross-till contamination "
                           "_open_cash_session_id's terminal predicate exists "
                           "to prevent (a phone's takings landing in the "
                           "desktop's Z report). The money fact itself "
                           "(amount, method, direction) converges through the "
                           "payload as before; only the drawer attribution "
                           "stays local. Proven, not asserted: "
                           "retail_day1_customer_cash_drawer_test.py reads the "
                           "payload dict and the sync apply source and fails "
                           "if either mentions the column.",
    },
    # reorder_requests joined this ratchet 2026-09-22 (SECOND-SHAPE WIDENING
    # -- see this module's docstring). All three exclusions below were
    # verified against sync_service.py's real reorder_request apply branch
    # (`elif entity_type == "reorder_request":`), not guessed at: none of
    # the three appears in that branch's own INSERT column list `(id,
    # company_id, branch_id, product_id, status, draft_message,
    # resolved_at, row_version, updated_at_utc)`.
    'reorder_requests': {
        'company_id': "Tenant-isolation key; sync_service.py's reorder_request "
                      "apply branch INSERTs `local_company_id`, never a value "
                      "read off the incoming payload, identical to every other "
                      "synced entity in this file.",
        'created_at': "Row-insertion bookkeeping timestamp; sync_service.py's "
                      "reorder_request apply-branch INSERT column list never "
                      "references it either, matching every other master-data "
                      "entity in this file (products/categories/customers/"
                      "suppliers/branches all share the identical omission).",
        'deleted_at_utc': "A row emitted by a `create` event has, by "
                          "definition, never been deleted; sync_service.py's "
                          "reorder_request INSERT column list never "
                          "references it either, so the apply-side row is "
                          "left at its implicit NULL exactly like the local "
                          "INSERT (core/retail/reorder_hook.py) that produced "
                          "the row in the first place.",
    },
    # v44: party_balance_adjustments' payload (core/retail/party_balance.py's
    # `insert_carry`) carries every column but the tenant key.
    'party_balance_adjustments': {
        'company_id': "Tenant-isolation key; sync_service.py's "
                      "party_balance_adjustment apply branch INSERTs the "
                      "receiver's own local_company_id, never a value read "
                      "off the incoming payload, identical to every other "
                      "synced entity in this file.",
    },
    # W4a (schema v45): purchase orders shared between computers. Every reason
    # below was checked against the sync apply branch and the writers, not
    # guessed.
    'purchase_orders': {
        'id': "Local SQLite autoincrement primary key; a purchase order's real "
              "cross-device identity is its `uid` (already in the payload), so "
              "the sender's own integer id is meaningless on a receiving "
              "computer -- that is the exact bug W4a fixes for the payments "
              "that used to point at it.",
        'company_id': "Tenant-isolation key; sync_service.py's purchase_order "
                      "apply branch INSERTs the receiver's own "
                      "local_company_id, never a value read off the incoming "
                      "payload, identical to every other synced entity here.",
        'branch_id': "A per-computer integer branches.id; the payload carries "
                     "`branch_uid` instead and the apply branch resolves it to "
                     "THIS computer's own branch through _resolve_branch_id, "
                     "exactly like sales and returns do.",
        'amount_paid': "DERIVED, never shipped: every computer recomputes it "
                       "from the synced payments (recompute_po_paid on the "
                       "writer, the apply branch's twin on a receiver), so a "
                       "shipped copy could only disagree after a void and "
                       "would reintroduce the stored-increment drift W4a removes.",
        'payment_status': "DERIVED from amount_paid and total by the same "
                          "recompute on every computer; shipping it would let "
                          "two computers hold two stories about one order.",
        'sync_backfill_pending': "Local outbox bookkeeping: 1 marks a pre-v45 "
                                 "order whose create event this computer still "
                                 "owes the outbox. Meaningless to a peer, and "
                                 "a receiver must keep it 0 or it would "
                                 "re-queue what it just pulled.",
    },
    'purchase_order_receipts': {
        'id': "Local SQLite autoincrement primary key; a receipt's cross-device "
              "identity is its deterministic `uid`, which the payload carries, "
              "so the sender's own integer id is meaningless on a peer.",
        'company_id': "Tenant-isolation key; the receipt apply branch INSERTs "
                      "the receiver's own local_company_id, never a value read "
                      "off the incoming payload, like every synced header.",
        'po_id': "A local autoincrement foreign key into this computer's own "
                 "purchase_orders.id; the payload carries `po_uid` instead and "
                 "the apply branch resolves the order by uid inside the "
                 "receiver's company (parking the receipt until it arrives).",
        'branch_id': "A per-computer integer branches.id; the payload carries "
                     "`branch_uid` and the apply branch resolves it to THIS "
                     "computer's branch through _resolve_branch_id.",
        'legacy': "Never queued and never shipped: a LEGACY receipt (1) is "
                  "synthesized locally from a pre-v46 order with a deterministic "
                  "uid, so every computer derives the identical row itself; "
                  "every receipt that travels is by definition legacy=0.",
    },
    'purchase_order_receipt_lines': {
        'id': "Local SQLite autoincrement primary key; a receipt line's "
              "cross-device identity is its deterministic `uid`, carried in "
              "the payload.",
        'receipt_id': "A local autoincrement foreign key into this computer's "
                      "purchase_order_receipts.id; the payload carries "
                      "`receipt_uid` and the apply branch resolves the real "
                      "parent by uid (parking the line until it arrives).",
        'po_item_id': "A local autoincrement foreign key into this computer's "
                      "purchase_order_items.id; the payload carries "
                      "`po_item_uid` and the apply branch resolves the item "
                      "by uid within the receipt's own order.",
    },
    'purchase_order_items': {
        'id': "Local SQLite autoincrement primary key; a line's cross-device "
              "identity is its `uid`, which the payload already carries.",
        'po_id': "A local autoincrement foreign key into this computer's own "
                 "purchase_orders.id; the payload carries `po_uid` instead "
                 "and the apply branch resolves the real parent by looking "
                 "up purchase_orders.uid, as sale_items does with sale_uid.",
    },
    # W3b (schema v47): stock transfers shared between computers. `id` is NOT
    # excluded on either table: it is a client-generated TEXT uuid and IS the
    # cross-device identity, carried in every payload.
    'stock_transfers': {
        'company_id': "Tenant-isolation key; the stock_transfer apply branch "
                      "INSERTs the receiver's own local_company_id, never a "
                      "value read off the incoming payload, identical to "
                      "every other synced header in this file.",
        'source_branch_id': "A per-computer integer branches.id; the payload "
                            "carries `source_branch_uid` instead and the apply "
                            "branch resolves it to THIS computer's own branch "
                            "through _resolve_branch_id, as sales do.",
        'destination_branch_id': "A per-computer integer branches.id; the "
                                 "payload carries `destination_branch_uid` "
                                 "instead and the apply branch resolves it to "
                                 "THIS computer's own branch through "
                                 "_resolve_branch_id, as sales do.",
        'sync_backfill_pending': "Local outbox bookkeeping: 1 marks a pre-v47 "
                                 "transfer whose create this computer still "
                                 "owes the outbox. Meaningless to a peer, and "
                                 "a receiver must keep it 0 or it would "
                                 "re-queue what it just pulled.",
    },
    # STOCK-TAKE (schema v57). Every other column rides the payload as itself.
    'stock_counts': {
        'id': "Local SQLite autoincrement primary key; a count's cross-device "
              "identity is its `uid`, which the payload already carries, so "
              "the sender's own integer is meaningless on a receiving computer "
              "and is never sent.",
        'company_id': "Tenant-isolation key; the stock_count apply branch "
                      "INSERTs the receiver's own local_company_id, never a "
                      "value read off the incoming payload, identical to "
                      "every other synced company-scoped header in this file.",
        'branch_id': "A per-computer integer branches.id; the payload carries "
                     "`branch_uid` instead and the apply branch resolves it "
                     "STRICTLY to THIS computer's own branch (parking the count "
                     "as missing_parent:branch when absent, never falling back).",
        'merged_into': "Derived locally and identically on every computer by "
                       "the smallest-uid rule when two open counts of one branch "
                       "meet; the merge itself is never announced, so it has no "
                       "wire key.",
    },
    'stock_count_lines': {
        'id': "Local SQLite autoincrement primary key; a line's cross-device "
              "identity is its `uid` (a pure function of count + product), "
              "which the payload already carries, so the sender's own integer "
              "is never sent.",
        'company_id': "Tenant-isolation key; the stock_count_line apply branch "
                      "stamps the receiver's own company_id (found through the "
                      "parent count) and never reads one off the payload.",
        'branch_id': "A per-computer integer branches.id; a line's branch is "
                     "its parent count's, which the apply branch reads from "
                     "the receiver's own copy of that count, so no branch "
                     "identity needs to ride the line.",
        'variance': "Derived: counted_qty - system_qty_at_count. The apply "
                    "branch recomputes it from the two quantities that DO "
                    "travel and never trusts a figure off the wire.",
        'post_unit_cost': "Local record of the unit cost THIS computer's post "
                          "booked at. A receiving computer reads the cost off "
                          "the synced correction movement (same uid), so it is "
                          "never sent on the line.",
    },
    # W2a (schema v48): loyalty points shared between computers. `uid`,
    # `customer_id`, `points_delta`, `entry_type`, `created_by` and `created_at`
    # ride the payload as themselves.
    'loyalty_ledger': {
        'id': "Local SQLite autoincrement primary key; a ledger row's "
              "cross-device identity is its `uid`, which the payload already "
              "carries as the entity id, so the sender's own integer is "
              "meaningless on a receiving computer and is never sent.",
        'company_id': "Tenant-isolation key; the loyalty_entry apply branch "
                      "INSERTs the receiver's own local_company_id, never a "
                      "value read off the incoming payload, identical to "
                      "every other synced company-scoped row in this file.",
        'sale_id': "A per-computer integer sales.id; the payload carries "
                   "`sale_uid` instead and the apply branch resolves it to "
                   "THIS computer's own sale (parking the entry as "
                   "missing_parent:sale until the sale arrives).",
        'return_id': "A per-computer integer returns.id; the payload carries "
                     "`return_uid` instead and the apply branch resolves it "
                     "to THIS computer's own return (parking the entry as "
                     "missing_parent:return until the return arrives).",
        'sync_backfill_pending': "Local outbox bookkeeping: 1 marks a pre-v48 "
                                 "ledger row whose create this computer still "
                                 "owes the outbox. Meaningless to a peer, and "
                                 "a receiver must keep it 0 or it would "
                                 "re-queue what it just pulled.",
    },
    # W2b/W3a (schema v50): the sender's `id`s of the modifier tables are
    # client-generated TEXT uuids and ARE the cross-device identity (carried);
    # `promotions.id` is a local autoincrement whose identity is the `uid`.
    'promotions': {
        'id': "Local SQLite autoincrement primary key; a promotion's real "
              "cross-device identity is its `uid` (already in the payload), so "
              "the sender's own integer id is meaningless on a receiving "
              "computer and is never sent.",
        'company_id': "Tenant-isolation key; the promotion apply branch INSERTs "
                      "the receiver's own local_company_id, never a value read "
                      "off the incoming payload, identical to every other "
                      "synced company-scoped row in this file.",
        'branch_id': "A per-computer integer branches.id; the payload carries "
                     "`branch_uid` instead (NULL = every branch) and the apply "
                     "branch resolves it STRICTLY, parking the promotion until "
                     "the branch arrives rather than widening or narrowing it.",
        'created_at': "Row-insertion bookkeeping timestamp; the apply branch's "
                      "INSERT leaves it at the column default, matching every "
                      "other master-data entity in this file, and nothing reads "
                      "it for money or ordering (the merge uses updated_at_utc).",
        'sync_backfill_pending': "Local outbox bookkeeping: 1 marks a pre-v50 "
                                 "promotion whose create this computer still "
                                 "owes the outbox. Meaningless to a peer, and "
                                 "a receiver must keep it 0 or it would "
                                 "re-queue what it just pulled.",
    },
    'modifier_groups': {
        'company_id': "Tenant-isolation key; the modifier_group apply branch "
                      "INSERTs the receiver's own local_company_id, never a "
                      "value read off the incoming payload.",
        'created_at': "Row-insertion bookkeeping timestamp; the apply branch's "
                      "INSERT leaves it at the column default and nothing reads "
                      "it (the merge uses row_version / updated_at_utc).",
        'sync_backfill_pending': "Local outbox bookkeeping: 1 marks a pre-v50 "
                                 "group whose create this computer still owes "
                                 "the outbox. Meaningless to a peer, and a "
                                 "receiver must keep it 0 or it would re-queue "
                                 "what it just pulled.",
    },
    'modifier_options': {
        'company_id': "Tenant-isolation key; the modifier_option apply branch "
                      "INSERTs the receiver's own local_company_id, never a "
                      "value read off the incoming payload.",
        'created_at': "Row-insertion bookkeeping timestamp; the apply branch's "
                      "INSERT leaves it at the column default and nothing reads "
                      "it (the merge uses row_version / updated_at_utc).",
        'sync_backfill_pending': "Local outbox bookkeeping: 1 marks a pre-v50 "
                                 "option whose create this computer still owes "
                                 "the outbox. Meaningless to a peer, and a "
                                 "receiver must keep it 0 or it would re-queue "
                                 "what it just pulled.",
    },
    'product_modifier_groups': {
        'company_id': "Tenant-isolation key; the product_modifier_group apply "
                      "branch INSERTs the receiver's own local_company_id, never "
                      "a value read off the incoming payload.",
        'sync_backfill_pending': "Local outbox bookkeeping: 1 marks a pre-v50 "
                                 "attachment whose create this computer still "
                                 "owes the outbox. Meaningless to a peer, and a "
                                 "receiver must keep it 0 or it would re-queue "
                                 "what it just pulled.",
    },
    # Nested snapshots (see NESTED_TABLES): `id` and `sale_item_id` are local
    # autoincrement keys, `company_id` is the receiver's own.
    'sale_item_modifiers': {
        'id': "Local SQLite autoincrement primary key; a snapshot row's "
              "cross-device identity is its `uid` (carried in the nested dict), "
              "so the sender's own integer is meaningless on a peer.",
        'company_id': "Tenant-isolation key; the receiver takes it from the "
                      "parent sale it already resolved by uid, never from the "
                      "incoming payload.",
        'sale_item_id': "A local autoincrement foreign key into this computer's "
                        "own sale_items.id; the snapshot travels INSIDE its "
                        "sale_item event and the apply branch attaches it to the "
                        "line it just resolved by that line's uid.",
    },
    'sale_item_promotions': {
        'id': "Local SQLite autoincrement primary key; a snapshot row's "
              "cross-device identity is its `uid` (carried in the nested dict), "
              "so the sender's own integer is meaningless on a peer.",
        'company_id': "Tenant-isolation key; the receiver takes it from the "
                      "parent sale it already resolved by uid, never from the "
                      "incoming payload.",
        'sale_item_id': "A local autoincrement foreign key into this computer's "
                        "own sale_items.id; the snapshot travels INSIDE its "
                        "sale_item event and the apply branch attaches it to the "
                        "line it just resolved by that line's uid.",
    },
}


# ── Config self-checks: the exclusion map itself must stay honest ───────────

def test_every_exclusion_has_a_real_reason():
    for table, cols in EXCLUSIONS.items():
        for col, reason in cols.items():
            assert isinstance(reason, str), f"{table}.{col}: exclusion reason must be a string"
            stripped = reason.strip()
            assert len(stripped) >= MIN_REASON_LEN, (
                f"{table}.{col}: exclusion reason is only {len(stripped)} chars "
                f"(< {MIN_REASON_LEN}) -- too short to be a real, checkable reason; "
                f"see this module's docstring on why an unreasoned exclusion is how "
                f"this ratchet rots into a rubber stamp."
            )
            assert ' ' in stripped, (
                f"{table}.{col}: exclusion reason must be a real sentence, not a "
                f"single token."
            )


def test_exclusions_reference_only_tracked_tables():
    for table in EXCLUSIONS:
        assert table in TRACKED_TABLES or table in NESTED_TABLES, (
            f"EXCLUSIONS has an entry for {table!r}, which is not in TRACKED_TABLES "
            f"-- dead configuration nobody's test actually exercises."
        )


# ── The real ratchet, one test per covered table ─────────────────────────────

def _rel(path):
    """Path relative to BACKEND_DIR for compact, stable assertion messages --
    the absolute temp-checkout path is noise; `api/import_api.py` says
    exactly which module without it."""
    try:
        return str(path.relative_to(BACKEND_DIR))
    except ValueError:
        return str(path)


def _assert_full_coverage(table):
    db_path = _install()
    sources = TRACKED_TABLES[table]
    per_module = per_module_payload_keys(_SYNC_MODULE_TREES, sources)

    total_literal = sum(lit for _, lit, _ in per_module.values())
    total_nonliteral = sum(nonlit for _, _, nonlit in per_module.values())
    assert total_literal > 0, (
        f"no literal-dict _queue_sync_event call found for table {table!r} via "
        f"{sources} in any of the {len(_SYNC_MODULE_TREES)} modules scanned "
        f"under {BACKEND_DIR} (see _discover_sync_modules) -- the AST "
        f"assumption this ratchet relies on no longer holds for this table; "
        f"update TRACKED_TABLES or investigate a changed payload-construction "
        f"shape."
    )
    nonliteral_modules = sorted(_rel(p) for p, (_, _, nl) in per_module.items() if nl)
    assert total_nonliteral == 0, (
        f"table {table!r} has a _queue_sync_event call matching {sources} in "
        f"{nonliteral_modules} whose payload is NOT a literal dict -- this "
        f"ratchet cannot see its keys and may be silently under-covering "
        f"{table!r}; investigate before trusting a green result on this table."
    )

    schema_cols = _table_columns(db_path, table)
    exclusions = EXCLUSIONS.get(table, {})
    common_keys = intersection_of_module_keys(per_module)
    missing = missing_coverage(schema_cols, common_keys, exclusions)
    if missing:
        writers = sorted(_rel(p) for p in per_module)
        detail = []
        for col in sorted(missing):
            carriers = sorted(_rel(p) for p, (keys, lit, _) in per_module.items()
                               if lit > 0 and col in keys)
            if carriers:
                detail.append(f"{col!r} carried by {carriers} but MISSING from "
                               f"the rest of {writers} -- a column on the wire "
                               f"from one path and not another")
            else:
                detail.append(f"{col!r} carried by none of {writers}")
        assert not missing, (
            f"{table}: column(s) exist in the live migrated schema but are not "
            f"consistently covered across every module that writes this table "
            f"({writers}), and are not in EXCLUSIONS[{table!r}] in this file -- "
            + "; ".join(detail) +
            f". A peer device will never receive these columns for at least "
            f"one of this table's write paths. Either add the missing column "
            f"to the payload dict at the relevant _queue_sync_event(...) call "
            f"in the module(s) named above (this file does not own any "
            f"production path -- report it), or add a genuinely reasoned "
            f"exclusion to EXCLUSIONS[{table!r}] here."
        )


def test_sales_outbox_payload_covers_every_column():
    _assert_full_coverage('sales')


def test_sale_items_outbox_payload_covers_every_column():
    _assert_full_coverage('sale_items')


def test_products_outbox_payload_covers_every_column():
    _assert_full_coverage('products')


def test_categories_outbox_payload_covers_every_column():
    _assert_full_coverage('categories')


def test_customers_outbox_payload_covers_every_column():
    _assert_full_coverage('customers')


def test_suppliers_outbox_payload_covers_every_column():
    _assert_full_coverage('suppliers')


def test_branches_outbox_payload_covers_every_column():
    _assert_full_coverage('branches')


def test_inventory_movements_outbox_payload_covers_every_column():
    _assert_full_coverage('inventory_movements')


def test_returns_outbox_payload_covers_every_column():
    _assert_full_coverage('returns')


def test_return_items_outbox_payload_covers_every_column():
    _assert_full_coverage('return_items')


def test_payments_outbox_payload_covers_every_column():
    _assert_full_coverage('payments')


def test_reorder_requests_outbox_payload_covers_every_column():
    _assert_full_coverage('reorder_requests')


def test_party_balance_adjustments_outbox_payload_covers_every_column():
    _assert_full_coverage('party_balance_adjustments')


def test_purchase_orders_outbox_payload_covers_every_column():
    _assert_full_coverage('purchase_orders')


def test_purchase_order_items_outbox_payload_covers_every_column():
    _assert_full_coverage('purchase_order_items')


def test_purchase_order_receipts_outbox_payload_covers_every_column():
    _assert_full_coverage('purchase_order_receipts')


def test_purchase_order_receipt_lines_outbox_payload_covers_every_column():
    _assert_full_coverage('purchase_order_receipt_lines')


def test_stock_transfers_outbox_payload_covers_every_column():
    _assert_full_coverage('stock_transfers')


def test_stock_transfer_items_outbox_payload_covers_every_column():
    _assert_full_coverage('stock_transfer_items')


# STOCK-TAKE (schema v57)
def test_stock_counts_outbox_payload_covers_every_column():
    _assert_full_coverage('stock_counts')


def test_stock_count_lines_outbox_payload_covers_every_column():
    _assert_full_coverage('stock_count_lines')


def test_loyalty_ledger_outbox_payload_covers_every_column():
    _assert_full_coverage('loyalty_ledger')


def test_promotions_outbox_payload_covers_every_column():
    _assert_full_coverage('promotions')


def test_modifier_groups_outbox_payload_covers_every_column():
    _assert_full_coverage('modifier_groups')


def test_modifier_options_outbox_payload_covers_every_column():
    _assert_full_coverage('modifier_options')


def test_product_modifier_groups_outbox_payload_covers_every_column():
    _assert_full_coverage('product_modifier_groups')


def _assert_nested_coverage(table):
    """W2b/W3a: the snapshot tables whose rows ride inside a sale_item event."""
    db_path = _install()
    entity_type, event_type, payload_key, wire_to_column = NESTED_TABLES[table]
    per_module = per_module_nested_keys(_SYNC_MODULE_TREES, entity_type, event_type, payload_key)
    assert per_module, (
        f"no `_queue_sync_event(..., {entity_type!r}, ..., {event_type!r}, {{...}})` "
        f"call found in any scanned module -- the shape this ratchet reads for "
        f"{table!r} no longer holds")
    nonliteral = sorted(_rel(p) for p, (_, _, nl) in per_module.items() if nl)
    assert not nonliteral, (
        f"{table}: the nested {payload_key!r} snapshot in {nonliteral} is bound to "
        f"something this ratchet cannot read (not a literal dict / `.append({{...}})`); "
        f"investigate before trusting a green result.")
    # Every writer of the parent event must carry the snapshot with the same keys:
    # a writer whose payload lacks the key has an empty set and empties the
    # intersection, so its columns are reported as uncovered.
    common = intersection_of_module_keys(per_module)
    on_wire = {wire_to_column.get(k, k) for k in common}
    missing = missing_coverage(_table_columns(db_path, table), on_wire, EXCLUSIONS.get(table, {}))
    assert not missing, (
        f"{table}: column(s) {sorted(missing)} exist in the live schema but the nested "
        f"{payload_key!r} snapshot of a {entity_type!r} {event_type!r} event does not carry "
        f"them (keys on the wire: {sorted(common)}) and they are not in EXCLUSIONS[{table!r}]. "
        f"A peer computer would never receive them.")


def test_sale_item_modifiers_snapshot_covers_every_column():
    _assert_nested_coverage('sale_item_modifiers')


def test_sale_item_promotions_snapshot_covers_every_column():
    _assert_nested_coverage('sale_item_promotions')


def test_nested_wire_mapping_names_real_columns():
    """The wire-key -> column maps above are hand-typed; a typo must go red
    (a mapped target that is not a real column would quietly cover nothing)."""
    db_path = _install()
    for table, (_, _, _, wire_to_column) in NESTED_TABLES.items():
        real = _table_columns(db_path, table)
        for wire, column in wire_to_column.items():
            assert column in real, f"{table}: {wire!r} maps to {column!r}, which is not a column"


def test_nested_extractor_reads_a_name_bound_in_the_same_function_and_flags_unreadable_ones():
    """NOT-VACUOUS PROOF for `nested_payload_keys_for`, from synthetic source:
    a list built by `.append({...})`, a dict assigned once and a None arm are
    read; a name bound to a helper call is NONLITERAL (loud), and a writer
    whose payload omits the key reports no keys (so the intersection empties)."""
    readable = ast.parse(
        "def f():\n"
        "    snaps = []\n"
        "    for s in rows:\n"
        "        snaps.append({'uid': 1, 'name': 2})\n"
        "    promo = None\n"
        "    if x:\n"
        "        promo = {'uid': 1, 'pct': 3}\n"
        "    _queue_sync_event(cur, 'sale_item', u, 'create', {'uid': u, 'modifiers': snaps, 'promotion': promo})\n")
    assert nested_payload_keys_for(readable, 'sale_item', 'create', 'modifiers') == ({'uid', 'name'}, 1, 0)
    assert nested_payload_keys_for(readable, 'sale_item', 'create', 'promotion') == ({'uid', 'pct'}, 1, 0)
    opaque = ast.parse(
        "def f():\n"
        "    snaps = build(rows)\n"
        "    _queue_sync_event(cur, 'sale_item', u, 'create', {'modifiers': snaps})\n")
    keys, lit, nonlit = nested_payload_keys_for(opaque, 'sale_item', 'create', 'modifiers')
    assert keys == set() and nonlit >= 1
    absent = ast.parse(
        "def f():\n"
        "    _queue_sync_event(cur, 'sale_item', u, 'create', {'uid': u})\n")
    assert nested_payload_keys_for(absent, 'sale_item', 'create', 'modifiers') == (set(), 1, 0)
    other = ast.parse(
        "def f():\n"
        "    _queue_sync_event(cur, 'sale', u, 'create', {'modifiers': {'a': 1}})\n")
    assert nested_payload_keys_for(other, 'sale_item', 'create', 'modifiers') == (set(), 0, 0)


# ── NOT-VACUOUS PROOF ─────────────────────────────────────────────────────────
# Both directions (RED when a column is genuinely uncovered, GREEN once it is
# either added to the payload or given a reasoned exclusion), driven entirely
# through synthetic inputs this file is free to invent -- none of these tests
# reads or writes retail_api.py or database/schema.py, which this file does
# not own.

def test_missing_coverage_flags_a_column_absent_from_payload_and_exclusions():
    """RED direction: a schema column absent from both the payload key set
    and the exclusions map must be reported by name."""
    result = missing_coverage(
        schema_columns={'a', 'b', 'c'},
        payload_keys={'a', 'b'},
        exclusions={},
    )
    assert result == {'c'}


def test_missing_coverage_clears_once_the_column_is_added_to_the_payload():
    """GREEN direction, fix #1: adding the column to the payload key set --
    the real fix a developer makes in retail_api.py -- clears the finding
    without touching EXCLUSIONS."""
    result = missing_coverage(
        schema_columns={'a', 'b', 'c'},
        payload_keys={'a', 'b', 'c'},
        exclusions={},
    )
    assert result == set()


def test_missing_coverage_clears_once_the_column_has_a_reasoned_exclusion():
    """GREEN direction, fix #2: a properly reasoned exclusion also clears it,
    without touching the payload -- the correct move for a column that is
    genuinely, deliberately never meant to sync."""
    result = missing_coverage(
        schema_columns={'a', 'b', 'c'},
        payload_keys={'a', 'b'},
        exclusions={'c': "c is a device-local counter column, deliberately never "
                          "carried on the wire, matching this file's real "
                          "credit_balance exclusion reasoning."},
    )
    assert result == set()


def test_payload_keys_for_extracts_from_synthetic_source_not_hardcoded():
    """Proves `payload_keys_for` genuinely reads the AST rather than
    returning a hand-typed constant: parses a SYNTHETIC source string (never
    retail_api.py) naming a fictional 'widget' entity, and checks the
    extracted key set matches exactly what that fake source wrote."""
    source = (
        "def create_widget():\n"
        "    _queue_sync_event(cur, 'widget', wid, 'create', {\n"
        "        'id': wid, 'name': data['name'], 'weight': data.get('weight', 0),\n"
        "    })\n"
    )
    tree = ast.parse(source)
    keys, literal_sites, nonliteral_sites = payload_keys_for(tree, 'widget', 'create')
    assert keys == {'id', 'name', 'weight'}
    assert literal_sites == 1
    assert nonliteral_sites == 0


def test_payload_keys_for_flags_a_nonliteral_payload_instead_of_silently_missing_it():
    """A matching call whose payload is a bound name (not a literal dict) --
    the exact shape `quotation`/`doc_series` already use in retail_api.py via
    a helper function -- must be counted as nonliteral, never silently
    treated as zero keys."""
    source = (
        "def create_widget():\n"
        "    payload = _build_widget_payload(wid)\n"
        "    _queue_sync_event(cur, 'widget', wid, 'create', payload)\n"
    )
    tree = ast.parse(source)
    keys, literal_sites, nonliteral_sites = payload_keys_for(tree, 'widget', 'create')
    assert keys == set()
    assert literal_sites == 0
    assert nonliteral_sites == 1


def test_full_pipeline_against_a_synthetic_table_and_source_end_to_end():
    """The strongest form of the not-vacuous proof: a throwaway in-memory
    sqlite table (never the real retail DB) and a synthetic retail_api.py-
    shaped source string (never the real file), run end to end through the
    SAME two functions the real per-table tests above call --
    `payload_keys_for` and `missing_coverage`.

    Simulates BOTH fixes in one test: adding the column to the payload
    (the `secret_new_column` half) and adding a reasoned exclusion (the
    `company_id` half), because a real migration is at least as likely to
    need one as the other.
    """
    conn = sqlite3.connect(':memory:')
    conn.execute(
        'CREATE TABLE widgets (id TEXT, name TEXT, weight REAL, company_id INTEGER, '
        'secret_new_column REAL)'
    )
    schema_cols = {r[1] for r in conn.execute('PRAGMA table_info(widgets)').fetchall()}
    conn.close()

    source_before = (
        "def create_widget():\n"
        "    _queue_sync_event(cur, 'widget', wid, 'create', {\n"
        "        'id': wid, 'name': data['name'], 'weight': data.get('weight', 0),\n"
        "    })\n"
    )
    tree_before = ast.parse(source_before)
    keys_before, lit_before, nonlit_before = payload_keys_for(tree_before, 'widget', 'create')
    assert lit_before == 1 and nonlit_before == 0

    # RED: neither company_id nor secret_new_column is covered yet.
    missing_before = missing_coverage(schema_cols, keys_before, exclusions={})
    assert missing_before == {'company_id', 'secret_new_column'}

    # GREEN: the real fix -- add secret_new_column to the payload (a
    # migration's job) and give company_id a reasoned exclusion (this
    # file's job, matching the real company_id exclusion used throughout
    # EXCLUSIONS above).
    source_after = (
        "def create_widget():\n"
        "    _queue_sync_event(cur, 'widget', wid, 'create', {\n"
        "        'id': wid, 'name': data['name'], 'weight': data.get('weight', 0),\n"
        "        'secret_new_column': data.get('secret_new_column', 0),\n"
        "    })\n"
    )
    tree_after = ast.parse(source_after)
    keys_after, lit_after, nonlit_after = payload_keys_for(tree_after, 'widget', 'create')
    assert lit_after == 1 and nonlit_after == 0

    missing_after = missing_coverage(
        schema_cols, keys_after,
        exclusions={'company_id': "Tenant-isolation key; synthetic test double of "
                                   "this file's real company_id exclusion reasoning "
                                   "used throughout EXCLUSIONS above."},
    )
    assert missing_after == set()


# ── CROSS-MODULE NOT-VACUOUS PROOF (2026-09-22 widening) ─────────────────────
# Everything above this section proves the SINGLE-MODULE machinery
# (`payload_keys_for`/`union_payload_keys`/`missing_coverage`) is not
# vacuous. It says nothing about `per_module_payload_keys` or
# `intersection_of_module_keys`, which is the actual new logic this pass
# added -- these tests are that proof, entirely through synthetic inputs
# again (no real file, no real table), covering the exact bug class this
# widening exists to catch: a column present in one module's payload and
# missing from another's.

def test_discovery_finds_every_known_sync_queuing_module():
    """Sanity floor under `_discover_sync_modules` itself: if `BACKEND_DIR`
    ever pointed at the wrong tree, or the substring check regressed, every
    per-table test below would silently start asserting against FEWER
    modules than intended -- weaker coverage, not an absent or red test, so
    nothing else in this file would fail on its own. Pins the two modules
    this file's own 2026-09-22 investigation confirmed genuinely CALL
    `_queue_sync_event` (not merely mention it in a comment -- see this
    module's docstring, "CROSS-MODULE WIDENING")."""
    names = {p.name for p in _SYNC_MODULE_TREES}
    assert 'retail_api.py' in names
    assert 'import_api.py' in names
    # reorder_hook.py mentions `_queue_sync_event` in its own docstring
    # (comparing itself to it) -- this substring-discovery test predates the
    # SECOND-SHAPE WIDENING pass that taught this file to actually parse its
    # differently-named call site, but the discovery step itself already
    # found the file before that pass; pinned here too so a regression in
    # `_discover_sync_modules` breaks this assertion, not just a silently
    # weaker reorder_requests coverage test.
    assert 'reorder_hook.py' in names


def test_per_module_payload_keys_keeps_each_modules_keys_separate_not_unioned():
    """The core distinction that makes cross-module drift visible at all:
    two synthetic modules writing the SAME fictional 'widget' entity, one
    with an extra key the other lacks. `per_module_payload_keys` must
    report each module's OWN key set, not a merged union -- if this test
    ever saw `{'id', 'name', 'weight'}` for BOTH modules, the drift-catching
    logic below would have nothing real to intersect."""
    tree_a = ast.parse(
        "def create_widget():\n"
        "    _queue_sync_event(cur, 'widget', wid, 'create', {\n"
        "        'id': wid, 'name': data['name'], 'weight': data.get('weight', 0),\n"
        "    })\n"
    )
    tree_b = ast.parse(
        "def _handle_widgets(records):\n"
        "    _queue_sync_event(cur, 'widget', wid, 'create', {\n"
        "        'id': wid, 'name': rec['name'],\n"
        "    })\n"
    )
    per_module = per_module_payload_keys(
        {Path('module_a.py'): tree_a, Path('module_b.py'): tree_b},
        [('widget', 'create')],
    )
    assert per_module[Path('module_a.py')][0] == {'id', 'name', 'weight'}
    assert per_module[Path('module_b.py')][0] == {'id', 'name'}
    assert per_module[Path('module_a.py')][1] == 1  # literal_sites
    assert per_module[Path('module_b.py')][1] == 1


def test_intersection_of_module_keys_flags_a_column_missing_from_one_path():
    """RED direction for the cross-module bug class this widening exists to
    catch -- the exact shape the real `products.avg_cost` bug took: present
    in one writer's payload (retail_api.py's create_product), absent from
    another's (import_api.py's create-event), until this pass's fix. A
    column carried by SOME module must not be treated as covered."""
    per_module = {
        Path('a.py'): ({'id', 'name', 'weight'}, 1, 0),
        Path('b.py'): ({'id', 'name'}, 1, 0),
    }
    common = intersection_of_module_keys(per_module)
    assert common == {'id', 'name'}
    missing = missing_coverage({'id', 'name', 'weight'}, common, exclusions={})
    assert missing == {'weight'}


def test_intersection_of_module_keys_clears_once_every_module_agrees():
    """GREEN direction: the real fix this pass made to import_api.py's
    create-event payload -- once every writing module's payload carries the
    same column, the intersection covers it and the finding clears."""
    per_module = {
        Path('a.py'): ({'id', 'name', 'weight'}, 1, 0),
        Path('b.py'): ({'id', 'name', 'weight'}, 1, 0),
    }
    common = intersection_of_module_keys(per_module)
    missing = missing_coverage({'id', 'name', 'weight'}, common, exclusions={})
    assert missing == set()


def test_intersection_of_module_keys_ignores_a_purely_nonliteral_writer():
    """A module whose only matching call is non-literal contributes zero
    literal keys and must NOT narrow the intersection to the empty set for
    every table it happens to also mention -- `_assert_full_coverage`'s own
    total-nonliteral-sites assertion is what should catch that module,
    loudly, rather than this function silently reporting every column as
    cross-module-missing for an unrelated reason."""
    per_module = {
        Path('a.py'): ({'id', 'name'}, 1, 0),
        Path('b.py'): (set(), 0, 1),
    }
    common = intersection_of_module_keys(per_module)
    assert common == {'id', 'name'}


def test_full_cross_module_pipeline_catches_a_column_present_in_one_writer_only():
    """Strongest form of the cross-module proof, mirroring
    `test_full_pipeline_against_a_synthetic_table_and_source_end_to_end`
    above but for TWO synthetic modules: runs `per_module_payload_keys` +
    `intersection_of_module_keys` + `missing_coverage` -- the exact pipeline
    `_assert_full_coverage` uses -- against a synthetic sqlite table,
    reproducing the real `products.avg_cost` bug and its fix in miniature."""
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE widgets (id TEXT, name TEXT, weight REAL)')
    schema_cols = {r[1] for r in conn.execute('PRAGMA table_info(widgets)').fetchall()}
    conn.close()

    source_a = (  # the "retail_api.py" stand-in: always had the column
        "def create_widget():\n"
        "    _queue_sync_event(cur, 'widget', wid, 'create', {\n"
        "        'id': wid, 'name': data['name'], 'weight': data.get('weight', 0),\n"
        "    })\n"
    )
    source_b_before = (  # the "import_api.py" stand-in: missing it, like the real bug
        "def _handle_widgets(records):\n"
        "    _queue_sync_event(cur, 'widget', wid, 'create', {\n"
        "        'id': wid, 'name': rec['name'],\n"
        "    })\n"
    )
    trees_before = {Path('a.py'): ast.parse(source_a), Path('b.py'): ast.parse(source_b_before)}
    per_module_before = per_module_payload_keys(trees_before, [('widget', 'create')])
    missing_before = missing_coverage(
        schema_cols, intersection_of_module_keys(per_module_before), exclusions={})
    assert missing_before == {'weight'}, (
        "the widened ratchet failed to catch a column present in one module's "
        "payload and absent from another's -- exactly the class this widening "
        "exists to close"
    )

    source_b_after = (  # the real fix: add the missing key
        "def _handle_widgets(records):\n"
        "    _queue_sync_event(cur, 'widget', wid, 'create', {\n"
        "        'id': wid, 'name': rec['name'], 'weight': rec.get('weight', 0),\n"
        "    })\n"
    )
    trees_after = {Path('a.py'): ast.parse(source_a), Path('b.py'): ast.parse(source_b_after)}
    per_module_after = per_module_payload_keys(trees_after, [('widget', 'create')])
    missing_after = missing_coverage(
        schema_cols, intersection_of_module_keys(per_module_after), exclusions={})
    assert missing_after == set()


# ── SECOND-SHAPE NOT-VACUOUS PROOF (2026-09-22) ──────────────────────────────
# `reorder_payload_keys_for`/`_sync_outbox_insert_dict_keys` are new code
# this same pass added -- proven here entirely through synthetic sources,
# exactly like every other not-vacuous proof in this file. The entity_type
# string used below is the real 'reorder_request' literal (not a fictional
# 'widget'-style name) because `reorder_payload_keys_for` is deliberately
# SCOPED to that one entity type (see its own docstring) rather than generic
# like `payload_keys_for` -- everything else about these sources (variable
# names, helper body order, the payload's own key set) is synthetic, never
# copied from core/retail/reorder_hook.py.

def test_reorder_payload_keys_for_reads_a_synthetic_second_shape_source():
    """Proves `reorder_payload_keys_for` genuinely parses the AST for the
    `_queue_reorder_sync_event` shape rather than returning a canned answer:
    different variable names, a different key set, and a made-up
    `custom_test_only_field` key that only appears in THIS synthetic source
    -- if this function were hardcoded to the real file's own key set, this
    key would never show up in the result."""
    source = (
        "def _queue_reorder_sync_event(conn, rid, cid, bid, pid, st, msg, ts, evt, "
        "*, row_version=None, updated_at_utc=None):\n"
        "    conn.execute(\n"
        "        'INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, "
        "payload, created_at) VALUES (?,?,?,?,?,?)',\n"
        "        (str(uuid4()), 'reorder_request', rid, evt, json.dumps({\n"
        "            'id': rid, 'status': st, 'custom_test_only_field': 1,\n"
        "        }), ts),\n"
        "    )\n"
        "\n"
        "def _caller():\n"
        "    _queue_reorder_sync_event(conn, rid, cid, bid, pid, 'pending', msg, now, "
        "'create', row_version=1, updated_at_utc=now)\n"
    )
    tree = ast.parse(source)
    keys, literal_sites, nonliteral_sites = reorder_payload_keys_for(tree, 'reorder_request', 'create')
    assert keys == {'id', 'status', 'custom_test_only_field'}
    assert literal_sites == 1
    assert nonliteral_sites == 0


def test_reorder_payload_keys_for_ignores_unrelated_entity_types():
    """RED-direction mutation target for the early-return guard in
    `reorder_payload_keys_for`: a call site whose literal `event_type` is
    'create' -- the SAME literal nearly every OTHER tracked table's `create`
    event also uses -- must NOT contribute a phantom nonliteral site to an
    unrelated table's coverage just because this module also happens to
    define `_queue_reorder_sync_event`. Deleting the `if entity_type !=
    REORDER_ENTITY_TYPE: return set(), 0, 0` guard turns this test red
    (nonliteral_sites becomes 1 instead of 0) -- verified by hand while
    writing this test, not merely asserted to be true."""
    source = (
        "def _queue_reorder_sync_event(conn, rid, cid, bid, pid, st, msg, ts, evt):\n"
        "    conn.execute(\n"
        "        'INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, "
        "payload, created_at) VALUES (?,?,?,?,?,?)',\n"
        "        (str(uuid4()), 'reorder_request', rid, evt, json.dumps({'id': rid}), ts),\n"
        "    )\n"
        "\n"
        "def _caller():\n"
        "    _queue_reorder_sync_event(conn, rid, cid, bid, pid, 'pending', msg, now, 'create')\n"
    )
    tree = ast.parse(source)
    keys, literal_sites, nonliteral_sites = reorder_payload_keys_for(tree, 'sale', 'create')
    assert keys == set()
    assert literal_sites == 0
    assert nonliteral_sites == 0


def test_reorder_payload_keys_for_flags_missing_function_def_as_nonliteral():
    """A call site with a literal, matching event_type but NO local
    `_queue_reorder_sync_event` definition in the same tree (e.g. imported
    from elsewhere) must be counted as a nonliteral site, not silently
    zero -- the same 'fail loudly rather than under-report' posture
    `payload_keys_for` already takes for its own shape (see
    test_payload_keys_for_flags_a_nonliteral_payload_instead_of_silently_
    missing_it above)."""
    source = (
        "def _caller():\n"
        "    _queue_reorder_sync_event(conn, rid, cid, bid, pid, 'pending', msg, now, 'create')\n"
    )
    tree = ast.parse(source)
    keys, literal_sites, nonliteral_sites = reorder_payload_keys_for(tree, 'reorder_request', 'create')
    assert keys == set()
    assert literal_sites == 0
    assert nonliteral_sites == 1


def test_per_module_payload_keys_folds_in_the_reorder_shaped_site():
    """Integration proof for the one-line addition inside
    `per_module_payload_keys` itself: a synthetic module carrying BOTH an
    ordinary `_queue_sync_event` site for an unrelated table AND a
    `_queue_reorder_sync_event` site for 'reorder_request' must report
    keys/literal_sites that include the second shape's contribution, not
    just the first."""
    source = (
        "def create_widget():\n"
        "    _queue_sync_event(cur, 'widget', wid, 'create', {'id': wid, 'name': n})\n"
        "\n"
        "def _queue_reorder_sync_event(conn, rid, cid, bid, pid, st, msg, ts, evt, "
        "*, row_version=None, updated_at_utc=None):\n"
        "    conn.execute(\n"
        "        'INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, "
        "payload, created_at) VALUES (?,?,?,?,?,?)',\n"
        "        (str(uuid4()), 'reorder_request', rid, evt, "
        "json.dumps({'id': rid, 'status': st}), ts),\n"
        "    )\n"
        "\n"
        "def _caller():\n"
        "    _queue_reorder_sync_event(conn, rid, cid, bid, pid, 'pending', msg, now, "
        "'create', row_version=1, updated_at_utc=now)\n"
    )
    tree = ast.parse(source)
    per_module = per_module_payload_keys({Path('mixed.py'): tree}, [('reorder_request', 'create')])
    keys, lit, nonlit = per_module[Path('mixed.py')]
    assert keys == {'id', 'status'}
    assert lit == 1
    assert nonlit == 0


# ── FUNCTIONAL PROOF: a real route, a real payload, REAL VALUES ──────────────
# Every test above proves the payload's KEY SET is complete -- via AST parsing
# plus a bare migrated schema -- but neither one ever executes retail_api.py's
# own request-handling code, so neither can catch a payload that carries the
# right KEYS with the WRONG values (a hardcoded 0, the wrong party echoed
# back, a swapped direction). This closes that gap for `payments` specifically
# (the table this pass added): a REAL HTTP POST through a REAL, fully-booted
# Flask app, read back from that install's own `sync_outbox` table, checked
# against the exact facts the request supplied -- not merely that each key is
# present. Harness technique copied from the established pattern this
# project's own money-sync suites already use (retail_money_sync_test.py /
# retail_sync_cost_basis_convergence_test.py's module docstrings have the
# full "AURA_APP_DATA trap" reasoning, not repeated here) -- but scoped to a
# single self-contained test function rather than a module-level boot, since
# this file's other 25 tests either mint and tear down their own temp
# AURA_APP_DATA via `_install()`/`_fresh_app_data()`, or (the synthetic
# not-vacuous proofs) touch no filesystem/database state at all, and this
# test must not disturb that per-test isolation.

def test_customer_payment_route_queues_real_values_for_every_synced_column():
    """POSTs a real payment through `POST /customers/<id>/payments`
    (retail_api.py's `customer_payment` view -- the same `_record_payment`
    funnel every payment-creating route in this file shares) against the
    real, module-booted Flask app (`_FUNCTIONAL_APP` above), then reads the
    one resulting `payment`/`create` sync_outbox row straight from that
    install's own database and checks every synced column's VALUE against
    the exact request/response facts -- not just that the key exists. A
    payload that hardcoded `amount=0` or echoed the wrong `party_id` would
    pass every test above this one (they only look at key sets) and fail
    here.

    Re-points `database.schema.BASE_DIR`/`SUBSYS_DIR` back at
    `_FUNCTIONAL_DATA` before touching anything: every `_install()`-based
    coverage test above this one in the file also imports and mutates those
    same two module attributes (each against its OWN fresh temp dir), so by
    the time this test runs they are left pointing at whichever coverage
    test happened to run last -- `get_retail_conn()` reads them live on
    every call, so this reset is required, not defensive boilerplate. The
    license/registry-db closures (`require_license_capability`,
    `registry_db.DB_PATH`) do NOT need an equivalent reset: both are bound
    ONCE, at their own module's first import, which `_FUNCTIONAL_APP`'s
    module-level boot above deliberately wins by running before any test
    (including the first coverage test's `_install()`) executes.

    Also resets `api.retail_api._CREDIT_SCHEMA_READY` to `False` -- the
    SAME process-global-flag trap `_install()` above already guards against
    (see this module's own docstring, "APPROACH"): an earlier coverage
    test's `_install()` call already flipped this flag `True` against ITS
    OWN temp database, so without resetting it here `customer_payment`'s own
    `_ensure_credit_schema(conn)` call would silently no-op against THIS
    fresh, still-bare database and the request would fail with
    `no such table: retail_settings` -- reproduced while writing this test.
    """
    import database.schema as sch
    sch.BASE_DIR = os.path.join(_FUNCTIONAL_DATA, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    os.environ['AURA_APP_DATA'] = _FUNCTIONAL_DATA

    import api.retail_api as retail_api_module
    retail_api_module._CREDIT_SCHEMA_READY = False

    from commercial_runtime.identity.registry_db import get_conn as registry_conn
    from commercial_runtime.security.passwords import hash_password

    email = f'sync-coverage-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'SyncCoveragePW1'
    company_id = str(uuid.uuid4())
    reg_conn = registry_conn()
    try:
        reg_conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
            "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
            (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'),
        )
        reg_conn.commit()
    finally:
        reg_conn.close()

    client = _FUNCTIONAL_APP.test_client()
    login = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert login.status_code == 200, login.get_json()

    API = '/api/sub/retail'
    cust = client.post(f'{API}/customers',
                        json={'name': f'Sync Coverage Customer {uuid.uuid4().hex[:6]}'})
    assert cust.status_code == 200, cust.get_json()
    cust_id = cust.get_json()['data']['id']

    pay = client.post(f'{API}/customers/{cust_id}/payments', json={
        'amount': 42.5, 'method': 'cash', 'notes': 'sync-coverage-check',
        'device': 'FUNCTIONAL-TEST-TILL',
    })
    assert pay.status_code == 200, pay.get_json()
    reference = pay.get_json()['data']['reference']

    conn = sch.get_retail_conn()
    try:
        row = conn.execute(
            "SELECT payload FROM sync_outbox WHERE entity_type='payment' AND event_type='create' "
            "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "customer_payment queued no payment/create sync_outbox row at all"
    payload = json.loads(row['payload'])

    # Real values, not just real keys -- this is the whole point of this test.
    assert payload['party_type'] == 'customer'
    assert payload['party_id'] == cust_id
    assert payload['direction'] == 'in'
    assert payload['amount'] == 42.5
    assert payload['method'] == 'cash'
    assert payload['notes'] == 'sync-coverage-check'
    assert payload['device'] == 'FUNCTIONAL-TEST-TILL'
    assert payload['status'] == 'active'
    assert payload['reference'] == reference
    assert payload['related_type'] is None
    assert payload['related_id'] is None
    assert isinstance(payload['uid'], str) and len(payload['uid']) > 0
    assert isinstance(payload['created_by'], str) and len(payload['created_by']) > 0
    assert isinstance(payload['created_at'], str) and len(payload['created_at']) > 0
    assert isinstance(payload['currency'], str) and len(payload['currency']) > 0
