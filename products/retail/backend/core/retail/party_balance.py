"""
Aura Retail -- customer / supplier balances DERIVED from shop-wide records
(schema v44, plan: job f8252b48 ledger/plan.md).

Pure SQL + integer arithmetic, no Flask, no retail_api import. Imported by
api/retail_api.py (writers, readers), the sync post-pull refresh, the boot
refresh, the diagnostic route and scripts/diag/party_balance_check.py.

WHY THIS MODULE EXISTS (v44). `customers.credit_balance` /
`suppliers.credit_balance` were mutated ONLY by the computer that performed
the event. A sale, payment or return pulled from a peer never moved it, and
the one apply path that did (the void-payment reversal) is what made a
receive-only computer show 30 where the books say 80. The credit-limit
check, the return settlement split and every receivables screen read that
wrong number on every computer except the one that rang the sale.

THE RULE. The balance of a party is a FUNCTION of rows that already sync
(sales, payments, returns, purchase_orders, party_balance_adjustments), so
every computer that holds the same rows computes the same figure. The stored
column is a display CACHE refreshed from this module; every money DECISION
and REPORT derives. The customer formula is the one `_statement_events`
(retail_api.py) already used term for term -- this module is that formula
moved into one place, so the statement, the receivables list and the credit
check cannot disagree.

  Customer (+ = owes the shop more):
    (1) sales with a customer: total - points_redeemed_amount - amount_paid,
        counted only when > eps                               -> +
    (2) active payments party_type='customer', related_type <> 'sale'
        ('in' -amount, 'out' +amount; a cheque bounce writes 'out')
    (3) completed returns of that customer's sales:
        -ar_forgiven_amount and -store_credit_amount, each only when > eps
    (4) party_balance_adjustments (customer)                  -> +amount
  Supplier (+ = the shop owes more):
    (1) purchase orders, by basis (W4a-2, schema v46):         -> +
        'receipt' basis -- the sum of the order's RECEIPT values
        'order' basis (orders that existed at v46; NULL too) -- the receipt
        values once the order is closed short (or cancelled after a
        delivery); 0 when cancelled; else the order total (> eps)
    (2) active payments party_type='supplier', ALL related_types incl 'po'
        and 'cheque': 'out' -amount, 'in' +amount
    (3) party_balance_adjustments (supplier)

THE SUM IS DONE IN INTEGER MINOR UNITS (fils for JOD): every term is
CAST(ROUND(x * 10^d) AS INTEGER) before it is added. A float SUM depends on
row order and differs by 1 ulp between two computers holding the same rows
in a different insert order; integers do not. (Mutation proof M10 turns the
unit order test red if this becomes a float SUM.)

`eps` is half a minor unit and MUST equal retail_api._money_epsilon -- a unit
test pins the two together, because the gates here have to agree with the
gates create_sale / create_return used when they decided a sale became a
debt.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from core.retail import pricing as tax_engine

log = logging.getLogger(__name__)

#: v44: the fixed namespace every carry uid is derived under. A literal, never
#: regenerated: two computers that both carry the same party must mint the SAME
#: uid so the sync apply's ON CONFLICT(uid) DO NOTHING collapses them into one
#: fact. (Mutation M7, a random uid here, turns the two-computer test red.)
CARRY_NAMESPACE = uuid.UUID('b44c2f0e-9d1a-5a7e-8c33-0f6d4a1e44ba')

CARRY_REASON = 'upgrade_v44_carry'


def carry_uid(party_type, party_id):
    """The uid of a party's one-and-only upgrade carry. Deliberately has NO
    company_id in it: a party is global to the shop (its id is a uuid that
    syncs), and the receiver stamps its own company_id on apply, so including
    the sender's company here would make the same carry look like two facts."""
    return str(uuid.uuid5(CARRY_NAMESPACE, f"{party_type}:{party_id}:v44-carry"))


def _queue_sync_event(cur, entity_type, entity_id, event_type, payload):
    """Same one INSERT retail_api's helper of this name does (this module
    cannot import retail_api: core/retail must not depend on api/)."""
    cur.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), entity_type, str(entity_id), event_type,
         json.dumps(payload), datetime.now(timezone.utc).isoformat()),
    )


def insert_carry(conn, company_id, party_type, party_id, amount, created_by='system:v44'):
    """Insert one party's upgrade carry (and queue its sync event) if it is not
    already there. Returns True when a row was inserted, False when the fixed
    uid already existed. Does NOT commit. No GL journal: the v38 opening
    journal already booked the stored balance this carry preserves."""
    uid = carry_uid(party_type, party_id)
    now_local = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    amt = float(amount)
    cur = conn.execute(
        "INSERT INTO party_balance_adjustments (uid, company_id, party_type, party_id, amount, reason, "
        "created_at, created_at_utc, created_by) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(uid) DO NOTHING",
        (uid, company_id, party_type, str(party_id), amt, CARRY_REASON, now_local, now_utc, created_by))
    if cur.rowcount != 1:
        return False
    has_outbox = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_outbox'").fetchone()
    if has_outbox:
        _queue_sync_event(conn, 'party_balance_adjustment', uid, 'create', {
            'uid': uid, 'party_type': party_type, 'party_id': str(party_id), 'amount': amt,
            'reason': CARRY_REASON, 'created_at': now_local, 'created_at_utc': now_utc,
            'created_by': created_by,
        })
    return True

#: Max ids bound into one `IN (...)` (SQLite's default variable limit is
#: 999 on older builds; 400 leaves room for the other parameters).
_CHUNK = 400

_TABLES = ('customers', 'suppliers')


def money_epsilon(currency=None):
    """Half a minor unit. Mirrors retail_api._money_epsilon exactly (pinned
    by retail_party_balance_unit_test.py)."""
    try:
        quant = tax_engine.currency_quantum(currency) if currency else Decimal('0.01')
        return float(quant) / 2.0
    except Exception:
        return 0.005


def _digits(currency):
    quant = tax_engine.currency_quantum(currency) if currency else Decimal('0.01')
    return -quant.as_tuple().exponent


def _factor(currency):
    return 10 ** _digits(currency)


def _columns(conn, table):
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _has(conn, table, *cols):
    have = _columns(conn, table)
    return bool(have) and all(c in have for c in cols)


def _scoped(ids):
    """Yield (clause, params) pairs narrowing a query to `ids`, chunked.
    ids=None yields one unfiltered pair (the clause is a company-wide no-op;
    every query keeps its own company_id filter either way)."""
    if ids is None:
        yield "", ()
        return
    ids = [str(i) for i in ids]
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i:i + _CHUNK]
        yield " AND {col} IN (%s)" % ",".join("?" * len(chunk)), tuple(chunk)


def _grouped(conn, sql, params, col, ids, scope_marker="{scope}"):
    """Run `sql` (which contains `{scope}` where the id narrowing goes) once
    per id chunk; returns {str(party_id): int minor units}. `scope_marker`
    names the placeholder when a query already uses `{scope}` for something
    else (W4a-2's nested supplier query)."""
    out = {}
    for clause, extra in _scoped(ids):
        q = sql.replace(scope_marker, clause.replace("{col}", col))
        for row in conn.execute(q, (*params, *extra)).fetchall():
            if row[0] is None:
                continue
            out[str(row[0])] = out.get(str(row[0]), 0) + int(row[1] or 0)
    return out


def _merge(total, part, sign=1):
    for k, v in part.items():
        total[k] = total.get(k, 0) + sign * v


def _adjustments(conn, cid, party_type, f, ids):
    if not _has(conn, 'party_balance_adjustments', 'company_id', 'party_type', 'party_id', 'amount'):
        return {}
    return _grouped(
        conn,
        "SELECT party_id, SUM(CAST(ROUND(amount * %d) AS INTEGER)) FROM party_balance_adjustments "
        "WHERE company_id=? AND party_type=?{scope} GROUP BY party_id" % f,
        (cid, party_type), "party_id", ids)


def _customer_minor(conn, cid, currency, ids):
    f, eps = _factor(currency), money_epsilon(currency)
    total = {}
    if _has(conn, 'sales', 'customer_id', 'total', 'amount_paid'):
        pts = "COALESCE(points_redeemed_amount,0)" if _has(conn, 'sales', 'points_redeemed_amount') else "0"
        net = "(total - %s - amount_paid)" % pts
        _merge(total, _grouped(
            conn,
            "SELECT customer_id, SUM(CAST(ROUND(%s * %d) AS INTEGER)) FROM sales "
            "WHERE company_id=? AND customer_id IS NOT NULL AND %s > ?{scope} GROUP BY customer_id"
            % (net, f, net), (cid, eps), "customer_id", ids))
    if _has(conn, 'payments', 'party_type', 'party_id', 'direction', 'amount', 'related_type', 'status'):
        _merge(total, _grouped(
            conn,
            "SELECT party_id, SUM(CASE WHEN direction='in' THEN -1 ELSE 1 END * CAST(ROUND(amount * %d) AS INTEGER)) "
            "FROM payments WHERE company_id=? AND party_type='customer' AND party_id IS NOT NULL "
            "AND COALESCE(status,'active')='active' AND COALESCE(related_type,'') <> 'sale'{scope} "
            "GROUP BY party_id" % f, (cid,), "party_id", ids))
    if _has(conn, 'returns', 'sale_id', 'status', 'ar_forgiven_amount', 'store_credit_amount'):
        for col in ('ar_forgiven_amount', 'store_credit_amount'):
            _merge(total, _grouped(
                conn,
                "SELECT s.customer_id, SUM(CAST(ROUND(r.%s * %d) AS INTEGER)) FROM returns r "
                "JOIN sales s ON s.id = r.sale_id AND s.company_id = r.company_id "
                "WHERE r.company_id=? AND r.status='completed' AND s.customer_id IS NOT NULL "
                "AND COALESCE(r.%s,0) > ?{scope} GROUP BY s.customer_id" % (col, f, col),
                (cid, eps), "s.customer_id", ids), sign=-1)
    _merge(total, _adjustments(conn, cid, 'customer', f, ids))
    return total


def _supplier_minor(conn, cid, currency, ids):
    f, eps = _factor(currency), money_epsilon(currency)
    total = {}
    if (_has(conn, 'purchase_orders', 'supplier_id', 'total', 'status', 'ap_basis', 'closed_at', 'cancelled_at')
            and _has(conn, 'purchase_order_receipts', 'po_id', 'value', 'company_id')):
        # W4a-2 (schema v46): what an order adds to its supplier's balance
        # depends on its BASIS (core/retail/purchase_orders.po_supplier_charge_
        # minor is the same rule per order -- the statement and the aging read
        # that one; a unit test pins the two together):
        #   'receipt' basis (every order v46 code creates): the sum of its
        #     RECEIPT values, whatever its state -- nothing arrived, nothing is
        #     owed, and a close-short needs no special case;
        #   'order' basis (every order that existed at the upgrade, NULL
        #     included -- AP was booked when it was typed): closed (or cancelled
        #     after a delivery, which IS a close-short) -> the receipt values;
        #     else cancelled -> 0; else the order total, as before.
        # Receipt values are summed in integer minor units like every term here.
        _merge(total, _grouped(
            conn,
            "SELECT supplier_id, SUM(CASE "
            "  WHEN ap_basis = 'receipt' THEN rv "
            "  WHEN closed_at IS NOT NULL OR (cancelled_at IS NOT NULL AND rn > 0) THEN rv "
            "  WHEN cancelled_at IS NOT NULL OR COALESCE(status,'') = 'cancelled' THEN 0 "
            "  WHEN total > ? THEN CAST(ROUND(total * %(f)d) AS INTEGER) "
            "  ELSE 0 END) "
            "FROM (SELECT p.supplier_id AS supplier_id, p.ap_basis AS ap_basis, p.closed_at AS closed_at, "
            "             p.cancelled_at AS cancelled_at, p.status AS status, p.total AS total, "
            "             (SELECT COUNT(*) FROM purchase_order_receipts r "
            "               WHERE r.company_id = p.company_id AND r.po_id = p.id) AS rn, "
            "             COALESCE((SELECT SUM(CAST(ROUND(r.value * %(f)d) AS INTEGER)) "
            "                         FROM purchase_order_receipts r "
            "                        WHERE r.company_id = p.company_id AND r.po_id = p.id), 0) AS rv "
            "        FROM purchase_orders p WHERE p.company_id=? AND p.supplier_id IS NOT NULL{scope_p}) "
            "GROUP BY supplier_id" % {'f': f},
            (eps, cid), "p.supplier_id", ids, scope_marker="{scope_p}"))
    elif _has(conn, 'purchase_orders', 'supplier_id', 'total'):
        # STOCK-BOOKS: a CANCELLED order is not owed -- its order journal is
        # reversed in the books, so the derived balance must drop it too or
        # the AP tie breaks. COALESCE: a row with no status still counts; a
        # table with no status column at all (old fixture) is filtered by
        # nothing, exactly as before. (A pre-v46 database -- no receipt tables
        # -- is derived this way, unchanged.)
        not_cancelled = (" AND COALESCE(status,'') <> 'cancelled'"
                         if _has(conn, 'purchase_orders', 'status') else "")
        _merge(total, _grouped(
            conn,
            "SELECT supplier_id, SUM(CAST(ROUND(total * %d) AS INTEGER)) FROM purchase_orders "
            "WHERE company_id=? AND supplier_id IS NOT NULL AND total > ?" % f
            + not_cancelled + "{scope} GROUP BY supplier_id",
            (cid, eps), "supplier_id", ids))
    if _has(conn, 'payments', 'party_type', 'party_id', 'direction', 'amount', 'status'):
        _merge(total, _grouped(
            conn,
            "SELECT party_id, SUM(CASE WHEN direction='out' THEN -1 ELSE 1 END * CAST(ROUND(amount * %d) AS INTEGER)) "
            "FROM payments WHERE company_id=? AND party_type='supplier' AND party_id IS NOT NULL "
            "AND COALESCE(status,'active')='active'{scope} GROUP BY party_id" % f,
            (cid,), "party_id", ids))
    _merge(total, _adjustments(conn, cid, 'supplier', f, ids))
    return total


def _as_decimals(minor, currency, ids, existing_ids):
    quant = tax_engine.currency_quantum(currency) if currency else Decimal('0.01')
    f = Decimal(_factor(currency))
    keys = set(minor) | {str(i) for i in (ids if ids is not None else existing_ids)}
    return {k: (Decimal(minor.get(k, 0)) / f).quantize(quant) for k in keys}


def _existing(conn, cid, table):
    return [str(r[0]) for r in conn.execute(
        f"SELECT id FROM {table} WHERE company_id=?", (cid,)).fetchall()]


def customer_balances(conn, cid, currency, ids=None):
    """{str(customer_id): Decimal}. ids=None -> every customer of the company
    (zero for one with no events); ids=[...] -> exactly those ids."""
    minor = _customer_minor(conn, cid, currency, ids)
    return _as_decimals(minor, currency, ids, _existing(conn, cid, 'customers') if ids is None else ())


def supplier_balances(conn, cid, currency, ids=None):
    minor = _supplier_minor(conn, cid, currency, ids)
    return _as_decimals(minor, currency, ids, _existing(conn, cid, 'suppliers') if ids is None else ())


def customer_balance(conn, cid, customer_id, currency):
    return customer_balances(conn, cid, currency, [customer_id])[str(customer_id)]


def supplier_balance(conn, cid, supplier_id, currency):
    return supplier_balances(conn, cid, currency, [supplier_id])[str(supplier_id)]


def _derive(conn, cid, table, currency, ids):
    return (customer_balances if table == 'customers' else supplier_balances)(conn, cid, currency, ids)


def _currency_of(conn, cid):
    try:
        row = conn.execute(
            "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='base_currency'", (cid,)).fetchone()
        return (row[0] if row and row[0] else None) or tax_engine.DEFAULT_BASE_CURRENCY
    except Exception:
        return tax_engine.DEFAULT_BASE_CURRENCY


def refresh_cache(conn, cid, table, ids=None, currency=None):
    """Rewrite `<table>.credit_balance` to the derived figure for the rows
    whose stored value differs (in minor units). Returns the number of rows
    changed. Does NOT commit -- the caller owns the transaction. UPDATE only
    touches `credit_balance` (device-local, outside every row_version bump,
    exactly like the old _adjust_credit)."""
    if table not in _TABLES:
        raise ValueError(table)
    if currency is None:
        currency = _currency_of(conn, cid)
    f = _factor(currency)
    derived = _derive(conn, cid, table, currency, ids)
    changed = 0
    # v44 F4: the stored figures are read in ONE company-scoped query (a
    # scoped call reads its ids in chunked IN queries) instead of one SELECT
    # per party -- on a 500-debtor shop the per-party read was the bulk of the
    # steady-state sweep. Only rows whose stored figure differs are UPDATEd.
    stored = {}
    if ids is None:
        for row in conn.execute(f"SELECT id, credit_balance FROM {table} WHERE company_id=?", (cid,)).fetchall():
            stored[str(row[0])] = row[1]
    else:
        for clause, extra in _scoped(ids):
            q = f"SELECT id, credit_balance FROM {table} WHERE company_id=?" + clause.replace("{col}", "id")
            for row in conn.execute(q, (cid, *extra)).fetchall():
                stored[str(row[0])] = row[1]
    for key, val in derived.items():
        if key not in stored:
            continue
        stored_minor = int(round(float(stored[key] or 0) * f))
        want_minor = int((val * f).to_integral_value())
        if stored_minor != want_minor:
            conn.execute(f"UPDATE {table} SET credit_balance=? WHERE id=? AND company_id=?",
                         (float(val), key, cid))
            changed += 1
    return changed


def refresh_all_companies(conn):
    """Boot / post-pull sweep over every company that has parties. Returns
    the total number of rows changed. Caller commits."""
    changed = 0
    for table in _TABLES:
        for (cid,) in conn.execute(f"SELECT DISTINCT company_id FROM {table}").fetchall():
            changed += refresh_cache(conn, cid, table)
    return changed


def drift_report(conn, cid, currency, include_equal=False):
    """Stored vs derived per party, as a list of dicts (customers then
    suppliers). Only parties whose figures differ by more than eps unless
    include_equal=True (then every party with a nonzero figure either way).
    Read-only."""
    eps = money_epsilon(currency)
    out = []
    for table, kind in (('customers', 'customer'), ('suppliers', 'supplier')):
        derived = _derive(conn, cid, table, currency, None)
        for row in conn.execute(
                f"SELECT id, name, credit_balance FROM {table} WHERE company_id=? ORDER BY id", (cid,)).fetchall():
            stored = float(row[2] or 0)
            der = float(derived.get(str(row[0]), Decimal(0)))
            differs = abs(stored - der) > eps
            if differs or (include_equal and (abs(stored) > eps or abs(der) > eps)):
                out.append({'party_type': kind, 'party_id': str(row[0]), 'name': row[1],
                            'stored': stored, 'derived': der, 'diff': round(stored - der, 6)})
    return out
