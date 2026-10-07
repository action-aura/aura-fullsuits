"""Aura Retail -- W2b + W3a: promotions and the extras (modifier) set-up are ONE
configuration across TWO computers -- through the REAL routes, the REAL FileRelay and a
REAL second OS process (harness shared with retail_loyalty_sync_twodevice_test.py).

THE CLAIM. Before schema v50 a promotion made on A did not exist on B (B rang full
price), B's till could not offer A's extras, and a sale rung on A with an extra or a
promotion looked plain in B's sale detail. These tests pin the cure at the only level that
can see it -- two independent databases:

  1. a promotion created on A discounts a sale rung on B (product-level, then category);
  2. deactivating it on A makes B ring full price again;
  3. concurrent edits of ONE promotion (A: percent, B: name) converge to the SAME row on
     both, and the computer whose edit was dropped records it;
  4. a promotion scoped to a branch B has not received yet never applies on B at ANY
     branch (not widened to all, not narrowed to the default); once the branch arrives it
     applies there and only there;
  5. an extra created on A prices a sale rung on B;
  6. both computers attach the SAME group to the SAME product offline -> one link on both
     and a LATER event still applies (the pull is not wedged);
  7. a sale with an extra AND a promotion rung on A shows both on B.

Hand-computed figures (tax 0, price 100.000 unless stated):
  10% promotion  -> 1 x 100 = 90.000        category 20%  -> 2 x 100 = 160.000
  extra +5       -> 2 x (100+5) = 210.000   extra +5 and 10% (modifier-inclusive) -> 94.500

Run:
    pytest products/retail/tests/retail_config_sync_twodevice_test.py -v
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retail_gl_two_device_sync_test import _device_retail_db  # noqa: E402
from retail_po_sync_twodevice_test import Shop  # noqa: E402
from _stock_sync_harness import run_device  # noqa: E402

PY = sys.executable


# ── harness helpers ─────────────────────────────────────────────────────────

def _provision(tmp_path):
    """A: company, branch Main, product P (100.000, no category) and product Q (100.000, in
    a category). B: bootstrap, then pulls everything."""
    s = Shop(tmp_path)
    [s.boot['a']] = run_device(PY, s.app['a'], s.relay, [{"op": "bootstrap"}])
    [s.boot['b']] = run_device(PY, s.app['b'], s.relay, [{"op": "bootstrap"}])
    br, cat, p, q = s.run(
        'a',
        {"op": "create_branch", "name": "Main", "address": "1 Rd", "phone": "555-0100"},
        {"op": "http", "method": "POST", "path": "/categories", "json": {"name": "Drinks"}},
        {"op": "create_product", "name": "Cfg Plain", "price": 100.0, "initial_stock": 100},
        {"op": "create_product", "name": "Cfg Cat", "price": 100.0, "initial_stock": 100},
    )
    for r in (br, cat, p, q):
        assert r["status_code"] == 200, r
    s.cat = cat["body"]["data"]["id"]
    s.p, s.q = p["product_id"], q["product_id"]
    r = s.http('a', "PATCH", f"/products/{s.q}", {"category_id": s.cat})
    assert r["status_code"] == 200, r
    s.sync('a')
    s.pull('b')
    for who in ('a', 'b'):
        assert len(s.sql(who, "SELECT 1 FROM branches")) == 1, who
        assert len(s.sql(who, "SELECT 1 FROM products WHERE id IN (?,?)", (s.p, s.q))) == 2, who
        assert s.sql(who, "SELECT category_id AS c FROM products WHERE id=?", (s.q,))[0]["c"] == s.cat, who
    return s


def _ok(r):
    assert r["status_code"] == 200, r
    return r["body"]["data"]


def _promo(s, who, name, pct, **target):
    body = {"name": name, "discount_pct": pct}
    body.update(target)
    return _ok(s.http(who, "POST", "/promotions", body))["id"]


def _promo_row(s, who, name_or_uid):
    rows = s.sql(who, "SELECT * FROM promotions WHERE name=? OR uid=?", (name_or_uid, name_or_uid))
    assert len(rows) == 1, (who, name_or_uid, rows)
    return rows[0]


def _sell(s, who, pid, qty, option_ids=None, branch_id=None):
    item = {"product_id": pid, "quantity": qty}
    if option_ids:
        item["modifier_option_ids"] = option_ids
    body = {"items": [item], "payment_method": "cash", "idempotency_key": str(uuid.uuid4())}
    if branch_id is not None:
        body["branch_id"] = branch_id
    r = s.http(who, "POST", "/sales", body)
    assert r["status_code"] == 200, r
    return r["body"]["data"]["id"]


def _total(s, who, sale_id):
    return s.sql(who, "SELECT total FROM sales WHERE id=?", (sale_id,))[0]["total"]


def _sale_uid(s, who, sale_id):
    return s.sql(who, "SELECT uid FROM sales WHERE id=?", (sale_id,))[0]["uid"]


def _local_sale_id(s, who, uid):
    rows = s.sql(who, "SELECT id FROM sales WHERE uid=?", (uid,))
    assert len(rows) == 1, (who, uid, rows)
    return rows[0]["id"]


def _config_conflicts(s, who, entity_type):
    return s.sql(who, "SELECT * FROM sync_conflicts WHERE entity_type=?", (entity_type,))


def _quarantine_reasons(s, who, entity_type):
    [r] = s.run(who, {"op": "quarantine_rows", "entity_type": entity_type}, login=False)
    return [row["reason"] for row in r["rows"]]


def _group_with_option(s, who, gname="Size", oname="Extra cheese", price_delta=5.0):
    gid = _ok(s.http(who, "POST", "/modifier-groups", {"name": gname, "min_select": 0, "max_select": 1}))["id"]
    oid = _ok(s.http(who, "POST", f"/modifier-groups/{gid}/options",
                     {"name": oname, "price_delta": price_delta, "cost_delta": 1.0}))["id"]
    return gid, oid


def _attach(s, who, pid, gid):
    return _ok(s.http(who, "POST", f"/products/{pid}/modifier-groups", {"group_id": gid}))["id"]


# ── 1. promotion made on A discounts a sale rung on B ───────────────────────

def test_promotion_created_on_a_discounts_a_sale_rung_on_b(tmp_path):
    s = _provision(tmp_path)
    assert _total(s, 'b', _sell(s, 'b', s.p, 1)) == 100.0          # control: no promotion yet

    _promo(s, 'a', "Ten off plain", 10, product_id=s.p)
    _promo(s, 'a', "Twenty off drinks", 20, category_id=s.cat)
    s.settle()

    for who in ('a', 'b'):                                           # one identical row each
        row = _promo_row(s, who, "Ten off plain")
        assert (row["discount_pct"], row["product_id"], row["status"]) == (10.0, s.p, "active"), (who, row)
    assert _promo_row(s, 'a', "Ten off plain")["uid"] == _promo_row(s, 'b', "Ten off plain")["uid"]

    sale_b = _sell(s, 'b', s.p, 1)
    assert _total(s, 'b', sale_b) == 90.0                            # B discounted a promotion A made
    sale_a = _sell(s, 'a', s.p, 1)
    assert _total(s, 'a', sale_a) == 90.0                            # same figure A rings itself
    pn = s.sql('b', "SELECT sip.name_snapshot AS n FROM sale_item_promotions sip "
                    "JOIN sale_items si ON si.id=sip.sale_item_id WHERE si.sale_id=?", (sale_b,))
    assert [r["n"] for r in pn] == ["Ten off plain"], pn

    assert _total(s, 'b', _sell(s, 'b', s.q, 2)) == 160.0            # category-level, 2 x 100 - 20%


# ── 2. deactivate on A -> B rings full price ────────────────────────────────

def test_deactivated_promotion_on_a_rings_full_price_on_b(tmp_path):
    s = _provision(tmp_path)
    pid_a = _promo(s, 'a', "Soon gone", 10, product_id=s.p)
    s.settle()
    assert _total(s, 'b', _sell(s, 'b', s.p, 1)) == 90.0

    r = s.http('a', "DELETE", f"/promotions/{pid_a}")
    assert r["status_code"] == 200, r
    s.settle()

    assert _promo_row(s, 'b', "Soon gone")["status"] == "inactive"
    assert _total(s, 'b', _sell(s, 'b', s.p, 1)) == 100.0            # full price again
    assert _total(s, 'a', _sell(s, 'a', s.p, 1)) == 100.0


# ── 3. concurrent edits converge ────────────────────────────────────────────

def test_concurrent_promotion_edits_converge_on_tiebreak(tmp_path):
    s = _provision(tmp_path)
    _promo(s, 'a', "Shared promo", 10, product_id=s.p)
    s.settle()
    uid = _promo_row(s, 'a', "Shared promo")["uid"]
    id_a, id_b = (_promo_row(s, w, uid)["id"] for w in ('a', 'b'))
    assert _promo_row(s, 'a', uid)["row_version"] == _promo_row(s, 'b', uid)["row_version"]

    # B edits FIRST, A edits SECOND (same row_version, A's updated_at_utc is later): the
    # later stamp must win on both computers whatever order the events arrive in. A rule
    # of "whoever arrives last wins" would converge too, but on B's edit -- the wrong one.
    assert s.http('b', "PATCH", f"/promotions/{id_b}", {"name": "Renamed on B"})["status_code"] == 200
    assert s.http('a', "PATCH", f"/promotions/{id_a}", {"discount_pct": 25})["status_code"] == 200
    s.settle()

    keep = ("uid", "name", "discount_pct", "product_id", "category_id", "branch_id", "starts_at",
            "ends_at", "status", "row_version", "updated_at_utc")
    ra = {k: _promo_row(s, 'a', uid)[k] for k in keep}
    rb = {k: _promo_row(s, 'b', uid)[k] for k in keep}
    assert ra == rb, (ra, rb)                                        # identical on both computers
    # Whole-row winner: exactly one of the two edits survived intact.
    a_won = (ra["discount_pct"], ra["name"]) == (25.0, "Shared promo")
    b_won = (ra["discount_pct"], ra["name"]) == (10.0, "Renamed on B")
    assert a_won != b_won, ra
    assert a_won, ra                                                 # the later edit (A's) survived
    loser = 'b' if a_won else 'a'
    assert len(_config_conflicts(s, loser, "promotion")) >= 1, (loser, _config_conflicts(s, loser, "promotion"))
    assert any('config_edit_superseded' in c["incoming_payload"] or c.get("reason") == 'config_edit_superseded'
               or 'config_edit_superseded' in json.dumps(c) for c in _config_conflicts(s, loser, "promotion"))
    # And the till agrees with the row: the surviving percent is what B rings.
    expect = 75.0 if a_won else 90.0
    assert _total(s, 'b', _sell(s, 'b', s.p, 1)) == expect


# ── 4. a branch-scoped promotion never widens on an unresolvable branch ─────

def test_branch_scoped_promotion_never_widens_on_unresolvable_branch(tmp_path):
    s = _provision(tmp_path)
    annex = _ok(s.http('a', "POST", "/branches", {"name": "Annex", "address": "9 Rd", "phone": "555-0199"}))["id"]
    held = s.sql('a', "SELECT * FROM sync_outbox WHERE entity_type='branch'")
    assert held, "precondition: Annex's branch event was queued"
    s.run('a', {"op": "outbox_delete_entity_type", "entity_type": "branch"}, login=False)
    _promo(s, 'a', "Annex only", 10, product_id=s.p, branch_id=annex)
    s.settle()                                                       # the promotion arrives; Annex does not

    assert s.sql('b', "SELECT 1 FROM promotions WHERE name='Annex only'") == []     # parked, not applied
    assert "missing_parent:branch" in _quarantine_reasons(s, 'b', "promotion")
    main_b = s.sql('b', "SELECT id FROM branches WHERE name='Main'")[0]["id"]
    assert _total(s, 'b', _sell(s, 'b', s.p, 1, branch_id=main_b)) == 100.0         # NOT widened to all

    conn = sqlite3.connect(str(_device_retail_db(s.app['a'])), timeout=30)
    try:
        for r in held:
            cols = list(r)
            conn.execute(f"INSERT INTO sync_outbox ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                         [r[c] for c in cols])
        conn.commit()
    finally:
        conn.close()
    s.settle()                                                       # the branch arrives; the parked event replays

    row = _promo_row(s, 'b', "Annex only")
    annex_b = s.sql('b', "SELECT id FROM branches WHERE name='Annex'")[0]["id"]
    assert row["branch_id"] == annex_b, row
    assert _total(s, 'b', _sell(s, 'b', s.p, 1, branch_id=main_b)) == 100.0         # still not at Main
    r = s.http('b', "POST", f"/products/{s.p}/stock-adjust", {"quantity": 10, "reason": "t", "branch_id": annex_b})
    assert r["status_code"] == 200, r
    assert _total(s, 'b', _sell(s, 'b', s.p, 1, branch_id=annex_b)) == 90.0         # only at Annex


# ── 5. an extra made on A prices a sale on B ────────────────────────────────

def test_modifier_option_created_on_a_prices_a_sale_on_b(tmp_path):
    s = _provision(tmp_path)
    gid, oid = _group_with_option(s, 'a')
    _attach(s, 'a', s.p, gid)
    s.settle()

    assert len(s.sql('b', "SELECT 1 FROM modifier_options WHERE id=?", (oid,))) == 1
    sale_b = _sell(s, 'b', s.p, 2, option_ids=[oid])
    sale_a = _sell(s, 'a', s.p, 2, option_ids=[oid])
    assert _total(s, 'b', sale_b) == _total(s, 'a', sale_a) == 210.0    # 2 x (100 + 5)
    r = s.http('b', "GET", f"/sales/{sale_b}")
    items = _ok(r)["items"]
    assert [m["name"] for m in items[0]["modifiers"]] == ["Extra cheese"], items


# ── 6. the same attachment made twice does not wedge the pull ───────────────

def test_duplicate_attachment_does_not_wedge_the_pull(tmp_path):
    s = _provision(tmp_path)
    gid, oid = _group_with_option(s, 'a')
    s.settle()
    link_a = _attach(s, 'a', s.p, gid)                               # both attach it, offline
    link_b = _attach(s, 'b', s.p, gid)
    assert link_a != link_b
    s.settle()

    ids = {}
    for who in ('a', 'b'):
        rows = s.sql(who, "SELECT id FROM product_modifier_groups WHERE product_id=? AND group_id=? "
                          "AND deleted_at_utc IS NULL", (s.p, gid))
        ids[who] = [r["id"] for r in rows]
        assert len(ids[who]) == 1, (who, rows)
    assert ids['a'] == ids['b'] == [min(link_a, link_b)], (ids, link_a, link_b)

    # Not wedged: a LATER event from A still reaches B and works at the till.
    oid2 = _ok(s.http('a', "POST", f"/modifier-groups/{gid}/options",
                      {"name": "Late extra", "price_delta": 3.0, "cost_delta": 0}))["id"]
    s.settle()
    assert len(s.sql('b', "SELECT 1 FROM modifier_options WHERE id=?", (oid2,))) == 1
    assert _total(s, 'b', _sell(s, 'b', s.p, 1, option_ids=[oid2])) == 103.0


# ── 7. a sale with an extra AND a promotion keeps both on the other computer ─

def test_sale_with_extra_and_promotion_shows_both_on_b(tmp_path):
    s = _provision(tmp_path)
    gid, oid = _group_with_option(s, 'a')
    _attach(s, 'a', s.p, gid)
    _promo(s, 'a', "Ten off plain", 10, product_id=s.p)
    s.settle()

    sale_a = _sell(s, 'a', s.p, 1, option_ids=[oid])
    assert _total(s, 'a', sale_a) == 94.5                            # (100 + 5) - 10%
    uid = _sale_uid(s, 'a', sale_a)
    s.settle()

    sale_b = _local_sale_id(s, 'b', uid)
    items = _ok(s.http('b', "GET", f"/sales/{sale_b}"))["items"]
    assert [m["name"] for m in items[0]["modifiers"]] == ["Extra cheese"], items
    assert _total(s, 'b', sale_b) == 94.5
    pn = s.sql('b', "SELECT sip.name_snapshot AS n, sip.discount_pct_snapshot AS pct FROM sale_item_promotions sip "
                    "JOIN sale_items si ON si.id=sip.sale_item_id WHERE si.sale_id=?", (sale_b,))
    assert [(r["n"], r["pct"]) for r in pn] == [("Ten off plain", 10.0)], pn
    # Fix round 1 (F6): the sale screen itself now shows the promotion on the
    # computer that did NOT ring the sale -- not only the stored snapshot row.
    ap = items[0].get("applied_promotion")
    assert ap and ap["name"] == "Ten off plain" and float(ap["discount_pct"]) == 10.0, items
