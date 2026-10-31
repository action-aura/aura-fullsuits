"""Leads Finder AI ranking: POST /leads/discover/rank, the Rank UI, and the AI
note saved on import. The AI is a fake; what it RECEIVED is asserted, because
"no phone number is sent to the AI" can only be proven by looking at the
request, not at the response."""
from __future__ import annotations

import json
import re

import pytest
from sqlalchemy import select

from app.extensions import db_session
from app.leads.discovery import DiscoveredPlace
from app.models.leads import Lead, LeadNote
from tests.ai_fakes import FakeProvider, error, patch_providers
from tests.conftest import force_login, get_csrf, make_staff
from tests.test_lead_discovery_routes import FakeProvider as FakePlaces
from tests.test_lead_discovery_routes import _enable_discovery, _make_profile

URL = "/leads/discover/rank"
PHONE = "+962791234567"


def _place(idx, **kw):
    base = {"idx": idx, "name": f"Shop {idx}", "address": "Main St", "types": ["store"], "rating": 4.5, "reviews": 120,
            "has_phone": True, "has_website": False, "status": "OPERATIONAL"}
    base.update(kw)
    return base


def _payload(*places, **kw):
    return {"segment": "pharmacies", "area": "Irbid", "lang": "ar", "places": list(places) or [_place(0), _place(1)], **kw}


def _rank_reply(*rows):
    return {"results": [{"idx": i, "score": s, "reason": r, "opener": o} for i, s, r, o in rows]}


@pytest.fixture()
def rep(app, client, seeded):
    sid = make_staff(app, "rep@example.com", role_codes=["SALES"])
    force_login(client, app, sid)
    _enable_discovery(app)
    csrf = get_csrf(client.get("/leads/discover").get_data(as_text=True))
    return client, csrf, sid


def _post(client, csrf, payload):
    return client.post(URL, json=payload, headers={"X-CSRFToken": csrf, "Accept": "application/json"})


def test_ranks_rows_and_no_phone_number_reaches_the_ai(rep, monkeypatch):
    client, csrf, _ = rep
    gemini = FakeProvider("gemini", trains_on_data=True, data=_rank_reply((0, 88, "busy pharmacy", "مرحبا"), (1, 40, "few reviews", "أهلا")))
    patch_providers(monkeypatch, gemini)
    # A crafted/leaky client: phone-ish values smuggled into several fields.
    leaky = _payload(
        _place(0, phone=PHONE, nationalPhoneNumber=PHONE, international_phone=PHONE),
        _place(1, website="https://x.example"),
    )
    resp = _post(client, csrf, leaky)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert [r["idx"] for r in resp.get_json()["results"]] == [0, 1]
    sent = json.dumps(gemini.calls[0]["messages"], ensure_ascii=False) + gemini.calls[0]["system"]
    assert PHONE not in sent and "962791234567" not in sent, "a phone number was sent to the AI"
    assert '"phone' not in sent.lower() and "lists_a_phone" in sent  # only the boolean fact crosses
    assert "https://x.example" not in sent, "websites are not sent either"
    assert gemini.calls[0]["json_schema"]["properties"]["results"]["items"]["properties"].keys() == {"idx", "score", "reason", "opener"}


def test_server_drops_unsent_idx_duplicates_and_clamps(rep, monkeypatch):
    client, csrf, _ = rep
    reply = _rank_reply(
        (0, 250, "r0", "o" * 900),        # clamp to 100, opener cut to 400
        (1, -7, "r1", "o1"),              # clamp to 0
        (99, 90, "never sent", "x"),      # idx the server did not send
        (0, 5, "dup", "dup"),             # repeated idx: first answer wins
        (-1, 50, "neg", "neg"),
    )
    patch_providers(monkeypatch, FakeProvider("gemini", trains_on_data=True, data=reply))
    rows = _post(client, csrf, _payload()).get_json()["results"]
    assert [r["idx"] for r in rows] == [0, 1], "an idx the server never sent was accepted"
    assert rows[0]["score"] == 100 and len(rows[0]["opener"]) == 400 and rows[0]["reason"] == "r0"
    assert rows[1]["score"] == 0


def test_model_scoring_nothing_we_sent_is_an_error_not_an_empty_success(rep, monkeypatch):
    client, csrf, _ = rep
    patch_providers(monkeypatch, FakeProvider("gemini", trains_on_data=True, data=_rank_reply((55, 50, "r", "o"))))
    resp = _post(client, csrf, _payload())
    assert resp.status_code == 502 and "results" not in resp.get_json()


@pytest.mark.parametrize("payload", [
    {"segment": "p", "area": "a", "places": []},
    {"segment": "", "area": "a", "places": [_place(0)]},
    {"segment": "p", "area": "a", "lang": "fr", "places": [_place(0)]},
    {"segment": "p", "area": "a", "places": [_place(i) for i in range(21)]},
    {"segment": "p", "area": "a", "places": [_place(0), _place(0)]},
    {"segment": "p", "area": "a", "places": [{"idx": "0", "name": "x"}]},
    {"segment": "p", "area": "a", "places": ["nope"]},
    [],
])
def test_bad_payloads_are_400_and_never_reach_the_ai(rep, monkeypatch, payload):
    client, csrf, _ = rep
    gemini = FakeProvider("gemini", trains_on_data=True, data=_rank_reply((0, 1, "r", "o")))
    patch_providers(monkeypatch, gemini)
    resp = _post(client, csrf, payload)
    assert resp.status_code == 400 and len(gemini.calls) == 0


def test_strings_are_bounded_before_they_reach_the_ai(rep, monkeypatch):
    client, csrf, _ = rep
    gemini = FakeProvider("gemini", trains_on_data=True, data=_rank_reply((0, 1, "r", "o")))
    patch_providers(monkeypatch, gemini)
    _post(client, csrf, _payload(_place(0, name="N" * 5000, address="A" * 5000, types=["t" * 500] * 50)))
    sent = gemini.calls[0]["messages"][0]["content"]
    assert "N" * 201 not in sent and "A" * 301 not in sent and "t" * 61 not in sent


def test_permission_login_and_csrf_are_enforced(app, client, seeded, monkeypatch):
    gemini = FakeProvider("gemini", trains_on_data=True, data=_rank_reply((0, 1, "r", "o")))
    patch_providers(monkeypatch, gemini)
    # anonymous (valid CSRF, so the 401 is the login check and not the CSRF one)
    anon_csrf = get_csrf(client.get("/auth/login").get_data(as_text=True))
    assert _post(client, anon_csrf, _payload()).status_code == 401
    # signed in but without leads.discover
    force_login(client, app, make_staff(app, "fin@example.com", role_codes=["FINANCE"]))
    csrf = get_csrf(client.get("/").get_data(as_text=True))
    assert _post(client, csrf, _payload()).status_code == 403
    assert len(gemini.calls) == 0


def test_csrf_header_is_required(rep, monkeypatch):
    client, csrf, _ = rep
    gemini = FakeProvider("gemini", trains_on_data=True, data=_rank_reply((0, 1, "r", "o")))
    patch_providers(monkeypatch, gemini)
    assert _post(client, csrf, _payload()).status_code == 200  # control
    before = len(gemini.calls)
    resp = client.post(URL, json=_payload(), headers={"Accept": "application/json"})
    assert resp.status_code == 400 and len(gemini.calls) == before


def test_daily_cap_and_not_configured(app, rep, monkeypatch):
    client, csrf, _ = rep
    gemini = FakeProvider("gemini", trains_on_data=True, data=_rank_reply((0, 1, "r", "o")))
    patch_providers(monkeypatch, gemini)
    app.config["AI_DAILY_CAP"] = "1"
    assert _post(client, csrf, _payload()).status_code == 200
    over = _post(client, csrf, _payload())
    assert over.status_code == 429 and over.get_json()["error"] == "AI_BUDGET_EXHAUSTED" and len(gemini.calls) == 1
    patch_providers(monkeypatch)
    assert _post(client, csrf, _payload()).status_code in (429, 503)
    app.config["AI_DAILY_CAP"] = "50"
    none = _post(client, csrf, _payload())
    assert none.status_code == 503 and none.get_json()["error"] == "AI_NOT_CONFIGURED"


# -- the screen ----------------------------------------------------------------
def _search(client, csrf, monkeypatch, places):
    monkeypatch.setattr("app.leads.routes.build_provider", lambda config, **kw: FakePlaces(places))
    return client.post("/leads/discover/search", data={"csrf_token": csrf, "segment": "pharmacies", "area": "Irbid", "priority": "MEDIUM"})


PLACES = [
    DiscoveredPlace(place_id="places/a", name="Acme Pharmacy", address="1 Main", phone=PHONE, website="https://a.example",
                    rating=4.6, user_rating_count=210, types=("pharmacy", "store"), business_status="OPERATIONAL", maps_uri="https://maps.example/a"),
    DiscoveredPlace(place_id="places/b", name="Bare Place", address=None, phone=None, website=None),
]


def test_rank_button_and_row_data_appear_only_when_configured(app, rep, monkeypatch):
    client, csrf, sid = rep
    _make_profile(app, sid, "E-AI-0")  # a search needs a real employee profile
    patch_providers(monkeypatch)
    off = _search(client, csrf, monkeypatch, PLACES).get_data(as_text=True)
    assert "Acme Pharmacy" in off and "leads-rank" not in off and "Rank with AI" not in off
    patch_providers(monkeypatch, FakeProvider("gemini", trains_on_data=True))
    on = _search(client, csrf, monkeypatch, PLACES).get_data(as_text=True)
    assert "Rank with AI" in on and 'data-rank-url="/leads/discover/rank"' in on and "js/leads-rank.js" in on
    assert 'data-rating="4.6"' in on and 'data-reviews="210"' in on and 'data-types="pharmacy|store"' in on
    assert 'data-has-phone="1"' in on and 'data-has-phone="0"' in on
    # The phone number is NOT in any data-* attribute (the script reads only those).
    assert not re.search(r'data-[a-z-]+="[^"]*962791234567', on)


def test_import_saves_the_ai_reason_and_opener_once_per_lead(app, rep, monkeypatch):
    client, csrf, sid = rep
    _make_profile(app, sid, "E-AI-1")
    form = {
        "csrf_token": csrf, "count": "1", "priority": "MEDIUM", "select-0": "on", "place_id-0": "places/a", "name-0": "Acme Pharmacy",
        "phone-0": PHONE, "address-0": "1 Main", "ai_score-0": "88", "ai_reason-0": "Busy pharmacy with many reviews",
        "ai_opener-0": "مرحبا، نقدم لكم Aura Retail",
    }
    assert client.post("/leads/discover/import", data=form).status_code == 302
    with app.app_context():
        lead = db_session.execute(select(Lead).where(Lead.organization_or_prospect_name == "Acme Pharmacy")).scalars().one()
        assert lead.phone == PHONE
        notes = db_session.execute(select(LeadNote).where(LeadNote.lead_id == lead.id)).scalars().all()
        assert len(notes) == 1
        assert "88/100" in notes[0].body and "Busy pharmacy" in notes[0].body and "Aura Retail" in notes[0].body
    # Re-importing the same place must not stack a second note.
    assert client.post("/leads/discover/import", data=form).status_code == 302
    with app.app_context():
        assert len(db_session.execute(select(LeadNote)).scalars().all()) == 1


def test_import_without_ai_fields_adds_no_note_and_oversized_text_is_cut(app, rep):
    client, csrf, sid = rep
    _make_profile(app, sid, "E-AI-2")
    base = {"csrf_token": csrf, "count": "2", "priority": "MEDIUM", "select-0": "on", "place_id-0": "places/x", "name-0": "Plain",
            "phone-0": PHONE, "select-1": "on", "place_id-1": "places/y", "name-1": "Long", "phone-1": "+962790000000",
            "ai_reason-1": "R" * 2000, "ai_opener-1": "O" * 2000}
    assert client.post("/leads/discover/import", data=base).status_code == 302
    with app.app_context():
        plain = db_session.execute(select(Lead).where(Lead.organization_or_prospect_name == "Plain")).scalars().one()
        assert db_session.execute(select(LeadNote).where(LeadNote.lead_id == plain.id)).scalars().all() == []
        long_lead = db_session.execute(select(Lead).where(Lead.organization_or_prospect_name == "Long")).scalars().one()
        (note,) = db_session.execute(select(LeadNote).where(LeadNote.lead_id == long_lead.id)).scalars().all()
        assert "R" * 301 not in note.body and "O" * 401 not in note.body


def test_places_provider_now_asks_for_and_parses_the_ranking_fields():
    from app.leads import discovery

    for field in ("places.rating", "places.userRatingCount", "places.types", "places.businessStatus", "places.googleMapsUri"):
        assert field in discovery._FIELD_MASK
    body = json.dumps({"places": [{"id": "p1", "displayName": {"text": "A"}, "rating": 4.2, "userRatingCount": 17,
                                   "types": ["store", 5], "businessStatus": "OPERATIONAL", "googleMapsUri": "https://m"},
                                  {"id": "p2", "displayName": {"text": "B"}, "rating": "high", "userRatingCount": True}]}).encode()
    provider = discovery.GooglePlacesTextSearchProvider("k", http_post=lambda *a: (200, body))
    a, b = provider.search("shops", "Amman")
    assert (a.rating, a.user_rating_count, a.types, a.business_status, a.maps_uri) == (4.2, 17, ("store",), "OPERATIONAL", "https://m")
    assert (b.rating, b.user_rating_count, b.types) == (None, None, ())
