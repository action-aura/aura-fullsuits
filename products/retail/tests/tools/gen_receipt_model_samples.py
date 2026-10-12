"""Write the sample receipt models (Arabic + English) the Android instrumented
render test draws (android/.../androidTest/assets/sample_{ar,en}.json).

They come from the REAL `receipt_model.build_receipt_model`, not hand-written
JSON, so the phone is drawn from exactly what the server would send: an Arabic
shop name, items "قهوة عربية" x2 and "Mint Tea" x1, JOD at 3 decimals, a cash
tender with change. Run from the repo root with the repo venv.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "products" / "retail" / "backend"))

from core.retail import receipt_model  # noqa: E402

OUT = ROOT / "android" / "aura-retail" / "app" / "src" / "androidTest" / "assets"

SALE = {"sale_number": "S-000123", "created_at": "2026-10-12 14:05", "subtotal": 5.25,
        "discount_amount": 0, "tax_amount": 0.0, "total": 5.25, "amount_paid": 10.0,
        "change_amount": 4.75, "customer_id": None}
ITEMS = [
    {"name": "قهوة عربية", "quantity": 2, "unit_price": 1.5, "modifiers": [], "applied_promotion": None},
    {"name": "Mint Tea", "quantity": 1, "unit_price": 2.25, "modifiers": [], "applied_promotion": None},
]
BRANDING = {"branding_business_name": "مقهى الأصالة", "branding_address": "عمّان - الرابية",
            "branding_phone": "+962 78 006 9485", "branding_receipt_language": "auto"}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for lang in ("ar", "en"):
        m = receipt_model.build_receipt_model(
            sale=SALE, items=ITEMS, branding=BRANDING, currency="JOD",
            requested_lang=lang, width_dots=576)
        m["kick_allowed"] = True
        (OUT / f"sample_{lang}.json").write_text(
            json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(lang, len(m["lines"]), "lines")


if __name__ == "__main__":
    main()
