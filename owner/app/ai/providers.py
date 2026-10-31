"""AI providers -- one narrow interface over Claude (official SDK) and Gemini
(plain REST), so which brain answers is a CONFIGURATION choice, never a code
change (docs/superpowers/specs/2026-10-31-ai-brain-design.md, decision 3).

Why this module is shaped the way it is -- each point is load-bearing:

1. `trains_on_data` is a property of the PROVIDER INSTANCE, not of its name.
   Gemini's free tier may use prompts for training and let humans read them;
   its paid tier does not. The router (app/ai/brain.py) reads this flag to
   keep private shop data away from a training provider (hard rule R1), so
   the flag must come from configuration (OWNER_GEMINI_PAID_TIER) and default
   to the safe answer. Claude's API does not train on API data.
2. The AI key never reaches an exception message, a `.detail`, or a log line
   (hard rule R2 and discovery.py's `_safe_detail` lesson). For Claude we do
   not even forward the SDK's message text -- only the HTTP status and the
   exception class name -- because an SDK error string is exactly the kind of
   place a header echo ends up. For Gemini we redact the key out of whatever
   a transport raised, and the key travels in a header, never the URL (a URL
   lands in access logs and in `urllib` error strings).
3. Output is NEVER trusted because the provider promised a schema. Both
   providers are asked for JSON, and both results are then validated here
   with `jsonschema`; a mismatch is AI_BAD_OUTPUT, not a half-parsed dict
   handed to a caller. The schemas deliberately have no phone / price field
   (rule R3), so there is nothing for the model to fabricate into one.
4. Transports are injectable (`client=` for the SDK, `http_post=` for Gemini),
   copying discovery.py's `http_post` style, so no test ever touches the
   network.
5. Only two failure codes mean "try the next provider": AI_RATE_LIMITED and
   AI_UPSTREAM_ERROR. Everything else (bad output, refusal, privacy) is a
   property of the REQUEST or of the answer, and re-asking another model
   would just spend money on the same problem; brain.py enforces that.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.parse import quote

import jsonschema

try:  # The SDK is a hard requirement of the Owner server image, but a dev box
    import anthropic  # without it must still boot: the provider just reports
except ImportError:  # pragma: no cover -- exercised only on a box lacking the SDK
    anthropic = None  # "not configured" (see AnthropicProvider.complete).

logger = logging.getLogger(__name__)

# Stable error codes (the spec's list). `code` is what callers branch on.
AI_NOT_CONFIGURED = "AI_NOT_CONFIGURED"
AI_RATE_LIMITED = "AI_RATE_LIMITED"
AI_UPSTREAM_ERROR = "AI_UPSTREAM_ERROR"
AI_BAD_OUTPUT = "AI_BAD_OUTPUT"
AI_PRIVACY_REFUSED = "AI_PRIVACY_REFUSED"
AI_BUDGET_EXHAUSTED = "AI_BUDGET_EXHAUSTED"

# The only two codes that justify asking the NEXT provider in a chain.
FALL_THROUGH_CODES = frozenset({AI_RATE_LIMITED, AI_UPSTREAM_ERROR})

_MAX_DETAIL_LEN = 300
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Keywords Gemini's `responseSchema` (an OpenAPI-style subset) understands.
# Anything else in our JSON Schema (additionalProperties, maxLength, ...) is
# dropped from the copy SENT to Gemini; the full schema still validates the
# reply locally, so dropping a keyword weakens the request, never the check.
_GEMINI_SCHEMA_KEYS = frozenset(
    {"type", "properties", "required", "items", "enum", "description", "format", "minItems", "maxItems",
     "minimum", "maximum", "nullable", "anyOf"}
)


class AIError(Exception):
    """code is one of the AI_* constants above. `detail` is for diagnosis and
    is guaranteed key-free (see module docstring, point 2)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class AIResult:
    text: str
    data: dict | None
    input_tokens: int
    output_tokens: int
    provider: str
    model: str


class AIProvider(Protocol):
    name: str
    trains_on_data: bool
    model: str

    def complete(
        self, *, system: str, messages: list[dict], max_tokens: int, json_schema: dict | None
    ) -> AIResult: ...


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def parse_and_validate(text: str, json_schema: dict) -> dict:
    """Parse `text` as JSON and check it against `json_schema` ourselves.
    A model that was asked for JSON still occasionally wraps it in a code
    fence or drifts from the schema; either way the caller gets a clean
    AI_BAD_OUTPUT, never a partial object."""
    candidate = (text or "").strip()
    fenced = _FENCE_RE.match(candidate)
    if fenced:
        candidate = fenced.group(1)
    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        raise AIError(AI_BAD_OUTPUT, detail="the model's reply was not valid JSON") from None
    try:
        jsonschema.validate(data, json_schema)
    except jsonschema.ValidationError as exc:
        # exc.message can quote model output; the path is enough to debug.
        path = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        raise AIError(AI_BAD_OUTPUT, detail=f"the model's reply did not match the schema at {path}") from None
    return data


def _gemini_schema(schema):
    """A copy of `schema` limited to what Gemini's responseSchema accepts."""
    if isinstance(schema, dict):
        out = {}
        for key, value in schema.items():
            if key not in _GEMINI_SCHEMA_KEYS:
                continue
            if key == "properties" and isinstance(value, dict):
                out[key] = {name: _gemini_schema(sub) for name, sub in value.items()}
            elif key == "type" and isinstance(value, str):
                # The Schema object's Type enum is upper-case on the wire
                # ("OBJECT", "STRING") -- that is what Google's own REST
                # example for generateContent sends. Lower-case is what the
                # JS SDK accepts and maps; the raw REST call has no SDK in
                # between, so it gets the enum spelling.
                out[key] = value.upper()
            else:
                out[key] = _gemini_schema(value)
        return out
    if isinstance(schema, list):
        return [_gemini_schema(item) for item in schema]
    return schema


class AnthropicProvider:
    """Claude through the official `anthropic` SDK.

    Haiku 5.5 thinks adaptively by default; `output_config.effort="low"` keeps
    a report summary cheap -- summarising a snapshot is not hard reasoning.
    No temperature/top_p/top_k and no `thinking` parameter are sent: current
    Claude models reject (or ignore) them, and the defaults are what we want.
    """

    name = "anthropic"
    trains_on_data = False

    def __init__(self, api_key: str, *, model: str, timeout_seconds: float = 40.0, client=None):
        self._api_key = api_key
        self.model = model
        self._timeout_seconds = timeout_seconds
        self._client = client

    def _get_client(self):
        if self._client is None:
            if anthropic is None:
                raise AIError(AI_NOT_CONFIGURED, detail="the anthropic package is not installed")
            # max_retries=1: the SDK already retries 429/5xx with backoff; one
            # retry keeps a request inside a worker's patience, and the brain
            # chain (not the SDK) decides whether to try another provider.
            self._client = anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout_seconds, max_retries=1)
        return self._client

    def complete(self, *, system, messages, max_tokens, json_schema) -> AIResult:
        client = self._get_client()
        output_config: dict = {"effort": "low"}
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                output_config=output_config,
            )
        except AIError:
            raise
        except Exception as exc:  # noqa: BLE001 -- every SDK/transport failure is mapped; see below
            raise self._map_exception(exc) from None

        # stop_reason BEFORE content: a refusal or a truncated reply has no
        # usable body, and reading it as an answer is how a half-sentence
        # becomes advice.
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            raise AIError(AI_BAD_OUTPUT, detail="the model declined to answer")
        if stop_reason == "max_tokens":
            raise AIError(AI_BAD_OUTPUT, detail="the model's reply was cut off (max_tokens)")
        text = "".join(block.text for block in (response.content or []) if getattr(block, "type", None) == "text")
        if not text.strip():
            raise AIError(AI_BAD_OUTPUT, detail="the model returned no text")
        usage = getattr(response, "usage", None)
        data = parse_and_validate(text, json_schema) if json_schema is not None else None
        return AIResult(
            text=text,
            data=data,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            provider=self.name,
            model=self.model,
        )

    def _map_exception(self, exc: Exception) -> AIError:
        # Status + class name only; never str(exc) (module docstring, point 2).
        status = getattr(exc, "status_code", None)
        label = f"{type(exc).__name__}" + (f" (HTTP {status})" if status else "")
        if anthropic is not None and isinstance(exc, anthropic.RateLimitError):
            return AIError(AI_RATE_LIMITED, detail=label)
        if status == 429:
            return AIError(AI_RATE_LIMITED, detail=label)
        # Overloaded (529), 5xx, timeouts, connection failures, and also 401/
        # 403/404 (a bad or revoked key is the operator's problem, and the next
        # provider in the chain may still serve the request).
        return AIError(AI_UPSTREAM_ERROR, detail=label)


def _urllib_http_post(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    """Default Gemini transport -- stdlib only (same reasoning and same
    https-only pin as app/leads/discovery.py's transport). An HTTPError is a
    normal non-2xx response and is unpacked to (status, body); a URLError or
    timeout is a real transport failure and propagates to complete(), which
    maps it in one place."""
    if not url.lower().startswith("https://"):
        raise ValueError(f"AI transport only speaks https, got scheme of {url[:12]!r}")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 -- https pinned above
            return response.getcode(), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class GeminiProvider:
    """Gemini `generateContent` over plain REST. `trains_on_data` is True
    unless the operator declares the key is on a billing-enabled project
    (OWNER_GEMINI_PAID_TIER) -- see module docstring, point 1."""

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        paid_tier: bool = False,
        timeout_seconds: float = 40.0,
        http_post: Callable | None = None,
    ):
        self._api_key = api_key
        self.model = model
        self.trains_on_data = not paid_tier
        self._timeout_seconds = timeout_seconds
        self._http_post = http_post or _urllib_http_post

    def _safe_detail(self, message: str) -> str:
        message = (message or "").strip()
        if self._api_key and self._api_key in message:
            message = message.replace(self._api_key, "[REDACTED]")
        return message[:_MAX_DETAIL_LEN]

    def complete(self, *, system, messages, max_tokens, json_schema) -> AIResult:
        generation_config: dict = {"maxOutputTokens": max_tokens}
        if json_schema is not None:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = _gemini_schema(json_schema)
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in messages
            ],
            "generationConfig": generation_config,
        }
        # Key in a header, never the URL; the model id is percent-encoded so a
        # config typo cannot reshape the path.
        headers = {"Content-Type": "application/json", "x-goog-api-key": self._api_key}
        url = _GEMINI_URL.format(model=quote(self.model, safe=""))
        try:
            status, response_body = self._http_post(
                url, headers, json.dumps(payload).encode("utf-8"), self._timeout_seconds
            )
        except AIError:
            raise
        except Exception as exc:  # noqa: BLE001 -- any transport failure becomes AI_UPSTREAM_ERROR
            # `from None`: the raw exception text is UNSANITIZED (a transport
            # may quote request headers) and a chained __cause__ is printed by
            # every traceback logger.
            raise AIError(AI_UPSTREAM_ERROR, detail=self._safe_detail(str(exc))) from None

        if status == 429:
            raise AIError(AI_RATE_LIMITED, detail="HTTP 429")
        if status != 200:
            raise AIError(AI_UPSTREAM_ERROR, detail=f"HTTP {status}")
        try:
            reply = json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise AIError(AI_UPSTREAM_ERROR, detail="malformed response body") from None
        if not isinstance(reply, dict):
            raise AIError(AI_UPSTREAM_ERROR, detail="malformed response body")

        block_reason = (reply.get("promptFeedback") or {}).get("blockReason")
        if block_reason:
            raise AIError(AI_BAD_OUTPUT, detail=f"the prompt was blocked ({block_reason})")
        candidates = reply.get("candidates") or []
        candidate = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
        finish_reason = candidate.get("finishReason")
        if finish_reason == "MAX_TOKENS":
            raise AIError(AI_BAD_OUTPUT, detail="the model's reply was cut off (MAX_TOKENS)")
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
        if not text.strip():
            raise AIError(AI_BAD_OUTPUT, detail=f"the model returned no text (finishReason={finish_reason})")
        usage = reply.get("usageMetadata") or {}
        data = parse_and_validate(text, json_schema) if json_schema is not None else None
        return AIResult(
            text=text,
            data=data,
            input_tokens=int(usage.get("promptTokenCount") or 0),
            # Thinking tokens are billed as output, so count them as such.
            output_tokens=int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0),
            provider=self.name,
            model=self.model,
        )


def build_providers(config) -> dict[str, AIProvider]:
    """The providers that have a key, by name. A provider with no key is
    simply absent (hard rule R5: invisible unless configured). `config` is a
    Flask app.config; read with .get() so a deployment that never set these
    keys behaves like one that set them empty."""
    out: dict[str, AIProvider] = {}
    timeout = float(config.get("AI_TIMEOUT_SECONDS") or 40.0)
    anthropic_key = config.get("ANTHROPIC_API_KEY")
    if isinstance(anthropic_key, str) and anthropic_key.strip():
        out["anthropic"] = AnthropicProvider(
            anthropic_key.strip(), model=config.get("AI_ANTHROPIC_MODEL") or "claude-haiku-5-5", timeout_seconds=timeout
        )
    gemini_key = config.get("GEMINI_API_KEY")
    if isinstance(gemini_key, str) and gemini_key.strip():
        out["gemini"] = GeminiProvider(
            gemini_key.strip(),
            model=config.get("AI_GEMINI_MODEL") or "gemini-3.8-flash",
            paid_tier=bool(config.get("GEMINI_PAID_TIER")),
            timeout_seconds=timeout,
        )
    return out
