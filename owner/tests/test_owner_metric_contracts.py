"""AUDIT-owner-cross-screen: the contract tests that make a dashboard-key
rename fail here instead of silently blanking a screen.

The five cross-screen metric bugs fixed alongside this file all shared one
root cause: every consumer bound to a service dict by loose string key, so
a producer-side rename raised nothing anywhere. Jinja's default Undefined
renders as the empty string ("Outstanding invoice total" on the Finance
dashboard was permanently blank for exactly this reason, while the JSON
twin beside it showed real money), and jsonify() will happily ship a key
no consumer reads.

Two directions are checked, and both are checked against the REAL thing,
never a fixture copy of it:

  * consumer -> contract: every key a consuming template names on its
    payload -- ``data.key``, ``data["key"]`` or ``data.get("key")`` -- is
    parsed out of the template file on disk and must be a field of that
    payload's contract. This is the check that would have caught the
    blank Finance figure the moment the service renamed its key. A key
    named by anything other than a literal (``data[some_var]``, or a
    payload aliased with ``{% set %}`` before it is read) is outside the
    scan and is documented as such in app/metrics_contracts.py rather
    than quietly claimed as covered.
  * producer -> contract: each service function is called against a real
    database and its payload's key set must equal the contract's field
    set exactly -- no missing key, no undeclared extra.

Plus the JSON twins: the four dashboard API routes must answer with
exactly the contract's fields, so the HTML screen and its API twin can
never again disagree about which keys exist.
"""
from __future__ import annotations

import os
import re
from datetime import date

from app.metrics_contracts import (
    EmployeeCommercialDashboard,
    FinanceCommercialDashboard,
    FinanceOperationalDashboard,
    ManagementOperationalDashboard,
)
from tests.conftest import force_login, make_staff

OWNER_ROOT = os.path.dirname(os.path.dirname(__file__))
TEMPLATES_ROOT = os.path.join(OWNER_ROOT, "app", "templates")

# Every consuming template, paired with the contract that owns the `data`
# it is rendered with. Adding a dashboard here is the whole cost of
# keeping it protected.
TEMPLATE_CONTRACTS = (
    ("commercial_sales/employee_dashboard.html", EmployeeCommercialDashboard),
    ("commercial_sales/finance_dashboard.html", FinanceCommercialDashboard),
    ("operations_ui/dashboard_management.html", ManagementOperationalDashboard),
    ("operations_ui/dashboard_finance.html", FinanceOperationalDashboard),
)

# Every way a Jinja template can actually read a key off the `data` dict:
#
#   data.foo / data.foo.bar / data.foo.items()  -> "foo" (only the first
#       segment, which is the key the payload itself owns)
#   data['foo'] / data["foo"]                   -> "foo"
#   data.get('foo') / data.get('foo', default)  -> "foo"
#
# The `.get(...)` alternative must be tried BEFORE the plain-attribute one,
# or `data.get("outstanding_invoice_totals")` would be recorded as a bind to
# a key literally named "get" -- which no contract declares, so the check
# would fail on a correct template while the real key went unchecked.
_DATA_KEY_PATTERN = re.compile(
    r"""\bdata(?:
          \.get\(\s*['"](?P<get_key>[^'"]+)['"]                # data.get("foo")
        | \[\s*['"](?P<item_key>[^'"]+)['"]\s*\]               # data["foo"]
        | \.(?P<attr_key>[A-Za-z_][A-Za-z0-9_]*)               # data.foo
      )""",
    re.VERBOSE,
)

_employee_number_counter = iter(range(600000, 700000))


def _extract_data_keys(source: str) -> set[str]:
    """Every payload key `source` binds, across all three access styles."""
    return {match.group("get_key") or match.group("item_key") or match.group("attr_key")
            for match in _DATA_KEY_PATTERN.finditer(source)}


def _template_data_keys(relative_path: str) -> set[str]:
    with open(os.path.join(TEMPLATES_ROOT, relative_path), encoding="utf-8") as handle:
        return _extract_data_keys(handle.read())


def _seed_super_admin_with_profile(app, email: str):
    """A SUPER_ADMIN (so every dashboard permission gate passes) that also
    owns a real EmployeeProfile (so the own-scope employee dashboard,
    which refuses to run without one, is reachable)."""
    from app.employees.services import create_employee_profile

    staff_id = make_staff(app, email, super_admin=True)
    with app.app_context():
        profile = create_employee_profile(
            {
                "staff_user_id": staff_id,
                "employee_number": f"EMP-{next(_employee_number_counter):05d}",
                "full_name": "Contract Test Staff",
                "employment_start_date": date(2026, 1, 1),
            },
            actor_staff_user_id=staff_id,
        )
        return staff_id, profile.id


# ------------------------------------------------- consumer -> contract --

def test_data_key_scan_covers_every_access_style_jinja_actually_supports():
    """The guarantee this whole module advertises is "a renamed producer key
    that a consumer missed fails a test". That guarantee is only as wide as
    this scan. It originally matched attribute access alone, so
    `data['outstanding_invoice_total']` -- identical in effect, and equally
    silent when it goes stale, because Jinja's Undefined renders as the
    empty string either way -- was quietly unprotected while the docstring
    promised otherwise. An overstated guarantee on a control that exists to
    stop silent renames is worse than a narrow one, so the scan is widened
    here and pinned."""
    assert _extract_data_keys("{{ data.outstanding_invoice_totals }}") == {"outstanding_invoice_totals"}
    assert _extract_data_keys("{{ data['outstanding_invoice_totals'] }}") == {"outstanding_invoice_totals"}
    assert _extract_data_keys('{{ data["outstanding_invoice_totals"] }}') == {"outstanding_invoice_totals"}
    assert _extract_data_keys("{{ data.get('outstanding_invoice_totals', {}) }}") == {"outstanding_invoice_totals"}
    # Only the first segment is the payload's own key; the rest belongs to
    # the nested value and is not the contract's business.
    assert _extract_data_keys("{% for k, v in data.outstanding_invoice_totals.items() %}") == {"outstanding_invoice_totals"}
    # `data.get(...)` must never be recorded as a bind to a key named "get".
    assert "get" not in _extract_data_keys("{{ data.get('currency') }}")
    # A different variable that merely ends in "data" is not this payload.
    assert _extract_data_keys("{{ metadata.currency }}{{ mydata['currency'] }}") == set()


def test_dashboard_templates_bind_only_fields_their_contract_declares():
    """The regression that motivated this whole file: finance_dashboard.html
    read `data.outstanding_invoice_total` long after the service renamed it
    to the per-currency `outstanding_invoice_totals`, and nothing anywhere
    complained -- the row just rendered empty."""
    for relative_path, contract in TEMPLATE_CONTRACTS:
        undeclared = _template_data_keys(relative_path) - contract.field_names()
        assert not undeclared, (
            f"{relative_path} binds {sorted(undeclared)}, which {contract.__name__} does not declare -- "
            "either the service renamed the key and this template still reads the old name "
            "(it will render blank, not error), or the contract is missing a field."
        )


# ------------------------------------------------- producer -> contract --

def test_employee_commercial_dashboard_payload_matches_contract(app, seeded):
    from app.commercial_sales.dashboards import employee_commercial_dashboard

    staff_id, profile_id = _seed_super_admin_with_profile(app, "contract-emp-comm@example.com")
    with app.app_context():
        payload = employee_commercial_dashboard(profile_id, staff_id, "USD")
    assert set(payload) == EmployeeCommercialDashboard.field_names()


def test_finance_commercial_dashboard_payload_matches_contract(app, seeded):
    from app.commercial_sales.dashboards import finance_commercial_dashboard

    with app.app_context():
        payload = finance_commercial_dashboard()
    assert set(payload) == FinanceCommercialDashboard.field_names()


def test_management_operational_dashboard_payload_matches_contract(app, seeded):
    from app.operational_reports.dashboards import management_operational_dashboard

    with app.app_context():
        payload = management_operational_dashboard("USD")
    assert set(payload) == ManagementOperationalDashboard.field_names()


def test_finance_operational_dashboard_payload_matches_contract(app, seeded):
    from app.operational_reports.dashboards import finance_operational_dashboard

    with app.app_context():
        payload = finance_operational_dashboard("USD")
    assert set(payload) == FinanceOperationalDashboard.field_names()


# --------------------------------------------------- JSON twin -> contract --

def test_json_dashboard_routes_answer_with_exactly_their_contract_fields(app, client, seeded):
    """The JSON twin of each screen is a second consumer of the same
    payload, and it drifted from the HTML screen once already (it was
    updated for the outstanding_invoice_totals rename; the template was
    not). Both are pinned to the same contract here."""
    staff_id, _profile_id = _seed_super_admin_with_profile(app, "contract-json@example.com")
    force_login(client, app, staff_id)

    for url, contract in (
        ("/api/operations/v1/commercial/dashboard/employee", EmployeeCommercialDashboard),
        ("/api/operations/v1/commercial/dashboard/finance", FinanceCommercialDashboard),
        ("/api/operations/v1/dashboards/management-operations", ManagementOperationalDashboard),
        ("/api/operations/v1/dashboards/finance-operations", FinanceOperationalDashboard),
    ):
        response = client.get(url)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        assert set(response.get_json()) == contract.field_names(), url
