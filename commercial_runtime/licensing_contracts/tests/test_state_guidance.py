"""r17 (owner decision 10A): the clock-moved-back stop must say how to fix it.

Both directions are covered, because the point is a refusal that EXPLAINS and
only for the state the shopkeeper can fix himself:
  deny + explain : CLOCK_REVIEW_REQUIRED -> 403, reason_code unchanged, plain
                   how-to-fix text in `message` (en) and `message_ar` (ar);
  deny, no advice: SUSPENDED -> still the old generic refusal. "Set your clock"
                   would be false advice for a commercial suspension.
"""
import json
from pathlib import Path

import pytest
from flask import Flask, jsonify

from commercial_runtime.licensing_contracts.flask_guard import make_capability_guard
from commercial_runtime.licensing_contracts.state_guidance import (
    CLOCK_REVIEW_MESSAGE_AR, CLOCK_REVIEW_MESSAGE_EN, guidance_for,
)
from commercial_runtime.licensing_contracts.state_machine import LicenseState
from commercial_runtime.licensing_contracts.state_repository import (
    LICENSING_SCHEMA_VERSION, LicenseStateRecord, LicenseStateRepository,
)
from commercial_runtime.licensing_contracts.status_presenter import present_status

GENERIC = "This action is not available in the current licensing state."


def _record(state):
    return LicenseStateRecord(
        licensing_schema_version=LICENSING_SCHEMA_VERSION, product_code="AURA_RETAIL",
        platform="WINDOWS", current_state=state, entitlements_json=json.dumps({}))


@pytest.fixture
def client(tmp_path):
    app_data = str(tmp_path / "appdata")
    guard = make_capability_guard(app_data)
    app = Flask(__name__)

    @app.route("/sell", methods=["POST"])
    @guard("retail.sale.create", restricted_mode_allowlist=frozenset({"retail.records.read"}))
    def sell():
        return jsonify({"status": "success"})

    repo = LicenseStateRepository(Path(app_data) / "database" / "subsystems" / "licensing.db")

    def post_in(state):
        repo.save(_record(state))
        return app.test_client().post("/sell")
    return post_in


def test_clock_stop_explains_how_to_fix_in_english_and_arabic(client):
    r = client("CLOCK_REVIEW_REQUIRED")
    body = r.get_json()
    assert r.status_code == 403
    assert body["reason_code"]  # the machine-readable refusal is unchanged
    assert body["message"] == CLOCK_REVIEW_MESSAGE_EN != GENERIC
    assert body["message_ar"] == CLOCK_REVIEW_MESSAGE_AR
    # The text must actually tell the person what to DO, and that data is safe.
    assert "date and time" in body["message"] and "restart" in body["message"]
    assert "data is safe" in body["message"]
    assert "التاريخ والوقت" in body["message_ar"] and "بياناتك بأمان" in body["message_ar"]


def test_a_commercial_suspension_gets_no_clock_advice(client):
    r = client("SUSPENDED")
    body = r.get_json()
    assert r.status_code == 403
    assert body["message"] == GENERIC
    assert "message_ar" not in body


def test_status_carries_guidance_only_for_the_clock_state():
    clock = present_status(_record("CLOCK_REVIEW_REQUIRED"))
    assert clock["guidance"] == CLOCK_REVIEW_MESSAGE_EN
    assert clock["guidance_ar"] == CLOCK_REVIEW_MESSAGE_AR
    for other in ("ACTIVE_ONLINE", "SUSPENDED", "LOCAL_STATE_CORRUPT"):
        shown = present_status(_record(other))
        assert "guidance" not in shown and "guidance_ar" not in shown


def test_guidance_for_never_raises_on_junk():
    assert guidance_for("NOT_A_STATE") is None
    assert guidance_for(None) is None
    assert guidance_for(LicenseState.CLOCK_REVIEW_REQUIRED)["en"] == CLOCK_REVIEW_MESSAGE_EN


def test_the_till_translation_files_carry_the_same_pair():
    """The browser till translates a server `message` by looking the English text
    up in locales/ar.json. If this sentence is edited here and not there, an
    Arabic till silently shows English -- so the pair is pinned."""
    locales = Path(__file__).resolve().parents[3] / "products" / "retail" / "frontend" / "locales"
    ar = json.loads((locales / "ar.json").read_text(encoding="utf-8"))
    en = json.loads((locales / "en.json").read_text(encoding="utf-8"))
    assert ar[CLOCK_REVIEW_MESSAGE_EN] == CLOCK_REVIEW_MESSAGE_AR
    assert en[CLOCK_REVIEW_MESSAGE_EN] == CLOCK_REVIEW_MESSAGE_EN
