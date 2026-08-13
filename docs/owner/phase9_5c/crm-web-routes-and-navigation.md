# Phase 9.5C — Milestones 16/17: Web Routes, Forms, UI, Navigation

## Blueprints

- `app/leads/routes.py` — `leads` blueprint (`/leads` prefix): list,
  dashboard, new/create, detail, edit, status, assign, convert, archive,
  contacts, interactions, followups, notes, locations.
- `app/leads/routes.py` — `crm_shared` blueprint (no prefix):
  `/followups/<id>/complete`, `/followups/<id>/cancel`,
  `/locations/<id>/verify` — shared between Lead and Customer parents,
  matching the governing spec's own suggested top-level route list.
- `app/customers/routes.py` — extended (not replaced): added `/assign`,
  `/interactions`, `/followups`, `/locations` to the pre-existing
  `customers` blueprint.

Both registered in `app/__init__.py` alongside every other blueprint —
confirmed via `app.url_map.iter_rules()` returning the full expected
route set (236 total routes after this phase's additions, up from 215
pre-CRM).

## Conventions followed (matching `customers/routes.py` exactly)

- CSRF: global `csrf.init_app(app)` protection, no per-route exemption —
  every POST form includes `<input type="hidden" name="csrf_token"
  value="{{ csrf_token() }}">`.
- Post/Redirect/Get: every mutating route ends in a `redirect()`, never
  renders a mutated-state page directly from a POST handler.
- No destructive GET: every archive/status/assign/convert/complete/
  cancel/verify action is `methods=["POST"]` only.
- Ownership enforced on every parent route (`_lead_or_none()`/
  `_customer_visible_to()`) **and** every child route (contacts/
  interactions/followups/notes/locations routes all resolve and
  authorize the parent before touching the child).
- Stable-code errors localized only at this layer
  (`app.i18n_labels.localize_lead_error`/`localize_customer_crm_error`/
  `localize_location_error`), never inside `app.leads.*`/
  `app.customers.services` (Non-Negotiable Rule 10).

## Navigation (`layout/base.html`)

Added three links to the existing unconditional nav bar: **Leads**,
**CRM Dashboard** — placed alongside the pre-existing **Customers**
link. Matches this codebase's existing, established convention: **no**
link in this nav bar is currently permission-conditional (every existing
link — Staff, Backups, Activation Service, etc. — renders for every
logged-in `staff` regardless of their actual permissions, relying
entirely on server-side `@require_permission` to block unauthorized
access if clicked). The new CRM links follow this same pattern rather
than introducing a new, inconsistent nav-visibility mechanism only for
this feature. Non-Negotiable Rule 4 ("UI hiding is not authorization")
is satisfied either way, since every link — old and new — is backed by a
real server-side permission check.

## Explicit browser location capture (Milestone 17 requirement)

`leads/detail.html` and `customers/detail.html` both include a single
inline `<script>` block: a button click handler that calls
`navigator.geolocation.getCurrentPosition()` **exactly once** per click,
populates 3 hidden form fields, and submits — no `watchPosition`, no
interval, no capture on page load. Permission-denied and
unsupported-browser cases both show a plain `alert()` (functional, not
polished — a toast/inline-banner replacement is a follow-up UI
refinement, not a functional gap).

## Known gaps, honestly recorded

- Several POST handlers in `leads/routes.py` (`archive`,
  `add_contact_route`, `add_interaction_route`, `add_followup_route`,
  `add_note_route`, `add_location_route`, and the three `crm_shared`
  actions) still swallow a caught `LeadError`/`LocationValidationError`
  silently before redirecting (no `?error=` query param attached) —
  unlike `update`/`status`/`assign`/`assign_route` (Customer), which
  were upgraded to surface the error. The underlying validation/
  authorization is never bypassed (the mutation genuinely does not
  happen on failure); only the user-facing feedback is currently
  incomplete for this subset of actions. Tracked for a follow-up pass.
- Full `edit_form` (GET `/leads/<id>/edit`) was not built — only the
  POST handler exists, intended to be driven by an inline edit form on
  the detail page itself rather than a separate page (matches the
  detail-page-centric design already used for status/assign/etc.), but
  no dedicated inline-edit form markup was added to `leads/detail.html`
  this wave.
- The fuller employee/management filter sets from the spec's Milestone 6
  list (product interest, location-available, creating employee,
  department, date ranges, duplicate-review state) are not implemented
  in the list templates — only `status` filtering exists.
