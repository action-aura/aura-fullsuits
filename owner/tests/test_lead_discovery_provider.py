"""Pure unit tests for app.leads.discovery -- NO app/client/seeded fixtures,
NO database. The test database serializes on one advisory lock; a
DB-touching test in this file would contend with another agent's
concurrently-running DB-backed test suite for the same feature. Every HTTP
call is faked via the injectable `http_post` transport.
"""
from __future__ import annotations

import inspect
import json

import pytest

from app.leads import discovery as discovery_module
from app.leads.discovery import (
    MAX_RESULTS,
    DiscoveredPlace,
    GooglePlacesTextSearchProvider,
    LeadDiscoveryError,
    build_provider,
    is_discovery_enabled,
)


def _response(payload: dict, status: int = 200) -> tuple[int, bytes]:
    return status, json.dumps(payload).encode("utf-8")


def _place(place_id="p1", name="Acme Pharmacy", address="123 Main St", international=None, national=None, website=None):
    entry: dict = {"id": place_id, "displayName": {"text": name}}
    if address is not None:
        entry["formattedAddress"] = address
    if international is not None:
        entry["internationalPhoneNumber"] = international
    if national is not None:
        entry["nationalPhoneNumber"] = national
    if website is not None:
        entry["websiteUri"] = website
    return entry


def test_module_never_imports_requests():
    """Production owner-server.txt does not include `requests` -- the
    default transport must be reachable with the stdlib alone."""
    source = inspect.getsource(discovery_module)
    assert "import requests" not in source
    assert "requests." not in source


# -- (a) realistic response parsing --------------------------------------


def test_parses_realistic_response_preferring_international_phone_and_preserving_order():
    payload = {
        "places": [
            _place(place_id="p1", name="Alpha Pharmacy", address="Street 1",
                   international="+962700000001", national="0700000001", website="https://alpha.example"),
            _place(place_id="p2", name="Beta Pharmacy", address="Street 2",
                   international="+962700000002", national="0700000002", website="https://beta.example"),
        ]
    }
    provider = GooglePlacesTextSearchProvider("key-1", http_post=lambda *a, **k: _response(payload))

    results = provider.search("pharmacies", "Irbid, Jordan")

    assert results == [
        DiscoveredPlace("p1", "Alpha Pharmacy", "Street 1", "+962700000001", "https://alpha.example"),
        DiscoveredPlace("p2", "Beta Pharmacy", "Street 2", "+962700000002", "https://beta.example"),
    ]


# -- (b) phone fallback / absence -----------------------------------------


def test_falls_back_to_national_phone_when_international_absent_and_returns_none_when_both_absent():
    payload = {
        "places": [
            _place(place_id="p1", name="National Only", national="0700000001"),
            _place(place_id="p2", name="No Phone At All"),
        ]
    }
    provider = GooglePlacesTextSearchProvider("key-1", http_post=lambda *a, **k: _response(payload))

    results = provider.search("pharmacies", "Irbid")

    assert len(results) == 2  # entry with no phone at all is still returned
    assert results[0].phone == "0700000001"
    assert results[1].phone is None


# -- (c) missing id/displayName skipped -----------------------------------


def test_skips_entries_missing_id_or_display_name():
    payload = {
        "places": [
            {"displayName": {"text": "No Id"}},
            {"id": "p2"},
            {"id": "p3", "displayName": {"text": "Valid Place"}},
        ]
    }
    provider = GooglePlacesTextSearchProvider("key-1", http_post=lambda *a, **k: _response(payload))

    results = provider.search("pharmacies", "Irbid")

    assert [r.place_id for r in results] == ["p3"]


# -- (d) request shape -----------------------------------------------------


def test_request_shape_url_headers_and_body():
    captured: dict = {}

    def fake_post(url, headers, body, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json.loads(body)
        captured["timeout"] = timeout
        return _response({"places": []})

    provider = GooglePlacesTextSearchProvider("SECRET-KEY", http_post=fake_post, timeout_seconds=7.5)

    provider.search("pharmacies", "Irbid, Jordan", limit=5)

    assert captured["url"] == "https://places.googleapis.com/v1/places:searchText"
    assert captured["headers"]["X-Goog-Api-Key"] == "SECRET-KEY"
    assert captured["headers"]["Content-Type"] == "application/json"
    field_mask = captured["headers"]["X-Goog-FieldMask"]
    for field in (
        "places.id", "places.displayName", "places.formattedAddress",
        "places.nationalPhoneNumber", "places.internationalPhoneNumber", "places.websiteUri",
    ):
        assert field in field_mask
    assert captured["body"]["textQuery"] == "pharmacies in Irbid, Jordan"
    assert captured["body"]["pageSize"] == 5
    assert captured["timeout"] == 7.5


# -- (e) limit clamping / truncation ----------------------------------------


def test_limit_is_clamped_to_max_results_and_results_truncated():
    captured: dict = {}
    many_places = {"places": [_place(place_id=f"p{i}", name=f"Place {i}") for i in range(MAX_RESULTS + 5)]}

    def fake_post(url, headers, body, timeout):
        captured["body"] = json.loads(body)
        return _response(many_places)

    provider = GooglePlacesTextSearchProvider("key-1", http_post=fake_post)

    results = provider.search("pharmacies", "Irbid", limit=MAX_RESULTS + 100)

    assert captured["body"]["pageSize"] == MAX_RESULTS
    assert len(results) == MAX_RESULTS


# -- (f) status code mapping -------------------------------------------------


@pytest.mark.parametrize(
    "status,expected_code",
    [(429, "RATE_LIMITED"), (400, "INVALID_QUERY"), (500, "PROVIDER_ERROR")],
)
def test_status_codes_map_to_expected_error_codes(status, expected_code):
    provider = GooglePlacesTextSearchProvider("key-1", http_post=lambda *a, **k: (status, b'{"error": "boom"}'))

    with pytest.raises(LeadDiscoveryError) as exc_info:
        provider.search("pharmacies", "Irbid")

    assert exc_info.value.code == expected_code


def test_non_json_200_body_is_provider_error():
    provider = GooglePlacesTextSearchProvider("key-1", http_post=lambda *a, **k: (200, b"not json at all"))

    with pytest.raises(LeadDiscoveryError) as exc_info:
        provider.search("pharmacies", "Irbid")

    assert exc_info.value.code == "PROVIDER_ERROR"


# -- (g) transport exception never leaks the api key -------------------------


def test_transport_exception_becomes_provider_error_without_leaking_the_api_key():
    api_key = "SECRET-KEY-DO-NOT-LEAK"

    def raising_post(url, headers, body, timeout):
        raise ConnectionError(f"failed to reach host while using key {api_key}")

    provider = GooglePlacesTextSearchProvider(api_key, http_post=raising_post)

    with pytest.raises(LeadDiscoveryError) as exc_info:
        provider.search("pharmacies", "Irbid")

    assert exc_info.value.code == "PROVIDER_ERROR"
    assert api_key not in str(exc_info.value)
    assert api_key not in exc_info.value.detail


# -- (h) invalid query rejected before any transport call ---------------------


@pytest.mark.parametrize(
    "segment,area",
    [
        ("", "Irbid"),
        ("   ", "Irbid"),
        ("pharmacies", ""),
        ("pharmacies", "   "),
        ("p" * 121, "Irbid"),
        ("pharmacies", "a" * 121),
    ],
)
def test_invalid_query_rejected_without_calling_transport(segment, area):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(args)
        return _response({"places": []})

    provider = GooglePlacesTextSearchProvider("key-1", http_post=fake_post)

    with pytest.raises(LeadDiscoveryError) as exc_info:
        provider.search(segment, area)

    assert exc_info.value.code == "INVALID_QUERY"
    assert calls == []


# -- (i) is_discovery_enabled / build_provider --------------------------------


def test_is_discovery_enabled_false_for_empty_config():
    assert is_discovery_enabled({}) is False


def test_is_discovery_enabled_false_when_provider_set_but_key_empty():
    config = {"LEAD_DISCOVERY_PROVIDER": "google_places", "GOOGLE_PLACES_API_KEY": ""}
    assert is_discovery_enabled(config) is False


def test_is_discovery_enabled_true_when_both_set():
    config = {"LEAD_DISCOVERY_PROVIDER": "google_places", "GOOGLE_PLACES_API_KEY": "key-1"}
    assert is_discovery_enabled(config) is True


def test_build_provider_raises_not_configured_when_disabled():
    with pytest.raises(LeadDiscoveryError) as exc_info:
        build_provider({})
    assert exc_info.value.code == "NOT_CONFIGURED"


def test_build_provider_returns_google_places_provider_when_enabled():
    config = {"LEAD_DISCOVERY_PROVIDER": "google_places", "GOOGLE_PLACES_API_KEY": "key-1"}

    provider = build_provider(config)

    assert isinstance(provider, GooglePlacesTextSearchProvider)
