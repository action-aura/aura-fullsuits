"""
Aura Retail -- purchase-order split-by-supplier preview calculation
authority (Thursday demo, Stream B -- descoped to PREVIEW-only PO
splitting: this module groups a basket into per-supplier slices and resolves
who to notify, but nothing here writes anything to the database or sends
anything anywhere; the routes that persist split_group_id/routing_status
etc. on `purchase_orders` and actually dispatch land later this week).

Pure module, no Flask, no DB connection, no session access -- mirrors
core/retail/pricing.py's shape exactly (the existing precedent for a
calculation authority retail_api.py imports rather than reimplementing
inline). Like pricing.py, this only ever receives plain dicts/lists that a
caller already loaded from the database and returns plain dicts/floats back;
it never queries anything itself.

CALCULATION PRECISION: money math here runs in Decimal, quantized with
ROUND_HALF_UP at 2 decimal places -- the same rounding rule
core/retail/pricing.py's `_money()` uses (which itself matches
api/retail_api.py's `_money()` for header-level figures, per AUDIT-006).
`_q2()` below is a deliberate, small duplicate of that rounding helper
rather than an import of retail_api.py's `_money()` -- retail_api.py already
imports FROM core/, so importing back from core/ into retail_api.py would be
circular. It is not imported from pricing.py either, to keep this module
independently testable/importable without pulling in pricing.py's tax-mode
machinery, which has nothing to do with PO splitting.

DESIGN DECISIONS (pin these down before changing behavior; each has a test
in retail_po_split_unit_test.py that will fail if the behavior drifts):

  - below_min_order boundary: a group's total EXACTLY equal to the
    supplier's min_order_value is NOT flagged as below minimum -- only a
    total strictly LESS than min_order_value is. "Minimum order value" reads
    as "at least this much", so meeting it exactly satisfies the
    requirement; only falling under it is a violation worth warning about.

  - Contact resolution is a fixed 5-rung priority ladder (see
    `resolve_contact` below), evaluated top to bottom, first match wins.
    Only contacts with status == 'active' are ever considered, at any rung.

  - `normalize_whatsapp` rejects any number that, after normalization, still
    starts with a leading '0' -- no real E.164 country code starts with 0,
    so a remaining leading zero means this is a local-format number with no
    country code (e.g. Jordan's "0791234567" written without "+962"), which
    is not a safely dispatchable WhatsApp destination. It also rejects
    anything shorter than MIN_E164_DIGITS after normalization.
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

MONEY_QUANT = Decimal('0.01')

DEFAULT_CONTACT_PURPOSE = 'orders'
ACTIVE_STATUS = 'active'

# Shortest international phone number length (country code + subscriber
# number, digits only) worth trusting as a real destination. Real E.164
# numbers run from ~8 digits (some small-country fixed lines) up to 15;
# anything shorter is almost certainly a local number missing its country
# code or outright junk input.
MIN_E164_DIGITS = 8

_NON_DIGITS_RE = re.compile(r'\D+')

_NONE_CONTACT = {
    'name': None, 'email': None, 'phone': None, 'whatsapp': None,
    'channel_preference': None, 'source': 'none',
}


def _d(x) -> Decimal:
    """Decimal from any numeric-ish input, going through str() to avoid
    binary-float artifacts (Decimal(0.1) != Decimal('0.1')). Mirrors
    pricing.py's identical helper -- duplicated, not imported, see module
    docstring."""
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x if x is not None else 0))


def _q2(x) -> Decimal:
    """Quantize to 2 decimal places, ROUND_HALF_UP -- this codebase's one
    financial-rounding rule (core/retail/pricing.py's `_money()`), applied
    here too so a PO split preview never disagrees with the rest of the app
    at a half-cent boundary."""
    return _d(x).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def normalize_whatsapp(raw) -> str | None:
    """Normalize a free-form phone number into a bare digit string suitable
    for WhatsApp dispatch, or None if it isn't usable.

    Accepts "+962 79 123 4567" and "00962791234567" (both normalize to the
    same "962791234567"). Rejects "0791234567" (local number, no country
    code) and "123" (too short to be a real number) -- see module docstring
    for the reasoning behind both rejections.
    """
    if not raw:
        return None
    digits = _NON_DIGITS_RE.sub('', raw)
    if digits.startswith('00'):
        digits = digits[2:]
    if len(digits) < MIN_E164_DIGITS:
        return None
    if digits.startswith('0'):
        return None
    return digits


def _is_primary(contact: dict) -> bool:
    return bool(contact.get('is_primary'))


def _contact_result(contact: dict, source: str) -> dict:
    return {
        'name': contact.get('name'),
        'email': contact.get('email'),
        'phone': contact.get('phone'),
        'whatsapp': contact.get('whatsapp'),
        'channel_preference': contact.get('channel_preference'),
        'source': source,
    }


def _supplier_fallback_contact(supplier: dict) -> dict:
    email = supplier.get('email')
    phone = supplier.get('phone')
    if not email and not phone:
        return dict(_NONE_CONTACT)
    return {
        'name': supplier.get('name'),
        'email': email,
        'phone': phone,
        'whatsapp': None,
        'channel_preference': 'email' if email else 'phone',
        'source': 'supplier_fallback',
    }


def resolve_contact(supplier: dict | None, contacts: list[dict] | None, purpose: str = DEFAULT_CONTACT_PURPOSE) -> dict:
    """Resolve which contact to route a supplier's split PO slice to, trying
    each rung in this exact priority order, first match wins. Only contacts
    with status == 'active' are considered at any rung:

      1. role == purpose AND is_primary == 1        -> 'contact_primary_role'
      2. role == purpose, lowest created_at          -> 'contact_role'
      3. is_primary == 1, any role                   -> 'contact_primary'
      4. supplier's own email/phone                  -> 'supplier_fallback'
      5. nothing usable                               -> 'none'

    Returns a dict with name/email/phone/whatsapp/channel_preference/source
    -- every field is present (possibly None) regardless of which rung
    matched, so callers never need to branch on which keys exist.
    """
    supplier = supplier or {}
    active = [c for c in (contacts or []) if (c.get('status') or ACTIVE_STATUS) == ACTIVE_STATUS]

    role_matches = [c for c in active if c.get('role') == purpose]

    rung1 = [c for c in role_matches if _is_primary(c)]
    if rung1:
        return _contact_result(rung1[0], 'contact_primary_role')

    if role_matches:
        earliest = sorted(role_matches, key=lambda c: c.get('created_at') or '')[0]
        return _contact_result(earliest, 'contact_role')

    rung3 = [c for c in active if _is_primary(c)]
    if rung3:
        earliest_primary = sorted(rung3, key=lambda c: c.get('created_at') or '')[0]
        return _contact_result(earliest_primary, 'contact_primary')

    fallback = _supplier_fallback_contact(supplier)
    if fallback['source'] == 'supplier_fallback':
        return fallback

    return dict(_NONE_CONTACT)


def _build_line(item: dict) -> dict:
    quantity_dec = _d(item.get('quantity'))
    unit_cost_dec = _d(item.get('unit_cost'))
    line_total_dec = _q2(quantity_dec * unit_cost_dec)
    return {
        'product_id': item.get('product_id'),
        'product_name': item.get('product_name'),
        'sku': item.get('sku'),
        'supplier_id': item.get('supplier_id'),
        'quantity': float(quantity_dec),
        'unit_cost': float(unit_cost_dec),
        'line_total': float(line_total_dec),
    }


def _sum_line_totals(lines: list[dict]) -> Decimal:
    total = Decimal('0.00')
    for line in lines:
        total += _d(line['line_total'])
    return _q2(total)


def _build_group(supplier_id, supplier: dict, lines: list[dict], contacts: list[dict], purpose: str) -> dict:
    subtotal_dec = _sum_line_totals(lines)
    # No tax/fee modeling at the preview stage -- total mirrors subtotal for
    # now (a deliberate 1:1 alias, not an oversight); this is a hook for
    # whichever later-week PO route adds header-level charges.
    total_dec = subtotal_dec
    min_order_dec = _d((supplier or {}).get('min_order_value'))
    below = total_dec < min_order_dec
    contact = resolve_contact(supplier, contacts, purpose=purpose)
    return {
        'supplier_id': supplier_id,
        'supplier_name': (supplier or {}).get('name'),
        'lines': lines,
        'subtotal': float(subtotal_dec),
        'total': float(total_dec),
        'line_count': len(lines),
        'below_min_order': bool(below),
        'min_order_value': float(min_order_dec),
        'contact': contact,
    }


def _build_unassigned_group(lines: list[dict]) -> dict:
    subtotal_dec = _sum_line_totals(lines)
    return {
        'supplier_id': None,
        'supplier_name': 'Unassigned',
        'lines': lines,
        'subtotal': float(subtotal_dec),
        'total': float(subtotal_dec),
        'line_count': len(lines),
        'below_min_order': False,
        'min_order_value': 0.0,
        'contact': dict(_NONE_CONTACT),
    }


def group_basket_by_supplier(
    items: list[dict],
    suppliers: dict | None = None,
    contacts: dict | None = None,
    purpose: str = DEFAULT_CONTACT_PURPOSE,
) -> dict:
    """Group a basket of cart-like items into per-supplier SplitGroups plus
    an 'unassigned' bucket for anything with no supplier_id.

    items: iterable of dicts -- {product_id, product_name, sku, supplier_id
        (nullable/omittable), quantity, unit_cost}.
    suppliers: dict[supplier_id] -> {name, min_order_value, email, phone}
        (any missing supplier_id simply resolves to an empty record, same as
        an unconfigured supplier).
    contacts: dict[supplier_id] -> list of supplier_contacts-shaped dicts
        (role/is_primary/status/created_at/email/phone/whatsapp/
        channel_preference), used for contact resolution (see
        resolve_contact above).
    purpose: contact role to prefer when resolving each group's contact
        (defaults to 'orders', matching supplier_contacts.role's default).

    Returns {'groups': [SplitGroup, ...], 'unassigned': SplitGroup}. Groups
    are sorted by supplier name (case-insensitive) then supplier_id, for a
    stable, predictable preview render order.
    """
    suppliers = suppliers or {}
    contacts = contacts or {}

    lines_by_supplier: dict = {}
    unassigned_lines = []

    for item in items or []:
        line = _build_line(item)
        supplier_id = item.get('supplier_id')
        if not supplier_id:
            unassigned_lines.append(line)
            continue
        lines_by_supplier.setdefault(supplier_id, []).append(line)

    groups = [
        _build_group(supplier_id, suppliers.get(supplier_id) or {}, lines, contacts.get(supplier_id) or [], purpose)
        for supplier_id, lines in lines_by_supplier.items()
    ]
    groups.sort(key=lambda g: ((g['supplier_name'] or '').lower(), str(g['supplier_id'])))

    return {
        'groups': groups,
        'unassigned': _build_unassigned_group(unassigned_lines),
    }
