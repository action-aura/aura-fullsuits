"""
Aura Retail -- unit tests for core/retail/po_split.py (PO-preview-by-supplier
foundation, Thursday demo Stream B). Pure module tests, no Flask app boot, no
DB connection -- mirrors retail_pricing_test.py Part A's bootstrap for
core/retail/pricing.py, since po_split.py is the same kind of module
(pure calculation authority, no session/DB access).

Written FIRST, before core/retail/po_split.py existed (true TDD) -- see the
implementation's module docstring for the design decisions these tests
pin down (Decimal rounding mode, the below-min-order boundary rule, the
contact-resolution ladder order).

Run:
    pytest products/retail/tests/retail_po_split_unit_test.py -v
"""
import sys
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail import po_split  # noqa: E402


# ═════════════════════════════════════════════════════════════════════════
# Grouping correctness
# ═════════════════════════════════════════════════════════════════════════

def test_groups_multi_supplier_basket_into_separate_split_groups():
    items = [
        {'product_id': 'p1', 'product_name': 'Widget', 'sku': 'SKU-1', 'supplier_id': 's1', 'quantity': 2, 'unit_cost': 10.0},
        {'product_id': 'p2', 'product_name': 'Gadget', 'sku': 'SKU-2', 'supplier_id': 's2', 'quantity': 1, 'unit_cost': 50.0},
        {'product_id': 'p3', 'product_name': 'Gizmo', 'sku': 'SKU-3', 'supplier_id': 's1', 'quantity': 3, 'unit_cost': 5.0},
    ]
    suppliers = {
        's1': {'name': 'Acme Supply', 'min_order_value': 0},
        's2': {'name': 'Beta Distrib', 'min_order_value': 0},
    }

    result = po_split.group_basket_by_supplier(items, suppliers)

    assert len(result['groups']) == 2
    by_id = {g['supplier_id']: g for g in result['groups']}
    assert by_id['s1']['line_count'] == 2
    assert by_id['s1']['subtotal'] == 35.0  # 2*10 + 3*5
    assert by_id['s2']['line_count'] == 1
    assert by_id['s2']['subtotal'] == 50.0
    assert result['unassigned']['line_count'] == 0


def test_group_lines_preserve_product_identity_fields():
    items = [{'product_id': 'p1', 'product_name': 'Widget', 'sku': 'SKU-1', 'supplier_id': 's1', 'quantity': 2, 'unit_cost': 10.0}]
    suppliers = {'s1': {'name': 'Acme Supply', 'min_order_value': 0}}

    result = po_split.group_basket_by_supplier(items, suppliers)
    line = result['groups'][0]['lines'][0]
    assert line['product_id'] == 'p1'
    assert line['product_name'] == 'Widget'
    assert line['sku'] == 'SKU-1'
    assert line['supplier_id'] == 's1'
    assert line['quantity'] == 2
    assert line['unit_cost'] == 10.0
    assert line['line_total'] == 20.0


def test_groups_are_returned_in_deterministic_supplier_name_order():
    items = [
        {'product_id': 'p1', 'supplier_id': 'zz', 'quantity': 1, 'unit_cost': 1.0},
        {'product_id': 'p2', 'supplier_id': 'aa', 'quantity': 1, 'unit_cost': 1.0},
    ]
    suppliers = {
        'zz': {'name': 'Zeta Traders', 'min_order_value': 0},
        'aa': {'name': 'Acme Supply', 'min_order_value': 0},
    }
    result = po_split.group_basket_by_supplier(items, suppliers)
    assert [g['supplier_id'] for g in result['groups']] == ['aa', 'zz']


# ═════════════════════════════════════════════════════════════════════════
# Decimal rounding correctness
# ═════════════════════════════════════════════════════════════════════════

def test_line_total_rounds_correctly_at_two_decimals():
    items = [{'product_id': 'p1', 'supplier_id': 's1', 'quantity': 3, 'unit_cost': 0.145}]
    suppliers = {'s1': {'name': 'Acme', 'min_order_value': 0}}

    result = po_split.group_basket_by_supplier(items, suppliers)
    line = result['groups'][0]['lines'][0]
    # 3 * 0.145 == 0.435 exactly in Decimal arithmetic (both operands are
    # converted to Decimal via str() BEFORE multiplying, never multiplied as
    # binary floats first) -> rounds to 0.44.
    assert line['line_total'] == 0.44


def test_group_subtotal_uses_round_half_up_not_bankers_rounding():
    # 10.005 sits exactly halfway between 10.00 and 10.01. Python's Decimal
    # default context rounds half-to-even (bankers' rounding), which would
    # give 10.00 here (0 is already even) -- but this codebase's one
    # financial-rounding rule is ROUND_HALF_UP (core/retail/pricing.py's
    # _money(), matched deliberately by po_split._q2()), which gives 10.01.
    # This test only passes if _q2 uses ROUND_HALF_UP explicitly.
    items = [
        {'product_id': 'p1', 'supplier_id': 's1', 'quantity': 1, 'unit_cost': 10.005},
        {'product_id': 'p2', 'supplier_id': 's1', 'quantity': 1, 'unit_cost': 10.005},
    ]
    suppliers = {'s1': {'name': 'Acme', 'min_order_value': 0}}

    result = po_split.group_basket_by_supplier(items, suppliers)
    group = result['groups'][0]
    assert group['lines'][0]['line_total'] == 10.01
    assert group['lines'][1]['line_total'] == 10.01
    assert group['subtotal'] == 20.02
    assert group['total'] == 20.02


def test_q2_helper_uses_decimal_not_binary_float():
    # classic binary-float trap: 0.1 + 0.2 != 0.3 in raw float arithmetic
    assert po_split._q2(0.1 + 0.2) == po_split._q2(0.3)


# ═════════════════════════════════════════════════════════════════════════
# Unassigned bucket
# ═════════════════════════════════════════════════════════════════════════

def test_items_without_supplier_id_land_in_unassigned_bucket():
    items = [
        {'product_id': 'p1', 'supplier_id': 's1', 'quantity': 1, 'unit_cost': 10.0},
        {'product_id': 'p2', 'supplier_id': None, 'quantity': 1, 'unit_cost': 5.0},
        {'product_id': 'p3', 'quantity': 2, 'unit_cost': 2.5},  # supplier_id key missing entirely
    ]
    suppliers = {'s1': {'name': 'Acme', 'min_order_value': 0}}

    result = po_split.group_basket_by_supplier(items, suppliers)

    assert len(result['groups']) == 1
    assert result['unassigned']['line_count'] == 2
    assert result['unassigned']['subtotal'] == 10.0  # 5.0 + 2*2.5
    assert result['unassigned']['supplier_id'] is None
    assert result['unassigned']['below_min_order'] is False


def test_all_unassigned_basket_produces_zero_groups():
    items = [{'product_id': 'p1', 'quantity': 1, 'unit_cost': 5.0}]
    result = po_split.group_basket_by_supplier(items, {})
    assert result['groups'] == []
    assert result['unassigned']['line_count'] == 1


# ═════════════════════════════════════════════════════════════════════════
# Contact resolution ladder
# ═════════════════════════════════════════════════════════════════════════

def _supplier(**kw):
    base = {'name': 'Acme Supply', 'email': 'fallback@acme.test', 'phone': '+1-555-0000', 'min_order_value': 0}
    base.update(kw)
    return base


def test_contact_resolution_rung1_primary_role_match_wins_over_everything():
    contacts = [
        {'name': 'Rung3 Primary', 'role': 'general', 'is_primary': 1, 'status': 'active', 'created_at': '2026-01-01'},
        {'name': 'Rung2 Role', 'role': 'orders', 'is_primary': 0, 'status': 'active', 'created_at': '2026-01-02'},
        {'name': 'Rung1 Primary Role', 'role': 'orders', 'is_primary': 1, 'status': 'active', 'created_at': '2026-01-03'},
    ]
    result = po_split.resolve_contact(_supplier(), contacts, purpose='orders')
    assert result['source'] == 'contact_primary_role'
    assert result['name'] == 'Rung1 Primary Role'


def test_contact_resolution_rung2_role_match_picks_lowest_created_at():
    contacts = [
        {'name': 'Later Orders Contact', 'role': 'orders', 'is_primary': 0, 'status': 'active', 'created_at': '2026-02-01'},
        {'name': 'Earlier Orders Contact', 'role': 'orders', 'is_primary': 0, 'status': 'active', 'created_at': '2026-01-01'},
    ]
    result = po_split.resolve_contact(_supplier(), contacts, purpose='orders')
    assert result['source'] == 'contact_role'
    assert result['name'] == 'Earlier Orders Contact'


def test_contact_resolution_rung3_any_primary_when_no_role_match():
    contacts = [
        {'name': 'General Primary', 'role': 'general', 'is_primary': 1, 'status': 'active', 'created_at': '2026-01-01'},
        {'name': 'Accounts Non Primary', 'role': 'accounts', 'is_primary': 0, 'status': 'active', 'created_at': '2026-01-01'},
    ]
    result = po_split.resolve_contact(_supplier(), contacts, purpose='orders')
    assert result['source'] == 'contact_primary'
    assert result['name'] == 'General Primary'


def test_contact_resolution_rung4_falls_back_to_supplier_email_phone():
    result = po_split.resolve_contact(_supplier(), [], purpose='orders')
    assert result['source'] == 'supplier_fallback'
    assert result['email'] == 'fallback@acme.test'
    assert result['phone'] == '+1-555-0000'


def test_contact_resolution_rung5_nothing_usable():
    supplier = {'name': 'No Contact Co', 'email': None, 'phone': None, 'min_order_value': 0}
    result = po_split.resolve_contact(supplier, [], purpose='orders')
    assert result['source'] == 'none'
    assert result['email'] is None
    assert result['phone'] is None


def test_inactive_contacts_are_excluded_from_every_rung():
    contacts = [
        {'name': 'Inactive Primary Role', 'role': 'orders', 'is_primary': 1, 'status': 'inactive', 'created_at': '2026-01-01'},
        {'name': 'Inactive Role Only', 'role': 'orders', 'is_primary': 0, 'status': 'inactive', 'created_at': '2026-01-01'},
        {'name': 'Inactive Primary Any Role', 'role': 'general', 'is_primary': 1, 'status': 'inactive', 'created_at': '2026-01-01'},
    ]
    result = po_split.resolve_contact(_supplier(), contacts, purpose='orders')
    # every contact present is inactive -> falls all the way through to the
    # supplier's own email/phone fallback
    assert result['source'] == 'supplier_fallback'


def test_active_contact_still_wins_when_mixed_with_inactive_higher_rungs():
    contacts = [
        {'name': 'Inactive Primary Role', 'role': 'orders', 'is_primary': 1, 'status': 'inactive', 'created_at': '2026-01-01'},
        {'name': 'Active Role Only', 'role': 'orders', 'is_primary': 0, 'status': 'active', 'created_at': '2026-01-02'},
    ]
    result = po_split.resolve_contact(_supplier(), contacts, purpose='orders')
    assert result['source'] == 'contact_role'
    assert result['name'] == 'Active Role Only'


# ═════════════════════════════════════════════════════════════════════════
# normalize_whatsapp
# ═════════════════════════════════════════════════════════════════════════

def test_normalize_whatsapp_accepts_plus_prefixed_international_number():
    assert po_split.normalize_whatsapp('+962 79 123 4567') == '962791234567'


def test_normalize_whatsapp_accepts_00_prefixed_international_number():
    assert po_split.normalize_whatsapp('00962791234567') == '962791234567'


def test_normalize_whatsapp_rejects_local_number_without_country_code():
    assert po_split.normalize_whatsapp('0791234567') is None


def test_normalize_whatsapp_rejects_too_short_input():
    assert po_split.normalize_whatsapp('123') is None


def test_normalize_whatsapp_rejects_none_and_empty_string():
    assert po_split.normalize_whatsapp(None) is None
    assert po_split.normalize_whatsapp('') is None


def test_normalize_whatsapp_strips_punctuation_and_whitespace():
    assert po_split.normalize_whatsapp('+962-79-123-4567') == '962791234567'


# ═════════════════════════════════════════════════════════════════════════
# MOQ (below_min_order) boundary
# ═════════════════════════════════════════════════════════════════════════

def test_below_min_order_is_false_when_total_exactly_equals_minimum():
    # Design choice: "minimum order value" is satisfied AT the minimum --
    # exactly meeting it is not a violation, only falling strictly under it
    # is. See po_split.py's module docstring for the full rationale.
    items = [{'product_id': 'p1', 'supplier_id': 's1', 'quantity': 1, 'unit_cost': 100.0}]
    suppliers = {'s1': {'name': 'Acme', 'min_order_value': 100.0}}
    result = po_split.group_basket_by_supplier(items, suppliers)
    assert result['groups'][0]['total'] == 100.0
    assert result['groups'][0]['below_min_order'] is False


def test_below_min_order_is_true_one_cent_under_minimum():
    items = [{'product_id': 'p1', 'supplier_id': 's1', 'quantity': 1, 'unit_cost': 99.99}]
    suppliers = {'s1': {'name': 'Acme', 'min_order_value': 100.0}}
    result = po_split.group_basket_by_supplier(items, suppliers)
    assert result['groups'][0]['total'] == 99.99
    assert result['groups'][0]['below_min_order'] is True


def test_below_min_order_is_false_when_supplier_has_no_minimum_configured():
    items = [{'product_id': 'p1', 'supplier_id': 's1', 'quantity': 1, 'unit_cost': 1.0}]
    suppliers = {'s1': {'name': 'Acme', 'min_order_value': 0}}
    result = po_split.group_basket_by_supplier(items, suppliers)
    assert result['groups'][0]['below_min_order'] is False
