import pytest

from commercial_runtime.einvoicing.document import (
    BuyerId,
    MissingBuyerIdError,
    derive_payment_type,
    require_buyer_id,
    select_buyer_id,
)


def test_select_buyer_id_precedence_tin_over_nin_over_pn():
    assert select_buyer_id({'TIN': 't1', 'NIN': 'n1', 'PN': 'p1'}) == BuyerId('TIN', 't1')
    assert select_buyer_id({'NIN': 'n1', 'PN': 'p1'}) == BuyerId('NIN', 'n1')
    assert select_buyer_id({'PN': 'p1'}) == BuyerId('PN', 'p1')


def test_select_buyer_id_ignores_empty_values():
    assert select_buyer_id({'TIN': '', 'NIN': 'n1'}) == BuyerId('NIN', 'n1')


def test_select_buyer_id_returns_none_when_nothing_on_file():
    assert select_buyer_id({}) is None
    assert select_buyer_id({'TIN': '', 'NIN': None}) is None


def test_require_buyer_id_returns_the_selected_id():
    assert require_buyer_id({'PN': 'p1'}) == BuyerId('PN', 'p1')


def test_require_buyer_id_raises_when_none_on_file():
    with pytest.raises(MissingBuyerIdError):
        require_buyer_id({})


def test_derive_payment_type_cash_when_fully_paid():
    assert derive_payment_type('cash', amount_paid=110.0, total=110.0) == 'cash'


def test_derive_payment_type_cash_when_overpaid():
    assert derive_payment_type('cash', amount_paid=999999.0, total=110.0) == 'cash'


def test_derive_payment_type_credit_when_underpaid():
    assert derive_payment_type('cash', amount_paid=50.0, total=110.0) == 'credit'


def test_derive_payment_type_credit_when_method_is_credit_even_if_paid():
    assert derive_payment_type('credit', amount_paid=110.0, total=110.0) == 'credit'


def test_derive_payment_type_tolerates_rounding_noise():
    """A one-cent floating rounding artifact must not misclassify a fully
    paid sale as credit."""
    assert derive_payment_type('cash', amount_paid=109.999, total=110.0) == 'cash'
