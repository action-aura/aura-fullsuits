import inspect

from commercial_runtime.einvoicing.document import BuyerId, EInvoiceDocument, EInvoiceLine
from commercial_runtime.einvoicing.providers import mock as mock_module
from commercial_runtime.einvoicing.providers.mock import MockProvider


def _doc(einvoice_no='INC-000001'):
    return EInvoiceDocument(
        company_id=1, einvoice_no=einvoice_no, local_document_no='SALE-1',
        invoice_family='income', payment_type='cash', currency='JOD',
        issue_datetime='2026-08-04T12:00:00', seller_name='Shop', seller_tin='1',
        buyer_name='Buyer', buyer_id=BuyerId('TIN', '2'),
        lines=(EInvoiceLine('Item', 1, 10.0, 0.0, 0.0, 10.0),),
        subtotal=10.0, discount_total=0.0, tax_total=0.0, grand_total=10.0,
    )


def test_source_never_imports_requests():
    """Zero network calls, enforced at the source level -- MockProvider must
    never be able to accidentally reach a real endpoint."""
    source = inspect.getsource(mock_module)
    assert 'import requests' not in source
    assert 'requests.' not in source


def test_default_submission_clears():
    provider = MockProvider()
    result = provider.submit_invoice('ref-1', _doc())
    assert result.outcome == 'CLEARED'
    assert result.provider_uuid
    assert 'MOCK' in result.qr_payload


def test_deterministic_uuid_for_the_same_invoice_ref():
    provider = MockProvider()
    r1 = provider.submit_invoice('same-ref', _doc())
    r2 = MockProvider().submit_invoice('same-ref', _doc())
    assert r1.provider_uuid == r2.provider_uuid


def test_different_invoice_refs_get_different_uuids():
    provider = MockProvider()
    r1 = provider.submit_invoice('ref-a', _doc())
    r2 = provider.submit_invoice('ref-b', _doc())
    assert r1.provider_uuid != r2.provider_uuid


def test_fail_first_n_then_succeeds():
    provider = MockProvider(fail_first_n=2)
    r1 = provider.submit_invoice('ref-1', _doc())
    r2 = provider.submit_invoice('ref-1', _doc())
    r3 = provider.submit_invoice('ref-1', _doc())
    assert r1.outcome == 'RETRY'
    assert r2.outcome == 'RETRY'
    assert r3.outcome == 'CLEARED'


def test_outcome_script_drives_successive_calls():
    provider = MockProvider(outcome_script=['RETRY', 'PENDING', 'CLEARED'])
    outcomes = [provider.submit_invoice('ref-1', _doc()).outcome for _ in range(3)]
    assert outcomes == ['RETRY', 'PENDING', 'CLEARED']


def test_outcome_script_holds_last_value_past_its_length():
    provider = MockProvider(outcome_script=['REJECTED'])
    r1 = provider.submit_invoice('ref-1', _doc())
    r2 = provider.submit_invoice('ref-1', _doc())
    assert r1.outcome == r2.outcome == 'REJECTED'


def test_unknown_after_send_returns_unknown_but_check_status_resolves_to_cleared():
    """Simulates a crash window: the mock 'received' the invoice but the
    caller's connection dropped before seeing the response. check_status
    must be able to resolve this later, exactly as the real worker's
    lease-reclaim path requires."""
    provider = MockProvider(unknown_after_send=True)
    submit_result = provider.submit_invoice('ref-1', _doc())
    assert submit_result.outcome == 'UNKNOWN'

    status_result = provider.check_status('ref-1', provider_uuid=None)
    assert status_result.outcome == 'CLEARED'


def test_check_status_for_unknown_ref_returns_unknown_not_a_guess():
    provider = MockProvider()
    result = provider.check_status('never-submitted', provider_uuid=None)
    assert result.outcome == 'UNKNOWN'


def test_rejected_outcome_has_no_provider_uuid():
    provider = MockProvider(outcome_script=['REJECTED'])
    result = provider.submit_invoice('ref-1', _doc())
    assert result.provider_uuid is None


def test_selftest_uses_clearly_synthetic_document_and_clears():
    provider = MockProvider()
    result = provider.selftest()
    assert result.outcome == 'CLEARED'
