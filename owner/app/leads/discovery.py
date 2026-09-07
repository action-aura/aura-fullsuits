"""Lead discovery -- Google Places (New) Text Search integration.

A sales rep types a segment ("pharmacies") and an area ("Irbid, Jordan")
and gets back real businesses with real phone numbers, so they stop doing
this by hand on Google Maps. Two decisions here are load-bearing, not
stylistic:

1. This module returns ONLY what Google Places itself reports -- never a
   synthesized or "best guess" contact. It would be trivial to backfill a
   missing phone number with an LLM call, and just as trivial for that LLM
   to fabricate a plausible-looking one. A sales team dialling a fabricated
   number is strictly worse than a sales team seeing `phone: None` and
   knowing to skip it -- so `phone` is nullable all the way through
   (DiscoveredPlace, the parsed API response, the eventual CRM import) and
   nothing downstream is allowed to paper over that gap.
2. The feature is OFF unless BOTH `OWNER_LEAD_DISCOVERY_PROVIDER` and
   `OWNER_GOOGLE_PLACES_API_KEY` are set (is_discovery_enabled()) --
   costing nothing to an install that never opts in, the same
   invisible-unless-enabled posture as e-invoicing
   (commercial_runtime/einvoicing) and license enforcement. build_provider()
   raises NOT_CONFIGURED rather than silently returning a no-op provider,
   so a caller can't accidentally ship a route that "works" by always
   returning an empty list.

MAX_RESULTS=20 is both the Places Text Search `pageSize` ceiling Google
documents and a deliberate cost bound on this module's side: each search
is a billed Places API call, and nothing here should let a caller (or a
future route bug) balloon that into requesting hundreds of results per
keystroke.

Uses ONLY the standard library (`urllib.request`) for the HTTP call --
`requests` is not in requirements/owner-server.txt and this feature isn't
worth adding a new production dependency for one POST call. The transport
is injected (`http_post`) so tests never make a real network call; see
owner/tests/test_lead_discovery_provider.py.

The API key is never allowed into an exception message or `.detail` --
see GooglePlacesTextSearchProvider._safe_detail(). A leaked key in a log
line or an error banner shown to a sales rep is a real credential leak,
not a cosmetic bug.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol

_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.nationalPhoneNumber,places.internationalPhoneNumber,places.websiteUri"
)
_MAX_QUERY_LEN = 120
# How much of a raw error/response body to surface in a LeadDiscoveryError's
# detail -- long enough to be useful, short enough that a runaway HTML error
# page from a misconfigured proxy never becomes the whole message.
_MAX_DETAIL_LEN = 300

MAX_RESULTS = 20  # Places Text Search pageSize ceiling; also a cost bound.


@dataclass(frozen=True)
class DiscoveredPlace:
    place_id: str
    name: str
    address: str | None
    phone: str | None  # internationalPhoneNumber if present, else nationalPhoneNumber, else None
    website: str | None


class LeadDiscoveryError(Exception):
    """code is one of: NOT_CONFIGURED, INVALID_QUERY, PROVIDER_ERROR, RATE_LIMITED"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class LeadDiscoveryProvider(Protocol):
    def search(self, segment: str, area: str, *, limit: int = 20) -> list[DiscoveredPlace]: ...


def _urllib_http_post(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    """Default transport -- stdlib only, no `requests`.

    An HTTPError is a normal (non-2xx) HTTP response with a readable body
    (Google returns structured JSON error detail on 4xx/5xx) so it's
    unpacked into the same (status, body) shape as a success -- the status
    mapping in search() decides what it means. A URLError (DNS failure,
    connection refused, TLS failure) or a socket timeout is a genuine
    transport failure, not a response, so it's left to propagate: search()
    catches any transport exception -- from this default transport or an
    injected one -- in exactly one place and maps it to PROVIDER_ERROR,
    so the sanitization in that one place is the only place that has to be
    right.
    """
    # urlopen also speaks file:// and ftp://; the only URL this transport is
    # ever handed is the provider's https endpoint, so refuse anything else
    # here rather than trust every future caller. The `nosec B310` below is
    # bandit's "audit url open for permitted schemes" finding (CI's bandit
    # gate, 2026-09-07): the scheme is pinned right here, so it is audited.
    if not url.lower().startswith("https://"):
        raise ValueError(f"lead discovery transport only speaks https, got scheme of {url[:12]!r}")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            return response.getcode(), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class GooglePlacesTextSearchProvider:
    def __init__(self, api_key: str, *, timeout_seconds: float = 10.0, http_post: Callable | None = None):
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._http_post = http_post or _urllib_http_post

    def _safe_detail(self, message: str) -> str:
        """Never let the API key reach a caller, even indirectly (an
        injected/third-party transport that echoes its request back into an
        exception message, a verbose provider error body, etc.)."""
        message = (message or "").strip()
        if self._api_key and self._api_key in message:
            message = message.replace(self._api_key, "[REDACTED]")
        return message[:_MAX_DETAIL_LEN]

    def search(self, segment: str, area: str, *, limit: int = MAX_RESULTS) -> list[DiscoveredPlace]:
        segment = (segment or "").strip()
        area = (area or "").strip()
        if not segment or not area or len(segment) > _MAX_QUERY_LEN or len(area) > _MAX_QUERY_LEN:
            raise LeadDiscoveryError(
                "INVALID_QUERY", detail=f"segment and area are required and must each be at most {_MAX_QUERY_LEN} characters"
            )
        # Clamp rather than reject -- a caller passing limit=0 or limit=500
        # is almost certainly a bug upstream, not a request this feature
        # should refuse outright; MAX_RESULTS is the real cost ceiling.
        limit = max(1, min(limit, MAX_RESULTS))

        body = json.dumps({"textQuery": f"{segment} in {area}", "pageSize": limit}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": _FIELD_MASK,
        }

        try:
            status, response_body = self._http_post(_SEARCH_URL, headers, body, self._timeout_seconds)
        except LeadDiscoveryError:
            raise
        except Exception as exc:  # noqa: BLE001 -- any transport failure becomes PROVIDER_ERROR, never propagates raw
            raise LeadDiscoveryError("PROVIDER_ERROR", detail=self._safe_detail(str(exc))) from exc

        if status == 429:
            raise LeadDiscoveryError("RATE_LIMITED", detail=self._safe_detail(_decode(response_body)))
        if status == 400:
            raise LeadDiscoveryError("INVALID_QUERY", detail=self._safe_detail(_decode(response_body)))
        if status != 200:
            raise LeadDiscoveryError("PROVIDER_ERROR", detail=self._safe_detail(_decode(response_body)))

        try:
            payload = json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LeadDiscoveryError("PROVIDER_ERROR", detail="malformed response body") from exc

        places = payload.get("places") if isinstance(payload, dict) else None
        if not isinstance(places, list):
            return []

        results: list[DiscoveredPlace] = []
        for place in places:
            if not isinstance(place, dict):
                continue
            place_id = place.get("id")
            display_name = place.get("displayName")
            name = display_name.get("text") if isinstance(display_name, dict) else None
            # Skip rather than raise -- one malformed entry in a page of 20
            # real results shouldn't cost the rep the other 19.
            if not place_id or not name:
                continue
            results.append(
                DiscoveredPlace(
                    place_id=place_id,
                    name=name,
                    address=place.get("formattedAddress"),
                    phone=place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber") or None,
                    website=place.get("websiteUri"),
                )
            )
        return results[:limit]


def _decode(response_body: bytes) -> str:
    try:
        return response_body.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 -- decoding a diagnostic string must never itself raise
        return ""


def is_discovery_enabled(config) -> bool:
    """`config` is a Flask app.config (a Mapping) -- always accessed via
    .get(), never subscripted, so a deployment that hasn't set these keys
    at all (rather than set them empty) is handled identically."""
    provider = config.get("LEAD_DISCOVERY_PROVIDER")
    api_key = config.get("GOOGLE_PLACES_API_KEY")
    return provider == "google_places" and isinstance(api_key, str) and bool(api_key)


def build_provider(config) -> LeadDiscoveryProvider:
    if not is_discovery_enabled(config):
        raise LeadDiscoveryError("NOT_CONFIGURED", detail="lead discovery is not configured for this deployment")
    provider_name = config.get("LEAD_DISCOVERY_PROVIDER")
    if provider_name == "google_places":
        return GooglePlacesTextSearchProvider(
            config.get("GOOGLE_PLACES_API_KEY"),
            timeout_seconds=config.get("LEAD_DISCOVERY_TIMEOUT_SECONDS", 10.0),
        )
    # Unreachable while is_discovery_enabled() only recognizes
    # "google_places", but kept explicit rather than falling through to an
    # AttributeError if a future provider name is added to one function and
    # not the other.
    raise LeadDiscoveryError("NOT_CONFIGURED", detail=f"unknown lead discovery provider: {provider_name}")
