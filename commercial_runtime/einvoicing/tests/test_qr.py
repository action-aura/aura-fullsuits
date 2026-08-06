import base64

from commercial_runtime.einvoicing.qr import (
    qr_for_outbox_row,
    render_qr_png_data_uri,
    render_qr_svg_data_uri,
)


def test_svg_data_uri_shape():
    uri = render_qr_svg_data_uri('hello')
    assert uri.startswith('data:image/svg+xml;base64,')
    decoded = base64.b64decode(uri.split(',', 1)[1])
    assert b'<svg' in decoded


def test_png_data_uri_shape():
    uri = render_qr_png_data_uri('hello')
    assert uri.startswith('data:image/png;base64,')
    decoded = base64.b64decode(uri.split(',', 1)[1])
    assert decoded[:8] == b'\x89PNG\r\n\x1a\n'  # PNG magic bytes


def test_rendering_is_deterministic_for_the_same_payload():
    assert render_qr_svg_data_uri('same-payload') == render_qr_svg_data_uri('same-payload')
    assert render_qr_png_data_uri('same-payload') == render_qr_png_data_uri('same-payload')


def test_different_payloads_render_differently():
    assert render_qr_png_data_uri('a') != render_qr_png_data_uri('b')


def test_outbox_row_prefers_provider_supplied_image_over_local_payload():
    fake_image_b64 = base64.b64encode(b'not a real png, just proving precedence').decode('ascii')
    row = {'qr_image_base64': fake_image_b64, 'qr_payload': 'should-be-ignored'}
    result = qr_for_outbox_row(row)
    assert result == f"data:image/png;base64,{fake_image_b64}"


def test_outbox_row_falls_back_to_local_payload_when_no_provider_image():
    row = {'qr_image_base64': None, 'qr_payload': 'INC-000001|110.00|2026-08-04'}
    result = qr_for_outbox_row(row)
    assert result is not None
    assert result.startswith('data:image/png;base64,')


def test_outbox_row_returns_none_when_neither_present():
    assert qr_for_outbox_row({}) is None
    assert qr_for_outbox_row({'qr_image_base64': None, 'qr_payload': None}) is None
