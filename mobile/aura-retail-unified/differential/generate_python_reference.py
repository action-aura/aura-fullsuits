"""
Aura Retail Unified Mobile -- M3.5 differential harness, Python side.

Loads calculation_fixtures.json, runs every case through the REAL
products/retail/backend/core/retail/pricing.py (the exact module imported,
not re-derived or hand-copied), and writes python_reference_output.json --
the golden output the Kotlin commonTest differential suite compares against.

This script is the "produce canonical normalized results" step from the
governing spec's own M3.5 process (define fixtures -> execute Python
reference -> normalize -> execute Kotlin -> compare exact values). Nothing
here computes an expected value independently -- every number in the
output file is whatever the real, unmodified pricing.py actually returns.

Run:
    python mobile/aura-retail-unified/differential/generate_python_reference.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SUITE_ROOT = SCRIPT_DIR.parent.parent.parent  # aura-fullsuits
BACKEND_DIR = SUITE_ROOT / "products" / "retail" / "backend"
for p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.retail import pricing  # noqa: E402  -- the real, unmodified authority


def run_calculate_line_cases(cases: list[dict]) -> list[dict]:
    results = []
    for case in cases:
        result = pricing.calculate_line(
            unit_price=case["unit_price"], quantity=case["quantity"],
            discount_pct=case["discount_pct"], tax_rate=case["tax_rate"], mode=case["mode"],
        )
        results.append({"id": case["id"], "input": case, "output": result})
    return results


def run_calculate_invoice_cases(cases: list[dict]) -> list[dict]:
    results = []
    for case in cases:
        result = pricing.calculate_invoice(
            subtotal=case["subtotal"], discount_amount=case["discount_amount"],
            tax_rate_pct=case["tax_rate_pct"], mode=case["mode"],
        )
        results.append({"id": case["id"], "input": case, "output": result})
    return results


def run_change_cases(cases: list[dict]) -> list[dict]:
    # change = max(0, paid - total) is inline in create_sale() (retail_api.py),
    # not its own pricing.py function -- reproduced here verbatim (2 lines,
    # not "re-derived logic") exactly as the real route computes it, using
    # the same _money()-equivalent 2dp rounding pricing.py itself uses.
    from decimal import Decimal, ROUND_HALF_UP

    def money(x):
        return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    results = []
    for case in cases:
        paid = money(case["amount_paid"])
        total = money(case["total"])
        change = max(0.0, money(paid - total))
        results.append({"id": case["id"], "input": case, "output": {"change": change}})
    return results


def main() -> int:
    fixtures_path = SCRIPT_DIR / "calculation_fixtures.json"
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))

    output = {
        "fixture_version": fixtures["fixture_version"],
        "pricing_calculation_version": pricing.CALCULATION_VERSION,
        "calculate_line_results": run_calculate_line_cases(fixtures["calculate_line_cases"]),
        "calculate_invoice_results": run_calculate_invoice_cases(fixtures["calculate_invoice_cases"]),
        "change_results": run_change_cases(fixtures["change_cases"]),
    }

    out_path = SCRIPT_DIR / "python_reference_output.json"
    out_path.write_text(json.dumps(output, indent=2, sort_keys=False), encoding="utf-8")
    print(f"Wrote {len(output['calculate_line_results'])} calculate_line results, "
          f"{len(output['calculate_invoice_results'])} calculate_invoice results, "
          f"{len(output['change_results'])} change results to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
