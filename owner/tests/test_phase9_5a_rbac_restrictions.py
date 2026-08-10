"""Phase 9.5A Milestone 24 -- RBAC restriction regression.

rbac-permission-matrix.md / duplication-risk-report.md commit to specific
sensitive permissions never being granted below SUPER_ADMIN. This is a
static-data regression test on app.staff.seed_data.ROLES -- cheap, and it is
exactly the kind of check that silently rots if a future role edit
accidentally grants one of these without anyone noticing (the real risk this
test exists to catch).
"""
from __future__ import annotations

from app.staff.seed_data import ROLES

SUPER_ADMIN_ONLY_PERMISSIONS = (
    "pricing.override",
    "device_policy.manage",
    "employees.terminate",
    "employees.assign_role",
    "management_notes.manage",
)


def test_sensitive_permissions_not_granted_below_super_admin():
    for perm_code in SUPER_ADMIN_ONLY_PERMISSIONS:
        for role_code, definition in ROLES.items():
            if role_code == "SUPER_ADMIN":
                continue
            granted = definition["permissions"]
            assert granted != "*", f"{role_code} unexpectedly uses wildcard permissions"
            assert perm_code not in granted, f"{perm_code} must not be granted to {role_code}"


def test_super_admin_has_every_permission_via_wildcard():
    assert ROLES["SUPER_ADMIN"]["permissions"] == "*"


def test_support_cannot_manage_pricing_or_device_policy():
    support_perms = ROLES["SUPPORT"]["permissions"]
    assert "pricing.override" not in support_perms
    assert "device_policy.manage" not in support_perms


def test_finance_cannot_manage_device_policy():
    finance_perms = ROLES["FINANCE"]["permissions"]
    assert "device_policy.manage" not in finance_perms


def test_sales_cannot_pay_commissions_or_terminate_employees():
    sales_perms = ROLES["SALES"]["permissions"]
    assert "commissions.pay" not in sales_perms  # SALES never approves its own commission payout
    assert "employees.terminate" not in sales_perms
    assert "employees.assign_role" not in sales_perms


def test_finance_is_the_deliberate_money_authorization_role_for_commissions():
    """commissions.pay IS granted to FINANCE, by deliberate design (seed_data.py's
    own comment: 'Finance is the money-authorization role... approves/pays
    commissions and expenses') -- this is real maker-checker separation, not a
    gap: FINANCE is never the employee earning the commission it pays."""
    finance_perms = ROLES["FINANCE"]["permissions"]
    assert "commissions.pay" in finance_perms
    assert "commissions.approve" in finance_perms
    # But FINANCE still cannot touch commercial-config/pricing authority.
    assert "pricing.override" not in finance_perms
    assert "device_policy.manage" not in finance_perms
