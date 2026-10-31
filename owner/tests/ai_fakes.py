"""Shared fakes for the AI brain tests. No test in the AI suite touches the
network: providers are either these fakes or the real provider classes driven
by an injected client / transport."""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.ai.providers import AIError, AIResult

REPORT = {
    "headline": "Sales are up 12% on last month",
    "performance": ["Revenue 1200.000 JOD"],
    "trends": ["Weekends are your strongest days"],
    "customers": ["Repeat buyers are a third of sales"],
    "suggestions": [{"title": "Restock olive oil", "why": "It sold out twice", "action": "Order 20 bottles Monday"}],
    "deep_dive": ["Tea outsells coffee 2 to 1"],
    "warnings": [],
}


class FakeProvider:
    """Stands in for a provider. `reply` is an AIResult-producing callable or an
    AIError to raise. `calls` records every request so a test can assert BOTH
    that a provider was (not) called and exactly what it was sent."""

    def __init__(self, name, *, trains_on_data=False, model="fake-model", data=None, text="ok", error=None):
        self.name = name
        self.trains_on_data = trains_on_data
        self.model = model
        self._data = data
        self._text = text
        self._error = error
        self.calls: list[dict] = []

    def complete(self, *, system, messages, max_tokens, json_schema):
        self.calls.append({"system": system, "messages": messages, "max_tokens": max_tokens, "json_schema": json_schema})
        if self._error is not None:
            raise self._error
        data = self._data if json_schema is not None else None
        text = json.dumps(data) if data is not None else self._text
        return AIResult(text=text, data=data, input_tokens=11, output_tokens=7, provider=self.name, model=self.model)


def error(code, detail=""):
    return AIError(code, detail)


def patch_providers(monkeypatch, *providers):
    """Make app.ai.providers.build_providers return exactly these fakes, keyed
    by name -- every code path (brain.run, brain.preflight, is_available) looks
    it up at call time, so this swaps the brain for the whole app."""
    table = {p.name: p for p in providers}
    monkeypatch.setattr("app.ai.providers.build_providers", lambda config: table)
    return table


def anthropic_response(text, *, stop_reason="end_turn", input_tokens=5, output_tokens=3):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class FakeAnthropicClient:
    def __init__(self, response=None, raises=None):
        self.requests: list[dict] = []
        self._response = response
        self._raises = raises
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._response
