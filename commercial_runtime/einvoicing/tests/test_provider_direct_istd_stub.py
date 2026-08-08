import inspect
import re

import pytest

from commercial_runtime.einvoicing.providers import direct_istd as direct_istd_module
from commercial_runtime.einvoicing.providers.base import EInvoiceProviderConfigError
from commercial_runtime.einvoicing.providers.direct_istd import DirectISTDProvider

# Guards against a future contributor "helpfully" filling in plausible-
# looking but fabricated ISTD technical details (endpoints, grant types)
# before real integration docs exist. Mentioning "JoFotara"/"ISTD" by name
# in comments is fine (that's just naming what this module is for) --
# what's forbidden is anything that looks like a real URL or a guessed
# protocol detail.
_FORBIDDEN_SUBSTRINGS = (
    'https://', 'http://', '.gov.jo', 'client_credentials', '/api/v',
)


def _provider():
    return DirectISTDProvider(base_url='https://example.invalid', credentials=object())


def test_submit_invoice_raises_not_implemented():
    provider = _provider()
    with pytest.raises(NotImplementedError):
        provider.submit_invoice('ref-1', object())


def test_check_status_raises_not_implemented():
    provider = _provider()
    with pytest.raises(NotImplementedError):
        provider.check_status('ref-1', None)


def test_verify_tls_false_is_rejected_at_construction():
    with pytest.raises(EInvoiceProviderConfigError):
        DirectISTDProvider(base_url='https://example.invalid', credentials=object(), verify_tls=False)


def test_source_contains_no_fabricated_istd_details():
    source = inspect.getsource(direct_istd_module).lower()
    for forbidden in _FORBIDDEN_SUBSTRINGS:
        assert forbidden not in source, f"direct_istd.py must not contain a guessed ISTD detail: {forbidden!r}"


def test_source_never_makes_an_http_call():
    source = inspect.getsource(direct_istd_module)
    assert 'requests.' not in source
    assert '.get(' not in source
    assert '.post(' not in source
