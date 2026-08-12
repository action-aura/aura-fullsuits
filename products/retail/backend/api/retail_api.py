"""
Aura Retail — Retail & POS API
Full-suite retail management: products, POS, customers, suppliers,
purchase orders, returns, reports.

Extracted verbatim (business logic unchanged) from Action Aura Enterprise's
api/subsystems/retail_api.py -- see docs/migration/retail-extraction-report.md.
"""
import os
import time
import json
import sqlite3
import uuid as _uuid
import requests
from decimal import Decimal, ROUND_HALF_UP
from flask import Blueprint, request, jsonify, session, current_app
from commercial_runtime.identity.mt_auth import mt_login_required, mt_require_subsystem
from commercial_runtime.licensing_contracts.flask_guard import make_capability_guard
from commercial_runtime.sync.sync_service import nudge as _sync_nudge
from commercial_runtime.sync.sync_service import get_active_health as _sync_get_active_health
from commercial_runtime.notifications import settings as _notification_settings
from commercial_runtime.notifications.outbox import EmailOutboxRepository as _EmailOutboxRepository
from database.schema import get_retail_conn, sub_create
from datetime import datetime, timedelta, timezone
from core.retail import pricing as tax_engine
from core.retail import po_split
from config import DATABASE_DIR

retail_bp = Blueprint('retail_api', __name__, url_prefix='/api/sub/retail')

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

# ── Session helpers ────────────────────────────────────────────────────────────
def _cid():
    return session.get('company_id') or session.get('mt_company_id', 1)

def _uid():
    return session.get('mt_user_id') or session.get('user_id', 'system')


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
    cur.execute("INSERT INTO branches (company_id,name,address,phone) VALUES (?,?,?,?)",
                (cid, 'Main Branch', '', ''))
    return cur.lastrowid

def _audit(conn, action, entity, entity_id, details=''):
    try:
        conn.execute(
            'INSERT INTO audit_log (company_id,user_id,action,entity,entity_id,details) VALUES (?,?,?,?,?,?)',
            (_cid(), _uid(), action, entity, entity_id, str(details))
        )
    except Exception:
        pass

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
def dashboard_stats():
    cid = _cid()
    conn = get_retail_conn()
    today = datetime.now().strftime('%Y-%m-%d')
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    month_start = datetime.now().replace(day=1).strftime('%Y-%m-%d')

    def q(sql, *params):
        return conn.execute(sql, params).fetchone()[0] or 0

    today_sales    = q("SELECT COALESCE(SUM(total),0) FROM sales WHERE company_id=? AND date(created_at)=?", cid, today)
    today_txns     = q("SELECT COUNT(*) FROM sales WHERE company_id=? AND date(created_at)=?", cid, today)
    yest_sales     = q("SELECT COALESCE(SUM(total),0) FROM sales WHERE company_id=? AND date(created_at)=?", cid, yesterday)
    month_sales    = q("SELECT COALESCE(SUM(total),0) FROM sales WHERE company_id=? AND date(created_at)>=?", cid, month_start)
    month_txns     = q("SELECT COUNT(*) FROM sales WHERE company_id=? AND date(created_at)>=?", cid, month_start)

    # Returns / refunds reduce revenue — net them out so the dashboard reflects
    # real money taken in (a refund must lower today's revenue, not leave it flat).
    try:
        today_returns = q("SELECT COALESCE(SUM(refund_amount),0) FROM returns WHERE company_id=? AND date(created_at)=?", cid, today)
        yest_returns  = q("SELECT COALESCE(SUM(refund_amount),0) FROM returns WHERE company_id=? AND date(created_at)=?", cid, yesterday)
        month_returns = q("SELECT COALESCE(SUM(refund_amount),0) FROM returns WHERE company_id=? AND date(created_at)>=?", cid, month_start)
    except Exception:
        today_returns = yest_returns = month_returns = 0
    today_sales = today_sales - today_returns
    yest_sales  = yest_sales - yest_returns
    month_sales = month_sales - month_returns
    total_customers = q("SELECT COUNT(*) FROM customers WHERE company_id=?", cid)
    total_products  = q("SELECT COUNT(*) FROM products WHERE company_id=? AND status='active'", cid)

    low_stock = conn.execute("""
        SELECT COUNT(p.id) FROM products p
        LEFT JOIN (SELECT product_id, SUM(quantity_on_hand) as qty
                   FROM inventory_balances WHERE company_id=? GROUP BY product_id) b ON p.id=b.product_id
        WHERE p.company_id=? AND COALESCE(b.qty,0) <= p.reorder_level AND p.status='active'
    """, (cid, cid)).fetchone()[0] or 0

    # Hourly breakdown today
    hourly = conn.execute("""
        SELECT strftime('%H',created_at) as hr, COALESCE(SUM(total),0) as rev, COUNT(*) as cnt
        FROM sales WHERE company_id=? AND date(created_at)=?
        GROUP BY hr ORDER BY hr
    """, (cid, today)).fetchall()
    hourly_labels = [f"{r['hr']}:00" for r in hourly]
    hourly_data   = [round(r['rev'], 2) for r in hourly]

    # Payment method breakdown today
    pay_rows = conn.execute("""
        SELECT payment_method, COUNT(*) as cnt, COALESCE(SUM(total),0) as rev
        FROM sales WHERE company_id=? AND date(created_at)=?
        GROUP BY payment_method
    """, (cid, today)).fetchall()
    pay_methods = {r['payment_method']: {'count': r['cnt'], 'revenue': round(r['rev'],2)} for r in pay_rows}

    # Recent sales
    recent = conn.execute("""
        SELECT s.sale_number, s.total, s.payment_method, s.created_at,
               COALESCE(c.name,'Walk-in') as customer_name,
               COUNT(si.id) as item_count
        FROM sales s
        LEFT JOIN customers c ON s.customer_id=c.id
        LEFT JOIN sale_items si ON s.id=si.sale_id
        WHERE s.company_id=?
        GROUP BY s.id
        ORDER BY s.created_at DESC LIMIT 8
    """, (cid,)).fetchall()

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
    rows = conn.execute(
        "SELECT c.*, COUNT(p.id) as product_count FROM categories c "
        "LEFT JOIN products p ON p.category_id=c.id AND p.status='active' "
        "WHERE c.company_id=? GROUP BY c.id ORDER BY c.name", (cid,)
    ).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@retail_bp.route('/categories', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.product.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def create_category():
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Category name required'}), 400
    cid = _cid()
    conn = get_retail_conn()
    cur = conn.cursor()
    new_id = str(_uuid.uuid4())
    cur.execute("INSERT INTO categories (id, company_id, name, description) VALUES (?,?,?,?)",
                (new_id, cid, data['name'], data.get('description', '')))
    # No `company_id` in the wire payload -- it is meaningless cross-device
    # (each device derives its own `company_id` locally at onboarding; see
    # commercial_runtime/sync/sync_service.py's module docstring). The
    # receiving device stamps ITS OWN company_id on apply.
    _queue_sync_event(cur, 'category', new_id, 'create', {
        'id': new_id, 'name': data['name'], 'description': data.get('description', ''),
    })
    conn.commit(); conn.close()
    _sync_nudge()  # best-effort immediate push -- see commercial_runtime/sync/sync_service.py
    return jsonify({'status': 'success', 'data': {'id': new_id}})

@retail_bp.route('/categories/<string:category_id>', methods=['PUT'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.product.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def update_category(category_id):
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Category name required'}), 400
    cid = _cid()
    conn = get_retail_conn()
    cur = conn.cursor()
    cur.execute("UPDATE categories SET name=?, description=? WHERE id=? AND company_id=?",
                (data['name'], data.get('description', ''), category_id, cid))
    if cur.rowcount == 0:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Category not found'}), 404
    # No `company_id` in the wire payload -- see create_category's comment above.
    _queue_sync_event(cur, 'category', category_id, 'update', {
        'id': category_id, 'name': data['name'], 'description': data.get('description', ''),
    })
    conn.commit(); conn.close()
    _sync_nudge()  # best-effort immediate push -- see commercial_runtime/sync/sync_service.py
    return jsonify({'status': 'success'})

@retail_bp.route('/categories/<string:category_id>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.product.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
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
        cur.execute("DELETE FROM categories WHERE id=? AND company_id=?", (category_id, cid))
        if cur.rowcount == 0:
            return jsonify({'status': 'error', 'message': 'Category not found'}), 404
        _queue_sync_event(cur, 'category', category_id, 'delete', {'id': category_id})
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
    rows = conn.execute("""
        SELECT p.*, c.name as category_name,
               COALESCE(SUM(b.quantity_on_hand), 0) as total_stock
        FROM products p
        LEFT JOIN categories c ON p.category_id=c.id
        LEFT JOIN inventory_balances b ON p.id=b.product_id AND b.company_id=p.company_id
        WHERE p.company_id=? AND p.status='active'
        GROUP BY p.id ORDER BY p.name
    """, (cid,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@retail_bp.route('/products', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.product.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def create_product():
    data = request.json or {}
    cid  = _cid()
    if not data.get('name') or not data.get('sku'):
        return jsonify({'status': 'error', 'message': 'Name and SKU are required'}), 400
    conn = get_retail_conn()
    try:
        existing = conn.execute("SELECT id FROM products WHERE company_id=? AND sku=?",
                                (cid, data['sku'])).fetchone()
        if existing:
            conn.close()
            return jsonify({'status': 'error', 'message': 'SKU already exists'}), 409
        cur = conn.cursor()
        pid = str(_uuid.uuid4())
        cur.execute("""
            INSERT INTO products (id,company_id,sku,barcode,name,category_id,supplier_id,cost_price,
                                  sell_price,tax_rate,unit,reorder_level,reorder_method,status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'active')
        """, (pid, cid, data['sku'], data.get('barcode',''), data['name'],
              data.get('category_id'), data.get('supplier_id'), data.get('cost_price',0), data.get('sell_price',0),
              data.get('tax_rate',0), data.get('unit','pcs'), data.get('reorder_level',5),
              data.get('reorder_method','none')))
        # File opening stock under the company's working branch — the SAME branch that
        # sales/returns/adjustments resolve to (via _default_branch), so a sale always
        # decrements the row this created. Self-heals a branch on fresh/standalone installs.
        bid = _default_branch(conn, cid)
        conn.execute("""
            INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand)
            VALUES (?,?,?,?)
        """, (cid, pid, bid, data.get('initial_stock', 0)))
        if data.get('initial_stock', 0) > 0:
            conn.execute("""
                INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,reference,created_by)
                VALUES (?,?,?,'opening_stock',?,?,?)
            """, (cid, pid, bid, data.get('initial_stock', 0), 'OPENING', _uid()))
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
def update_product(pid):
    data = request.json or {}
    cid  = _cid()
    allowed = ['name','barcode','category_id','supplier_id','cost_price','sell_price','tax_rate','unit','reorder_level','reorder_method','status']
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({'status': 'error', 'message': 'No valid fields'}), 400
    conn = get_retail_conn()
    cur = conn.cursor()
    sets = ', '.join(f'{k}=?' for k in fields)
    cur.execute(f'UPDATE products SET {sets} WHERE id=? AND company_id=?',
                list(fields.values()) + [pid, cid])
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
    row = conn.execute("SELECT sku,barcode,name,category_id,supplier_id,cost_price,sell_price,tax_rate,unit,reorder_level,reorder_method,status FROM products WHERE id=?", (pid,)).fetchone()
    _queue_sync_event(cur, 'product', pid, 'update', dict(row) | {'id': pid})
    conn.commit(); conn.close()
    _sync_nudge()
    return jsonify({'status': 'success'})

@retail_bp.route('/products/<string:pid>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.product.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def delete_product(pid):
    cid = _cid()
    conn = get_retail_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE products SET status='inactive' WHERE id=? AND company_id=?", (pid, cid))
        if cur.rowcount == 0:
            return jsonify({'status': 'error', 'message': 'Product not found'}), 404
        _audit(conn, 'PRODUCT_DELETED', 'product', pid, 'Product deactivated')
        _queue_sync_event(cur, 'product', pid, 'delete', {'id': pid})
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
def adjust_stock(pid):
    data = request.json or {}
    cid  = _cid()
    qty  = float(data.get('quantity', 0))
    reason = data.get('reason', 'Manual adjustment')
    if qty == 0:
        return jsonify({'status': 'error', 'message': 'Quantity cannot be zero'}), 400
    conn = get_retail_conn()
    bid = _default_branch(conn, cid)
    conn.execute("""
        INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand)
        VALUES (?,?,?,0)
    """, (cid, pid, bid))
    conn.execute("""
        UPDATE inventory_balances SET quantity_on_hand = quantity_on_hand + ?
        WHERE company_id=? AND product_id=? AND branch_id=?
    """, (qty, cid, pid, bid))
    conn.execute("""
        INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,reference,notes,created_by)
        VALUES (?,?,?,?,?,?,?,?)
    """, (cid, pid, bid,
          'stock_in' if qty > 0 else 'stock_out',
          qty, 'ADJ', reason, _uid()))
    _audit(conn, 'STOCK_ADJUSTED', 'product', pid, f'qty={qty}, reason={reason}')
    conn.commit()
    new_qty = conn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (cid, pid, bid)
    ).fetchone()
    conn.close()
    return jsonify({'status': 'success', 'new_stock': new_qty['quantity_on_hand'] if new_qty else 0})

# ── Customers ─────────────────────────────────────────────────────────────────

@retail_bp.route('/customers', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def list_customers():
    cid = _cid()
    q   = request.args.get('q', '')
    conn = get_retail_conn()
    _ensure_credit_schema(conn)
    if q:
        rows = conn.execute("""
            SELECT c.*, COUNT(s.id) as order_count
            FROM customers c LEFT JOIN sales s ON s.customer_id=c.id AND s.company_id=c.company_id
            WHERE c.company_id=? AND c.status='active' AND (c.name LIKE ? OR c.phone LIKE ? OR c.email LIKE ?)
            GROUP BY c.id ORDER BY c.name
        """, (cid, f'%{q}%', f'%{q}%', f'%{q}%')).fetchall()
    else:
        rows = conn.execute("""
            SELECT c.*, COUNT(s.id) as order_count
            FROM customers c LEFT JOIN sales s ON s.customer_id=c.id AND s.company_id=c.company_id
            WHERE c.company_id=? AND c.status='active' GROUP BY c.id ORDER BY c.total_spent DESC LIMIT 200
        """, (cid,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@retail_bp.route('/customers', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.customer.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def create_customer():
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Customer name required'}), 400
    cid  = _cid()
    conn = get_retail_conn()
    cur  = conn.cursor()
    nid = str(_uuid.uuid4())
    cur.execute("INSERT INTO customers (id,company_id,name,phone,email,address) VALUES (?,?,?,?,?,?)",
                (nid, cid, data['name'], data.get('phone',''), data.get('email',''), data.get('address','')))
    _audit(conn, 'CUSTOMER_CREATED', 'customer', nid, data['name'])
    _queue_sync_event(cur, 'customer', nid, 'create', {
        'id': nid, 'name': data['name'], 'phone': data.get('phone', ''),
        'email': data.get('email', ''), 'address': data.get('address', ''),
    })
    conn.commit(); conn.close()
    _sync_nudge()
    return jsonify({'status': 'success', 'data': {'id': nid}})

@retail_bp.route('/customers/<string:cust_id>', methods=['PATCH'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.customer.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def update_customer(cust_id):
    data = request.json or {}
    cid  = _cid()
    fields = {k: v for k, v in data.items() if k in ['name','phone','email','address','credit_mode','credit_limit']}
    if not fields:
        return jsonify({'status': 'error', 'message': 'No valid fields'}), 400
    conn = get_retail_conn()
    cur = conn.cursor()
    sets = ', '.join(f'{k}=?' for k in fields)
    cur.execute(f'UPDATE customers SET {sets} WHERE id=? AND company_id=?',
                list(fields.values()) + [cust_id, cid])
    if cur.rowcount == 0:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Customer not found'}), 404
    _audit(conn, 'CUSTOMER_UPDATED', 'customer', cust_id)
    row = conn.execute("SELECT name,phone,email,address FROM customers WHERE id=?", (cust_id,)).fetchone()
    _queue_sync_event(cur, 'customer', cust_id, 'update', dict(row) | {'id': cust_id})
    conn.commit(); conn.close()
    _sync_nudge()
    return jsonify({'status': 'success'})

@retail_bp.route('/customers/<string:cust_id>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.customer.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def delete_customer(cust_id):
    cid = _cid()
    conn = get_retail_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE customers SET status='inactive' WHERE id=? AND company_id=?", (cust_id, cid))
        if cur.rowcount == 0:
            return jsonify({'status': 'error', 'message': 'Customer not found'}), 404
        _audit(conn, 'CUSTOMER_DELETED', 'customer', cust_id, 'Customer deactivated')
        _queue_sync_event(cur, 'customer', cust_id, 'delete', {'id': cust_id})
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
        SELECT s.sale_number, s.total, s.payment_method, s.created_at,
               COUNT(si.id) as items
        FROM sales s LEFT JOIN sale_items si ON si.sale_id=s.id
        WHERE s.company_id=? AND s.customer_id=?
        GROUP BY s.id ORDER BY s.created_at DESC LIMIT 50
    """, (cid, cust_id)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

# ── Suppliers ─────────────────────────────────────────────────────────────────

@retail_bp.route('/suppliers', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def list_suppliers():
    cid = _cid()
    conn = get_retail_conn()
    _ensure_credit_schema(conn)
    rows = conn.execute("""
        SELECT s.*, COUNT(po.id) as order_count
        FROM suppliers s LEFT JOIN purchase_orders po ON po.supplier_id=s.id AND po.company_id=s.company_id
        WHERE s.company_id=? AND s.status='active'
        GROUP BY s.id ORDER BY s.name
    """, (cid,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@retail_bp.route('/suppliers', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.supplier.manage", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def create_supplier():
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Supplier name required'}), 400
    cid = _cid()
    conn = get_retail_conn()
    cur = conn.cursor()
    nid = str(_uuid.uuid4())
    cur.execute("INSERT INTO suppliers (id,company_id,name,phone,email,address) VALUES (?,?,?,?,?,?)",
                (nid, cid, data['name'], data.get('phone',''), data.get('email',''), data.get('address','')))
    _audit(conn, 'SUPPLIER_CREATED', 'supplier', nid, data['name'])
    _queue_sync_event(cur, 'supplier', nid, 'create', {
        'id': nid, 'name': data['name'], 'phone': data.get('phone', ''),
        'email': data.get('email', ''), 'address': data.get('address', ''),
    })
    conn.commit(); conn.close()
    _sync_nudge()
    return jsonify({'status': 'success', 'data': {'id': nid}})

@retail_bp.route('/suppliers/<string:sid>', methods=['PATCH'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.supplier.manage", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def update_supplier(sid):
    data = request.json or {}
    cid  = _cid()
    fields = {k: v for k, v in data.items() if k in ['name','phone','email','address','status','payment_terms']}
    if not fields:
        return jsonify({'status': 'error', 'message': 'No valid fields'}), 400
    conn = get_retail_conn()
    cur = conn.cursor()
    sets = ', '.join(f'{k}=?' for k in fields)
    cur.execute(f'UPDATE suppliers SET {sets} WHERE id=? AND company_id=?',
                list(fields.values()) + [sid, cid])
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
    row = conn.execute("SELECT name,phone,email,address,status,payment_terms FROM suppliers WHERE id=?", (sid,)).fetchone()
    _queue_sync_event(cur, 'supplier', sid, 'update', dict(row) | {'id': sid})
    conn.commit(); conn.close()
    _sync_nudge()
    return jsonify({'status': 'success'})

@retail_bp.route('/suppliers/<string:sid>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.supplier.manage", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def delete_supplier(sid):
    cid = _cid()
    conn = get_retail_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE suppliers SET status='inactive' WHERE id=? AND company_id=?", (sid, cid))
        if cur.rowcount == 0:
            return jsonify({'status': 'error', 'message': 'Supplier not found'}), 404
        _audit(conn, 'SUPPLIER_DELETED', 'supplier', sid, 'Supplier deactivated')
        _queue_sync_event(cur, 'supplier', sid, 'delete', {'id': sid})
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
def create_supplier_contact(sid):
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Contact name required'}), 400
    cid = _cid()
    conn = get_retail_conn()
    try:
        sup = conn.execute("SELECT id FROM suppliers WHERE id=? AND company_id=?", (sid, cid)).fetchone()
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
    rows = conn.execute("""
        SELECT po.*, s.name as supplier_name
        FROM purchase_orders po LEFT JOIN suppliers s ON po.supplier_id=s.id
        WHERE po.company_id=? ORDER BY po.created_at DESC LIMIT 100
    """, (cid,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@retail_bp.route('/purchase-orders', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.purchase.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def create_purchase_order():
    data = request.json or {}
    cid  = _cid()
    items = data.get('items', [])
    if not items:
        return jsonify({'status': 'error', 'message': 'At least one item required'}), 400
    conn = get_retail_conn()
    _ensure_credit_schema(conn)
    cur  = conn.cursor()
    po_number = _next_ref(conn, cid, 'po')
    total = _money(sum(float(i.get('unit_cost', 0)) * float(i.get('quantity', 0)) for i in items))
    supplier_id = data.get('supplier_id')
    amount_paid = _money(data.get('amount_paid', 0))
    payment_status = 'paid' if amount_paid >= total - 0.005 else ('partial' if amount_paid > 0.005 else 'unpaid')
    cur.execute("""
        INSERT INTO purchase_orders (company_id,po_number,supplier_id,branch_id,status,subtotal,total,notes,ordered_at,
                                     amount_paid,payment_status,due_date)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (cid, po_number, supplier_id, data.get('branch_id') or _default_branch(conn, cid),
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
    conn.commit(); conn.close()
    return jsonify({'status': 'success', 'data': {'id': po_id, 'po_number': po_number, 'payment_status': payment_status}})

@retail_bp.route('/purchase-orders/<int:po_id>', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def get_purchase_order(po_id):
    cid = _cid()
    conn = get_retail_conn()
    po   = conn.execute("SELECT po.*,s.name as supplier_name FROM purchase_orders po LEFT JOIN suppliers s ON po.supplier_id=s.id WHERE po.id=? AND po.company_id=?", (po_id, cid)).fetchone()
    if not po:
        conn.close(); return jsonify({'status': 'error', 'message': 'Not found'}), 404
    items = conn.execute("""
        SELECT poi.*, p.name as product_name, p.sku
        FROM purchase_order_items poi LEFT JOIN products p ON poi.product_id=p.id
        WHERE poi.po_id=?
    """, (po_id,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': {'po': dict(po), 'items': [dict(i) for i in items]}})

@retail_bp.route('/purchase-orders/<int:po_id>/receive', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.purchase.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def receive_purchase_order(po_id):
    """Mark PO as received and add stock to inventory."""
    cid  = _cid()
    conn = get_retail_conn()
    po   = conn.execute("SELECT * FROM purchase_orders WHERE id=? AND company_id=?", (po_id, cid)).fetchone()
    if not po:
        conn.close(); return jsonify({'status': 'error', 'message': 'PO not found'}), 404
    if po['status'] == 'received':
        conn.close(); return jsonify({'status': 'error', 'message': 'PO already received'}), 409

    items = conn.execute("SELECT * FROM purchase_order_items WHERE po_id=?", (po_id,)).fetchall()
    bid   = po['branch_id'] or _default_branch(conn, cid)
    for item in items:
        qty = item['quantity']
        conn.execute("""
            INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand)
            VALUES (?,?,?,0)
        """, (cid, item['product_id'], bid))
        conn.execute("""
            UPDATE inventory_balances SET quantity_on_hand = quantity_on_hand + ?
            WHERE company_id=? AND product_id=? AND branch_id=?
        """, (qty, cid, item['product_id'], bid))
        conn.execute("""
            INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,unit_cost,reference,created_by)
            VALUES (?,?,?,'purchase_in',?,?,?,?)
        """, (cid, item['product_id'], bid, qty, item['unit_cost'], po['po_number'], _uid()))
        conn.execute("UPDATE purchase_order_items SET received_qty=? WHERE id=?", (qty, item['id']))

    conn.execute("UPDATE purchase_orders SET status='received', received_at=? WHERE id=?",
                 (datetime.now().strftime('%Y-%m-%d'), po_id))
    _audit(conn, 'PO_RECEIVED', 'purchase_order', po_id, po['po_number'])
    conn.commit(); conn.close()
    _emit('StockReceived', {'po_id': po_id, 'po_number': po['po_number']})
    return jsonify({'status': 'success'})

@retail_bp.route('/purchase-orders/split-preview', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.purchase.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
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

        placeholders = ','.join('?' * len(product_ids))
        prod_rows = conn.execute(
            f"SELECT id, name, sku, supplier_id, cost_price FROM products "
            f"WHERE company_id=? AND id IN ({placeholders})",
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

        product = conn.execute(
            "SELECT id, name, supplier_id, cost_price, reorder_level FROM products WHERE id=? AND company_id=?",
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
        branch_id = req['branch_id'] or _default_branch(conn, cid)

        po_number = _next_ref(conn, cid, 'po')
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
        cur.execute(
            "UPDATE reorder_requests SET status='accepted', resolved_at=? WHERE id=?",
            (now, rid),
        )
        _queue_sync_event(cur, 'reorder_request', rid, 'update', {
            'id': rid, 'branch_id': req['branch_id'], 'product_id': req['product_id'],
            'status': 'accepted', 'draft_message': req['draft_message'], 'resolved_at': now,
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
        cur.execute(
            "UPDATE reorder_requests SET status='declined', resolved_at=? WHERE id=?",
            (now, rid),
        )
        _queue_sync_event(cur, 'reorder_request', rid, 'update', {
            'id': rid, 'branch_id': req['branch_id'], 'product_id': req['product_id'],
            'status': 'declined', 'draft_message': req['draft_message'], 'resolved_at': now,
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
            ex = cur.execute("SELECT id,sale_number FROM sales WHERE idempotency_key=?", (idem,)).fetchone()
            if ex:
                conn.close()
                return jsonify({'status': 'success', 'data': {'id': ex['id'], 'sale_number': ex['sale_number']}})

        items_in = data.get('items') or []
        if not items_in:
            conn.close()
            return jsonify({'status': 'error', 'message': 'No items in sale.'}), 400

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
        bid = int(data.get('branch_id') or _default_branch(conn, cid))
        mode = _settings(conn, cid).get('tax_calculation_mode', tax_engine.DEFAULT_MODE)

        # ── Resolve authoritative line data (server is the sole financial
        # authority -- AUDIT-002/AUDIT-003). unit_price/tax_rate always come
        # from the product row; only quantity and discount_pct are accepted
        # as client-submitted commercial intent, and discount_pct is clamped.
        resolved_lines = []
        subtotal = discount = tax = total = Decimal('0')
        for item in items_in:
            pid = item.get('product_id')
            product = cur.execute(
                "SELECT id, name, sell_price, tax_rate, status FROM products WHERE id=? AND company_id=?",
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
            if qty > on_hand:
                conn.rollback(); conn.close()
                return jsonify({'status': 'error',
                                 'message': f'Insufficient stock for "{product["name"]}" (have {on_hand}, requested {qty}).'}), 400

            discount_pct = tax_engine.clamp_discount_pct(item.get('discount_pct', 0))
            unit_price = float(product['sell_price'])
            tax_rate = float(product['tax_rate'])
            calc = tax_engine.calculate_line(unit_price, qty, discount_pct, tax_rate, mode=mode)

            resolved_lines.append({
                'product_id': pid, 'quantity': qty, 'unit_price': unit_price,
                'discount_pct': discount_pct, 'tax_rate': tax_rate,
                'line_total': calc['taxable_amount'], 'branch_id': bid,
            })
            subtotal += Decimal(str(calc['gross']))
            discount += Decimal(str(calc['discount_amount']))
            tax      += Decimal(str(calc['tax']))
            total    += Decimal(str(calc['total']))

        subtotal = float(subtotal.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        discount = float(discount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        tax      = float(tax.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        total    = float(total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

        paid      = _money(data.get('amount_paid', total))
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
            cust = cur.execute("SELECT credit_mode,credit_limit,credit_balance FROM customers WHERE id=? AND company_id=?",
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
        # sales.sale_number carries a bare (not company-scoped) UNIQUE
        # constraint, but _next_ref()'s counter resets per company -- two
        # different companies' first sale would otherwise both generate
        # "SALE-000001" and collide in this shared multi-tenant database.
        # Same fix already applied to returns.return_number above (see that
        # comment) -- appending a company fragment keeps the sequential
        # part human-readable/searchable while guaranteeing global
        # uniqueness without altering the shared _next_ref helper or its
        # format for other doc types.
        sale_number = f"{_next_ref(conn, cid, 'sale')}-{str(cid)[:8]}"

        cur.execute("""
            INSERT INTO sales (company_id,sale_number,branch_id,customer_id,cashier,
                               subtotal,discount_amount,tax_amount,total,amount_paid,
                               change_amount,payment_method,status,idempotency_key,notes,created_at,due_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'completed',?,?,?,?)
        """, (cid, sale_number, bid, customer_id, data.get('cashier', _uid()),
              subtotal, discount, tax, total, paid, change,
              pm, idem, data.get('notes',''), now_local, due_date))
        sale_id = cur.lastrowid

        for line in resolved_lines:
            pid, qty = line['product_id'], line['quantity']
            cur.execute("""
                INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,discount_pct,tax_rate,line_total)
                VALUES (?,?,?,?,?,?,?)
            """, (sale_id, pid, qty, line['unit_price'], line['discount_pct'], line['tax_rate'], line['line_total']))
            cur.execute("""
                INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,reference,created_by)
                VALUES (?,?,?,'sale_out',?,?,?)
            """, (cid, pid, bid, -qty, sale_number, _uid()))
            cur.execute("""
                UPDATE inventory_balances SET quantity_on_hand = quantity_on_hand - ?
                WHERE company_id=? AND product_id=? AND branch_id=?
            """, (qty, cid, pid, bid))

        # Update customer total spent & loyalty points
        if customer_id:
            pts = int(total / 10)  # 1 point per $10
            cur.execute("""
                UPDATE customers SET total_spent=total_spent+?, loyalty_points=loyalty_points+?
                WHERE id=? AND company_id=?
            """, (total, pts, customer_id, cid))

        # Ledger: record the amount actually received now (feeds the daily cash
        # summary), and push any unpaid balance onto the customer's AR.
        if paid > 0.005:
            _record_payment(conn, cid, ('customer' if customer_id else None), customer_id, 'in', paid,
                            method=(pm if pm != 'credit' else 'cash'),
                            related_type='sale', related_id=sale_id, doc_type='receipt')
        if is_credit and balance_due > 0.005 and customer_id:
            _adjust_credit(conn, 'customers', customer_id, cid, balance_due)

        conn.commit()
        _emit('SaleCompleted', {'sale_id': sale_id, 'sale_number': sale_number, 'total': total,
                                 'payment_method': pm})

        response_data = {
            'id': sale_id, 'sale_number': sale_number,
            'idempotency_key': idem, 'currency': _settings(conn, cid)['base_currency'],
            'subtotal': subtotal, 'discount_amount': discount, 'tax_amount': tax,
            'change': round(change, 2), 'total': round(total, 2),
            'amount_paid': paid, 'balance_due': balance_due, 'warning': warning,
            'lines': resolved_lines, 'calculation_version': tax_engine.CALCULATION_VERSION,
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

@retail_bp.route('/sales/recent', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def recent_sales():
    cid   = _cid()
    limit = int(request.args.get('limit', 50))
    conn  = get_retail_conn()
    rows  = conn.execute("""
        SELECT s.*, COALESCE(c.name,'Walk-in') as customer_name,
               COUNT(si.id) as item_count
        FROM sales s
        LEFT JOIN customers c ON s.customer_id=c.id
        LEFT JOIN sale_items si ON s.id=si.sale_id
        WHERE s.company_id=? GROUP BY s.id ORDER BY s.created_at DESC LIMIT ?
    """, (cid, limit)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@retail_bp.route('/sales/<int:sale_id>', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def get_sale(sale_id):
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
    return jsonify({'status': 'success', 'data': {'sale': dict(sale), 'items': [dict(i) for i in items]}})

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

        sale = cur.execute("SELECT id, branch_id FROM sales WHERE id=? AND company_id=?", (sale_id, cid)).fetchone()
        if not sale:
            conn.close()
            return jsonify({'status': 'error', 'message': 'Original sale not found.'}), 404

        # BEGIN IMMEDIATE: two returns against the same sale+product racing each
        # other must not both read the same "remaining returnable" snapshot.
        conn.execute("BEGIN IMMEDIATE")
        bid = sale['branch_id'] or data.get('branch_id') or _default_branch(conn, cid)
        mode = _settings(conn, cid).get('tax_calculation_mode', tax_engine.DEFAULT_MODE)

        resolved_items = []
        refund_total = Decimal('0')
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
            remaining = float(sold['quantity']) - float(already_returned or 0)
            if qty > remaining + 0.0001:
                conn.rollback(); conn.close()
                return jsonify({'status': 'error', 'message':
                    f'Cannot return {qty} of product {pid}: only {remaining} remain returnable '
                    f'(sold {sold["quantity"]}, already returned {already_returned}).'}), 400

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
        # returns.return_number carries a bare (not company-scoped) UNIQUE
        # constraint, but _next_ref()'s counter resets per company -- two
        # different companies' first return would otherwise both generate
        # "RET-000001" and collide in this shared multi-tenant database.
        # Appending a company fragment keeps the sequential part
        # human-readable/searchable while guaranteeing global uniqueness
        # without altering the shared _next_ref helper or its format for
        # other doc types (out of scope for this correction).
        ret_num = f"{_next_ref(conn, cid, 'return')}-{str(cid)[:8]}"
        # Write LOCAL time, not the UTC CURRENT_TIMESTAMP default: the dashboard nets
        # returns out of today's revenue by local date(created_at), so a UTC timestamp
        # would file a late-evening return under the wrong day and leave the KPI stale.
        now_local = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cur.execute("""
            INSERT INTO returns (company_id,return_number,sale_id,branch_id,cashier,
                                 reason,refund_method,refund_amount,status,idempotency_key,created_at)
            VALUES (?,?,?,?,?,?,?,?,'completed',?,?)
        """, (cid, ret_num, sale_id, bid, _uid(),
              data.get('reason','Customer return'), data.get('refund_method','cash'), refund, idem, now_local))
        ret_id = cur.lastrowid
        for line in resolved_items:
            pid, qty = line['product_id'], line['quantity']
            cur.execute("""
                INSERT INTO return_items (return_id,product_id,quantity,unit_price,line_total)
                VALUES (?,?,?,?,?)
            """, (ret_id, pid, qty, line['unit_price'], line['line_total']))
            cur.execute("""
                INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand)
                VALUES (?,?,?,0)
            """, (cid, pid, bid))
            cur.execute("""
                UPDATE inventory_balances SET quantity_on_hand=quantity_on_hand+?
                WHERE company_id=? AND product_id=? AND branch_id=?
            """, (qty, cid, pid, bid))
            cur.execute("""
                INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,reference,created_by)
                VALUES (?,?,?,'return_in',?,?,?)
            """, (cid, pid, bid, qty, ret_num, _uid()))
        _audit(conn, 'RETURN_PROCESSED', 'return', ret_id, f'{ret_num} refund={refund}')
        conn.commit()
        return jsonify({'status': 'success', 'data': {
            'id': ret_id, 'return_number': ret_num, 'refund_amount': round(refund, 2),
            'idempotency_key': idem, 'items': resolved_items,
            'calculation_version': tax_engine.CALCULATION_VERSION,
        }})
    except Exception as e:
        conn.rollback(); conn.close()
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ── Reports ───────────────────────────────────────────────────────────────────

@retail_bp.route('/reports/sales-trend', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def report_sales_trend():
    cid  = _cid()
    days = int(request.args.get('days', 14))
    conn = get_retail_conn()
    rows = conn.execute("""
        SELECT date(created_at) as day,
               COALESCE(SUM(total),0) as revenue,
               COUNT(*) as transactions,
               COALESCE(AVG(total),0) as avg_ticket
        FROM sales WHERE company_id=?
          AND date(created_at) >= date('now', 'localtime', ?)
        GROUP BY day ORDER BY day
    """, (cid, f'-{days} days')).fetchall()
    conn.close()
    return jsonify({'success': True,
                    'labels': [r['day'] for r in rows],
                    'data':   [round(r['revenue'], 2) for r in rows],
                    'transactions': [r['transactions'] for r in rows],
                    'avg_ticket': [round(r['avg_ticket'], 2) for r in rows]})

@retail_bp.route('/reports/top-products', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def report_top_products():
    cid   = _cid()
    limit = int(request.args.get('limit', 10))
    conn  = get_retail_conn()
    rows  = conn.execute("""
        SELECT p.name, p.sku,
               SUM(si.quantity) as units_sold,
               SUM(si.line_total) as revenue,
               SUM(si.quantity * p.cost_price) as cost,
               SUM(si.line_total) - SUM(si.quantity * p.cost_price) as profit
        FROM sale_items si
        JOIN products p ON si.product_id=p.id
        JOIN sales s ON si.sale_id=s.id
        WHERE s.company_id=?
        GROUP BY p.id ORDER BY units_sold DESC LIMIT ?
    """, (cid, limit)).fetchall()
    conn.close()
    return jsonify({'success': True,
                    'labels': [r['name'] for r in rows],
                    'data':   [round(r['units_sold'], 0) for r in rows],
                    'revenue': [round(r['revenue'], 2) for r in rows],
                    'profit':  [round(r['profit'], 2) for r in rows]})

@retail_bp.route('/reports/payment-methods', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def report_payment_methods():
    cid  = _cid()
    days = int(request.args.get('days', 30))
    conn = get_retail_conn()
    rows = conn.execute("""
        SELECT payment_method, COUNT(*) as count, COALESCE(SUM(total),0) as revenue
        FROM sales WHERE company_id=? AND date(created_at) >= date('now', 'localtime', ?)
        GROUP BY payment_method ORDER BY revenue DESC
    """, (cid, f'-{days} days')).fetchall()
    conn.close()
    return jsonify({'success': True, 'data': [dict(r) for r in rows]})

def _compute_report_summary(conn, cid, days):
    """Extracted from report_summary() (feat/email-outbox-foundation) so
    the existing GET route and the new POST /reports/email route below
    share ONE implementation of this query set -- never two copies to keep
    in sync. Does not close `conn` -- same convention as every other
    helper in this file that takes a connection instead of opening its
    own (_settings, _record_payment, ...)."""
    period_start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    prev_start   = (datetime.now() - timedelta(days=days*2)).strftime('%Y-%m-%d')

    def q(sql, *p):
        return conn.execute(sql, p).fetchone()[0] or 0

    cur_rev   = q("SELECT COALESCE(SUM(total),0) FROM sales WHERE company_id=? AND date(created_at)>=?", cid, period_start)
    prev_rev  = q("SELECT COALESCE(SUM(total),0) FROM sales WHERE company_id=? AND date(created_at)>=? AND date(created_at)<?", cid, prev_start, period_start)
    cur_txns  = q("SELECT COUNT(*) FROM sales WHERE company_id=? AND date(created_at)>=?", cid, period_start)
    cur_cost  = q("""
        SELECT COALESCE(SUM(si.quantity * p.cost_price),0)
        FROM sale_items si JOIN products p ON si.product_id=p.id
        JOIN sales s ON si.sale_id=s.id WHERE s.company_id=? AND date(s.created_at)>=?
    """, cid, period_start)
    inv_value = q("""
        SELECT COALESCE(SUM(b.quantity_on_hand * p.cost_price),0)
        FROM inventory_balances b JOIN products p ON b.product_id=p.id
        WHERE b.company_id=? AND p.status='active'
    """, cid)

    gross_profit = cur_rev - cur_cost
    margin_pct   = round(gross_profit / cur_rev * 100, 1) if cur_rev > 0 else 0
    rev_change   = round((cur_rev - prev_rev) / prev_rev * 100, 1) if prev_rev > 0 else 0

    return {
        'period_days':    days,
        'revenue':        round(cur_rev, 2),
        'prev_revenue':   round(prev_rev, 2),
        'revenue_change': rev_change,
        'transactions':   cur_txns,
        'avg_ticket':     round(cur_rev / cur_txns, 2) if cur_txns > 0 else 0,
        'cogs':           round(cur_cost, 2),
        'gross_profit':   round(gross_profit, 2),
        'margin_pct':     margin_pct,
        'inventory_value': round(inv_value, 2),
    }

@retail_bp.route('/reports/summary', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def report_summary():
    cid  = _cid()
    days = int(request.args.get('days', 30))
    conn = get_retail_conn()
    data = _compute_report_summary(conn, cid, days)
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
def create_branch():
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Branch name required'}), 400
    cid = _cid()
    conn = get_retail_conn()
    cur  = conn.cursor()
    cur.execute("INSERT INTO branches (company_id,name,address,phone) VALUES (?,?,?,?)",
                (cid, data['name'], data.get('address',''), data.get('phone','')))
    nid = cur.lastrowid
    conn.commit(); conn.close()
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

_REF_PREFIX = {'sale': 'SALE', 'receipt': 'REC', 'po': 'PO', 'supplier_payment': 'PAY', 'return': 'RET'}

def _next_ref(conn, cid, doc_type):
    """Atomic, zero-padded, human-readable + searchable reference (e.g. REC-000053)."""
    conn.execute("INSERT OR IGNORE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,0)", (cid, doc_type))
    conn.execute("UPDATE doc_sequences SET last_no=last_no+1 WHERE company_id=? AND doc_type=?", (cid, doc_type))
    n = conn.execute("SELECT last_no FROM doc_sequences WHERE company_id=? AND doc_type=?", (cid, doc_type)).fetchone()[0]
    return f"{_REF_PREFIX.get(doc_type, doc_type.upper())}-{int(n):06d}"

_DEFAULT_SETTINGS = {
    'base_currency': 'USD',
    'default_credit_mode': 'none',      # none | limited | unlimited
    'default_credit_limit': '0',
    'enforce_credit_limit': 'warn',     # warn | block
    # 'after_discount' (default) | 'before_discount' -- see core/retail/pricing.py,
    # the single source of truth for what these two modes mean and compute.
    'tax_calculation_mode': tax_engine.DEFAULT_MODE,
}
_DEFAULT_METHODS = [('Cash', 'cash'), ('Card', 'card'), ('Bank Transfer', 'bank'),
                    ('Mobile Wallet', 'wallet'), ('Check', 'check')]

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
    conn.execute("""INSERT INTO payments
        (company_id,reference,party_type,party_id,direction,amount,currency,fx_rate,method,
         related_type,related_id,sale_id,notes,status,created_by,device,created_at)
        VALUES (?,?,?,?,?,?,?,1,?,?,?,?,?, 'active', ?, ?, ?)""",
        (cid, ref, party_type, party_id, direction, amt, cur, method,
         related_type, related_id, (related_id if related_type == 'sale' else None),
         notes, _uid(), device, _now()))
    return ref

def _adjust_credit(conn, table, pid, cid, delta):
    """Decimal-safe balance update (avoids SQL float accumulation). Returns new balance."""
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
def customers_receivables():
    cid = _cid(); conn = get_retail_conn(); _ensure_credit_schema(conn)
    rows = conn.execute("SELECT id,name,phone,credit_mode,credit_limit,credit_balance FROM customers "
                        "WHERE company_id=? AND COALESCE(credit_balance,0) > 0.005 ORDER BY credit_balance DESC", (cid,)).fetchall()
    total = conn.execute("SELECT COALESCE(SUM(credit_balance),0) FROM customers WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()
    return jsonify({'status': 'success', 'total_receivable': _money(total), 'data': [dict(r) for r in rows]})

@retail_bp.route('/customers/<string:cust_id>/statement', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
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
        return jsonify({'status': 'success', 'data': {'reference': ref, 'new_balance': newbal}})
    except Exception as e:
        conn.rollback(); conn.close(); return jsonify({'status': 'error', 'message': str(e)}), 500

# ── Supplier credit (Accounts Payable) ────────────────────────────────────────
@retail_bp.route('/suppliers/payables', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
def suppliers_payables():
    cid = _cid(); conn = get_retail_conn(); _ensure_credit_schema(conn)
    rows = conn.execute("SELECT id,name,phone,payment_terms,credit_balance FROM suppliers "
                        "WHERE company_id=? AND COALESCE(credit_balance,0) > 0.005 ORDER BY credit_balance DESC", (cid,)).fetchall()
    total = conn.execute("SELECT COALESCE(SUM(credit_balance),0) FROM suppliers WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()
    return jsonify({'status': 'success', 'total_payable': _money(total), 'data': [dict(r) for r in rows]})

@retail_bp.route('/suppliers/<string:sid>/statement', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
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
        return jsonify({'status': 'success', 'data': {'reference': ref, 'new_balance': newbal}})
    except Exception as e:
        conn.rollback(); conn.close(); return jsonify({'status': 'error', 'message': str(e)}), 500

@retail_bp.route('/purchase-orders/<int:po_id>/pay', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.purchase.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
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
        return jsonify({'status': 'success', 'data': {'reference': ref, 'payment_status': status, 'amount_paid': new_paid}})
    except Exception as e:
        conn.rollback(); conn.close(); return jsonify({'status': 'error', 'message': str(e)}), 500

# ── Cash summary + aging (structured for future dashboard KPIs) ────────────────
@retail_bp.route('/reports/daily-cash', methods=['GET'])
@mt_login_required
@mt_require_subsystem('retail')
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

# ── Void (immutability: never edit/delete, only reverse) ──────────────────────
@retail_bp.route('/payments/<int:pid>/void', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.settings.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def void_payment(pid):
    cid = _cid(); data = request.json or {}
    conn = get_retail_conn(); _ensure_credit_schema(conn)
    p = conn.execute("SELECT * FROM payments WHERE id=? AND company_id=?", (pid, cid)).fetchone()
    if not p:
        conn.close(); return jsonify({'status': 'error', 'message': 'Payment not found'}), 404
    if (p['status'] or 'active') != 'active':
        conn.close(); return jsonify({'status': 'error', 'message': 'Payment is not active'}), 409
    try:
        conn.execute("UPDATE payments SET status='voided', voided_by=?, voided_at=?, notes=COALESCE(notes,'')||? WHERE id=?",
                     (_uid(), _now(), f" [VOID: {data.get('reason','')}]", pid))
        # Reverse the balance effect of the voided receipt/payment.
        if p['party_id'] and p['party_type'] in ('customer', 'supplier'):
            table = 'customers' if p['party_type'] == 'customer' else 'suppliers'
            # 'in' reduced AR / 'out' reduced AP, so voiding adds it back.
            _adjust_credit(conn, table, p['party_id'], cid, _money(p['amount']))
        _audit(conn, 'PAYMENT_VOIDED', 'payment', pid, data.get('reason', ''))
        conn.commit(); conn.close()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback(); conn.close(); return jsonify({'status': 'error', 'message': str(e)}), 500

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


def _require_confirmation(expected_prefix, cid):
    data = request.get_json(silent=True) or {}
    expected = f'{expected_prefix}-{cid}'
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
