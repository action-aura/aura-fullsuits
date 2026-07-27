# Phase 8V — Owner UI Security Boundary

Applies uniformly to every route added in `phase8-service-route-map.md`. Verified per-route in
`phase8v-automated-test-report.md`, not just declared here.

## CSRF

App-wide `CSRFProtect` (`app/extensions.py::csrf.init_app(app)`) already covers every POST route in
this Flask app, including all new ones -- nothing to add per-route. Every new template's `<form>`
includes `{{ csrf_token() }}` as a hidden field, matching every existing template.

## Server-side RBAC (never client-hidden-only)

Every new route carries `@require_permission("<code>")` from `app/security/rbac.py` -- the same
decorator, not a new mechanism. A hidden button/disabled-in-template is never the only gate; every
service-layer function these routes call was already independently permission-agnostic (callable
from a test or a future CLI without any HTTP layer), so the route is the *only* place authorization
can be bypassed, and it is where `require_permission` sits.

## Recent-auth / MFA

`@require_recent_auth` (existing decorator, `app/security/rbac.py`) applied to every route marked
"yes" in the map doc: renewal approve/apply, emergency-extension create/revoke, pending-activation
approve/reject, activation-policy management. This matches exactly the set Milestones 2/4/5 already
decided needed it at the service-design level -- Phase 8V does not change which actions require it,
only adds the HTTP entry point that enforces it.

## Object-level authorization (no IDOR)

Every `<id>` route parameter is looked up via `db_session.get()`/`select().where(Model.id == id)` and
a 404 is returned if the row doesn't exist -- no route trusts a client-supplied ID to imply
ownership beyond "this exact row exists," matching the existing `subscriptions.detail()` pattern.
Cross-tenant leakage is not applicable (Owner has no tenant partitioning below the whole-platform
staff level; every staff account with the permission can see every commercial record by design,
exactly as `subscriptions.list_subscriptions()` already works).

## Optimistic locking

`RenewalRequest` and `PilotRecord` both carry `version_id_col` (Milestones 2/4). The apply/approve
routes surface the current `version` in the form as a hidden field and re-check it is unchanged
before calling the service (the service itself already raises on a stale write via SQLAlchemy's
`StaleDataError` — the route's job is only to turn that into a friendly "someone else already
changed this, reload and retry" message instead of a 500).

## No secret display

Every new template is grep-checked (see `phase8v-automated-test-report.md`) for the same forbidden
substrings `assertions.py::_guard_payload()` already enforces: no full license key, no
`key_secret_hmac`, no pepper, no signing/device private key material, no raw session token, no DB
credential. `License.key_prefix`/`key_suffix_masked` (already-masked fields, Phase 6) are the only
key-shaped values ever rendered, matching the existing `licensing/detail.html` convention.

## No raw internal ID exposure beyond what already happens

Every existing Owner template already renders public UUID primary keys directly in URLs (there is no
separate "public ID" vs "internal ID" concept anywhere in this schema — the UUID primary key IS the
public identifier, unguessable by design). Phase 8V's new routes follow the same, already-established
convention; introducing a second ID scheme now would be schema redesign, out of scope.

## Output escaping / no unsafe HTML

Jinja2 autoescaping is on by default for this app (no `{% autoescape false %}` anywhere in the
existing templates, confirmed by grep) and no new template turns it off. No route accepts or renders
raw user HTML.

## Stable error handling

This app has no `flash()` mechanism anywhere (confirmed by grep across `app/`) -- the actual,
consistently-used convention (`auth/routes.py`, every login/MFA/password/invitation route) is:
re-render the same page template with an `error="<message>"` variable and a non-2xx status code on
failure, PRG (redirect) only on success. Every new route in this phase follows that exact existing
convention (`.error` CSS class already defined in `layout/base.html`) -- a domain error (e.g.
`InvalidPilotTransitionError`, `RenewalConcurrencyError`) becomes `render_template(..., error=str(exc)), 400`,
never a raw stack trace, and never a newly-invented flash/redirect pattern this codebase doesn't
already use.
