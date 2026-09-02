"""Aura Owner -- every route an employee needs must have a doorway.

WHY THIS EXISTS.

Same defect class this cycle closed in Retail (see
products/retail/tests/retail_route_reachability_test.py, which this file
mirrors deliberately): a route that is written, validated, capability-gated,
even tested in isolation -- and reachable by nothing a real client ever
calls. Reading the code finds nothing wrong with it. Only asking "can
anybody actually GET here" finds it.

Owner is architecturally different from Retail in the one way that matters
for this check: Retail's frontend is vanilla JS calling literal fetch
paths, so the retail guard keys on the longest literal PATH segment. Owner
is server-rendered Jinja, and its dominant reference form is
`url_for('blueprint.endpoint', ...)` -- ENDPOINT NAMES, not URL paths. Since
Flask enforces globally-unique endpoint names (two routes can never share
one), keying this guard on the full endpoint name is both correct and,
unlike a path-segment key, collision-free by construction -- Owner has
`leads.detail`, `customers.detail`, `employees.detail`, `staff.detail`,
`subscriptions.detail`, `installations.detail` all sharing the path
segment "detail", which would have made a path-keyed version of this guard
useless here.

A NAIVE `url_for('blueprint.endpoint')`-literal grep is not enough, and
undershooting it silently is exactly the false-negative trap the retail
file's own `_search_key()` docstring warns about ("a guard whose failure
mode is a false NEGATIVE is worse than no guard, because it is trusted").
Two real indirections this file had to account for, found by reading the
actual templates rather than assuming the naive grep was complete:

  1. `layout/_nav_model.html` and `command_palette/service.py` build the
     sidebar and command-palette destinations from data -- a list of
     `{'endpoint': 'blueprint.func', 'label': ...}` dicts, then call
     `url_for(link.endpoint)` with a VARIABLE, not a literal. A dozen real,
     reachable routes (audit.security_events, every commercial_sales_web
     dashboard/list view, several operations_ui views, etc.) are reachable
     ONLY through this indirection. Missing it would have wrongly reported
     a dozen genuinely-fine routes as gaps -- the opposite failure mode,
     but still a guard that cannot be trusted.
  2. Exactly six routes (`auth.login_submit`, `mfa_verify_submit`,
     `mfa_enroll_submit`, `reauth_submit`, `change_password_submit`,
     `accept_invitation_submit`) are POST siblings of a GET form page,
     reached by `<form method="post">` with NO `action` attribute --
     the browser posts back to whatever URL rendered the page. Verified by
     grepping every `<form method="post">` in the entire templates tree:
     exactly these six lack an `action`, all under templates/auth/*.html,
     nowhere else. (A generic "same URL path is reachable if any sibling
     method is" heuristic was tried and REJECTED here -- every other form
     in the app sets an explicit `action="{{ url_for(...) }}"`, so that
     heuristic produced a false accept for `customers.update`, which really
     is unreachable. Precision over convenience.)

Because a redirect the SERVER issues is still a URL the browser follows,
this file's "client" also includes `redirect(url_for(...))` call sites in
Owner's own Python (e.g. the MFA-recovery-codes page, reached only via a
post-enrollment redirect, never a template link) -- not just Jinja/JS.

THE RULE: every Owner route is either referenced by a shipped client
(a template, the static JS, or a server-side redirect a browser follows),
or named below with a REASON. There is no third state.

Six real, previously-undiscovered gaps came out of writing this file (see
KNOWN_GAPS): lead edit/assign/archive and customer edit have complete,
permission-gated backends and no UI control; a followup can be completed
but never cancelled from the UI; a license's offline policy can never be
assigned and its resolved entitlements never previewed; an installation's
device can never be replaced from the UI (a different, unrelated
`commercial_ops_ui.replace_installation` -- device-SLOT replacement -- is
wired and is not a duplicate); a report snapshot can be generated but never
regenerated; product-version/release-channel views have real templates and
no link to them anywhere; and a subscription payment can be recorded but
never corrected.

Separately, ~150 machine-only routes are real and complete but were BUILT
deliberately ahead of any client and documented as such at build time --
see the reasons below for each family. That is the same "deferred in
writing" shape as retail's modifier-groups case, not an accident.

Run:
    pytest owner/tests/test_route_reachability.py -v
"""
from __future__ import annotations

import io
import re
from pathlib import Path

OWNER_DIR = Path(__file__).resolve().parents[1]
APP_DIR = OWNER_DIR / 'app'
TEMPLATES_DIR = APP_DIR / 'templates'
STATIC_DIR = APP_DIR / 'static'
APP_INIT = APP_DIR / '__init__.py'

# Directories under owner/app that are never route/client source: caches,
# the test suite itself (not a shipped client), and Alembic's own tree.
_EXCLUDED_DIR_PARTS = {'__pycache__', 'tests', 'migrations'}

_BLUEPRINT_DEF_RE = re.compile(
    r"^(\w+)\s*=\s*Blueprint\(\s*['\"]([^'\"]+)['\"]\s*,\s*__name__"
    r"(?:\s*,\s*url_prefix=['\"]([^'\"]*)['\"])?", re.M)

# `route|get|post|put|patch|delete` covers both `@bp.route(path,
# methods=[...])` and the Flask 2 shorthand `@bp.get(path)` this codebase
# also uses (owner/app/health.py). The path group uses `[^'\"]*` --
# ZERO or more -- not `+`: a blueprint mounted at its own url_prefix root
# registers its collection route as `@bp.route("", methods=[...])`, an
# EMPTY string. Requiring 1+ chars here silently dropped 21 real routes
# (dashboard.index, customers.create/list_customers, leads.create/
# list_leads, and 17 more) out of the scanner entirely on an earlier pass
# of this file -- not misclassified, structurally invisible, which is a
# worse failure than any classification mistake this file could make.
_DECORATOR_RE = re.compile(
    r"^@(\w+)\.(route|get|post|put|patch|delete)\(\s*['\"]([^'\"]*)['\"]"
    r"(?:\s*,\s*methods=\[([^\]]*)\])?")
_DEF_RE = re.compile(r"^(?:async\s+)?def\s+(\w+)\s*\(")

# Owner's context processor (app/__init__.py) is the ONE place a route is
# registered directly on `app` rather than a Blueprint (`/healthz`, for a
# process supervisor / load balancer -- see the health-check reason below).
# Flask's endpoint name for an app-level route is just the function name,
# no blueprint prefix.
_APP_LEVEL_ROUTE_RE = re.compile(
    r"^\s*@app\.(route|get|post|put|patch|delete)\(\s*['\"]([^'\"]*)['\"]")


def _iter_source_py_files():
    for p in APP_DIR.rglob('*.py'):
        if any(part in _EXCLUDED_DIR_PARTS for part in p.parts):
            continue
        yield p


def _parse_blueprints(src):
    """var_name -> (registered_blueprint_name, url_prefix), scoped to one file."""
    out = {}
    for m in _BLUEPRINT_DEF_RE.finditer(src):
        out[m.group(1)] = (m.group(2), m.group(3) or '')
    return out


def _parse_routes_in_file(path):
    """Every `@var.route/get/post/put/patch/delete(...)` in one file, paired
    with the function name that follows -- possibly past other decorators
    (permission checks sit between the route decorator and `def` in nearly
    every route in this codebase, e.g. `@require_permission(...)`)."""
    src = io.open(path, encoding='utf-8').read()
    blueprints = _parse_blueprints(src)
    lines = src.split('\n')
    found = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        m = _DECORATOR_RE.match(stripped)
        # `_DECORATOR_RE`'s var group is `\w+`, so it also matches `@app.get(
        # ...)` (var='app') -- which is never a real Blueprint variable, so
        # falling straight into "not a real Blueprint var, skip" below would
        # silently drop the one app-level route (/healthz) instead of
        # recognizing it. Try the app-level match whenever the blueprint
        # lookup would otherwise fail, not only when `m` didn't match at all.
        var = m.group(1) if m else None
        app_m = _APP_LEVEL_ROUTE_RE.match(line) if (not m or var not in blueprints) else None
        if not m and not app_m:
            continue
        # Walk forward past any further decorator lines to the def.
        funcname = None
        for j in range(i + 1, len(lines)):
            nxt = lines[j].strip()
            if nxt.startswith('@'):
                continue
            dm = _DEF_RE.match(nxt)
            if dm:
                funcname = dm.group(1)
            break
        if funcname is None:
            continue
        if m and var in blueprints:
            _var, call, route_path, methods_raw = m.groups()
            blueprint_name, prefix = blueprints[var]
            endpoint = f'{blueprint_name}.{funcname}'
        elif app_m:
            call, route_path = app_m.groups()
            methods_raw = None
            endpoint = funcname  # app-level route: no blueprint prefix
        else:
            continue  # a decorator on something that isn't a real Blueprint var and isn't @app either
        methods = (methods_raw or "'GET'").replace("'", '').replace('"', '').replace(' ', '') \
            if call == 'route' else call.upper()
        found.append({
            'endpoint': endpoint, 'path': route_path, 'methods': methods,
            'file': path, 'lineno': i + 1,
        })
    return found


def _routes():
    """Every route Owner registers, across every blueprint file plus the one
    app-level route in app/__init__.py."""
    found = []
    for path in _iter_source_py_files():
        found.extend(_parse_routes_in_file(path))
    return found


_URLFOR_RE = re.compile(r"url_for\(\s*['\"]([A-Za-z0-9_]+\.[A-Za-z0-9_]+)['\"]")
# layout/_nav_model.html and command_palette/service.py both build nav
# destinations as `{'endpoint': 'blueprint.func', ...}` data, then call
# `url_for(link.endpoint)` -- a variable, invisible to the regex above. See
# module docstring point 1.
_ENDPOINT_KEY_RE = re.compile(r"['\"]endpoint['\"]\s*:\s*['\"]([A-Za-z0-9_]+\.[A-Za-z0-9_]+)['\"]")


def _client_source():
    """Everything a real employee's browser could be sent to, or navigate
    itself to: every Jinja template, the static JS, AND Owner's own Python
    (route handlers issue `redirect(url_for(...))`, which a real browser
    follows -- the MFA-recovery-codes page is reachable ONLY this way, no
    template links to it). Test/migration code is excluded (not a client)."""
    chunks = []
    for p in TEMPLATES_DIR.rglob('*.html'):
        chunks.append(io.open(p, encoding='utf-8', errors='replace').read())
    for p in STATIC_DIR.rglob('*.js'):
        chunks.append(io.open(p, encoding='utf-8', errors='replace').read())
    for p in _iter_source_py_files():
        chunks.append(io.open(p, encoding='utf-8', errors='replace').read())
    return '\n'.join(chunks)


def _referenced_endpoints(client_text):
    refs = set(m.group(1) for m in _URLFOR_RE.finditer(client_text))
    refs |= set(m.group(1) for m in _ENDPOINT_KEY_RE.finditer(client_text))
    return refs


# ---------------------------------------------------------------------------
# INTENTIONALLY_UNREACHABLE -- deliberate, written decisions.
#
# KEEP THIS HONEST. Every family below was verified by reading the actual
# route AND grepping for whether anything calls it, not assumed from the
# blueprint's name. "We have not built the client yet" is a legitimate
# reason when it is written down at build time (as it is here, repeatedly,
# across five phase docs) -- it stops being legitimate the moment someone
# adds an entry here just to make the test pass without doing that reading.
# ---------------------------------------------------------------------------

_MOBILE_READY_NOT_MOBILE_BUILT_REASON = (
    "Part of the /api/operations/v1 JSON API, built deliberately ahead of "
    "any client and documented as such at build time, repeatedly, across "
    "five phase docs -- not a guess made to close this test. "
    "docs/owner/phase9_5a/internal-api-contract.md: \"Status this phase: "
    "contract only ... matching the 'mobile-ready, not mobile-built' / "
    "'foundation only' boundary.\" Reaffirmed in "
    "docs/owner/phase9_5b/final-residual-risk-register.md ('no mobile "
    "client exists to consume them yet'), phase9_5b_r/final-residual-risk-"
    "register.md ('No mobile client, no /api/operations/v1/auth/mobile/* "
    "routes'), phase9_5a/mobile-authentication-adr.md, and "
    "phase9_5a/openapi.yaml ('mobile-ready schema, mobile app not built'). "
    "owner/README.md documents the final shape explicitly: \"A 35-route "
    "/api/operations/v1 API and a 32-route web UI (11 templates) sit on "
    "top\" -- two parallel, deliberately separate surfaces, not one "
    "wired to the other. Every route in this family delegates to the "
    "exact same service functions its web-UI sibling route calls (see "
    "api_operations/crm.py's own module docstring), so the browser-facing "
    "half of this feature is NOT missing -- it lives at a different, "
    "already-reachable route (leads.*, customers.*, commercial_sales_web.*, "
    "operations_ui.*). Verified empirically, not just by doc citation: "
    "none of these endpoint names appear in any template, the static JS, "
    "or any url_for()/redirect() call anywhere in owner/app."
)

_MOBILE_API_ENDPOINTS = (
    # api_operations/routes.py -- employee self-service, presence, admin
    # employee CRUD (the web-UI equivalents are employees.*, profile.*).
    'api_operations.activate_route',
    'api_operations.archive_route',
    'api_operations.create_employee_route',
    'api_operations.employees_presence',
    'api_operations.get_employee_roles_route',
    'api_operations.get_employee_route',
    'api_operations.list_employees_route',
    'api_operations.me',
    'api_operations.my_sessions',
    'api_operations.presence_heartbeat',
    'api_operations.put_employee_roles_route',
    'api_operations.reactivate_route',
    'api_operations.revoke_employee_sessions_route',
    'api_operations.revoke_my_session',
    'api_operations.suspend_route',
    'api_operations.terminate_route',
    'api_operations.update_employee_route',
    'api_operations.update_me',
    # api_operations/crm.py -- leads/customers/contacts/interactions/notes/
    # locations/followups (web-UI equivalents: leads.*, customers.*, crm_shared.*).
    'api_operations_crm.add_customer_contact_route',
    'api_operations_crm.add_customer_interaction_route',
    'api_operations_crm.add_customer_note_route',
    'api_operations_crm.add_lead_contact_route',
    'api_operations_crm.add_lead_interaction_route',
    'api_operations_crm.add_lead_note_route',
    'api_operations_crm.cancel_followup_route',
    'api_operations_crm.capture_customer_location_route',
    'api_operations_crm.capture_lead_location_route',
    'api_operations_crm.complete_followup_route',
    'api_operations_crm.create_customer_followup_route',
    'api_operations_crm.create_lead_followup_route',
    'api_operations_crm.create_lead_route',
    'api_operations_crm.customer_archive_route',
    'api_operations_crm.customer_assign_route',
    'api_operations_crm.customer_assignment_history_route',
    'api_operations_crm.get_customer_route',
    'api_operations_crm.get_lead_route',
    'api_operations_crm.lead_archive_route',
    'api_operations_crm.lead_assign_route',
    'api_operations_crm.lead_assignment_history_route',
    'api_operations_crm.lead_convert_route',
    'api_operations_crm.lead_status_history_route',
    'api_operations_crm.lead_status_route',
    'api_operations_crm.list_customer_contacts_route',
    'api_operations_crm.list_customer_interactions_route',
    'api_operations_crm.list_customer_locations_route',
    'api_operations_crm.list_customer_notes_route',
    'api_operations_crm.list_customers_route',
    'api_operations_crm.list_followups_route',
    'api_operations_crm.list_lead_contacts_route',
    'api_operations_crm.list_lead_interactions_route',
    'api_operations_crm.list_lead_locations_route',
    'api_operations_crm.list_lead_notes_route',
    'api_operations_crm.list_leads_route',
    'api_operations_crm.update_customer_route',
    'api_operations_crm.update_lead_route',
    'api_operations_crm.verify_location_route',
    # api_operations/commercial_sales.py -- quotes/orders/invoices/payments/
    # refunds/commissions (web-UI equivalent: commercial_sales_web.*).
    'api_operations_commercial_sales.add_quote_line_route',
    'api_operations_commercial_sales.allocate_payment_route',
    'api_operations_commercial_sales.approve_commission_route',
    'api_operations_commercial_sales.approve_payout_batch_route',
    'api_operations_commercial_sales.approve_refund_route',
    'api_operations_commercial_sales.cancel_order_route',
    'api_operations_commercial_sales.cancel_quote_route',
    'api_operations_commercial_sales.confirm_order_route',
    'api_operations_commercial_sales.confirm_payment_route',
    'api_operations_commercial_sales.confirm_refund_route',
    'api_operations_commercial_sales.create_invoice_route',
    'api_operations_commercial_sales.create_order_route',
    'api_operations_commercial_sales.create_payout_batch_route',
    'api_operations_commercial_sales.create_quote_route',
    'api_operations_commercial_sales.create_refund_route',
    'api_operations_commercial_sales.decide_approval_route',
    'api_operations_commercial_sales.employee_commercial_dashboard_route',
    'api_operations_commercial_sales.finance_commercial_dashboard_route',
    'api_operations_commercial_sales.fulfill_order_route',
    'api_operations_commercial_sales.get_invoice_route',
    'api_operations_commercial_sales.get_order_route',
    'api_operations_commercial_sales.get_payment_route',
    'api_operations_commercial_sales.get_quote_route',
    'api_operations_commercial_sales.get_refund_route',
    'api_operations_commercial_sales.issue_invoice_route',
    'api_operations_commercial_sales.list_commissions_route',
    'api_operations_commercial_sales.list_invoices_route',
    'api_operations_commercial_sales.list_orders_route',
    'api_operations_commercial_sales.list_quotes_route',
    'api_operations_commercial_sales.quote_decision_route',
    'api_operations_commercial_sales.record_payout_route',
    'api_operations_commercial_sales.reject_payment_route',
    'api_operations_commercial_sales.remove_quote_line_route',
    'api_operations_commercial_sales.reverse_allocation_route',
    'api_operations_commercial_sales.submit_payment_route',
    'api_operations_commercial_sales.submit_quote_route',
    'api_operations_commercial_sales.void_invoice_route',
    'api_operations_commercial_sales.void_refund_route',
    # api_operations/expenses_and_operations.py -- expenses/payees/cash
    # closings/report snapshots/management notes (web-UI equivalent: operations_ui.*).
    'api_operations_expenses.add_adjustment_route',
    'api_operations_expenses.add_management_note_comment_route',
    'api_operations_expenses.approve_adjustment_route',
    'api_operations_expenses.archive_attachment_route',
    'api_operations_expenses.assign_management_note_route',
    'api_operations_expenses.cash_closings_route',
    'api_operations_expenses.close_closing_route',
    'api_operations_expenses.create_expense_route',
    'api_operations_expenses.create_management_note_route',
    'api_operations_expenses.create_payee_route',
    'api_operations_expenses.deactivate_payee_route',
    'api_operations_expenses.decide_closing_route',
    'api_operations_expenses.decide_expense_approval_route',
    'api_operations_expenses.download_attachment_route',
    'api_operations_expenses.employee_expense_dashboard_route',
    'api_operations_expenses.expense_duplicate_signals_route',
    'api_operations_expenses.finance_operational_dashboard_route',
    'api_operations_expenses.generate_report_snapshot_route',
    'api_operations_expenses.get_expense_route',
    'api_operations_expenses.get_management_note_route',
    'api_operations_expenses.get_pending_approval_route',
    'api_operations_expenses.list_expense_categories_route',
    'api_operations_expenses.list_expenses_route',
    'api_operations_expenses.list_management_notes_route',
    'api_operations_expenses.list_payees_route',
    'api_operations_expenses.list_report_snapshots_route',
    'api_operations_expenses.management_operational_dashboard_route',
    'api_operations_expenses.override_duplicate_route',
    'api_operations_expenses.record_expense_payment_route',
    'api_operations_expenses.regenerate_report_snapshot_route',
    'api_operations_expenses.reopen_closing_route',
    'api_operations_expenses.reverse_expense_payment_route',
    'api_operations_expenses.revise_expense_route',
    'api_operations_expenses.set_management_note_status_route',
    'api_operations_expenses.submit_closing_route',
    'api_operations_expenses.submit_expense_route',
    'api_operations_expenses.upload_attachment_route',
    'api_operations_expenses.void_expense_route',
)

_COMMERCIAL_OPS_MACHINE_REASON = (
    "commercial_ops/routes.py is the base JSON blueprint (`/commercial-ops`), "
    "a SEPARATE Flask blueprint from commercial_ops/ui_routes.py (`/commercial-"
    "ops/ui`, the actual browser-facing renewals UI, linked from the sidebar "
    "as commercial_ops_ui.list_renewals and reachable). "
    "docs/owner/phase9_5b_r2/unreachable-and-future-surface-report.md names "
    "`commercial_ops (base JSON API)` explicitly, alongside api/api_external/"
    "api_operations/locale/health/licensing_api, as one of '7 blueprints, "
    "all JSON/redirect-only, zero render_template calls between them' -- "
    "confirmed out of scope for translation for the same reason it is out "
    "of scope for a browser link: it is a machine surface, not a page."
)

_COMMERCIAL_OPS_MACHINE_ENDPOINTS = (
    'commercial_ops.apply_renewal',
    'commercial_ops.approve_renewal',
    'commercial_ops.create_renewal',
    'commercial_ops.get_renewal',
    'commercial_ops.list_renewals',
    'commercial_ops.transition_renewal',
)

_EXTERNAL_PRODUCT_API_REASON = (
    "The /api/v1 external_api blueprint, gated behind EXTERNAL_API_ENABLED "
    "and never registered otherwise (see app/__init__.py's own comment: "
    "'no route for an external activation/check-in/deactivation/sync "
    "request to reach'). Consumed by a Retail or Clinic product install "
    "during its own version-check/registration flow, not by an Owner "
    "employee's browser -- confirmed by owner/contracts/product-version-"
    "check-v1.schema.json and installation-registration-v1.schema.json, "
    "which document the wire format for exactly those two callers."
)

_EXTERNAL_PRODUCT_API_ENDPOINTS = (
    'external_api.installation_registration',
    'external_api.product_version_check',
)

_EXTERNAL_LICENSING_API_REASON = (
    "The /api/licensing/v1 (api_external_licensing) blueprint: device "
    "activation/check-in/deactivation, signing-key distribution, and "
    "release-download authorization, gated behind EXTERNAL_API_ENABLED. "
    "Consumed by a product install's own licensing client "
    "(commercial_runtime/licensing_contracts), authenticated by device "
    "Ed25519 signature + nonce rather than a browser session -- which is "
    "exactly why app/__init__.py explicitly CSRF-exempts this blueprint "
    "('this API is authenticated by device Ed25519 signatures + nonces "
    "instead ... consumed by non-browser clients with no session'). "
    "owner/contracts/activation-*-v1.schema.json, license-check-*-v1."
    "schema.json, and entitlement-response-v1.schema.json document the "
    "wire contract for that caller, not for Owner's own UI."
)

_EXTERNAL_LICENSING_API_ENDPOINTS = (
    'api_external_licensing.activation_check',
    'api_external_licensing.activations',
    'api_external_licensing.authorize_release_download',
    'api_external_licensing.check_ins',
    'api_external_licensing.deactivations',
    'api_external_licensing.fetch_release_download',
    'api_external_licensing.service_info',
    'api_external_licensing.signing_keys',
)

_EXTERNAL_SYNC_API_REASON = (
    "The /api/sync/v1 (sync) blueprint -- the multi-device sync relay's "
    "push/pull endpoints, gated behind EXTERNAL_API_ENABLED. Per the "
    "module's own docstring, authentication 'mirrors owner/app/"
    "licensing_service/checkin.py's device-signature-verification "
    "pattern' -- the same non-browser, Ed25519-signed, nonce-protected "
    "shape as the licensing API above, which is why app/__init__.py "
    "CSRF-exempts this blueprint for the identical reason. Consumed by "
    "Retail/Clinic's own commercial_runtime/sync desktop relay client and "
    "the future KMP mobile app's Ktor transport (named explicitly in the "
    "module docstring's 'downstream client tasks'), never by an Owner "
    "employee's browser. sync_quarantine (a DIFFERENT blueprint, `/sync`) "
    "is Owner's own UI over this data and IS reachable -- linked from "
    "licensing_admin/status.html -- so this is not a blanket 'sync is "
    "unreachable' claim, only the raw device-facing relay endpoints."
)

_EXTERNAL_SYNC_API_ENDPOINTS = (
    'sync.pull',
    'sync.push',
)

_HEALTH_CHECK_REASON = (
    "Liveness/readiness endpoints for a process supervisor or reverse "
    "proxy, not a page: /health/live answers 'should this worker be "
    "killed and restarted' (health.py's own docstring), /health/ready "
    "answers 'should the reverse proxy route traffic here' via real DB/"
    "migration/preflight checks, and /healthz (registered directly on "
    "`app`, not a blueprint -- app/__init__.py) is the bare liveness probe "
    "a load balancer hits before either of those exist. None of the three "
    "return HTML or are ever the target of a browser navigation."
)

_HEALTH_CHECK_ENDPOINTS = (
    'health.live',
    'health.ready',
    'healthz',
)

_IMPLICIT_FORM_SIBLING_REASON = (
    "POST sibling of a GET form-page route, reached by `<form "
    "method=\"post\">` with NO `action` attribute in the corresponding "
    "templates/auth/*.html template -- the browser posts back to "
    "whatever URL rendered the page, so this endpoint is genuinely "
    "reachable, just invisible to a url_for()-literal search because no "
    "url_for() call anywhere names it. Verified by grepping every `<form "
    "method=\"post\">` in the entire owner/app/templates tree: exactly "
    "six lack an action attribute, all six are these auth routes, and "
    "nowhere else in the app uses this pattern (every other form in this "
    "codebase sets an explicit action=\"{{ url_for(...) }}\")."
)

_IMPLICIT_FORM_SIBLING_ENDPOINTS = (
    'auth.accept_invitation_submit',
    'auth.change_password_submit',
    'auth.login_submit',
    'auth.mfa_enroll_submit',
    'auth.mfa_verify_submit',
    'auth.reauth_submit',
)

INTENTIONALLY_UNREACHABLE = {}
INTENTIONALLY_UNREACHABLE.update({e: _MOBILE_READY_NOT_MOBILE_BUILT_REASON for e in _MOBILE_API_ENDPOINTS})
INTENTIONALLY_UNREACHABLE.update({e: _COMMERCIAL_OPS_MACHINE_REASON for e in _COMMERCIAL_OPS_MACHINE_ENDPOINTS})
INTENTIONALLY_UNREACHABLE.update({e: _EXTERNAL_PRODUCT_API_REASON for e in _EXTERNAL_PRODUCT_API_ENDPOINTS})
INTENTIONALLY_UNREACHABLE.update({e: _EXTERNAL_LICENSING_API_REASON for e in _EXTERNAL_LICENSING_API_ENDPOINTS})
INTENTIONALLY_UNREACHABLE.update({e: _EXTERNAL_SYNC_API_REASON for e in _EXTERNAL_SYNC_API_ENDPOINTS})
INTENTIONALLY_UNREACHABLE.update({e: _HEALTH_CHECK_REASON for e in _HEALTH_CHECK_ENDPOINTS})
INTENTIONALLY_UNREACHABLE.update({e: _IMPLICIT_FORM_SIBLING_REASON for e in _IMPLICIT_FORM_SIBLING_ENDPOINTS})

assert len(INTENTIONALLY_UNREACHABLE) == (
    len(_MOBILE_API_ENDPOINTS) + len(_COMMERCIAL_OPS_MACHINE_ENDPOINTS)
    + len(_EXTERNAL_PRODUCT_API_ENDPOINTS) + len(_EXTERNAL_LICENSING_API_ENDPOINTS)
    + len(_EXTERNAL_SYNC_API_ENDPOINTS) + len(_HEALTH_CHECK_ENDPOINTS)
    + len(_IMPLICIT_FORM_SIBLING_ENDPOINTS)
), "duplicate endpoint name across the INTENTIONALLY_UNREACHABLE source tuples -- fix the typo, don't just let one silently overwrite another"


#: Routes that are unreachable and SHOULD NOT BE. Each is a real gap with an
#: owner. This list exists so the number stays visible and shrinking rather
#: than being rediscovered by an employee. Every entry is a promise to fix,
#: or to consciously downgrade to the map above.
KNOWN_GAPS = {
    'customers.update':
        "Complete, permission-gated handler (leads.update_own/update_all-"
        "equivalent: customers.update_own/update_all) for editing a "
        "customer's legal_name/trade_name/country/city/commercial_"
        "registration_reference exists in customers/routes.py, but "
        "customers/detail.html renders every one of those fields read-only "
        "-- no edit form anywhere on the page targets this route. Real "
        "gap, no owner assigned yet.",
    'leads.update':
        "Complete, permission-gated handler for editing a lead's core "
        "fields (organization/contact name, phone, email, source, "
        "priority) exists in leads/routes.py, but leads/detail.html has "
        "no edit form -- only status-change, convert, contact/interaction/"
        "followup/note/location forms are wired. Real gap.",
    'leads.assign':
        "Complete, permission-gated (leads.assign) handler for "
        "reassigning a lead to a different employee exists in leads/"
        "routes.py, but leads/detail.html has no assign control. Same "
        "gap as leads.update -- the detail page's action set was never "
        "extended to cover edit/assign/archive.",
    'leads.archive':
        "Complete, permission-gated (leads.archive) handler for archiving "
        "a lead exists in leads/routes.py, but leads/detail.html has no "
        "archive button -- unlike customers/detail.html, which DOES wire "
        "customers.archive. The asymmetry between the two detail pages is "
        "itself evidence this was missed, not deferred on purpose.",
    'crm_shared.cancel_followup':
        "Complete, permission-gated handler for cancelling an open "
        "followup (with a reason) exists in leads/routes.py on the shared "
        "crm_shared blueprint, and its sibling crm_shared.complete_"
        "followup IS wired -- both leads/detail.html and customers/"
        "detail.html render a 'complete' button per open followup, but "
        "neither renders a 'cancel' button, even though the backend "
        "supports both outcomes identically.",
    'licensing_admin.replace_device':
        "Complete, permission-gated (installations.replace_device, plus "
        "@require_recent_auth) handler that revokes an installation's "
        "active device key exists in licensing_admin/routes.py, but "
        "installations/detail.html has no control for it. Not a "
        "duplicate of the adjacent commercial_ops_ui.replace_installation "
        "form that IS on that page -- that one calls replace_device_slot "
        "(a device-SLOT/entitlement concept), this one calls device_"
        "identity.revoke_device_key (the actual cryptographic key). Two "
        "different features living at the same URL prefix; only one has "
        "a button.",
    'licensing_admin.assign_offline_policy':
        "Complete, permission-gated (offline_policies.manage, plus "
        "@require_recent_auth) handler for assigning an offline policy "
        "to a license exists in licensing_admin/routes.py, and licensing_"
        "admin/offline_policies.html lists every available policy -- but "
        "nothing on that page, nor on licensing/detail.html (the license "
        "page it would naturally act on), submits to this route.",
    'licensing_admin.entitlement_preview':
        "Complete, permission-gated (entitlement_resolution.preview) "
        "read-only view that resolves and previews a license's real "
        "entitlements exists in licensing_admin/routes.py with a real "
        "template (licensing_admin/entitlement_preview.html), but "
        "licensing/detail.html -- which links to issue/transition/"
        "replace/add-devices/slot-exceptions for the same license -- has "
        "no link to this one.",
    'operations_ui.regenerate_snapshot_route':
        "Complete, permission-gated (report_snapshots.regenerate -- a "
        "SEPARATE, more privileged permission than the report_snapshots."
        "view that gates plain generation) handler for recomputing an "
        "existing report snapshot exists in operations_ui/routes.py, but "
        "operations_ui/report_snapshots_list.html only wires the 'generate "
        "a new one' form (operations_ui.generate_snapshot_route); no "
        "per-row regenerate control exists.",
    'releases.versions':
        "Complete, permission-gated (catalog.view) read-only view over "
        "imported product versions exists in releases/routes.py with a "
        "real template (catalog/versions.html), but catalog/index.html -- "
        "the page it would naturally hang off -- links to plans only, "
        "and no nav/command-palette entry reaches it either.",
    'releases.channels':
        "Same gap as releases.versions, same file, same missing link: a "
        "complete read-only view over release channels (catalog/"
        "channels.html) that nothing in the UI ever points to.",
    'subscriptions.correct_payment_route':
        "Complete, permission-gated handler (subscriptions/routes.py's "
        "correct_payment_route, calling the real correct_payment() "
        "service function to fix a payment's status/note) exists, and "
        "subscriptions/detail.html wires the adjacent 'record a new "
        "payment' form (subscriptions.create_payment) -- but no control "
        "for correcting an existing one.",
}


def test_every_route_is_reachable_or_named():
    """The guard. A route no client calls must be written down, with a reason."""
    referenced = _referenced_endpoints(_client_source())
    unaccounted = []

    for r in _routes():
        ep = r['endpoint']
        if ep in referenced:
            continue
        if ep in INTENTIONALLY_UNREACHABLE or ep in KNOWN_GAPS:
            continue
        unaccounted.append('%s  [%s]  %s:%d' % (ep, r['methods'], r['file'].relative_to(OWNER_DIR), r['lineno']))

    assert not unaccounted, (
        'These Owner routes are reachable by NO shipped client (no url_for() '
        'call, no {\'endpoint\': ...} nav-model entry, no server-side '
        'redirect), and are not named in INTENTIONALLY_UNREACHABLE or '
        'KNOWN_GAPS:\n  '
        + '\n  '.join(sorted(unaccounted))
        + '\n\nA complete, correct, gated route that nothing calls is a '
          'feature nobody can use.\n'
          'Either give it a doorway, or add it to one of the two maps in '
          'this file WITH A REASON. Adding it without reading the route and '
          'checking every template/JS/redirect for a reference defeats the '
          'point.'
    )


def test_the_allowlists_do_not_rot():
    """Every named endpoint must still EXIST.

    A stale entry is worse than none: it silently excuses whatever route was
    renamed or deleted, and the next genuinely-unreachable route to reuse
    that name inherits the excuse.
    """
    live = {r['endpoint'] for r in _routes()}
    named = set(INTENTIONALLY_UNREACHABLE) | set(KNOWN_GAPS)
    stale = sorted(k for k in named if k not in live)
    assert not stale, (
        'These endpoints are named in this file but no route registers them '
        'any more:\n  '
        + '\n  '.join(stale)
        + '\n\nRemove them. A stale exemption silently covers whatever '
          'route reuses that endpoint name next.'
    )


def test_nothing_listed_is_actually_reachable():
    """The maps must not outlive the problem they describe.

    When a gap is finally fixed -- a form is added, a nav link appears -- its
    entry here becomes a lie, and a lie in an exemption list is how the NEXT
    unreachable route inherits a reason that was written about something
    else. A listed endpoint a client now calls is a failure, and the fix is
    to delete the entry.
    """
    referenced = _referenced_endpoints(_client_source())
    resolved = sorted(
        key for key in (set(INTENTIONALLY_UNREACHABLE) | set(KNOWN_GAPS))
        if key in referenced
    )
    assert not resolved, (
        'These endpoints are listed as unreachable but a client now calls '
        'them:\n  '
        + '\n  '.join(resolved)
        + '\n\nGood news -- the gap closed. Delete the entry, so the list '
          'keeps describing only what is still true.'
    )


def test_known_gaps_are_declared_not_hidden():
    """KNOWN_GAPS must never be empty-by-neglect, and never padded with a
    one-word excuse. Every entry must carry a real explanation."""
    for endpoint, reason in KNOWN_GAPS.items():
        assert len(reason) > 60, (
            '%s has no real reason written against it. A one-word excuse in '
            'this list is how a genuine gap becomes permanent.' % endpoint
        )
