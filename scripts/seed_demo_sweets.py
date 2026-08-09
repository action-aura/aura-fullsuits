#!/usr/bin/env python3
"""
Aura Retail -- Thursday demo seed data (sweets shop).

Raw sqlite3, no ORM, matching this repo's actual data-access pattern.
Idempotent: safe to re-run, checks for an existing marker product before
inserting anything. Writes matching sync_outbox events for every row it
creates, so the data propagates to any other device paired on the same
license -- seeding only the local DB would leave the second phone's screen
empty during the sync part of the demo.

IMPORTANT -- run onboarding FIRST: company_id is not the literal integer 1.
It's a registry-issued TEXT id assigned when the first admin account is
created (checked empirically: a real onboarded account gets something like
"97d9690122575cfa3528f3dabfb908d7", never "1" -- retail/backend's own
`_cid()` returns whatever's in the login session verbatim, and every retail
table's `company_id INTEGER DEFAULT 1` column declaration is stale/advisory
under SQLite's dynamic typing, not actually enforced). Seeding against a
hardcoded "1" silently produces products no logged-in account can ever see.
This script resolves the real company_id from the registry itself instead.

Usage (run AFTER creating the real admin account through onboarding):
    AURA_APP_DATA=<path used by the app you're about to demo with> \
    .venv\\Scripts\\python.exe scripts\\seed_demo_sweets.py
"""
import os
import sys
import json
import sqlite3
import uuid
from datetime import datetime, timezone

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'products', 'retail', 'backend')
sys.path.insert(0, BACKEND_DIR)

from database.schema import _get_path  # noqa: E402  -- same path resolution app.py itself uses

MARKER_SKU = 'SWEET-SEED-MARKER'


def _resolve_company_id():
    """The registry (not retail.db) is the source of truth for company_id --
    it's issued at onboarding time, not the literal integer 1. Requires
    exactly one company to exist (true for a standalone demo install that's
    just been through first-run setup); refuses to guess if that's not
    true, since silently picking the wrong one reproduces the exact bug
    this function exists to avoid."""
    registry_path = os.path.join(os.path.dirname(_get_path('retail')), '..', 'registry.db')
    registry_path = os.path.normpath(registry_path)
    if not os.path.exists(registry_path):
        sys.exit(f'No registry.db found at {registry_path} -- create the admin account '
                  f'through onboarding first (POST /api/onboarding/create-admin), then run this script.')
    rconn = sqlite3.connect(registry_path)
    try:
        rows = rconn.execute("SELECT DISTINCT company_id FROM users").fetchall()
    finally:
        rconn.close()
    if not rows:
        sys.exit('No users found in registry.db -- create the admin account through onboarding first.')
    if len(rows) > 1:
        sys.exit(f'Multiple company_ids found in registry.db ({[r[0] for r in rows]}) -- '
                  f'this script only supports a single-company demo install. Pick one manually and '
                  f'hardcode it if you really need this.')
    return rows[0][0]


def _default_branch(conn, cid):
    row = conn.execute("SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (cid,)).fetchone()
    if row:
        return row[0]
    cur = conn.cursor()
    cur.execute("INSERT INTO branches (company_id,name,address,phone) VALUES (?,?,?,?)",
                (cid, 'Main Branch', '', ''))
    return cur.lastrowid


def _queue_sync_event(cur, entity_type, entity_id, event_type, payload):
    cur.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), entity_type, str(entity_id), event_type,
         json.dumps(payload), datetime.now(timezone.utc).isoformat()),
    )


def main():
    company_id = _resolve_company_id()
    db_path = _get_path('retail')
    print(f'Seeding: {db_path}')
    print(f'Resolved company_id: {company_id}')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    already = cur.execute("SELECT id FROM products WHERE sku=?", (MARKER_SKU,)).fetchone()
    if already:
        print('Demo data already seeded (marker product found) -- nothing to do. '
              'Delete that product first if you want to reseed.')
        conn.close()
        return

    try:
        bid = _default_branch(conn, company_id)

        # ---- Categories ----
        categories = {}
        for name in ('Ice Cream', 'Crepes', 'Cookies & Baked Goods', 'Beverages'):
            cid_ = str(uuid.uuid4())
            cur.execute("INSERT INTO categories (id,company_id,name) VALUES (?,?,?)", (cid_, company_id, name))
            _queue_sync_event(cur, 'category', cid_, 'create', {'id': cid_, 'name': name})
            categories[name] = cid_

        # ---- Suppliers ----
        suppliers = {}
        supplier_rows = [
            ('Sweet Supply Co.', '+962-6-555-0101', 'orders@sweetsupply.jo'),
            ('Cocoa & Cream Wholesale', '+962-6-555-0142', 'sales@cocoacream.jo'),
        ]
        for name, phone, email in supplier_rows:
            sid = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO suppliers (id,company_id,name,phone,email,status) VALUES (?,?,?,?,?,'active')",
                (sid, company_id, name, phone, email))
            _queue_sync_event(cur, 'supplier', sid, 'create',
                               {'id': sid, 'name': name, 'phone': phone, 'email': email, 'status': 'active'})
            suppliers[name] = sid

        # ---- Products (sweets shop -- no waffles) ----
        products = [
            # name, category, supplier, sku, barcode, cost, sell, unit, stock
            ('Vanilla Bean Ice Cream (Scoop)',   'Ice Cream', 'Sweet Supply Co.',        'ICE-VAN-001', '6291000000011', 0.60, 2.00, 'scoop', 60),
            ('Pistachio Ice Cream (Scoop)',      'Ice Cream', 'Sweet Supply Co.',        'ICE-PIS-002', '6291000000028', 0.90, 2.50, 'scoop', 45),
            ('Chocolate Fudge Ice Cream (Scoop)','Ice Cream', 'Cocoa & Cream Wholesale', 'ICE-CHO-003', '6291000000035', 0.75, 2.25, 'scoop', 50),
            ('Nutella Crepe',                    'Crepes',    'Cocoa & Cream Wholesale', 'CRP-NUT-001', '6291000000042', 1.50, 4.50, 'pcs',   30),
            ('Banana & Honey Crepe',              'Crepes',    'Sweet Supply Co.',        'CRP-BAN-002', '6291000000059', 1.20, 3.75, 'pcs',   25),
            ('Strawberry Cream Crepe',            'Crepes',    'Cocoa & Cream Wholesale', 'CRP-STR-003', '6291000000066', 1.35, 4.00, 'pcs',   25),
            ('Chocolate Chip Cookie',             'Cookies & Baked Goods', 'Sweet Supply Co.', 'CKI-CHC-001', '6291000000073', 0.30, 1.25, 'pcs', 100),
            ('Double Chocolate Brownie',          'Cookies & Baked Goods', 'Cocoa & Cream Wholesale', 'CKI-BRW-002', '6291000000080', 0.55, 2.00, 'pcs', 40),
            ('Oatmeal Raisin Cookie',             'Cookies & Baked Goods', 'Sweet Supply Co.', 'CKI-OAT-003', '6291000000097', 0.28, 1.15, 'pcs', 80),
            ('Iced Chocolate Milkshake',          'Beverages', 'Cocoa & Cream Wholesale', 'BEV-MLK-001', '6291000000103', 0.80, 3.00, 'cup', 40),
            ('Fresh Mint Lemonade',               'Beverages', 'Sweet Supply Co.',        'BEV-LEM-002', '6291000000110', 0.40, 2.00, 'cup', 50),
            (MARKER_SKU,                          'Beverages', 'Sweet Supply Co.',        MARKER_SKU,    '', 0, 0, 'pcs', 0),  # seed marker, not for sale
        ]

        for name, cat_name, sup_name, sku, barcode, cost, sell, unit, stock in products:
            pid = str(uuid.uuid4())
            category_id = categories[cat_name]
            supplier_id = suppliers[sup_name]
            cur.execute("""
                INSERT INTO products (id,company_id,sku,barcode,name,category_id,supplier_id,
                                      cost_price,sell_price,tax_rate,unit,reorder_level,status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'active')
            """, (pid, company_id, sku, barcode, name, category_id, supplier_id,
                  cost, sell, 0, unit, 10))
            cur.execute("""
                INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand)
                VALUES (?,?,?,?)
            """, (company_id, pid, bid, stock))
            if stock > 0:
                cur.execute("""
                    INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,reference,created_by)
                    VALUES (?,?,?,'opening_stock',?,?,?)
                """, (company_id, pid, bid, stock, 'DEMO_SEED', 'seed_script'))
            _queue_sync_event(cur, 'product', pid, 'create', {
                'id': pid, 'sku': sku, 'barcode': barcode, 'name': name,
                'category_id': category_id, 'supplier_id': supplier_id,
                'cost_price': cost, 'sell_price': sell, 'tax_rate': 0,
                'unit': unit, 'reorder_level': 10,
            })

        # ---- Customers ----
        customer_rows = [
            ('Layla Haddad',  '+962-79-555-1201', 'layla.haddad@example.jo'),
            ('Omar Nasser',   '+962-77-555-1202', 'omar.nasser@example.jo'),
            ('Rania Odeh',    '+962-78-555-1203', 'rania.odeh@example.jo'),
        ]
        for name, phone, email in customer_rows:
            cust_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO customers (id,company_id,name,phone,email,status,loyalty_points,total_spent)
                VALUES (?,?,?,?,?,'active',0,0)
            """, (cust_id, company_id, name, phone, email))
            _queue_sync_event(cur, 'customer', cust_id, 'create',
                               {'id': cust_id, 'name': name, 'phone': phone, 'email': email, 'status': 'active'})

        conn.commit()
        print(f'Seeded: {len(categories)} categories, {len(suppliers)} suppliers, '
              f'{len(products)} products (incl. marker), {len(customer_rows)} customers.')
        print('All rows queued to sync_outbox -- run a sync pass (or just wait for the '
              'background sync loop) before demoing the second device.')
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
