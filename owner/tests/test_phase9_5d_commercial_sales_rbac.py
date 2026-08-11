"""Phase 9.5D Milestone 16 -- commercial-sales RBAC / segregation-of-duties
regression.

Static-data checks on app.staff.seed_data.ROLES, matching
test_phase9_5a_rbac_restrictions.py's established pattern -- cheap, and
exactly the kind of check that silently rots if a future role edit
accidentally collapses the create/approve separation without anyone
noticing. Routes don't exist yet (Milestones 18/19 build them on top of
this catalog) so there is nothing to test at the HTTP layer this
milestone; this is the earlier, foundational guarantee those routes will
inherit for free once they enforce `require_permission(...)` per the
funnel contract's own permission column.

Real gap found and closed this milestone: `quotes.approve`/`orders.approve`
were pre-seeded (Phase 9.5A) but never granted to any role -- only
SUPER_ADMIN could ever confirm a Sales Order or approve a quote/pricing
exception. Fixed by granting both to FINANCE (see seed_data.py's own
comment for the reasoning) -- proven here.
"""
from __future__ import annotations

from app.staff.seed_data import ROLES

# Every "approve/confirm/issue" verb in the commercial-sales/commission
# domain -- the money-authorization tier. Non-Negotiable Principle 4/8:
# employees select from admin-managed prices, they don't authorize their
# own overrides or approve their own money. SALES must never hold any of
# these, no matter how the role catalog evolves.
COMMERCIAL_SALES_APPROVAL_TIER_PERMISSIONS = (
    "quotes.approve",
    "orders.approve",
    "invoices.issue",
    "refunds.approve",
    "pricing.override",
    "commissions.approve",
    "commissions.pay",
    "commissions.reverse",
)

# The creation/pipeline-ownership tier -- SALES's own working permissions.
COMMERCIAL_SALES_CREATE_TIER_PERMISSIONS = (
    "quotes.create",
    "orders.create",
    "invoices.create",
)


def test_sales_role_never_holds_any_commercial_sales_approval_permission():
    sales_perms = ROLES["SALES"]["permissions"]
    for perm_code in COMMERCIAL_SALES_APPROVAL_TIER_PERMISSIONS:
        assert perm_code not in sales_perms, f"{perm_code} must never be granted to SALES -- creation and approval are separate tiers"


def test_creation_tier_permissions_are_not_granted_to_finance():
    """FINANCE approves/confirms/issues; it does not create quotes, orders,
    or invoices -- a real, deliberate boundary (FINANCE isn't a
    sales-pipeline role, per seed_data.py's own comment)."""
    finance_perms = ROLES["FINANCE"]["permissions"]
    for perm_code in COMMERCIAL_SALES_CREATE_TIER_PERMISSIONS:
        assert perm_code not in finance_perms, f"{perm_code} must not be granted to FINANCE -- it is a creation-tier, sales-pipeline permission"


def test_quotes_and_orders_approve_are_granted_to_finance():
    """The Milestone 16 fix: prior to this milestone neither permission was
    granted to any concrete role, so no route built on top of the funnel
    contract could ever let anyone but SUPER_ADMIN confirm an Order or
    approve a quote/pricing exception."""
    finance_perms = ROLES["FINANCE"]["permissions"]
    assert "quotes.approve" in finance_perms
    assert "orders.approve" in finance_perms


def test_no_non_super_admin_role_holds_both_a_create_and_approve_verb_for_the_same_document_type():
    """The core maker-checker guarantee for the commercial-sales domain:
    for each of Quote/Order, no single role (other than SUPER_ADMIN, which
    is exempt by design -- the emergency/administrative bypass) can both
    create AND approve the same document type. Invoice is asymmetric by
    design (invoices.create is SALES, invoices.issue is FINANCE, and there
    is no separate invoices.approve) so it is covered by the create-tier
    test above instead."""
    pairs = (("quotes.create", "quotes.approve"), ("orders.create", "orders.approve"))
    for role_code, definition in ROLES.items():
        if role_code == "SUPER_ADMIN":
            continue
        granted = definition["permissions"]
        assert granted != "*", f"{role_code} unexpectedly uses wildcard permissions"
        for create_perm, approve_perm in pairs:
            assert not (create_perm in granted and approve_perm in granted), (
                f"{role_code} holds both {create_perm} and {approve_perm} -- maker-checker violation"
            )


def test_support_and_viewer_hold_no_commercial_sales_write_permission():
    """SUPPORT and VIEWER are explicitly non-sales-pipeline roles (per
    their own seed_data.py descriptions) -- neither should ever gain a
    commercial-sales creation or approval permission by accident."""
    all_commercial_sales_perms = COMMERCIAL_SALES_CREATE_TIER_PERMISSIONS + COMMERCIAL_SALES_APPROVAL_TIER_PERMISSIONS
    for role_code in ("SUPPORT", "VIEWER"):
        role_perms = ROLES[role_code]["permissions"]
        for perm_code in all_commercial_sales_perms:
            assert perm_code not in role_perms, f"{perm_code} must not be granted to {role_code}"


def test_commissions_approve_pay_reverse_never_granted_to_sales():
    """Extends the existing Phase 9.5A test (which only checked
    commissions.pay) to the full approval-tier trio, matching Milestone
    15's own Non-Negotiable rule: a beneficiary can never approve their
    own commission, and SALES is exactly the role commission
    beneficiaries hold."""
    sales_perms = ROLES["SALES"]["permissions"]
    assert "commissions.approve" not in sales_perms
    assert "commissions.pay" not in sales_perms
    assert "commissions.reverse" not in sales_perms
    assert "commissions.view_own" in sales_perms  # SALES can see its own earnings, just never self-authorize them
