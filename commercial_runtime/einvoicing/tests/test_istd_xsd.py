"""Validates the golden ISTD documents against the OFFICIAL OASIS UBL 2.1
Invoice XSD (xsdrt/ runtime schemas).

The XSD is NOT vendored: its files carry an "All Rights Reserved" OASIS
header and the licence terms for redistribution were not confirmed. Fetch it
yourself (https://docs.oasis-open.org/ubl/os-UBL-2.1/UBL-2.1.zip, ~58 MB; only
xsdrt/ is needed) and point AURA_UBL21_XSD_DIR at the folder that contains
xsdrt/. lxml is also needed (not a repo dependency). Without both, these tests
SKIP -- they are an offline conformance check run on demand, and the always-on
structural assertions live in test_istd_profile.py.
"""
import os

import pytest

from commercial_runtime.einvoicing.tests.test_istd_profile import (
    _cash_b2c, _credit_note, _mixed, _receivable_b2b,
)
from commercial_runtime.einvoicing.istd_ubl import to_istd_xml

lxml = pytest.importorskip('lxml.etree', reason='lxml not installed')
_DIR = os.environ.get('AURA_UBL21_XSD_DIR')
pytestmark = pytest.mark.skipif(not _DIR, reason='AURA_UBL21_XSD_DIR not set (see module docstring)')


@pytest.fixture(scope='module')
def schema():
    path = os.path.join(_DIR, 'xsdrt', 'maindoc', 'UBL-Invoice-2.1.xsd')
    return lxml.XMLSchema(lxml.parse(path))


@pytest.mark.parametrize('make', [_cash_b2c, _receivable_b2b, _mixed, _credit_note])
def test_document_validates_against_ubl_2_1_xsd(schema, make):
    doc = lxml.fromstring(to_istd_xml(make()).encode('utf-8'))
    assert schema.validate(doc), schema.error_log


def test_xsd_rejects_a_misordered_document(schema):
    """Guards the guard: the schema must actually bite on element order."""
    xml = to_istd_xml(_cash_b2c())
    bad = xml.replace('<cbc:ProfileID>reporting:1.0</cbc:ProfileID>', '').replace(
        '</Invoice>', '<cbc:ProfileID>reporting:1.0</cbc:ProfileID></Invoice>')
    assert not schema.validate(lxml.fromstring(bad.encode('utf-8')))
