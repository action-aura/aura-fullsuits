"""Outbound WhatsApp -- WhatsApp Business Cloud API (Meta Graph API) transport.

Same shape as smtp_client.py deliberately -- see that module's docstring for
the pattern this mirrors. No new dependency: `requests` is already a base
requirement (used elsewhere for e-invoicing/licensing HTTP calls).

Configured ENTIRELY via env vars, all empty-by-default:

    AURA_WHATSAPP_PHONE_NUMBER_ID  -- unset by default. This is the single
                                      gate that keeps this transport inert
                                      (mirrors AURA_SMTP_HOST's role for
                                      email) -- there is no safe shared demo
                                      credential for somebody else's WhatsApp
                                      Business number, so this must default
                                      to nothing.
    AURA_WHATSAPP_ACCESS_TOKEN     -- the System User permanent token from
                                      Meta Business Manager (WhatsApp product
                                      -> System User -> permanent token).
    AURA_WHATSAPP_API_VERSION      -- default 'v20.0'.

Real Cloud API constraint this module respects on purpose: outside an active
24-hour customer-service window, Meta only allows sending a pre-approved
MESSAGE TEMPLATE, never arbitrary free text -- a proactive alert (low stock,
on-demand report) is always a business-initiated message, so
send_whatsapp_template_message() is the only send path here. A free-text
session-reply path is a real, different API shape (type: "text", no template
approval needed, only valid within that 24h window) -- deliberately not
built here since there is no current caller for it; add it if/when an
inbound-reply use case exists, per this codebase's "do not invent
unsupported types" convention (see app/commercial_sales/fulfillment.py's own
docstring for the same principle applied elsewhere).

Template name/language/component structure must match a template already
created and approved in Meta Business Manager -- this module cannot create
or approve templates, only send using one that already exists.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import requests

_DEFAULT_API_VERSION = 'v20.0'
_DEFAULT_TIMEOUT_SECONDS = 15.0
_GRAPH_API_BASE = 'https://graph.facebook.com'


class WhatsAppNotConfiguredError(RuntimeError):
    """Raised by send_whatsapp_template_message() when called with no
    AURA_WHATSAPP_PHONE_NUMBER_ID set and no explicit config override --
    callers must never let this escape as an unhandled crash; it is the
    expected, common-case outcome on any install that hasn't opted in."""


class WhatsAppSendError(RuntimeError):
    """Raised when the Graph API itself rejects the send (bad token, unknown
    template, recipient not opted in, etc.) -- carries the real API error
    body so a retry/failed-permanent decision can be made on the actual
    reason, not just a generic HTTP status."""

    def __init__(self, status_code: int, api_error: dict):
        self.status_code = status_code
        self.api_error = api_error
        message = api_error.get('message', 'unknown WhatsApp API error')
        code = api_error.get('code')
        super().__init__(f"WhatsApp API error {code}: {message}")


@dataclass(frozen=True)
class WhatsAppConfig:
    phone_number_id: str
    access_token: str
    api_version: str


def load_config_from_env() -> Optional[WhatsAppConfig]:
    """Returns None -- not a config with empty fields -- when
    AURA_WHATSAPP_PHONE_NUMBER_ID is unset/blank. That None is the
    load-bearing signal is_configured() keys off; never change this to
    return a "disabled" WhatsAppConfig instead, or every caller that checks
    `is None` silently breaks (same rule as smtp_client.py's own
    load_config_from_env)."""
    phone_number_id = os.environ.get('AURA_WHATSAPP_PHONE_NUMBER_ID', '').strip()
    if not phone_number_id:
        return None
    return WhatsAppConfig(
        phone_number_id=phone_number_id,
        access_token=os.environ.get('AURA_WHATSAPP_ACCESS_TOKEN', '').strip(),
        api_version=os.environ.get('AURA_WHATSAPP_API_VERSION', '').strip() or _DEFAULT_API_VERSION,
    )


def is_configured() -> bool:
    return load_config_from_env() is not None


def build_template_payload(*, recipient_phone_e164: str, template_name: str,
                            language_code: str, component_params: Optional[list[str]] = None) -> dict:
    """component_params is the ordered list of {{1}}, {{2}}, ... body
    placeholder values for the template -- None/empty for a template with no
    variable placeholders. Header/button components are out of scope here;
    add them when a real template needs one."""
    payload = {
        'messaging_product': 'whatsapp',
        'to': recipient_phone_e164,
        'type': 'template',
        'template': {
            'name': template_name,
            'language': {'code': language_code},
        },
    }
    if component_params:
        payload['template']['components'] = [{
            'type': 'body',
            'parameters': [{'type': 'text', 'text': p} for p in component_params],
        }]
    return payload


def send_whatsapp_template_message(
    *,
    recipient_phone_e164: str,
    template_name: str,
    language_code: str = 'en_US',
    component_params: Optional[list[str]] = None,
    config: Optional[WhatsAppConfig] = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Sends a pre-approved WhatsApp message template. Returns the WhatsApp
    message id (wamid...) on success.

    Raises on any failure (WhatsAppNotConfiguredError, WhatsAppSendError,
    requests.RequestException) -- never returns a sentinel/False. The
    caller is the single place that should catch this and translate it into
    a retry/failed-permanent transition, mirroring smtp_client.send_email's
    contract."""
    cfg = config or load_config_from_env()
    if cfg is None:
        raise WhatsAppNotConfiguredError(
            "AURA_WHATSAPP_PHONE_NUMBER_ID is not set -- outbound WhatsApp is not configured on this installation."
        )

    url = f"{_GRAPH_API_BASE}/{cfg.api_version}/{cfg.phone_number_id}/messages"
    payload = build_template_payload(
        recipient_phone_e164=recipient_phone_e164, template_name=template_name,
        language_code=language_code, component_params=component_params,
    )
    response = requests.post(
        url, json=payload,
        headers={'Authorization': f'Bearer {cfg.access_token}', 'Content-Type': 'application/json'},
        timeout=timeout_seconds,
    )
    body = response.json() if response.content else {}
    if response.status_code >= 400 or 'error' in body:
        raise WhatsAppSendError(response.status_code, body.get('error', {}))
    return body['messages'][0]['id']
