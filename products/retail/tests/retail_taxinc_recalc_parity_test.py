"""TAXINC: the till's PREVIEW (subsystem-retail.js `_recalc`) must show what the server CHARGES
(core/retail/pricing.calculate_line), to the fil, in the new inclusive mode.

WHY: the two are hand-written copies of one money rule in two languages (see
retail_pricing_parity_test.py for the discount half of the same hazard). The server is the only
thing allowed to persist a total, so a drift never mischarges -- it makes the till QUOTE one
number and CHARGE another, which is exactly the defect TAXINC exists to remove (a shelf price a
fil off). This drives the REAL browser code through node and the REAL Python module over the same
carts and compares every figure.

Covers: every 3-dp price 0.001..1.500 x qty {1,3,7} x rate {0,8,16}% x discount {0,10,33.3333}%
on single-line carts, in all three modes (the two old modes are included so a regression there is
caught too); and random multi-line carts with a cart-level AMOUNT discount and a "make the total X"
target (the `_cartDiscPlan` / `_cartTotalsAt` path, which carries its own copy of the tax formula).

Run:  pytest products/retail/tests/retail_taxinc_recalc_parity_test.py -q
"""
import json
import random
import shutil
import subprocess
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PRODUCT_DIR = TESTS_DIR.parent
BACKEND_DIR = PRODUCT_DIR / "backend"
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail import cart_discount as cd  # noqa: E402
from core.retail import pricing  # noqa: E402

DRIVER = TESTS_DIR / "retail_taxinc_recalc_parity_driver.js"
MODES = (pricing.TAX_AFTER_DISCOUNT, pricing.TAX_BEFORE_DISCOUNT, pricing.TAX_INCLUSIVE)
Q = pricing.currency_quantum("JOD")


def _node(scenarios):
    node = shutil.which("node")
    if node is None:  # pragma: no cover
        pytest.skip("node is not on PATH; cannot compare against the browser code")
    proc = subprocess.run([node, str(DRIVER)], input=json.dumps(scenarios),
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, "driver failed:\n" + (proc.stderr or "")[:2000]
    out = json.loads(proc.stdout)
    assert len(out) == len(scenarios)
    return out


def _server_single(s):
    tot = {"subtotal": Decimal(0), "discount": Decimal(0), "tax": Decimal(0), "total": Decimal(0)}
    for l in s["lines"]:
        r = pricing.calculate_line(l["price"], l["qty"], s["disc_pct"], l["rate"], mode=s["mode"], currency="JOD")
        tot["subtotal"] += Decimal(str(r["gross"]))
        tot["discount"] += Decimal(str(r["discount_amount"]))
        tot["tax"] += Decimal(str(r["tax"]))
        tot["total"] += Decimal(str(r["total"]))
    return tot


def _server_cart_disc(s):
    """Returns (totals, None) or (None, 'unreachable')."""
    mode = s["mode"]

    def calc(p, q, pct, r):
        return pricing.calculate_line(p, q, pct, r, mode=mode, currency="JOD")

    lines = [(l["price"], l["qty"], l["rate"]) for l in s["lines"]]
    try:
        if s["cart_disc"]["mode"] == "amount":
            amount = cd.parse_amount(s["cart_disc"]["value"], Q)
            _p, calcs, _sh = cd.apply_cart_discount(lines, amount, Q, calc)
        else:
            target = cd.parse_amount(s["cart_disc"]["value"], Q, code=cd.CODE_BAD_TARGET, what="new total")
            _a, _p, calcs, _sh = cd.solve_target_total(lines, target, Q, calc)
            if calcs is None:
                calcs = [calc(p, q, 0, r) for p, q, r in lines]
    except cd.CartDiscountError:
        return None, "unreachable"
    tot = {k: sum((Decimal(str(c[f])) for c in calcs), Decimal(0))
           for k, f in (("subtotal", "gross"), ("discount", "discount_amount"), ("tax", "tax"), ("total", "total"))}
    return tot, None


def _same(js, srv, label):
    for k in ("subtotal", "discount", "tax", "total"):
        assert abs(Decimal(str(js[k])) - srv[k]) < Decimal("0.0000005"), (label, k, js, {a: str(b) for a, b in srv.items()})


def test_single_line_grid_matches_server_in_every_mode():
    scenarios = []
    for mode in MODES:
        for fils in range(1, 1501):
            price = float(Decimal(fils) / 1000)
            for qty in (1, 3, 7):
                for rate in (0, 8, 16):
                    for pct in (0, 10, 33.3333):
                        scenarios.append({"mode": mode, "lines": [{"price": price, "qty": qty, "rate": rate}],
                                          "disc_pct": pct, "cart_disc": None})
    out = _node(scenarios)
    assert len(scenarios) == 3 * 1500 * 27
    for s, js in zip(scenarios, out):
        _same(js, _server_single(s), s)


def test_inclusive_totals_are_the_shelf_price_in_the_browser_too():
    """The user-visible promise, checked on the browser side directly (not via the server
    module): total == round(price*qty) for every 3-dp shelf price, no discount."""
    scenarios = [{"mode": "inclusive", "lines": [{"price": float(Decimal(f) / 1000), "qty": 3, "rate": 16}],
                  "disc_pct": 0, "cart_disc": None} for f in range(1, 3001)]
    for f, js in zip(range(1, 3001), _node(scenarios)):
        assert Decimal(str(js["total"])) == (Decimal(f) / 1000 * 3).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP), (f, js)


@pytest.mark.parametrize("cart_mode", ["amount", "target"])
def test_cart_level_discount_matches_server_in_every_mode(cart_mode):
    rng = random.Random(20261016)
    scenarios = []
    for mode in MODES:
        for _ in range(300):
            lines = [{"price": rng.randint(1, 25000) / 1000, "qty": rng.randint(1, 5),
                      "rate": rng.choice((0, 4, 8, 16))} for _ in range(rng.randint(1, 4))]
            gross = sum(Decimal(str(l["price"])) * l["qty"] for l in lines)
            if cart_mode == "amount":
                value = float((gross * Decimal(rng.randint(1, 40)) / 100).quantize(Q, rounding=ROUND_HALF_UP))
                if value <= 0:
                    continue
            else:
                value = float((gross * Decimal(rng.randint(40, 99)) / 100).quantize(Q, rounding=ROUND_HALF_UP))
            scenarios.append({"mode": mode, "lines": lines, "disc_pct": 0,
                              "cart_disc": {"mode": cart_mode, "value": value}})
    out = _node(scenarios)
    checked = reachable = 0
    for s, js in zip(scenarios, out):
        srv, why = _server_cart_disc(s)
        if why:
            # the server refuses -> the till must have dropped the discount, not shown a number
            assert js["cart_disc_cleared"], (s, js)
        else:
            assert not js["cart_disc_cleared"], (s, js)
            _same(js, srv, s)
            reachable += 1
        checked += 1
    assert checked > 700 and reachable > 400, (checked, reachable)
