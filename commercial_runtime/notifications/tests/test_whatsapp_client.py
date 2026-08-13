import pytest

from commercial_runtime.notifications import whatsapp_client


class _FakeResponse:
    def __init__(self, status_code, json_body):
        self.status_code = status_code
        self._json_body = json_body
        self.content = b'x' if json_body is not None else b''

    def json(self):
        return self._json_body


def test_load_config_from_env_returns_none_when_phone_number_id_unset(monkeypatch):
    monkeypatch.delenv('AURA_WHATSAPP_PHONE_NUMBER_ID', raising=False)
    assert whatsapp_client.load_config_from_env() is None
    assert whatsapp_client.is_configured() is False


def test_load_config_from_env_returns_none_when_phone_number_id_is_blank(monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '   ')
    assert whatsapp_client.load_config_from_env() is None


def test_load_config_from_env_reads_all_fields(monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '123456789')
    monkeypatch.setenv('AURA_WHATSAPP_ACCESS_TOKEN', 'test-token-abc')
    monkeypatch.setenv('AURA_WHATSAPP_API_VERSION', 'v21.0')

    cfg = whatsapp_client.load_config_from_env()
    assert cfg.phone_number_id == '123456789'
    assert cfg.access_token == 'test-token-abc'
    assert cfg.api_version == 'v21.0'
    assert whatsapp_client.is_configured() is True


def test_api_version_defaults_when_unset(monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '123456789')
    monkeypatch.delenv('AURA_WHATSAPP_API_VERSION', raising=False)
    cfg = whatsapp_client.load_config_from_env()
    assert cfg.api_version == 'v20.0'


def test_build_template_payload_without_params():
    payload = whatsapp_client.build_template_payload(
        recipient_phone_e164='+15551234567', template_name='low_stock_alert', language_code='en_US',
    )
    assert payload == {
        'messaging_product': 'whatsapp',
        'to': '+15551234567',
        'type': 'template',
        'template': {'name': 'low_stock_alert', 'language': {'code': 'en_US'}},
    }


def test_build_template_payload_with_params():
    payload = whatsapp_client.build_template_payload(
        recipient_phone_e164='+15551234567', template_name='low_stock_alert', language_code='en_US',
        component_params=['Widget', '3'],
    )
    assert payload['template']['components'] == [
        {'type': 'body', 'parameters': [{'type': 'text', 'text': 'Widget'}, {'type': 'text', 'text': '3'}]}
    ]


def test_send_raises_not_configured_when_no_phone_number_id(monkeypatch):
    monkeypatch.delenv('AURA_WHATSAPP_PHONE_NUMBER_ID', raising=False)
    with pytest.raises(whatsapp_client.WhatsAppNotConfiguredError):
        whatsapp_client.send_whatsapp_template_message(
            recipient_phone_e164='+15551234567', template_name='low_stock_alert',
        )


def test_send_success_returns_message_id(monkeypatch):
    cfg = whatsapp_client.WhatsAppConfig(
        phone_number_id='123456789', access_token='test-token', api_version='v20.0',
    )
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured['url'] = url
        captured['json'] = json
        captured['headers'] = headers
        captured['timeout'] = timeout
        return _FakeResponse(200, {
            'messaging_product': 'whatsapp',
            'contacts': [{'input': '+15551234567', 'wa_id': '15551234567'}],
            'messages': [{'id': 'wamid.TEST123'}],
        })

    monkeypatch.setattr(whatsapp_client.requests, 'post', fake_post)

    message_id = whatsapp_client.send_whatsapp_template_message(
        recipient_phone_e164='+15551234567', template_name='low_stock_alert',
        language_code='en_US', component_params=['Widget', '3'], config=cfg,
    )

    assert message_id == 'wamid.TEST123'
    assert captured['url'] == 'https://graph.facebook.com/v20.0/123456789/messages'
    assert captured['headers']['Authorization'] == 'Bearer test-token'
    assert captured['json']['to'] == '+15551234567'
    assert captured['json']['template']['name'] == 'low_stock_alert'


def test_send_raises_whatsapp_send_error_on_api_error(monkeypatch):
    cfg = whatsapp_client.WhatsAppConfig(
        phone_number_id='123456789', access_token='bad-token', api_version='v20.0',
    )

    def fake_post(url, json, headers, timeout):
        return _FakeResponse(401, {
            'error': {'message': 'Invalid OAuth access token', 'type': 'OAuthException', 'code': 190},
        })

    monkeypatch.setattr(whatsapp_client.requests, 'post', fake_post)

    with pytest.raises(whatsapp_client.WhatsAppSendError) as exc_info:
        whatsapp_client.send_whatsapp_template_message(
            recipient_phone_e164='+15551234567', template_name='low_stock_alert', config=cfg,
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.api_error['code'] == 190
    assert 'Invalid OAuth access token' in str(exc_info.value)


def test_send_raises_when_network_fails(monkeypatch):
    """No mocked transport at all -- send must raise (requests.RequestException
    via a real connection attempt to an unroutable address), never silently
    succeed or hang. A future worker integration relies on exactly this
    behavior to translate a send failure into a retry, same contract as
    smtp_client.send_email's own unreachable-server test."""
    cfg = whatsapp_client.WhatsAppConfig(
        phone_number_id='123456789', access_token='test-token', api_version='v20.0',
    )

    def fake_post(url, json, headers, timeout):
        import requests as requests_lib
        raise requests_lib.exceptions.ConnectionError("simulated network failure")

    monkeypatch.setattr(whatsapp_client.requests, 'post', fake_post)

    with pytest.raises(Exception):
        whatsapp_client.send_whatsapp_template_message(
            recipient_phone_e164='+15551234567', template_name='low_stock_alert', config=cfg,
        )
