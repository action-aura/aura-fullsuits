"""
Aura Retail -- W2b + W3a, the WRITE side of promotions and extras (modifiers)
sharing between computers (schema v50). Plan: W2B3A-PLAN.md.

WHAT THIS FILE PROVES (route level, through the real Flask app and the real
outbox):

  1. Every promotion / extras-configuration write queues ONE event with the
     exact wire key set the receiving side reads, and bumps row_version AND
     updated_at_utc (D3 -- update_promotion, delete_promotion and
     detach_product_modifier_group used to bump one or neither).
  2. The boot backfill announces every flagged row once, in parent-first order,
     clears the flag, and a second run emits nothing. A row that fails is
     skipped and the run still ends (the no-progress guard).
  3. A sale line carries the extras and the promotion it was rung with as
     snapshots (`modifiers`, `promotion`), so another computer's receipt shows
     the same lines. Money is unchanged: prices are already baked into the line.
  4. Every reason the receiving side can park these under has a plain sentence
     in English and Arabic.

The plan's key sets, exactly (the apply side and the coverage ratchet read the
same ones): see PROMO_KEYS / GROUP_KEYS / OPTION_KEYS / LINK_KEYS below.

Run (one file per process, never two in one pytest invocation):
    .venv\\Scripts\\python.exe -m pytest -q -p no:cacheprovider -p no:warnings \
        products/retail/tests/retail_config_sync_routes_test.py
"""
import json
import os
import shutil
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_config_sync_routes_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
                  AURA_SITE_RELAY_ENABLED="0")
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SYNC_RELAY_URL", None)
os.environ.pop("AURA_OWNER_LICENSING_URL", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

import api.retail_api as retail_api  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'

#: The exact wire key sets (plan D1/D4). `promotion` travels under `uid` and a
#: branch UID (NULL = every branch); the three modifier tables travel under
#: their TEXT `id`. `_changed_fields` rides on updates only.
PROMO_KEYS = {'uid', 'name', 'discount_pct', 'product_id', 'category_id', 'branch_uid',
              'starts_at', 'ends_at', 'status', 'row_version', 'updated_at_utc'}
GROUP_KEYS = {'id', 'name', 'min_select', 'max_select', 'status', 'deleted_at_utc',
              'row_version', 'updated_at_utc'}
OPTION_KEYS = {'id', 'group_id', 'name', 'price_delta', 'cost_delta', 'is_default', 'sort_order',
               'status', 'deleted_at_utc', 'row_version', 'updated_at_utc'}
LINK_KEYS = {'id', 'product_id', 'group_id', 'sort_order', 'deleted_at_utc',
             'row_version', 'updated_at_utc'}
SNAP_MOD_KEYS = {'uid', 'modifier_option_id', 'group_name', 'name', 'price_delta', 'cost_delta'}
SNAP_PROMO_KEYS = {'uid', 'promotion_uid', 'name', 'discount_pct', 'discount_amount'}
CONFIG_TYPES = ('promotion', 'modifier_group', 'modifier_option', 'product_modifier_group')
LONG_AGO = '2000-01-01T00:00:00+00:00'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_background_push(monkeypatch):
    """These tests READ the outbox; the real route's `_sync_nudge()` starts a
    background push whose ack deletes outbox rows."""
    monkeypatch.setattr(retail_api, '_sync_nudge', lambda: None)


def _login_admin(company_id):
    email = f"w2b-{uuid.uuid4().hex[:10]}@test.local"
    password = "W2bRoutesPW1"  # pragma: allowlist secret
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), 'admin', 'active'))
    conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                 (str(uuid.uuid4()), user_id, 'retail', 'full'))
    user_accounts.seed_capabilities_for_user(conn, user_id, 'admin')
    conn.commit(); conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c


@pytest.fixture
def shop():
    cid = str(uuid.uuid4())
    client = _login_admin(cid)
    client.get(f'{API}/settings/tax')   # warms the lazy schema
    return client, cid


def _q(sql, *args):
    conn = get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _x(sql, *args):
    conn = get_retail_conn()
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def _mark():
    """A position in the outbox: events AFTER it are the ones a step produced."""
    return _q("SELECT COALESCE(MAX(rowid),0) AS m FROM sync_outbox")[0]['m']


def _events_since(mark, *types):
    rows = _q("SELECT entity_type, entity_id, event_type, payload FROM sync_outbox "
              "WHERE rowid>? ORDER BY rowid", mark)
    out = [dict(r, payload=json.loads(r['payload'])) for r in rows]
    return [e for e in out if not types or e['entity_type'] in types]


def _product(client, price=10.0):
    tag = uuid.uuid4().hex[:8]
    r = client.post(f'{API}/products', json={
        'name': f'W2b item {tag}', 'sku': f'W2B-{tag}', 'sell_price': price,
        'cost_price': 1.0, 'tax_rate': 0, 'initial_stock': 1000})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _promo(client, product_id, pct=10, **extra):
    r = client.post(f'{API}/promotions', json=dict(
        {'name': f'Promo {uuid.uuid4().hex[:6]}', 'discount_pct': pct, 'product_id': product_id}, **extra))
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _group(client, name='Extras'):
    r = client.post(f'{API}/modifier-groups', json={'name': name})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _option(client, group_id, name='Cheese', price_delta=0.5):
    r = client.post(f'{API}/modifier-groups/{group_id}/options',
                    json={'name': name, 'price_delta': price_delta})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _attach(client, pid, group_id):
    r = client.post(f'{API}/products/{pid}/modifier-groups', json={'group_id': group_id})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _only(events, entity_type, event_type):
    hit = [e for e in events if e['entity_type'] == entity_type and e['event_type'] == event_type]
    assert len(hit) == 1, (entity_type, event_type, [(e['entity_type'], e['event_type']) for e in events])
    return hit[0]


# ── 1. promotions ────────────────────────────────────────────────────────────

def test_create_promotion_queues_one_create_event_with_the_exact_key_set(shop):
    client, cid = shop
    pid = _product(client)
    m = _mark()
    promo_id = _promo(client, pid, pct=12.5, starts_at='2026-01-01 00:00:00', ends_at='2026-12-31 23:59:59')
    row = _q("SELECT * FROM promotions WHERE id=?", promo_id)[0]
    assert row['uid'] and row['row_version'] == 1 and row['updated_at_utc'] and not row['sync_backfill_pending']
    ev = _only(_events_since(m, *CONFIG_TYPES), 'promotion', 'create')
    assert set(ev['payload']) == PROMO_KEYS, set(ev['payload']) ^ PROMO_KEYS
    assert ev['entity_id'] == row['uid'] == ev['payload']['uid']
    assert ev['payload']['row_version'] == 1 and ev['payload']['updated_at_utc'] == row['updated_at_utc']
    assert ev['payload']['discount_pct'] == 12.5 and ev['payload']['product_id'] == pid
    assert ev['payload']['category_id'] is None and ev['payload']['branch_uid'] is None
    # Local wall-clock window travels verbatim (D4: never converted).
    assert ev['payload']['starts_at'] == '2026-01-01 00:00:00'
    assert ev['payload']['ends_at'] == '2026-12-31 23:59:59'
    assert len(_events_since(m, *CONFIG_TYPES)) == 1


def test_branch_scoped_promotion_carries_the_branch_uid_not_the_local_id(shop):
    client, cid = shop
    pid = _product(client)
    branch = _q("SELECT id, uid FROM branches WHERE company_id=? ORDER BY id", cid)[0]
    assert branch['uid']
    m = _mark()
    _promo(client, pid, branch_id=branch['id'])
    ev = _only(_events_since(m, *CONFIG_TYPES), 'promotion', 'create')
    assert ev['payload']['branch_uid'] == branch['uid']
    assert 'branch_id' not in ev['payload']


def test_update_promotion_bumps_version_and_time_and_names_only_what_changed(shop):
    client, cid = shop
    pid = _product(client)
    promo_id = _promo(client, pid, pct=10)
    _x("UPDATE promotions SET updated_at_utc=? WHERE id=?", LONG_AGO, promo_id)
    m = _mark()
    r = client.patch(f'{API}/promotions/{promo_id}', json={'discount_pct': 25})
    assert r.status_code == 200, r.get_json()
    row = _q("SELECT * FROM promotions WHERE id=?", promo_id)[0]
    assert row['row_version'] == 2, "update_promotion must bump row_version"
    assert row['updated_at_utc'] > LONG_AGO, "update_promotion must stamp updated_at_utc"
    ev = _only(_events_since(m, *CONFIG_TYPES), 'promotion', 'update')
    assert set(ev['payload']) == PROMO_KEYS | {'_changed_fields'}
    assert ev['payload']['_changed_fields'] == ['discount_pct']
    assert ev['payload']['row_version'] == 2 and ev['payload']['updated_at_utc'] == row['updated_at_utc']
    assert ev['payload']['discount_pct'] == 25 and ev['entity_id'] == row['uid']


def test_changing_a_promotions_branch_is_announced_as_branch_uid(shop):
    client, cid = shop
    pid = _product(client)
    branch = _q("SELECT id, uid FROM branches WHERE company_id=? ORDER BY id", cid)[0]
    promo_id = _promo(client, pid)
    m = _mark()
    r = client.patch(f'{API}/promotions/{promo_id}', json={'branch_id': branch['id'], 'name': 'Renamed'})
    assert r.status_code == 200, r.get_json()
    ev = _only(_events_since(m, *CONFIG_TYPES), 'promotion', 'update')
    assert ev['payload']['_changed_fields'] == ['branch_uid', 'name']
    assert ev['payload']['branch_uid'] == branch['uid']


def test_deactivating_a_promotion_bumps_and_announces_the_status_change(shop):
    client, cid = shop
    pid = _product(client)
    promo_id = _promo(client, pid)
    _x("UPDATE promotions SET updated_at_utc=? WHERE id=?", LONG_AGO, promo_id)
    m = _mark()
    assert client.delete(f'{API}/promotions/{promo_id}').status_code == 200
    row = _q("SELECT * FROM promotions WHERE id=?", promo_id)[0]
    assert row['status'] == 'inactive' and row['row_version'] == 2 and row['updated_at_utc'] > LONG_AGO
    ev = _only(_events_since(m, *CONFIG_TYPES), 'promotion', 'update')
    assert ev['payload']['_changed_fields'] == ['status'] and ev['payload']['status'] == 'inactive'
    assert ev['payload']['row_version'] == 2
    # A refused delete (already inactive) emits nothing.
    m2 = _mark()
    assert client.delete(f'{API}/promotions/{promo_id}').status_code == 404
    assert _events_since(m2, *CONFIG_TYPES) == []


# ── 2. extras (modifier groups / options / attachments) ──────────────────────

def test_group_write_cycle_queues_exact_key_sets_and_bumps(shop):
    client, cid = shop
    m = _mark()
    gid = _group(client, 'Size')
    ev = _only(_events_since(m, *CONFIG_TYPES), 'modifier_group', 'create')
    assert set(ev['payload']) == GROUP_KEYS and ev['entity_id'] == gid == ev['payload']['id']
    assert ev['payload']['row_version'] == 1 and ev['payload']['deleted_at_utc'] is None

    _x("UPDATE modifier_groups SET updated_at_utc=? WHERE id=?", LONG_AGO, gid)
    m = _mark()
    assert client.patch(f'{API}/modifier-groups/{gid}', json={'max_select': 3}).status_code == 200
    ev = _only(_events_since(m, *CONFIG_TYPES), 'modifier_group', 'update')
    assert set(ev['payload']) == GROUP_KEYS | {'_changed_fields'}
    assert ev['payload']['_changed_fields'] == ['max_select']
    assert ev['payload']['row_version'] == 2 and ev['payload']['max_select'] == 3
    assert ev['payload']['updated_at_utc'] > LONG_AGO

    m = _mark()
    assert client.delete(f'{API}/modifier-groups/{gid}').status_code == 200
    ev = _only(_events_since(m, *CONFIG_TYPES), 'modifier_group', 'update')
    assert ev['payload']['_changed_fields'] == ['status'] and ev['payload']['status'] == 'inactive'
    assert ev['payload']['row_version'] == 3


def test_option_write_cycle_queues_exact_key_sets_and_bumps(shop):
    client, cid = shop
    gid = _group(client)
    m = _mark()
    oid = _option(client, gid, 'Extra cheese', 0.5)
    ev = _only(_events_since(m, *CONFIG_TYPES), 'modifier_option', 'create')
    assert set(ev['payload']) == OPTION_KEYS and ev['entity_id'] == oid == ev['payload']['id']
    assert ev['payload']['group_id'] == gid and ev['payload']['price_delta'] == 0.5

    _x("UPDATE modifier_options SET updated_at_utc=? WHERE id=?", LONG_AGO, oid)
    m = _mark()
    assert client.patch(f'{API}/modifier-options/{oid}',
                        json={'price_delta': 0.75, 'is_default': True}).status_code == 200
    ev = _only(_events_since(m, *CONFIG_TYPES), 'modifier_option', 'update')
    assert set(ev['payload']) == OPTION_KEYS | {'_changed_fields'}
    assert ev['payload']['_changed_fields'] == ['is_default', 'price_delta']
    assert ev['payload']['row_version'] == 2 and ev['payload']['updated_at_utc'] > LONG_AGO
    assert ev['payload']['price_delta'] == 0.75 and ev['payload']['is_default'] == 1

    m = _mark()
    assert client.delete(f'{API}/modifier-options/{oid}').status_code == 200
    ev = _only(_events_since(m, *CONFIG_TYPES), 'modifier_option', 'update')
    assert ev['payload']['_changed_fields'] == ['status'] and ev['payload']['row_version'] == 3


def test_attach_detach_reattach_each_queue_one_event_and_stamp_time(shop):
    client, cid = shop
    pid = _product(client)
    gid = _group(client)
    m = _mark()
    link = _attach(client, pid, gid)
    ev = _only(_events_since(m, *CONFIG_TYPES), 'product_modifier_group', 'create')
    assert set(ev['payload']) == LINK_KEYS and ev['entity_id'] == link == ev['payload']['id']
    assert ev['payload']['product_id'] == pid and ev['payload']['group_id'] == gid

    # Attaching what is already attached is a no-op: nothing to announce.
    m = _mark()
    _attach(client, pid, gid)
    assert _events_since(m, *CONFIG_TYPES) == []

    _x("UPDATE product_modifier_groups SET updated_at_utc=? WHERE id=?", LONG_AGO, link)
    m = _mark()
    assert client.delete(f'{API}/products/{pid}/modifier-groups/{gid}').status_code == 200
    row = _q("SELECT * FROM product_modifier_groups WHERE id=?", link)[0]
    assert row['deleted_at_utc'] and row['row_version'] == 2
    assert row['updated_at_utc'] > LONG_AGO, "detach must stamp updated_at_utc (D3)"
    ev = _only(_events_since(m, *CONFIG_TYPES), 'product_modifier_group', 'update')
    assert set(ev['payload']) == LINK_KEYS | {'_changed_fields'}
    assert ev['payload']['_changed_fields'] == ['deleted_at_utc']
    assert ev['payload']['deleted_at_utc'] == row['deleted_at_utc'] and ev['payload']['row_version'] == 2

    m = _mark()
    assert _attach(client, pid, gid) == link          # the SAME row is reused
    ev = _only(_events_since(m, *CONFIG_TYPES), 'product_modifier_group', 'update')
    assert ev['payload']['_changed_fields'] == ['deleted_at_utc', 'sort_order']
    assert ev['payload']['deleted_at_utc'] is None and ev['payload']['row_version'] == 3


# ── 3. boot backfill ─────────────────────────────────────────────────────────

def _flag_everything(cid):
    for t in ('promotions', 'modifier_groups', 'modifier_options', 'product_modifier_groups'):
        _x(f"UPDATE {t} SET sync_backfill_pending=1 WHERE company_id=?", cid)


def test_backfill_announces_each_flagged_row_once_parents_first_then_never_again(shop):
    client, cid = shop
    pid = _product(client)
    gid = _group(client)
    oid = _option(client, gid)
    link = _attach(client, pid, gid)
    promo = _promo(client, pid)
    puid = _q("SELECT uid FROM promotions WHERE id=?", promo)[0]['uid']
    _flag_everything(cid)
    m = _mark()
    conn = get_retail_conn()
    try:
        assert retail_api.backfill_legacy_config(conn) == 4
    finally:
        conn.close()
    evs = _events_since(m, *CONFIG_TYPES)
    assert [(e['entity_type'], e['event_type']) for e in evs] == [
        ('modifier_group', 'create'), ('modifier_option', 'create'),
        ('product_modifier_group', 'create'), ('promotion', 'create')]
    assert [e['entity_id'] for e in evs] == [gid, oid, link, puid]
    assert set(evs[0]['payload']) == GROUP_KEYS and set(evs[1]['payload']) == OPTION_KEYS
    assert set(evs[2]['payload']) == LINK_KEYS and set(evs[3]['payload']) == PROMO_KEYS
    for t in ('promotions', 'modifier_groups', 'modifier_options', 'product_modifier_groups'):
        assert _q(f"SELECT COUNT(*) AS n FROM {t} WHERE company_id=? AND sync_backfill_pending=1", cid)[0]['n'] == 0
    # A second boot emits nothing.
    m2 = _mark()
    conn = get_retail_conn()
    try:
        assert retail_api.backfill_legacy_config(conn) == 0
    finally:
        conn.close()
    assert _events_since(m2, *CONFIG_TYPES) == []


def test_backfill_one_failing_row_is_skipped_and_the_run_still_ends(shop, monkeypatch):
    client, cid = shop
    gid = _group(client)
    pid = _product(client)
    promo = _promo(client, pid)
    _flag_everything(cid)

    def boom(*a, **k):
        raise RuntimeError('simulated payload failure')

    monkeypatch.setattr(retail_api, '_queue_modifier_group', boom)
    m = _mark()
    conn = get_retail_conn()
    try:
        done = retail_api.backfill_legacy_config(conn)      # must RETURN, not spin
    finally:
        conn.close()
    assert done == 1                                         # the promotion still went out
    assert [e['entity_type'] for e in _events_since(m, *CONFIG_TYPES)] == ['promotion']
    # The failed group keeps its flag so the next boot retries it; nothing was half-written.
    assert _q("SELECT sync_backfill_pending AS f FROM modifier_groups WHERE id=?", gid)[0]['f'] == 1
    assert _q("SELECT sync_backfill_pending AS f FROM promotions WHERE id=?", promo)[0]['f'] == 0


# ── 4. a sale line carries its extras and its promotion ──────────────────────

def _sale(client, items):
    return client.post(f'{API}/sales', json={'items': items, 'amount_paid': 1_000_000,
                                             'payment_method': 'cash',
                                             'idempotency_key': str(uuid.uuid4())})


def test_sale_item_event_carries_extras_and_promotion_snapshots(shop):
    client, cid = shop
    pid = _product(client, price=10.0)
    gid = _group(client)
    cheese = _option(client, gid, 'Extra cheese', 0.5)
    _attach(client, pid, gid)
    promo = _promo(client, pid, pct=20)
    puid = _q("SELECT uid FROM promotions WHERE id=?", promo)[0]['uid']
    m = _mark()
    r = _sale(client, [{'product_id': pid, 'quantity': 2, 'modifier_option_ids': [cheese]}])
    assert r.status_code == 200, r.get_json()
    ev = _only(_events_since(m, 'sale_item'), 'sale_item', 'create')
    p = ev['payload']
    assert len(p['modifiers']) == 1 and set(p['modifiers'][0]) == SNAP_MOD_KEYS
    mod = p['modifiers'][0]
    assert (mod['modifier_option_id'], mod['group_name'], mod['name'], mod['price_delta']) == (
        cheese, 'Extras', 'Extra cheese', 0.5)
    assert set(p['promotion']) == SNAP_PROMO_KEYS
    assert p['promotion']['promotion_uid'] == puid and p['promotion']['discount_pct'] == 20
    # The wire carries what the LOCAL snapshot rows hold, under the same uids.
    local_mod = _q("SELECT uid FROM sale_item_modifiers ORDER BY id DESC LIMIT 1")[0]
    local_promo = _q("SELECT uid, discount_amount_snapshot AS d FROM sale_item_promotions ORDER BY id DESC LIMIT 1")[0]
    assert mod['uid'] == local_mod['uid'] and p['promotion']['uid'] == local_promo['uid']
    assert p['promotion']['discount_amount'] == local_promo['d'] > 0
    # Money is unchanged by any of this: the line's own figures are still on the event.
    assert p['unit_price'] == 10.5 and p['quantity'] == 2


def test_plain_sale_item_carries_empty_snapshots(shop):
    client, cid = shop
    pid = _product(client)
    m = _mark()
    assert _sale(client, [{'product_id': pid, 'quantity': 1}]).status_code == 200
    p = _only(_events_since(m, 'sale_item'), 'sale_item', 'create')['payload']
    assert p['modifiers'] == [] and p['promotion'] is None


# ── 5. plain sentences, both languages ───────────────────────────────────────

NEW_REASONS = ('missing_parent:branch', 'missing_parent:modifier_group', 'malformed_promotion',
               'modifier_link_duplicate', 'modifier_group_same_name', 'config_edit_superseded')
NEW_KINDS = ('promotion', 'modifier_group', 'modifier_option', 'product_modifier_group')


def _locale(name):
    with open(PRODUCT_DIR / 'frontend' / 'locales' / name, encoding='utf-8') as f:
        return json.load(f)


def test_every_new_reason_and_kind_has_a_plain_sentence_in_en_and_ar():
    en, ar = _locale('en.json'), _locale('ar.json')
    generic = retail_api._SYNC_REVIEW_REASON_UNKNOWN[0]
    other = retail_api._SYNC_REVIEW_REASON_MISSING_OTHER[0]
    for reason in NEW_REASONS:
        assert reason in retail_api._SYNC_REVIEW_REASONS, reason
        text, _ = retail_api._sync_review_reason(reason, 'promotion')
        assert text not in (generic, other), reason
        assert en.get(text) == text, ('en', reason)
        assert ar.get(text) and ar[text] != text, ('ar', reason)
    for kind in NEW_KINDS:
        text = retail_api._SYNC_REVIEW_KINDS.get(kind)
        assert text, kind
        assert en.get(text) == text, ('en', kind)
        assert ar.get(text) and ar[text] != text, ('ar', kind)
    # missing_parent:branch / modifier_group are retried by the engine on its own.
    assert retail_api._SYNC_REVIEW_REASONS['missing_parent:branch'][1] is True
    assert retail_api._SYNC_REVIEW_REASONS['modifier_link_duplicate'][1] is False
