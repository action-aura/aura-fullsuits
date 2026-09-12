"""The till's PREVIEW and the server's CHARGE must resolve the same discount.

WHY THIS EXISTS

`subsystem-retail.js`'s `_bestPromoFor()` and `core/retail/promotions.py`'s
`resolve_line_discount_pct()` are two independent implementations of the same
money rule, in two languages. The JS file says so itself, right above the
mirrored formula:

    "if either side changes, both must change together (there is no shared
     runtime between browser JS and the Python backend to enforce this
     automatically)"

The hazard was written down. Nothing enforced it. Each side has a good test
suite -- `retail_promotions_ui_test.js` and `retail_promotions_test.py` -- but
each tests its own implementation against its own understanding of the rules,
so the two can drift apart and BOTH stay green.

What drift costs is not abstract. The server is the only thing allowed to
compute a persisted total, so a divergence never mischarges -- it makes the
till QUOTE one price and CHARGE another. The cashier reads a number to the
customer and the receipt prints a different one. That exact defect exists on
the Android app today (see ROADMAP 2026-09-02, "the phone till QUOTES a
different number than it CHARGES"), where the preview has no promotion logic
at all. This file makes sure the DESKTOP till cannot drift into the same
state silently.

Scope, deliberately narrow: the per-line effective discount percentage, which
is the part with tiers and tie-breaks and therefore the part most likely to
drift. Not the whole invoice -- `retail_pricing_test.py` owns the tax and
rounding formula.

Run:
    py -3.14 -m pytest products/retail/tests/retail_pricing_parity_test.py -q
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PRODUCT_DIR = TESTS_DIR.parent
BACKEND_DIR = PRODUCT_DIR / "backend"
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail.promotions import resolve_line_discount_pct  # noqa: E402

DRIVER = TESTS_DIR / "retail_pricing_parity_driver.js"


def _promo(pid, *, product_id=None, category_id=None, pct):
    """One active promotion row, in the shape both sides consume."""
    return {"id": pid, "product_id": product_id,
            "category_id": category_id, "discount_pct": pct}


def _item(product_id="P1", parent_product_id=None, category_id="C1"):
    return {"product_id": product_id,
            "parent_product_id": parent_product_id,
            "category_id": category_id}


#: Each case is (label, promotions, item, manual_pct).
#:
#: Chosen to hit every branch the resolver has tiers for, plus the tie-breaks
#: and the "manual wins" rule -- these are the places two hand-written
#: implementations actually diverge.
SCENARIOS = [
    ("no promotions at all", [], _item(), 0),
    ("no promotions, manual discount only", [], _item(), 15),

    ("product-specific promotion", [_promo(1, product_id="P1", pct=20)], _item(), 0),
    ("category promotion", [_promo(2, category_id="C1", pct=10)], _item(), 0),

    ("product beats category even when category is bigger",
     [_promo(1, product_id="P1", pct=5), _promo(2, category_id="C1", pct=40)],
     _item(), 0),

    ("highest wins among two product promotions",
     [_promo(1, product_id="P1", pct=5), _promo(2, product_id="P1", pct=25)],
     _item(), 0),

    ("highest wins among two category promotions",
     [_promo(1, category_id="C1", pct=7), _promo(2, category_id="C1", pct=12)],
     _item(), 0),

    ("manual beats a smaller promotion",
     [_promo(1, product_id="P1", pct=5)], _item(), 30),
    ("promotion beats a smaller manual",
     [_promo(1, product_id="P1", pct=35)], _item(), 10),
    ("manual equals promotion",
     [_promo(1, product_id="P1", pct=20)], _item(), 20),

    ("parent-tier promotion reaches a variant",
     [_promo(1, product_id="PARENT", pct=20)],
     _item(product_id="V1", parent_product_id="PARENT"), 0),
    ("exact variant promotion beats the parent tier",
     [_promo(1, product_id="PARENT", pct=20), _promo(2, product_id="V1", pct=5)],
     _item(product_id="V1", parent_product_id="PARENT"), 0),
    ("parent tier beats category",
     [_promo(1, product_id="PARENT", pct=10), _promo(2, category_id="C1", pct=30)],
     _item(product_id="V1", parent_product_id="PARENT"), 0),

    ("promotion for a different product does not apply",
     [_promo(1, product_id="OTHER", pct=50)], _item(), 0),
    ("promotion for a different category does not apply",
     [_promo(1, category_id="OTHER", pct=50)], _item(), 0),

    ("item with no category, category promo present",
     [_promo(1, category_id="C1", pct=25)],
     _item(category_id=None), 0),

    ("zero-percent promotion is not treated as a match worth taking",
     [_promo(1, product_id="P1", pct=0)], _item(), 0),
]


def _client_answers(scenarios):
    """Drive the REAL browser code through node and return its answers."""
    node = shutil.which("node")
    if node is None:  # pragma: no cover - environment guard
        pytest.skip("node is not on PATH; cannot compare against the browser rule")
    payload = json.dumps([
        {"promotions": promos, "item": item, "manual_pct": manual}
        for _label, promos, item, manual in scenarios
    ])
    proc = subprocess.run([node, str(DRIVER)], input=payload,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, (
        "the browser-side driver failed:\n" + (proc.stderr or "")[:2000])
    return json.loads(proc.stdout)


def _server_answer(promos, item, manual_pct):
    effective, applied = resolve_line_discount_pct(
        promos, item["product_id"], item["parent_product_id"],
        item["category_id"], manual_pct)
    return float(effective), applied


def test_the_driver_actually_exercises_the_real_frontend():
    """Guard the harness itself.

    If the driver silently returned an empty list, or loaded nothing, every
    parity assertion below would pass vacuously -- the exact shape this
    codebase keeps getting burned by.
    """
    answers = _client_answers(SCENARIOS)
    assert len(answers) == len(SCENARIOS), (
        "the browser driver returned %d answers for %d scenarios"
        % (len(answers), len(SCENARIOS)))
    assert any(a["effective_pct"] > 0 for a in answers), (
        "every browser answer was zero discount -- the driver is not really "
        "resolving promotions, so the comparison below would prove nothing")


@pytest.mark.parametrize("index", range(len(SCENARIOS)))
def test_client_and_server_resolve_the_same_discount(index):
    label, promos, item, manual = SCENARIOS[index]
    answers = _client_answers([SCENARIOS[index]])
    client_pct = float(answers[0]["effective_pct"])
    server_pct, _applied = _server_answer(promos, item, manual)

    assert client_pct == pytest.approx(server_pct), (
        "PREVIEW/CHARGE DIVERGENCE on: %s\n"
        "  browser (_bestPromoFor + max(manual, promo)) -> %s%%\n"
        "  server  (resolve_line_discount_pct)          -> %s%%\n"
        "These are two hand-written implementations of one money rule. When "
        "they disagree the till quotes one price and charges another, and "
        "both existing promotion suites still pass, because each only tests "
        "its own side." % (label, client_pct, server_pct)
    )
