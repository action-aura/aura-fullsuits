"""Whose credit terms apply to a customer: the customer's own, or the shop's.

WHY THIS MODULE EXISTS (MONEY-4c, schema v53)
---------------------------------------------
`customers.credit_mode` used to default to the literal string 'none'. Every way a
customer was created (the Add Customer dialog, the till's "+ New customer", import,
the sync create event) therefore STORED 'none', and `create_sale` resolved terms as
`cust['credit_mode'] or settings['default_credit_mode']` -- but 'none' is a truthy
string, so the `or` never fell through: whatever the owner set as the SHOP default
(Settings > Credit) was dead for every normally created customer. UI-2 measured it in
a real browser: shop default "unlimited" + a new customer -> Credit tile disabled.

The fix is a fourth stored value, 'inherit', meaning "follow the shop default".
'none' goes back to meaning only what it says: THIS customer may not buy on credit.

THE ONE RULE: every reader goes through `effective_credit_mode` / `effective_credit_limit`.
A reader that compares the stored column itself re-opens the bug (it would read 'inherit'
as "an unknown mode"). create_sale, the customer list the till reads, and the till's own
mirror of this rule (subsystem-retail.js `_creditGateReason`) all use the resolved value.

An UNKNOWN stored value (a typo, a future mode a newer peer wrote) resolves to 'none':
the credit check in create_sale only refuses on 'none' and caps 'limited', so anything
else used to fall through as UNLIMITED credit -- failing open on a money gate. Failing
closed is the only safe direction for a value nobody can vouch for.
"""

CREDIT_MODE_INHERIT = 'inherit'
# The three terms a customer (or the shop default) can actually have.
EXPLICIT_CREDIT_MODES = ('none', 'limited', 'unlimited')
# Everything a customer row may legitimately store.
STORED_CREDIT_MODES = EXPLICIT_CREDIT_MODES + (CREDIT_MODE_INHERIT,)


def _get(row, key):
    """Read `key` from a sqlite3.Row / dict / None without caring which."""
    if row is None:
        return None
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def effective_credit_mode(cust_row, settings):
    """The mode that actually governs this customer: 'none' | 'limited' | 'unlimited'.

    NULL, '' and 'inherit' follow the shop's `default_credit_mode` (itself missing or
    unrecognised -> 'none'); an explicit stored mode is used as stored; anything else
    fails closed to 'none'."""
    stored = _get(cust_row, 'credit_mode')
    if stored is None or stored == '' or stored == CREDIT_MODE_INHERIT:
        shop = _get(settings, 'default_credit_mode')
        return shop if shop in EXPLICIT_CREDIT_MODES else 'none'
    return stored if stored in EXPLICIT_CREDIT_MODES else 'none'


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def effective_credit_limit(cust_row, settings):
    """The ceiling that applies when the effective mode is 'limited'.

    A NULL limit has always meant "use the shop's default limit". An INHERITING customer
    also takes the shop limit when their own is 0: `credit_limit` is created `DEFAULT 0`,
    so a customer who never had a limit set stores 0, and without this a shop default of
    "limited up to 500" would silently become "limited up to 0" for every inheriting
    customer -- the same dead-default bug, one column over. An explicit mode keeps its own
    figure, 0 included (a deliberate 'limited, 0' is a legitimate way to say "no new debt")."""
    own = _as_float(_get(cust_row, 'credit_limit'))
    shop = _as_float(_get(settings, 'default_credit_limit')) or 0.0
    stored = _get(cust_row, 'credit_mode')
    inheriting = stored is None or stored == '' or stored == CREDIT_MODE_INHERIT
    if own is None or (inheriting and own <= 0):
        return shop
    return own
